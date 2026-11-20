"""Running the master script (a job) and comparing its results with the targets.

The command is the template's own (template.toml), run in the package folder; callers can't supply one. It is a
program and its arguments, started directly rather than through a shell, so Windows needs no bash; the program is
looked up where Python and R are usually installed (run_argv). outputs/results.json is removed first, so a failed run never leaves stale results behind (the
previous file stays in git history). After the run, new outputs are registered, results.json is checked,
and everything is committed.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import childenv, files, gitrepo, jobs, evidence
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
    argv = run_argv(tpl.command)  # before anything is cleaned up or committed: a missing program changes nothing
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

    return jobs.start(pkg, jid=jid, type="run", argv=argv, stdin="", timeout_s=settings().run_timeout_s,
                      events_format="text", profile=None, request={"template": tpl.id, "command": tpl.command}, finish=finish, merge_stderr=True,
                      env={"PYTHONUNBUFFERED": "1"})  # live, ordered log lines from Python pipelines


# Other names for a template's program. On Windows, Python is usually `python` or the `py` launcher; `python3` there
# may be only the Microsoft Store's stand-in, which asks to install Python instead of running the script.
ALTERNATIVES: dict[str, list[list[str]]] = {"python3": [["python3"], ["python"], ["py", "-3"]]}
INSTALL_HINTS = {
    "python3": "Python isn't installed, or ReproCrowd can't find it. Install it from https://www.python.org/downloads/, then run again.",
    "Rscript": "R isn't installed, or ReproCrowd can't find it. Install it from https://cloud.r-project.org/, then run again.",
}


def _store_stub(path: str) -> bool:
    """An app alias in WindowsApps: the Microsoft Store's stand-in, or a Python installed from the Store."""
    return os.name == "nt" and "\\microsoft\\windowsapps\\" in path.lower()


def _runs(argv: list[str]) -> bool:
    """Does the program start? The Store's stand-in exits with an error (and an offer to install Python)."""
    try:
        # with the run's environment (the scripts' allowlist), not the service's
        return subprocess.run([*argv, "--version"], capture_output=True, timeout=15, env=jobs.job_env(base=childenv.for_script())).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _r_installs() -> list[str]:
    """Rscript where R's installers put it, newest version first: Windows doesn't add R to the PATH."""
    if sys.platform == "darwin":
        return [p for p in ["/Library/Frameworks/R.framework/Resources/bin/Rscript"] if Path(p).is_file()]
    if os.name != "nt":
        return []
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files")]  # per machine, and per user:
    roots += [os.path.join(os.environ["LOCALAPPDATA"], "Programs")] if os.environ.get("LOCALAPPDATA") else []
    found = [p for root in roots for p in glob.glob(os.path.join(root, "R", "R-*", "bin", "Rscript.exe"))]
    version = lambda p: tuple(int(n) for n in re.findall(r"\d+", Path(p).parent.parent.name))
    return sorted(found, key=version, reverse=True)


def find_program(name: str) -> list[str] | None:
    """The command line that starts the template's program, or None if it isn't installed."""
    options = ALTERNATIVES.get(name, [[name]])
    path = jobs.job_env(base=childenv.for_script()).get("PATH")  # the run's own PATH, without ReproCrowd's virtualenv
    found = [[exe, *rest] for prog, *rest in options if (exe := shutil.which(prog, path=path))]
    if name == "Rscript":
        found += [[p] for p in _r_installs()]
    # Store aliases last, and only if they really start (a Store-installed Python does; the stand-in doesn't).
    found = [f for f in found if not _store_stub(f[0])] + [f for f in found if _store_stub(f[0]) and _runs(f)]
    return found[0] if found else None


def run_argv(command: str) -> list[str]:
    """The template's command as the program and its arguments, the program found by find_program."""
    name, *args = shlex.split(command)
    program = find_program(name)
    if program is None:
        raise conflict("program_not_found", INSTALL_HINTS.get(name, f"{name} isn't installed, or ReproCrowd can't find it. Install it, then run again."),
                       {"program": name})
    return [*program, *args]


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
