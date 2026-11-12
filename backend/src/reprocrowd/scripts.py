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

from . import claims as claims_mod
from . import jobs
from .curation import Curation
from .errors import ApiError, conflict, invalid, not_found
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
    claims: dict | None = None  # claims mode: runner / prepare / claim files ({src, dest}) and its agent_rule


def templates() -> dict[str, Template]:
    out = {}
    for d in resources.files("reprocrowd.templates").iterdir():
        spec = d.joinpath("template.toml")
        if d.is_dir() and spec.is_file():
            data = tomllib.loads(spec.read_text(encoding="utf-8"))
            out[d.name] = Template(id=d.name, **data)
    return dict(sorted(out.items()))


def _env() -> Environment:
    return Environment(loader=PackageLoader("reprocrowd", "templates"), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True, autoescape=False)


def resolve_plan(pkg: Package) -> AnalysisPlan:
    """Load steps (one per used data input) → enabled actions in order → the results step.

    In claims mode (the package has claims) only the ticked claims' methods and targets and the transform actions."""
    m = pkg.load()
    data = [r for r in m.by_kind("input") if r.reprocrowd.used and r.reprocrowd.role == "data"]
    loads = [PlanStep(id=f"L{i:02d}", kind="load", summary=f"Load {r.path}", inputs=[r.path]) for i, r in enumerate(data, 1)]
    load_of = {r.name: s.id for r, s in zip(data, loads)}
    path_of = {r.name: r.path for r in m.by_kind("input")}
    actions = _ordered([a for a, _ in Curation(pkg, "actions").effective()])
    targets = {t.id for t, _ in Curation(pkg, "targets").effective()}
    if (scope := claims_mod.claim_scope(pkg)) is not None:
        actions = [a for a in actions if a.id in scope.actions]
        targets &= scope.targets
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
        if (scope := claims_mod.claim_scope(pkg)) is not None:
            return _write_claims(pkg, tpl, scope, on_modified)
        targets = [t for t, _ in Curation(pkg, "targets").effective()]
        if not targets or not Curation(pkg, "actions").effective():
            raise invalid("nothing_to_script", "at least one enabled target and one enabled action are needed")
        m = pkg.load()
        plan = resolve_plan(pkg)
        paper = _paper(m)
        dests = {f["dest"] for f in tpl.files} | {"README.md"}

        dests.add(AGENT_PROMPT)
        # Refuse to overwrite files changed since they were generated (e.g. implemented by the agent).
        modified = [r.path for r in m.by_kind("script") if pkg.path(r.path).exists() and _current_hash(pkg, r.path) != r.reprocrowd.generated_hash]
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
                        r.reprocrowd = OtherMeta(kind="code", added_at=r.reprocrowd.added_at)
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


def _paper(m) -> Paper:
    return Paper(doi=m.reprocrowd.identifiers.get("doi") or (m.reprocrowd.identifier.value if m.reprocrowd.identifier and m.reprocrowd.identifier.scheme == "doi" else None),
                 title=m.title, identifier=m.reprocrowd.identifier, identifiers=m.reprocrowd.identifiers)


def _claim_dest(tpl: Template, claim_id: str) -> str:
    return tpl.claims["claim"]["dest"].replace("{id}", claim_id)


def _claims_context(pkg: Package, tpl: Template, scope: claims_mod.ClaimScope) -> tuple[dict, list, Any]:
    """(base template context, the scope's targets, claim -> [{id, step, type, description}] of its evidence)."""
    plan = resolve_plan(pkg)
    steps = {s.id: s for s in plan.steps}
    targets = [t for t, _ in Curation(pkg, "targets").effective() if t.id in scope.targets]
    target_of = {t.id: t for t in targets}

    def claim_targets(c):
        return [{"id": t, "step": o.method if o.method in steps else c.id, "type": target_of[t].type, "description": target_of[t].description}
                for o in (c, *c.sub_claims) for t in o.evidence if t in target_of]

    base = {"paper": _paper(pkg.load()), "plan": plan, "tpl": tpl, "marker": TODO_MARK, "steps_by_id": steps}
    return base, targets, claim_targets


def _has_todo(pkg: Package, rel: str) -> bool:
    p = pkg.path(rel)
    return p.is_file() and bool(TODO_RE.search(p.read_text(encoding="utf-8", errors="replace")))


def runnable(pkg: Package, tpl: Template, scope: claims_mod.ClaimScope) -> list:
    """The scope's claims that a run covers: their file exists and has no TODO(LLM): marker left. A ticked claim whose
    file is not implemented yet is not run (and stays "Not run")."""
    return [c for c in scope.claims if pkg.path(_claim_dest(tpl, c.id)).is_file() and not _has_todo(pkg, _claim_dest(tpl, c.id))]


