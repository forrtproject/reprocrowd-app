"""Running the master script (a job) and comparing its results with the targets.

The command is the template's own (template.toml), run with bash in the package folder; callers can't
supply one. outputs/results.json is removed first, so a failed run never leaves stale results behind (the
previous file stays in git history). After the run, new outputs are registered, results.json is checked,
and everything is committed.
"""
from __future__ import annotations

from typing import Any

from . import files, gitrepo, jobs, evidence
from .compare import check_output, compare
from .config import settings
from .errors import conflict
from .package import Package
from .schemas.comparison import Comparison, CompareSettings, ComparisonBasis
from .schemas.common import now
from .schemas.exports import AnalysisPlan
from .schemas.curation import Target
from .schemas.exports import ResultsSchema
from .schemas.jobs import Job, RunResult
from .scripts import current_template

RESULTS = "outputs/results.json"


def frozen_targets(pkg: Package) -> list[Target]:
    """The targets the code was written against (results_schema.json from the script step)."""
    if not pkg.path("results_schema.json").exists():
        raise conflict("script_missing", "create the master script first (PUT …/script)")
    return ResultsSchema.model_validate(pkg.read_json("results_schema.json")).results


def start(pkg: Package) -> Job:
    tpl = current_template(pkg)
    if tpl is None:
        raise conflict("script_missing", "create the master script first (PUT …/script)")
    jobs.ensure_idle(pkg)
    targets = frozen_targets(pkg)
    jid = jobs.new_id(pkg)
    pkg.path(RESULTS).unlink(missing_ok=True)
    files.sync(pkg)
    before_snapshot = evidence.snapshot(pkg)
    before = None
    use_git = gitrepo.available()
    if use_git:
        before = gitrepo.commit(pkg, f"Before run ({jid})") or gitrepo.head(pkg)

    def finish(job: Job) -> RunResult:
        files.sync(pkg)
        errors: list[str] = []
        warnings: list[str] = []
        exists = pkg.path(RESULTS).exists()
        if exists:
            try:
                plan = load_plan(pkg)
                errors, warnings, _ = check_output(pkg.read_json(RESULTS), targets, plan)
                warnings += [] if plan else [NO_PLAN]
            except ValueError as e:
                errors = [f"not valid JSON: {e}"]
        after = gitrepo.commit(pkg, f"Run ({jid}, {job.status}, exit {job.exit_code})") if use_git else None
        return RunResult(commit_before=before, snapshot=before_snapshot, results_sha256=evidence.digest(pkg, RESULTS),
                         log_sha256=evidence.digest(pkg, job.stdout), command=tpl.command, log=job.stdout, results_file=exists, output_errors=errors, output_warnings=warnings, commit_after=after)

    return jobs.start(pkg, jid=jid, type="run", argv=["bash", "-c", tpl.command], stdin="", timeout_s=settings().run_timeout_s,
                      events_format="text", profile=None, request={"template": tpl.id, "command": tpl.command}, finish=finish, merge_stderr=True,
                      env={"PYTHONUNBUFFERED": "1"})  # live, ordered log lines from Python pipelines


NO_PLAN = "analysis_plan.json is missing or invalid; output steps were not checked against the plan"


def load_plan(pkg: Package) -> AnalysisPlan | None:
    """The frozen plan, or None if it is missing or invalid: it only adds step warnings."""
    try:
        return AnalysisPlan.model_validate(pkg.read_json("analysis_plan.json"))
    except (OSError, ValueError):
        return None


def comparison(pkg: Package, st: CompareSettings) -> Comparison:
    targets = frozen_targets(pkg)
    if not pkg.path(RESULTS).exists():
        raise conflict("results_missing", f"{RESULTS} doesn't exist; run the master script first")
    try:
        raw: Any = pkg.read_json(RESULTS)
    except ValueError as e:
        raw = {"error": str(e)}
    result = compare(targets, raw, st)
    result.basis = ComparisonBasis(results_sha256=evidence.digest(pkg, RESULTS), results_schema_sha256=evidence.digest(pkg, "results_schema.json"), created_at=now())
    plan = load_plan(pkg)
    _, warnings, _ = check_output(raw, targets, plan)
    result.output_warnings = warnings + ([] if plan else [NO_PLAN])
    return result
