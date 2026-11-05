"""The reproduction report (report.html + comparison.json) and the package archive (ZIP).

The report is rendered from templates/_report.html.j2. The archive holds exactly what the manifest lists
(datapackage.json + every resource), so it is a valid Frictionless zipped data package. It is only built
from a package that passes validation, and its hash is recorded in the manifest for submission.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import jobs
from .errors import conflict
from .figures import listing as figure_listing
from .package import MANIFEST, Package, atomic_write, sha256_file, slug
from .run import RESULTS, comparison, frozen_targets
from .schemas.common import now
from . import provenance
from .extractions import get as get_extraction
from .schemas.comparison import STATUS_LABEL, CompareSettings
from .schemas.exports import AnalysisPlan, InputEntry, InputsList
from .schemas.manifest import ArchiveInfo, OtherMeta
from .scripts import current_template
from .validation import validate_package

REPORT = "report.html"
COMPARISON = "comparison.json"
ARCHIVE_DIR = "archive"

def _last_run(pkg: Package):
    runs = [j for j in jobs.list_jobs(pkg) if j.type == "run"]
    return runs[-1] if runs else None


def write_report(pkg: Package, st: CompareSettings) -> dict[str, Any]:
    jobs.ensure_idle(pkg)
    tpl = current_template(pkg)
    cmp = comparison(pkg, st)  # raises script_missing / results_missing
    targets = frozen_targets(pkg)
    plan = AnalysisPlan.model_validate(pkg.read_json("analysis_plan.json"))
    m = pkg.load()
    work: dict[str, Any] = pkg.read_json("metadata/openalex.json") if m.get("metadata-openalex") else {}
    raw_out = pkg.read_json(RESULTS)
    raw_out = raw_out if isinstance(raw_out, dict) else {}
    outputs = {r.get("id"): r.get("step") for r in raw_out.get("results", []) if isinstance(r, dict)}
    files = {r.path for r in m.resources}
    # original crops (targets/figures/, see figures.py) and the reproduction's files, linked by relative path
    pages = {f["target"]: f["page"] for f in figure_listing(pkg) if f["method"] == "page"}
    figures = {t.id: {"original": f"targets/figures/{t.id}.png" if f"targets/figures/{t.id}.png" in files else None, "reproduced": None, "page": pages.get(t.id)}
               for t in targets if t.type == "figure"}
    for row in cmp.rows:
        if row.target in figures and row.status == "figure":
            figures[row.target]["reproduced"] = row.reproduced_text
    llm_runs = []
    from .curation import Curation

    for kind in ("targets", "actions"):
        for run in Curation(pkg, kind).load().llm_runs:
            llm_runs.append({"kind": kind, "id": run.id, "model": run.call.model, "effort": run.call.effort, "profile": run.call.profile,
                             "entries": len(run.entries), "rejected": len(run.rejected), "disabled": run.disabled})
    codegen = [j for j in jobs.list_jobs(pkg) if j.type == "codegen"]
    docs = {}
    for r in m.by_kind("extraction"):
        try:
            docs[r.name] = get_extraction(pkg, r.name)[1]
        except Exception:  # unreadable extraction: passages show without text; validation reports it
            pass
    target_refs = {t.id: [(ref.extraction, ref.id) for ref in t.refs] for t in targets}
    graph = provenance.build(m, docs, plan, cmp, outputs, target_refs)
    traces = {t.id: provenance.trace(graph, t.id) for t in targets}
    validation = validate_package(pkg)
    numeric = [r for r in cmp.rows if r.type != "figure"]
    env = Environment(loader=PackageLoader("reprocrowd", "templates"), undefined=StrictUndefined, autoescape=True, trim_blocks=True, lstrip_blocks=True)
    html = env.get_template("_report.html.j2").render(
        title=m.title or "(untitled)", identifiers=m.reprocrowd.identifiers,
        authors=", ".join(a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])),
        generated_at=now().isoformat(), cmp=cmp, settings=st, labels=STATUS_LABEL, targets={t.id: t for t in targets},
        reproduced=sum(r.status in ("match", "within") for r in numeric), numeric=len(numeric),
        plan=plan, svg=provenance.svg(graph), lineage=graph.lineage(), traces=traces, colors=provenance.COLORS, tpl=tpl, run=_last_run(pkg), codegen=codegen[-1] if codegen else None,
        inputs=m.by_kind("input"), extractions=m.by_kind("extraction"), fulltexts=m.by_kind("fulltext"), llm_runs=llm_runs,
        figures=figures, validation=validation, resources=sorted(m.resources, key=lambda r: r.path))
    with pkg.edit() as m:
        atomic_write(pkg.path(REPORT), html.encode())
        pkg.put(m, pkg.describe("report", REPORT, type="file", format="html", mediatype="text/html", meta=OtherMeta(kind="report", added_at=now())))
        pkg.write_json(m, "comparison", COMPARISON, cmp, schema="comparison", meta=OtherMeta(kind="report", added_at=now()))
    return {"path": REPORT, "comparison": COMPARISON, "summary": cmp.summary, "reproduced": f"{sum(r.status in ('match', 'within') for r in numeric)}/{len(numeric)}"}


def write_archive(pkg: Package) -> ArchiveInfo:
    jobs.ensure_idle(pkg)
    report = validate_package(pkg)
    if not report.valid:
        raise conflict("package_invalid", "the package doesn't validate; fix it before archiving",
                       {"errors": [e.model_dump(exclude_none=True) for e in report.errors][:50]})
    m = pkg.load()
    if not m.get("report"):
        raise conflict("report_missing", "create the report first (PUT …/report)")
    with pkg.edit() as m:  # inputs.json: the input list in the package database's format
        entries = [InputEntry(path=r.path, role=r.reprocrowd.role, note=r.reprocrowd.note, used=r.reprocrowd.used,
                              archive=m.get(r.reprocrowd.archive).path if r.reprocrowd.archive and m.get(r.reprocrowd.archive) else None)
                   for r in m.by_kind("input")]
        pkg.write_json(m, "inputs-list", "inputs.json", InputsList(inputs=entries), schema="inputs", meta=OtherMeta(kind="export", added_at=now()))
    stem = slug(m.reprocrowd.identifiers.get("doi") or m.name).replace(".", "-")
    rel = f"{ARCHIVE_DIR}/{stem}-reproduction.zip"
    out = pkg.path(rel)
    out.parent.mkdir(exist_ok=True)
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(pkg.path(MANIFEST), MANIFEST)
        for r in sorted(m.resources, key=lambda r: r.path):
            z.write(pkg.path(r.path), r.path)
    tmp.replace(out)
    info = ArchiveInfo(path=rel, bytes=out.stat().st_size, sha256=sha256_file(out), created_at=now(),
                       manifest_sha256=sha256_file(pkg.path(MANIFEST)), files=len(m.resources) + 1)
    with pkg.edit() as m:
        m.reprocrowd.archive = info
    return info


def archive_path(pkg: Package) -> Path:
    info = pkg.load().reprocrowd.archive
    if info is None or not pkg.path(info.path).exists():
        raise conflict("archive_missing", "create the archive first (PUT …/archive)")
    return pkg.path(info.path)