EMPTY_SCOPE = claims_mod.ClaimScope(claims=[], targets=set(), actions=set(), preparation=[])


def _put_runner(pkg: Package, m, tpl: Template, scope: claims_mod.ClaimScope) -> list[str]:
    """Render the runner: CLAIMS, the scope's runnable claims (their ids are returned); PENDING, its other ticked claims,
    which the runner itself runs only once their file has no marker left (the coding agent testing its work)."""
    base, targets, claim_targets = _claims_context(pkg, tpl, scope)
    run = runnable(pkg, tpl, scope)
    ctx = base | {"claims": [{"id": c.id, "targets": claim_targets(c)} for c in run],
                  "pending": [{"id": c.id, "targets": claim_targets(c)} for c in scope.claims if c not in run],
                  "fields": [{"id": t.id, "field": {"table": "table", "figure": "file"}[t.type]} for t in targets if t.type in ("table", "figure")]}
    spec = tpl.claims["runner"]
    _put_script(pkg, m, tpl, spec["dest"], _env().get_template(f"{tpl.id}/{spec['src']}").render(ctx), {"claims_role": "runner"})
    return [c.id for c in run]


def refresh_runner(pkg: Package) -> list[str] | None:
    """Claims mode: re-render only the runner from the current claim_scope (ticked claims with an implemented file), e.g.
    before a run, and return the ids of the claims it runs. Never touches the prepare or claim files. With every claim
    switched off the runner runs no claim. None (nothing done) unless the master script is a claims runner."""
    tpl = current_template(pkg)
    if tpl is None or tpl.claims is None:
        return None
    with pkg.locked():
        r = pkg.load().get(f"script-{_rname(tpl.claims['runner']['dest'])}")
        if r is None or not isinstance(r.reprocrowd, ScriptMeta) or r.reprocrowd.claims_role != "runner":
            return None
        with pkg.edit() as m:
            return _put_runner(pkg, m, tpl, claims_mod.claim_scope(pkg) or EMPTY_SCOPE)


def _generated_unchanged(pkg: Package, r) -> bool:
    return r is not None and isinstance(r.reprocrowd, ScriptMeta) and _current_hash(pkg, r.path) == r.reprocrowd.generated_hash


def _backup_path(pkg: Package, rel: str) -> str:
    stem, dot, ext = rel.rpartition(".") if "." in rel else (rel, "", "")
    cand, n = f"{stem}.pre-claims{dot}{ext}", 2
    while pkg.path(cand).exists():
        cand, n = f"{stem}.pre-claims-{n}{dot}{ext}", n + 1
    return cand


