"""Running the master script (a job) and comparing its results with the targets.

The command is the template's own (template.toml), run with bash in the package folder; callers can't
supply one. outputs/results.json is removed first, so a failed run never leaves stale results behind (the
previous file stays in git history). After the run, new outputs are registered, results.json is checked,
and everything is committed.
"""
from __future__ import annotations

import logging
from typing import Any

from . import claims, files, gitrepo, jobs
from .compare import check_output, compare
from .config import settings
from .errors import conflict
from .package import Package
from .schemas.comparison import Comparison, CompareSettings
from .schemas.curation import Target
from .schemas.exports import ResultsSchema
from .schemas.jobs import Job, RunResult
from .scripts import current_template, refresh_runner

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
    ran = refresh_runner(pkg)  # claims mode: the claims ticked now whose file is implemented; None otherwise
    targets = frozen_targets(pkg)
    jid = jobs.new_id()
    pkg.path(RESULTS).unlink(missing_ok=True)
    use_git = gitrepo.available()
    if use_git:
        files.sync(pkg)
        gitrepo.commit(pkg, f"Before run ({jid})")

    def finish(job: Job) -> RunResult:
        files.sync(pkg)
        errors: list[str] = []
        warnings: list[str] = []
        exists = pkg.path(RESULTS).exists()
        if exists:
            try:
                errors, warnings, _ = check_output(pkg.read_json(RESULTS), targets)
            except ValueError as e:
                errors = [f"not valid JSON: {e}"]
        if exists and ran is not None:
            try:
                claims.record_run(pkg, jid, ran)  # claims mode: which claims this run covered, with their results
            except Exception:  # bookkeeping must not break the run
                logging.getLogger(__name__).exception("recording the claims of run %s failed", jid)
        after = gitrepo.commit(pkg, f"Run ({jid}, {job.status}, exit {job.exit_code})") if use_git else None
        return RunResult(command=tpl.command, log=job.stdout, results_file=exists, output_errors=errors, output_warnings=warnings, commit_after=after)

    return jobs.start(pkg, jid=jid, type="run", argv=["bash", "-c", tpl.command], stdin="", timeout_s=settings().run_timeout_s,
                      events_format="text", profile=None, request={"template": tpl.id, "command": tpl.command} | ({"claims": ran} if ran is not None else {}), finish=finish, merge_stderr=True,
                      env={"PYTHONUNBUFFERED": "1"})  # live, ordered log lines from Python pipelines


def comparison(pkg: Package, st: CompareSettings) -> Comparison:
    targets = frozen_targets(pkg)
    if not pkg.path(RESULTS).exists():
        raise conflict("results_missing", f"{RESULTS} doesn't exist; run the master script first")
    try:
        raw: Any = pkg.read_json(RESULTS)
    except ValueError as e:
        raw = {"error": str(e)}
    return compare(targets, raw, st)
