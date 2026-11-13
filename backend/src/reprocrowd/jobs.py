"""Jobs: long-running processes started by the API and followed asynchronously.

A job is a configured command run in the package folder in its own process group, with stdout and stderr
written to jobs/<id>/. Its record (jobs/<id>.json, schema "job") is written when it starts and when it ends.
A ``finish`` hook (sync files, git commit, count TODOs, check outputs) produces the job's result.

At most one job runs per package: jobs change the package's files. When the service shuts down it ends its
running jobs (stop_all), which are recorded as "interrupted"; so is a job whose service died without that.
"""
from __future__ import annotations

import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .errors import ApiError, conflict, not_found
from .llm import resolve_argv
from .package import Package, atomic_write, dump_json
from .schemas.common import now
from .schemas.jobs import Job, JobType

log = logging.getLogger("reprocrowd.jobs")
_procs: dict[str, subprocess.Popen] = {}  # "<package root>::<job id>" → process
_cancelled: set[str] = set()
_interrupted: set[str] = set()  # ended by stop_all
_guard = threading.Lock()
KILL_GRACE_S = 5


def _key(pkg: Package, jid: str) -> str:
    return f"{pkg.root}::{jid}"


def new_id() -> str:
    return f"job-{time.strftime('%Y%m%d-%H%M%S').lower()}-{secrets.token_hex(2)}"


def _record_path(pkg: Package, jid: str) -> Path:
    return pkg.path(f"jobs/{jid}.json")


def save(pkg: Package, job: Job) -> None:
    atomic_write(_record_path(pkg, job.id), dump_json(job))


def load(pkg: Package, jid: str) -> Job:
    p = _record_path(pkg, jid)
    if not jid.startswith("job-") or not p.exists():
        raise not_found("job", jid)
    job = Job.model_validate_json(p.read_bytes())
    if job.status == "running":
        with _guard:
            if _key(pkg, jid) not in _procs:
                job = Job.model_validate_json(p.read_bytes())  # it may have just ended: the watcher saves before it deregisters
                if job.status == "running":
                    job.status, job.finished_at = "interrupted", now()  # the service stopped while it ran
                    save(pkg, job)
    return job


def list_jobs(pkg: Package) -> list[Job]:
    d = pkg.path("jobs")
    if not d.exists():
        return []
    return sorted((load(pkg, p.stem) for p in d.glob("job-*.json")), key=lambda j: j.created_at)


def active(pkg: Package) -> Job | None:
    with _guard:
        keys = [k for k in _procs if k.startswith(f"{pkg.root}::")]
    return load(pkg, keys[0].split("::", 1)[1]) if keys else None


def running() -> list[tuple[Path, str]]:
    """(package folder, job id) of every job running in this service now."""
    with _guard:
        keys = list(_procs)
    return [(Path(root), jid) for root, jid in (k.rsplit("::", 1) for k in keys)]


def ensure_idle(pkg: Package) -> None:
    if job := active(pkg):
        raise conflict("job_running", f"{job.id} ({job.type}) is running in this package; wait for it or cancel it", {"job": job.id})


