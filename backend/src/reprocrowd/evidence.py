"""Live and archived evidence of what the local process actually ran."""
from . import jobs
from .package import Package, sha256_file, atomic_write
from .schemas.common import now
from .schemas.evidence import ExecutionEvidence, TargetEvidence
from .schemas.manifest import OtherMeta

LOG_LIMIT = 5 * 1024 * 1024


def digest(pkg: Package, path: str) -> str | None:
    p = pkg.path(path)
    return f"sha256:{sha256_file(p)}" if p.is_file() else None


def snapshot(pkg: Package) -> dict[str, str]:
    m = pkg.load()
    paths = {"results_schema.json", "analysis_plan.json"}
    paths |= {r.path for r in m.resources if (r.reprocrowd.kind in ("script", "code") or
              r.reprocrowd.kind == "input" and r.reprocrowd.used) and
              not r.path.startswith(("outputs/", "evidence/"))}
    return {p: h for p in sorted(paths) if (h := digest(pkg, p)) is not None}


def latest_run(pkg: Package):
    return next((j for j in reversed(jobs.list_jobs(pkg)) if j.type == "run"), None)


def get(pkg: Package) -> ExecutionEvidence:
    from .run import frozen_targets, load_plan, RESULTS
    m = pkg.load()
    run = latest_run(pkg)
    ev = ExecutionEvidence(track=m.reprocrowd.track, stale_reason="no run")
    if run:
        ev.run = {"id": run.id, "status": run.status, "exit_code": run.exit_code,
                  "created_at": run.created_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                  "command": run.request.get("command"), "template": run.request.get("template")}
        result = run.result
        if result and result.type == "run" and result.snapshot is not None:
            current = snapshot(pkg)
            ev.stale = [p for p, h in result.snapshot.items() if current.get(p) != h]
            ev.added_since_run = sorted(set(current) - set(result.snapshot))
            ev.stale_hashes = {p: current.get(p) for p in sorted(set(ev.stale) | set(ev.added_since_run))}
            ev.stale_reason = None
            ev.results_match_run = digest(pkg, RESULTS) == result.results_sha256 if result.results_sha256 else False
            ev.log_sha256 = result.log_sha256
        else:
            ev.stale_reason = ("run in progress" if run.status == "running" else
                               "no run result recorded (interrupted, or the run's bookkeeping failed)" if result is None else
                               "no snapshot (older run)")
        # A log edited after the run no longer matches its recorded hash; it is not offered as the run's log.
        ev.log_path = run.stdout if pkg.path(run.stdout).is_file() and (ev.log_sha256 is None or digest(pkg, run.stdout) == ev.log_sha256) else None
    cg = next((j for j in reversed(jobs.list_jobs(pkg)) if j.type == "codegen"), None)
    if cg:
        ev.codegen = {"profile": cg.profile, "model": cg.request.get("model"), "allow_exec": cg.request.get("allow_exec"),
                      "exec_restriction": cg.request.get("exec_restriction", "unknown"),
                      "code_context": cg.request.get("code_context", "unknown"),
                      "code_files_on_disk": cg.request.get("code_files_on_disk", [])}
    if not pkg.path("results_schema.json").exists():
        return ev
    plan = load_plan(pkg)
    steps = {s.id: s for s in plan.steps} if plan else {}
    try:
        raw = pkg.read_json(RESULTS) if pkg.path(RESULTS).exists() else {}
    except ValueError:
        raw = {}
    results = raw.get("results") if isinstance(raw, dict) else None
    outputs = {r["id"]: r for r in results if isinstance(r, dict) and isinstance(r.get("id"), str)} if isinstance(results, list) else {}
    for t in frozen_targets(pkg):
        declared = outputs.get(t.id, {}).get("step")
        declared = declared if isinstance(declared, str) else None
        planned = next((s.id for s in steps.values() if t.id in s.produces and s.kind != "results"), None)
        if declared and plan is None:
            state = "declared_unchecked"  # no usable plan to check the declaration against
        elif declared:
            state = "declared_matches_plan" if declared in steps and t.id in steps[declared].produces else "declared_conflicts_plan"
        else:
            state = "planned_only" if planned else "none"
        ev.targets.append(TargetEvidence(target=t.id, refs=t.refs, step=declared or planned, step_evidence=state))
    return ev


def write(pkg: Package) -> ExecutionEvidence:
    ev = get(pkg)
    if ev.log_path and pkg.path(ev.log_path).is_file():
        p = pkg.path(ev.log_path)
        ev.log_truncated = p.stat().st_size > LOG_LIMIT
        with p.open("rb") as f:
            if ev.log_truncated:
                f.seek(-LOG_LIMIT, 2)
            data = f.read(LOG_LIMIT)
        ev.log_path = "evidence/run.log"
        atomic_write(pkg.path(ev.log_path), data)
    with pkg.edit() as m:
        meta = OtherMeta(kind="report", added_at=now())
        if not ev.log_path and m.get("evidence-log"):
            pkg.remove(m, "evidence-log")
        if ev.log_path == "evidence/run.log":
            pkg.put(m, pkg.describe("evidence-log", ev.log_path, type="file", format="log", mediatype="text/plain", meta=meta))
        pkg.write_json(m, "execution-evidence", "evidence/execution.json", ev, schema="execution-evidence", meta=meta)
    return ev