def _write_claims(pkg: Package, tpl: Template, scope: claims_mod.ClaimScope, on_modified: OnModified) -> dict[str, Any]:
    """Claims mode (called under the package lock): the runner (always regenerated; the agent never edits it), the
    prepare file (regenerated while unedited) and one file per ticked claim (written if missing, rewritten if the claim
    changed since and the file is unedited; an edited file is kept and marked out of date). Files of unticked claims stay on disk and are not run. on_modified applies only to an edited monolithic
    master that the runner would replace: refuse (409), keep (moved to <stem>.pre-claims.<ext>) or overwrite."""
    if tpl.claims is None:
        raise ApiError(400, "template_not_claims_ready", f"template {tpl.id!r} has no claims mode; available: "
                       + ", ".join(t.id for t in templates().values() if t.claims))
    if not scope.claims:
        raise invalid("nothing_to_script", "tick at least one testable claim that has an enabled evidence target")
    m = pkg.load()
    base, targets, claim_targets = _claims_context(pkg, tpl, scope)
    plan, steps = base["plan"], base["steps_by_id"]
    env = _env()
    spec = tpl.claims
    runner, prepare = spec["runner"]["dest"], spec["prepare"]["dest"]
    kept: list[str] = []

    # an edited master that is not a claims runner (e.g. the implemented monolithic script)
    old_master = m.get(f"script-{_rname(runner)}")
    backup = None
    if pkg.path(runner).exists() and not _generated_unchanged(pkg, old_master) and not (
            old_master is not None and isinstance(old_master.reprocrowd, ScriptMeta) and old_master.reprocrowd.claims_role == "runner"):
        if on_modified == "refuse":
            raise conflict("script_modified", "these files changed since they were generated; keep them (on_modified=keep, "
                           "moved aside) or overwrite them", {"files": [runner]})
        if on_modified == "keep":
            backup = _backup_path(pkg, runner)
            kept.append(backup)

    # (dest, src, context, ScriptMeta extras) of the prepare and claim files due
    out: list[tuple[str, str, dict, dict]] = []
    if not pkg.path(prepare).exists() or _generated_unchanged(pkg, m.get(f"script-{_rname(prepare)}")):
        prep_steps = [s for s in plan.steps if s.kind == "load" or s.id in scope.preparation]
        out.append((prepare, f"{tpl.id}/{spec['prepare']['src']}", base | {"steps": prep_steps}, {"claims_role": "prepare"}))
    else:
        kept.append(prepare)
    for c in scope.claims:
        dest = _claim_dest(tpl, c.id)
        digest = claims_mod.content_hash(pkg, c)
        r = m.get(f"script-{_rname(dest)}")
        generated = r is not None and isinstance(r.reprocrowd, ScriptMeta)
        # kept: the user's own file (not generated), an unchanged claim, or a file edited since it was generated (the
        # agent's code; its old claim_hash marks it out of date, so the agent prompt asks to revise it)
        if pkg.path(dest).exists() and (not generated or r.reprocrowd.claim_hash == digest or not _generated_unchanged(pkg, r)):
            kept.append(dest)
            continue
        ctx = base | {"claim": c, "targets": claim_targets(c), "steps": [steps[a] for a in claims_mod.methods(c) if a in steps]}
        out.append((dest, f"{tpl.id}/{spec['claim']['src']}", ctx, {"claim": c.id, "claim_hash": digest}))

    keep_paths = {d for d, *_ in out} | set(kept) | {runner, "README.md", AGENT_PROMPT}
    with pkg.edit() as m:
        created = not m.by_kind("script")
        if backup is not None:
            added = old_master.reprocrowd.added_at if old_master is not None else now()
            if old_master is not None:
                pkg.remove(m, old_master.name, delete_file=False)
            pkg.path(runner).replace(pkg.path(backup))
            pkg.put(m, pkg.describe(Package.free_name(m, "code-" + slug(backup, 60)), backup, type="file", format=backup.rsplit(".", 1)[-1].lower(),
                                    mediatype="text/plain", meta=OtherMeta(kind="code", added_at=added)))
        for r in m.by_kind("script"):
            if r.path in keep_paths or (r.reprocrowd.claim and r.reprocrowd.template == tpl.id):
                continue  # files of unticked claims stay
            if pkg.path(r.path).exists() and _current_hash(pkg, r.path) != r.reprocrowd.generated_hash:
                r.reprocrowd = OtherMeta(kind="code", added_at=r.reprocrowd.added_at)  # edited: no longer a generated file
            else:
                pkg.remove(m, r.name)
        written = [runner]
        for dest, src, ctx, extra in out:
            _put_script(pkg, m, tpl, dest, env.get_template(src).render(ctx), extra)
            written.append(dest)
        _put_runner(pkg, m, tpl, scope)  # after the claim files: it runs the implemented ones
        for dest in kept:  # implemented since: the manifest gets the current bytes and hash
            if m.get(f"script-{_rname(dest)}") is not None:
                pkg.refresh(m, f"script-{_rname(dest)}")
        _put_script(pkg, m, tpl, "README.md", env.get_template("_README.md.j2").render(base), {})
        _put_script(pkg, m, tpl, AGENT_PROMPT, agent_prompt(pkg, tpl, claims=_claim_todo(pkg, tpl, scope, m)), {})
        written += ["README.md", AGENT_PROMPT]
        meta = OtherMeta(kind="export", added_at=now())
        pkg.write_json(m, "results-schema", "results_schema.json", ResultsSchema(paper=base["paper"], results=targets), schema="results-schema", meta=meta)
        pkg.write_json(m, "analysis-plan", "analysis_plan.json", plan, schema="analysis-plan", meta=meta)
    return {"created": created, "template": tpl.id, "command": tpl.command, "master": runner,
            "files": sorted(written) + ["analysis_plan.json", "results_schema.json"], "kept": sorted(kept)}


def _put_script(pkg: Package, m, tpl: Template, dest: str, text: str, extra: dict) -> None:
    atomic_write(pkg.path(dest), text.encode())
    pkg.put(m, pkg.describe(f"script-{_rname(dest)}", dest, type="file", format=dest.rsplit(".", 1)[-1].lower() if "." in dest else "txt",
                            mediatype="text/plain", meta=ScriptMeta(added_at=now(), template=tpl.id, generated_hash=_current_hash(pkg, dest), **extra)))


def _claim_todo(pkg: Package, tpl: Template, scope: claims_mod.ClaimScope | None, m=None) -> list[dict]:
    """The prepare file and each ticked claim's file, with whether it still has TODO(LLM): markers and whether it is out
    of date (generated for an earlier version of the claim and kept because it was edited since)."""
    if tpl.claims is None or scope is None:
        return []
    m = m or pkg.load()  # m: the manifest being edited (claim_hash of files written just now)
    out = [{"path": tpl.claims["prepare"]["dest"], "claim": None, "outdated": False}]
    for c in scope.claims:
        r = m.get(f"script-{_rname(_claim_dest(tpl, c.id))}")
        outdated = (r is not None and isinstance(r.reprocrowd, ScriptMeta) and r.reprocrowd.claim_hash is not None
                    and r.reprocrowd.claim_hash != claims_mod.content_hash(pkg, c))
        out.append({"path": _claim_dest(tpl, c.id), "claim": c, "outdated": outdated})
    for f in out:
        f["todo"] = _has_todo(pkg, f["path"])
    return out