def job_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The service's environment minus its own virtualenv: jobs run the user's tools (python3, Rscript, …), not the
    interpreter ReproCrowd happens to be installed in."""
    env = dict(os.environ)
    own = Path(sys.prefix)
    if own != Path(sys.base_prefix):  # running inside a virtualenv
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and Path(p).parent != own)
        if Path(env.get("VIRTUAL_ENV", "/nonexistent")) == own:
            env.pop("VIRTUAL_ENV")
    return env | (extra or {})


def start(pkg: Package, *, jid: str, type: JobType, argv: list[str], stdin: str, timeout_s: int, events_format: str,
          profile: str | None, request: dict[str, Any], finish: Callable[[Job], BaseModel | None], merge_stderr: bool = False, env: dict[str, str] | None = None) -> Job:
    """merge_stderr: write stderr into stdout too (pipeline runs: errors belong in the log, in order).
    env: extra environment variables."""
    folder = pkg.path(f"jobs/{jid}")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "stdin.txt").write_text(stdin, encoding="utf-8")
    job = Job(id=jid, type=type, status="running", profile=profile, argv=argv, request=request, created_at=datetime.now(timezone.utc),  # full precision: orders jobs
              stdout=f"jobs/{jid}/stdout.log", stderr=f"jobs/{jid}/stderr.log", events_format=events_format)
    with _guard:
        if any(k.startswith(f"{pkg.root}::") for k in _procs):
            raise conflict("job_running", "another job is running in this package")
        out, err = open(pkg.path(job.stdout), "wb"), open(pkg.path(job.stderr), "wb")
        try:
            proc = subprocess.Popen(resolve_argv(argv), cwd=pkg.root, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT if merge_stderr else err,
                                    start_new_session=True, env=job_env(env))
        except OSError as e:
            out.close(), err.close()
            job.status, job.finished_at, job.error = "failed", now(), f"could not start: {e}"
            save(pkg, job)
            raise ApiError(502, "job_start_failed", f"could not start {argv[0]}: {e}", {"job": jid})
        _procs[_key(pkg, jid)] = proc
        save(pkg, job)
    threading.Thread(target=_watch, args=(pkg, job, proc, stdin, timeout_s, finish, out, err), daemon=True).start()
    return job


def _watch(pkg: Package, job: Job, proc: subprocess.Popen, stdin: str, timeout_s: int, finish, out, err) -> None:
    key = _key(pkg, job.id)
    t0 = time.monotonic()
    try:
        try:
            proc.stdin.write(stdin.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # the process exited early; its exit code says why
        try:
            code = proc.wait(timeout=timeout_s)
            status = "interrupted" if key in _interrupted else "cancelled" if key in _cancelled else ("succeeded" if code == 0 else "failed")
        except subprocess.TimeoutExpired:
            _kill(proc)
            code, status = proc.wait(), "timeout"
        out.close(), err.close()
        job.status, job.exit_code = status, code
        job.duration_ms = int((time.monotonic() - t0) * 1000)
        try:
            job.result = finish(job)
        except Exception as e:  # the job ran; report the bookkeeping failure without losing the record
            log.exception("job %s: finishing failed", job.id)
            job.error = f"after the job: {type(e).__name__}: {e}"
    except Exception as e:  # never leave a record that says "running" for a job that ended
        log.exception("job %s: watcher failed", job.id)
        job.status = "failed" if job.status == "running" else job.status
        job.error = job.error or f"{type(e).__name__}: {e}"
    finally:
        job.finished_at = now()
        try:
            save(pkg, job)
        finally:
            with _guard:
                _procs.pop(key, None)
                _cancelled.discard(key)
                _interrupted.discard(key)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    def hard():
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    threading.Timer(KILL_GRACE_S, hard).start()


def stop_all(grace: float = KILL_GRACE_S, settle: float = 3) -> None:
    """Service shutdown: end every running job, so none outlives the service (jobs run in their own process
    group, so they wouldn't get the service's signal). SIGTERM to each process group, SIGKILL after `grace`
    seconds; then up to `settle` seconds for the watchers to record them as interrupted."""
    with _guard:
        procs = dict(_procs)
        _interrupted.update(procs)
    if not procs:
        return
    log.info("stopping %d running job(s)", len(procs))
    for proc in procs.values():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.monotonic() + grace
    for proc in procs.values():
        try:
            proc.wait(timeout=max(0.0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    end = time.monotonic() + settle
    while time.monotonic() < end:
        with _guard:
            if not any(k in _procs for k in procs):
                return
        time.sleep(0.05)


def cancel(pkg: Package, jid: str) -> Job:
    job = load(pkg, jid)
    key = _key(pkg, jid)
    with _guard:
        proc = _procs.get(key)
        if job.status != "running" or proc is None:
            raise conflict("job_finished", f"{jid} is not running (status {job.status})")
        _cancelled.add(key)
    _kill(proc)
    return job


def wait(pkg: Package, jid: str, timeout: float = 60) -> Job:
    """Block until the job has finished (tests, CLI convenience)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = load(pkg, jid)
        if job.status != "running" and job.finished_at:
            return job
        time.sleep(0.05)
    raise TimeoutError(jid)
