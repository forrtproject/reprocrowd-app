"""Master-script templates: resolve the analysis plan and render a template into the package.

A template is a folder in templates/ with a template.toml (label, language, command, master, files) and
Jinja files; templates/_macros.j2 holds the shared step headers, templates/_README.md.j2 the README. To add
a template, add a folder: it is discovered automatically.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import jobs
from .curation import Curation
from .errors import ApiError, conflict, invalid
from .package import Package, atomic_write, sha256_file, slug
from .schemas.common import now
from .schemas.exports import AnalysisPlan, Paper, PlanStep, ResultsSchema
from .schemas.jobs import TodoFile, TodoLine, TodoStatus
from .schemas.manifest import OtherMeta, ScriptMeta

TODO_MARK = "TODO(LLM)"


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    language: str
    command: str
    master: str
    files: list[dict[str, str]]
    agent_rule: str = ""  # template-specific instruction in AGENT_PROMPT.md


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


OnModified = Literal["refuse", "keep", "overwrite"]


def write(pkg: Package, template_id: str, on_modified: OnModified = "refuse") -> dict[str, Any]:
    """Render the template, results_schema.json and analysis_plan.json.

    Files edited since they were generated (e.g. implemented by the agent): ``refuse`` (409), ``keep`` them and
    regenerate everything else (e.g. to update the exports after targets changed), or ``overwrite`` them."""
    tpls = templates()
    tpl = tpls.get(template_id)
    if tpl is None:
        raise ApiError(400, "unknown_template", f"no template {template_id!r}; available: {', '.join(tpls)}")
    jobs.ensure_idle(pkg)
    with pkg.locked():
        targets = [t for t, _ in Curation(pkg, "targets").effective()]
        if not targets or not Curation(pkg, "actions").effective():
            raise invalid("nothing_to_script", "at least one enabled target and one enabled action are needed")
        m = pkg.load()
        plan = resolve_plan(pkg)
        paper = Paper(doi=m.metareader.identifiers.get("doi") or (m.metareader.identifier.value if m.metareader.identifier and m.metareader.identifier.scheme == "doi" else None),
                      title=m.title, identifier=m.metareader.identifier, identifiers=m.metareader.identifiers)
        dests = {f["dest"] for f in tpl.files} | {"README.md"}

        dests.add(AGENT_PROMPT)
        # Refuse to overwrite files changed since they were generated (e.g. implemented by the agent).
        modified = [r.path for r in m.by_kind("script") if pkg.path(r.path).exists() and _current_hash(pkg, r.path) != r.metareader.generated_hash]
        modified += [d for d in dests if pkg.path(d).exists() and not m.get(f"script-{_rname(d)}")]
        modified = sorted(set(modified))
        if modified and on_modified == "refuse":
            raise conflict("script_modified", "these files changed since they were generated; keep them (on_modified=keep) or overwrite them",
                           {"files": modified})
        kept = modified if on_modified == "keep" else []

        env = _env()
        ctx = {"paper": paper, "plan": plan, "tpl": tpl, "marker": TODO_MARK, "steps_by_id": {s.id: s for s in plan.steps}}
        with pkg.edit() as m:
            created = not m.by_kind("script")
            for r in m.by_kind("script"):  # files of a previously chosen template
                if r.path not in dests:
                    if r.path in kept:  # edited: stays, but is no longer a generated file
                        r.metareader = OtherMeta(kind="code", added_at=r.metareader.added_at)
                    else:
                        pkg.remove(m, r.name)
            rendered = {f["dest"]: env.get_template(f"{tpl.id}/{f['src']}").render(ctx) for f in tpl.files}
            rendered["README.md"] = env.get_template("_README.md.j2").render(ctx)
            rendered[AGENT_PROMPT] = agent_prompt(pkg, tpl)
            rendered = {d: t for d, t in rendered.items() if d not in kept}
            for dest, text in rendered.items():
                atomic_write(pkg.path(dest), text.encode())
                digest = _current_hash(pkg, dest)
                pkg.put(m, pkg.describe(f"script-{_rname(dest)}", dest, type="file", format=dest.rsplit(".", 1)[-1].lower() if "." in dest else "txt",
                                        mediatype="text/plain", meta=ScriptMeta(added_at=now(), template=tpl.id, generated_hash=digest)))
            meta = OtherMeta(kind="export", added_at=now())
            pkg.write_json(m, "results-schema", "results_schema.json", ResultsSchema(paper=paper, results=targets), schema="results-schema", meta=meta)
            pkg.write_json(m, "analysis-plan", "analysis_plan.json", plan, schema="analysis-plan", meta=meta)
    return {"created": created, "template": tpl.id, "command": tpl.command, "master": tpl.master,
            "files": sorted(rendered) + ["analysis_plan.json", "results_schema.json"], "kept": kept}


def _rname(path: str) -> str:
    return slug(path)


def _current_hash(pkg: Package, rel: str) -> str:
    return f"sha256:{sha256_file(pkg.path(rel))}"


# ---------- state of the master script ----------

AGENT_PROMPT = "AGENT_PROMPT.md"
# An actionable marker is TODO(LLM) followed by a colon ("# TODO(LLM): implement A02"). The header line that
# explains the marker ("Every TODO(LLM) block must be implemented") has no colon and is not counted.
TODO_RE = re.compile(re.escape(TODO_MARK) + r":")


def current_template(pkg: Package) -> Template | None:
    scripts = pkg.load().by_kind("script")
    return templates().get(scripts[0].metareader.template) if scripts else None


def todo_status(pkg: Package, tpl: Template) -> TodoStatus:
    files = []
    for f in tpl.files:
        p = pkg.path(f["dest"])
        if not p.exists():
            continue
        lines = [TodoLine(line=i, text=line.strip()) for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if TODO_RE.search(line)]
        files.append(TodoFile(path=f["dest"], remaining=len(lines), lines=lines))
    return TodoStatus(remaining=sum(f.remaining for f in files), files=files)


def agent_prompt(pkg: Package, tpl: Template, instructions: str = "") -> str:
    m = pkg.load()
    used = [r for r in m.by_kind("input") if r.metareader.used]
    by_role = {role: [r for r in used if r.metareader.role == role] for role in ("data", "code", "other")}
    text = _env().get_template("_AGENT_PROMPT.md.j2").render(tpl=tpl, inputs=by_role, marker=TODO_MARK, folder=str(pkg.root))
    if instructions.strip():
        text += f"\nAdditional instructions:\n{instructions.strip()}\n"
    return text
