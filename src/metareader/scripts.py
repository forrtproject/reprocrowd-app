"""Master-script templates: resolve the analysis plan and render a template into the package.

A template is a folder in templates/ with a template.toml (label, language, command, master, files) and
Jinja files; templates/_macros.j2 holds the shared step headers, templates/_README.md.j2 the README. To add
a template, add a folder: it is discovered automatically.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from .curation import Curation
from .errors import ApiError, conflict, invalid
from .package import Package, atomic_write, sha256_file, slug
from .schemas.common import now
from .schemas.exports import AnalysisPlan, Paper, PlanStep, ResultsSchema
from .schemas.manifest import OtherMeta

TODO_MARK = "TODO(LLM)"


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    language: str
    command: str
    master: str
    files: list[dict[str, str]]


def templates() -> dict[str, Template]:
    out = {}
    for d in resources.files("metareader.templates").iterdir():
        spec = d.joinpath("template.toml")
        if d.is_dir() and spec.is_file():
            data = tomllib.loads(spec.read_text(encoding="utf-8"))
            out[d.name] = Template(id=d.name, **data)
    return dict(sorted(out.items()))


def _env() -> Environment:
    return Environment(loader=PackageLoader("metareader", "templates"), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True, autoescape=False)


def resolve_plan(pkg: Package) -> AnalysisPlan:
    """Load steps (one per used data input) → enabled actions in order → the results step."""
    m = pkg.load()
    data = [r for r in m.by_kind("input") if r.metareader.used and r.metareader.role == "data"]
    loads = [PlanStep(id=f"L{i:02d}", kind="load", summary=f"Load {r.path}", inputs=[r.path]) for i, r in enumerate(data, 1)]
    load_of = {r.name: s.id for r, s in zip(data, loads)}
    path_of = {r.name: r.path for r in m.by_kind("input")}
    actions = _ordered([a for a, _ in Curation(pkg, "actions").effective()])
    targets = {t.id for t, _ in Curation(pkg, "targets").effective()}
    enabled = {a.id for a in actions}
    mid: list[PlanStep] = []
    for i, a in enumerate(actions):
        deps = [d for d in a.depends_on if d in enabled] + [load_of[n] for n in a.inputs if n in load_of]
        if not deps:
            deps = [s.id for s in loads] if i == 0 else [actions[i - 1].id]
        mid.append(PlanStep(id=a.id, kind=a.kind, summary=a.summary, quote=a.quote, refs=a.refs,
                            inputs=[path_of[n] for n in a.inputs if n in path_of], depends_on=list(dict.fromkeys(deps)),
                            produces=[t for t in a.produces if t in targets]))
    used = {d for s in mid for d in s.depends_on}
    sinks = [s.id for s in mid if s.id not in used]
    out = PlanStep(id="OUT", kind="results",
                   summary="Collect all reproduced results and write outputs/results.json (format: schemas/results-output.schema.json); "
                           "save figures to outputs/figures/<target id>.png",
                   depends_on=sinks or [s.id for s in loads], produces=sorted(targets))
    return AnalysisPlan(steps=[*loads, *mid, out])


def _ordered(actions: list) -> list:
    """Creation (id) order, with each action moved after the actions it depends on (stable topological sort)."""
    pending = sorted(actions, key=lambda a: int(a.id[1:]))
    ids = {a.id for a in pending}
    done: set[str] = set()
    out = []
    while pending:
        nxt = next((a for a in pending if all(d in done or d not in ids for d in a.depends_on)), pending[0])  # cycles are rejected earlier
        pending.remove(nxt)
        done.add(nxt.id)
        out.append(nxt)
    return out


def write(pkg: Package, template_id: str, force: bool) -> dict[str, Any]:
    tpls = templates()
    tpl = tpls.get(template_id)
    if tpl is None:
        raise ApiError(400, "unknown_template", f"no template {template_id!r}; available: {', '.join(tpls)}")
    with pkg.locked():
        targets = [t for t, _ in Curation(pkg, "targets").effective()]
        if not targets or not Curation(pkg, "actions").effective():
            raise invalid("nothing_to_script", "at least one enabled target and one enabled action are needed")
        m = pkg.load()
        plan = resolve_plan(pkg)
        paper = Paper(title=m.title, identifier=m.metareader.identifier, identifiers=m.metareader.identifiers)
        dests = {f["dest"] for f in tpl.files} | {"README.md"}

        # Refuse to overwrite files changed since they were generated (the manifest has their hash at that time).
        modified = []
        for r in m.by_kind("script"):
            p = pkg.path(r.path)
            if p.exists() and f"sha256:{sha256_file(p)}" != r.hash:
                modified.append(r.path)
        modified += [d for d in dests if pkg.path(d).exists() and not m.get(f"script-{_rname(d)}")]
        if modified and not force:
            raise conflict("script_modified", "these files changed since they were generated; pass force to overwrite",
                           {"files": sorted(set(modified))})

        env = _env()
        ctx = {"paper": paper, "plan": plan, "tpl": tpl, "marker": TODO_MARK, "steps_by_id": {s.id: s for s in plan.steps}}
        with pkg.edit() as m:
            created = not m.by_kind("script")
            for r in m.by_kind("script"):  # files of a previously chosen template
                if r.path not in dests:
                    pkg.remove(m, r.name)
            rendered = {f["dest"]: env.get_template(f"{tpl.id}/{f['src']}").render(ctx) for f in tpl.files}
            rendered["README.md"] = env.get_template("_README.md.j2").render(ctx)
            for dest, text in rendered.items():
                atomic_write(pkg.path(dest), text.encode())
                pkg.put(m, pkg.describe(f"script-{_rname(dest)}", dest, type="file", format=dest.rsplit(".", 1)[-1].lower() if "." in dest else "txt",
                                        mediatype="text/plain", meta=OtherMeta(kind="script", added_at=now())))
            meta = OtherMeta(kind="export", added_at=now())
            pkg.write_json(m, "results-schema", "results_schema.json", ResultsSchema(paper=paper, targets=targets), schema="results-schema", meta=meta)
            pkg.write_json(m, "analysis-plan", "analysis_plan.json", plan, schema="analysis-plan", meta=meta)
    return {"created": created, "template": tpl.id, "command": tpl.command, "master": tpl.master,
            "files": sorted(rendered) + ["analysis_plan.json", "results_schema.json"]}


def _rname(path: str) -> str:
    return slug(path)