def outdated_claims(pkg: Package, tpl: Template, claim: str | None = None) -> dict[str, str]:
    """{claim file: the claim's content hash now} of the out-of-date claim files a codegen job asks the agent to revise
    (all of them, or only `claim`'s)."""
    if tpl.claims is None or not _claims_script(pkg):
        return {}
    return {f["path"]: claims_mod.content_hash(pkg, f["claim"]) for f in _claim_todo(pkg, tpl, claims_mod.claim_scope(pkg))
            if f["claim"] is not None and f["outdated"] and (claim is None or f["claim"].id == claim)}


def mark_revised(pkg: Package, revised: dict[str, str]) -> None:
    """After a successful codegen job: the claim files it revised are up to date with the claim hashes it was given."""
    if not revised:
        return
    with pkg.edit() as m:
        for path, digest in revised.items():
            r = m.get(f"script-{_rname(path)}")
            if r is not None and isinstance(r.reprocrowd, ScriptMeta):
                r.reprocrowd.claim_hash = digest


def _claims_script(pkg: Package) -> bool:
    """The package's master script was generated in claims mode."""
    return any(getattr(r.reprocrowd, "claim", None) for r in pkg.load().by_kind("script"))


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
    return templates().get(scripts[0].reprocrowd.template) if scripts else None


def todo_status(pkg: Package, tpl: Template) -> TodoStatus:
    files = []
    paths = [f["dest"] for f in tpl.files]
    if tpl.claims and _claims_script(pkg):
        paths = [tpl.claims["runner"]["dest"]] + [f["path"] for f in _claim_todo(pkg, tpl, claims_mod.claim_scope(pkg))]
    for dest in paths:
        p = pkg.path(dest)
        if not p.exists():
            continue
        lines = [TodoLine(line=i, text=line.strip()) for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if TODO_RE.search(line)]
        files.append(TodoFile(path=dest, remaining=len(lines), lines=lines))
    return TodoStatus(remaining=sum(f.remaining for f in files), files=files)


def agent_prompt(pkg: Package, tpl: Template, instructions: str = "", claim: str | None = None, *,
                 claims: list[dict] | None = None) -> str:
    """claims: the files of a claims-mode script (see _claim_todo); found from the package when not given.
    claim: scope the prompt to this claim's file (and the prepare file while it has TODO markers)."""
    m = pkg.load()
    used = [r for r in m.by_kind("input") if r.reprocrowd.used]
    by_role = {role: [r for r in used if r.reprocrowd.role == role] for role in ("data", "code", "other")}
    if claims is None and tpl.claims and _claims_script(pkg):
        claims = _claim_todo(pkg, tpl, claims_mod.claim_scope(pkg))
    one = None
    if claim:
        one = _scoped_claim(pkg, tpl, claim)
        claims = [f for f in claims or [] if f["claim"] is None and f["todo"]] + [{**f, "revise": not f["todo"]} for f in claims or [] if f["claim"] and f["claim"].id == claim]
    text = _env().get_template("_AGENT_PROMPT.md.j2").render(tpl=tpl, inputs=by_role, marker=TODO_MARK, folder=str(pkg.root),
                                                             claims=claims or [], one=one)
    if instructions.strip():
        text += f"\nAdditional instructions:\n{instructions.strip()}\n"
    return text


def _scoped_claim(pkg: Package, tpl: Template, claim_id: str):
    """The claim a codegen job is scoped to; raises the precondition problems (404, then 409s)."""
    if tpl.claims is None:
        raise conflict("claim_not_checked", "this template has no claims mode")
    found = next((c for c, _ in Curation(pkg, "claims").effective() if c.id == claim_id), None)
    if found is None:
        raise not_found("claim", claim_id)
    if not found.testable:
        raise conflict("claim_not_testable", f"claim {claim_id} is not testable")
    if not claims_mod.evidence(found):
        raise conflict("claim_no_evidence", f"claim {claim_id} has no evidence targets")
    scope = claims_mod.claim_scope(pkg)
    if not found.checked or scope is None or claim_id not in {c.id for c in scope.claims}:
        raise conflict("claim_not_checked", f"claim {claim_id} is not ticked; tick it and regenerate the script")
    if not (pkg.path(_claim_dest(tpl, claim_id)).exists() and claims_mod.claim_files(pkg, claim_id)):
        raise conflict("script_missing", f"claim {claim_id} has no file yet; regenerate the script (PUT …/script)")
    return found
