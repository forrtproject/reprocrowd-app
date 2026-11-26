"""Running the master script (a job) and comparing its results with the targets.

The command is the template's own (template.toml), run in the package folder; callers can't supply one. It is a
program and its arguments, started directly rather than through a shell, so Windows needs no bash; the program is
looked up where Python and R are usually installed (run_argv). outputs/results.json is removed first, so a failed run never leaves stale results behind (the
previous file stays in git history). After the run, new outputs are registered, results.json is checked,
and everything is committed.
"""
from __future__ import annotations

import functools
import glob
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Literal

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
from .schemas.jobs import DeterminismCheck, DeterminismDiff, FailureHint, Job, RunIsolation, RunResult
from .scripts import current_template

RESULTS = "outputs/results.json"
_CLOSE_ENOUGH = 5e-5  # four significant figures (consistent with ReproAI / Certify T1)
_DEAD_PROXY = "http://127.0.0.1:9"
_SANDBOX_PROFILE = '(version 1)(allow default)(deny network*)(allow network* (local ip "localhost:*"))'
_RUNTIME_CACHE_NAMES = frozenset({".RData", ".Rhistory", ".DS_Store", "Thumbs.db"})
_RUNTIME_CACHE_DIRS = frozenset({"__pycache__", ".Rproj.user", ".ipynb_checkpoints"})
_SKIP_WALK_DIRS = frozenset({".git", "jobs", "llm-calls", "archive"})
_TIME_OF_DAY = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b|\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_PDF_STAMPS = re.compile(rb"/(?:CreationDate|ModDate)\s*\([^)]*\)|/ID\s*\[[^\]]*\]")
_PDF_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)endstream", re.DOTALL)
_PDF_LAYOUT = re.compile(rb"/Length\s+\d+(?:\s+\d+\s+R)?|\bxref\b.*?(?=\btrailer\b)|startxref\s+\d+", re.DOTALL)
_PDF_LENGTH_REF = re.compile(rb"/Length\s+(\d+)\s+\d+\s+R")
_PDF_NUMBER_OBJ = re.compile(rb"\b(\d+)\s+\d+\s+obj\s*\d+\s*endobj")

_PY_MISSING_PKG = re.compile(r"""(?:ModuleNotFoundError|ImportError):\s+No module named ['"]([^'"]+)['"]""")
_R_MISSING_PKG = re.compile(r"""there is no package called [\u2018\u2019'"`]([^\u2018\u2019'"`]+)[\u2018\u2019'"`]""")
_R_MISSING_FN = re.compile(r"""could not find function [\u2018\u2019'"`]([^\u2018\u2019'"`]+)[\u2018\u2019'"`]""")
_PY_NAME_ERR = re.compile(r"""NameError:\s+name ['"]([^'"]+)['"] is not defined""")
_R_OBJ_ERR = re.compile(r"""object [\u2018\u2019'"`]([^\u2018\u2019'"`]+)[\u2018\u2019'"`] not found""")
_HINT_PATTERNS: tuple[tuple[re.Pattern[str], Literal["path", "network", "encoding", "permission", "syntax", "memory"], str, str], ...] = (
    (re.compile(r"FileNotFoundError|No such file or directory|cannot open (?:the )?(?:file|connection)|does not exist|cannot change working directory", re.I),
     "path", "A file or directory path could not be opened.",
     "Check that paths are relative to the package root (e.g. `inputs/...`) and create output folders before writing."),
    (re.compile(r"Could not resolve host|Temporary failure in name resolution|Network is unreachable|Connection refused|ProxyError|cannot open URL|Failed to connect|nodename nor servname provided", re.I),
     "network", "The script attempted to access the network, which runs deny.",
     "Download any required file into `inputs/` beforehand and read it from a package-relative path."),
    (re.compile(r"UnicodeDecodeError|invalid multibyte string|multibyte string|invalid input found on input connection", re.I),
     "encoding", "A text or CSV file could not be decoded as UTF-8.",
     "Pass an explicit encoding when reading the file (e.g. `encoding='latin-1'` in Python or `fileEncoding='latin1'` in R)."),
    (re.compile(r"PermissionError|Permission denied", re.I),
     "permission", "The script lacked permission to read or write a path.",
     "Keep all reads and writes inside the package folder (e.g. `inputs/` and `outputs/`)."),
    (re.compile(r"\bSyntaxError:|\bIndentationError:|Error:\s+unexpected\b"),
     "syntax", "The script failed with a syntax error.",
     "Inspect the line cited in the run log and fix the syntax error."),
    (re.compile(r"\bMemoryError\b|cannot allocate vector of size", re.I),
     "memory", "The script ran out of memory.",
     "Reduce memory usage (e.g. drop unused columns or process data in chunks)."),
)


@functools.lru_cache(maxsize=1)
def network_means() -> Literal["namespace", "sandbox", "proxy"]:
    """Best-effort network denial available on this machine: Linux namespace, macOS sandbox, or dead-proxy env vars."""
    sys_name = platform.system()
    if sys_name == "Linux" and shutil.which("unshare"):
        try:
            if subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10).returncode == 0:
                return "namespace"
        except (OSError, subprocess.SubprocessError):
            pass
    if sys_name == "Darwin" and shutil.which("sandbox-exec"):
        try:
            if subprocess.run(["sandbox-exec", "-p", _SANDBOX_PROFILE, "/usr/bin/true"], capture_output=True, timeout=10).returncode == 0:
                return "sandbox"
        except (OSError, subprocess.SubprocessError):
            pass
    return "proxy"


def _wrap_network(argv: list[str], means: Literal["namespace", "sandbox", "proxy"]) -> list[str]:
    if means == "namespace":
        return ["unshare", "-rn", *argv]
    if means == "sandbox":
        return ["sandbox-exec", "-p", _SANDBOX_PROFILE, *argv]
    return list(argv)


@functools.lru_cache(maxsize=1)
def _clean_locale() -> str:
    if os.name == "nt":
        return "C.UTF-8"
    try:
        r = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            available = {line.strip() for line in r.stdout.splitlines() if line.strip()}
            for candidate in ("C.UTF-8", "C.utf8", "en_US.UTF-8", "UTF-8", "C"):
                if candidate in available:
                    return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return "C.UTF-8"


def _preserve_user_libs(argv: list[str], base_env: dict[str, str]) -> dict[str, str]:
    """Read the user's installed R/Python library locations under their real HOME before HOME is replaced."""
    preserved: dict[str, str] = {}
    if not argv:
        return preserved
    exe_name = Path(argv[0].replace("\\", "/")).name.lower()
    if "rscript" in exe_name:
        try:
            r = subprocess.run([argv[0], "--vanilla", "-e", "cat(.libPaths(), sep='\\n')"],
                               capture_output=True, text=True, timeout=15, env=base_env)
            if r.returncode == 0 and r.stdout.strip():
                preserved["R_LIBS"] = os.pathsep.join(line.strip() for line in r.stdout.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    elif exe_name.startswith("py"):
        if "PYTHONUSERBASE" not in base_env:
            try:
                cmd = [argv[0], argv[1]] if len(argv) > 1 and argv[1] == "-3" else [argv[0]]
                r = subprocess.run([*cmd, "-c", "import site; print(site.getuserbase())"],
                                   capture_output=True, text=True, timeout=15, env=base_env)
                if r.returncode == 0 and r.stdout.strip():
                    preserved["PYTHONUSERBASE"] = r.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return preserved


def _prepare_clean_env(argv: list[str], scratch: Path) -> tuple[dict[str, str], RunIsolation]:
    """Build deterministic clean-room env vars while keeping already-installed user R/Python libraries readable."""
    base = jobs.job_env(base=childenv.for_script())
    preserved = _preserve_user_libs(argv, base)
    home = scratch / "home"
    tmp = scratch / "tmp"
    cache = scratch / "cache"
    config = scratch / "config"
    mpl = scratch / "mpl"
    r_lib = scratch / "R-library"
    for d in (home, tmp, cache, config, mpl, r_lib):
        d.mkdir(parents=True, exist_ok=True)
    (mpl / "matplotlibrc").write_text("backend: Agg\nsvg.hashsalt: reprocrowd\n", encoding="utf-8")

    loc = _clean_locale()
    means = network_means()
    env: dict[str, str] = {
        "TZ": "UTC",
        "LC_ALL": loc,
        "LANG": loc,
        "PYTHONUTF8": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(mpl),
        "SOURCE_DATE_EPOCH": "1717200000",
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "R_PROFILE_USER": str(scratch / ".Rprofile-none"),
        "R_ENVIRON_USER": str(scratch / ".Renviron-none"),
        "R_LIBS_USER": str(r_lib),
    }
    if os.name != "nt":
        env["HOME"] = str(home)
        env["XDG_CACHE_HOME"] = str(cache)
        env["XDG_CONFIG_HOME"] = str(config)
    else:
        env["HOME"] = str(home)
    if "PYTHONUSERBASE" in preserved:
        env["PYTHONUSERBASE"] = preserved["PYTHONUSERBASE"]
    existing_r_libs = preserved.get("R_LIBS") or base.get("R_LIBS", "")
    env["R_LIBS"] = os.pathsep.join(p for p in (str(r_lib), existing_r_libs) if p)
    for var in ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY"):
        env[var] = _DEAD_PROXY
    env["no_proxy"] = env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    return env, RunIsolation(network_denied_by=means, clean_home=True, tz="UTC", locale=loc)


def _runtime_caches(root: Path) -> list[str]:
    """Find runtime cache files/folders inside the package tree (excluding .git, jobs, llm-calls, archive)."""
    found: list[str] = []
    for current, dirs, names in os.walk(root):
        rel_cur = Path(current).relative_to(root)
        if rel_cur.parts and rel_cur.parts[0] in _SKIP_WALK_DIRS:
            dirs[:] = []
            continue
        for d in list(dirs):
            if d in _SKIP_WALK_DIRS:
                dirs.remove(d)
            elif d in _RUNTIME_CACHE_DIRS:
                found.append((rel_cur / d).as_posix() + "/")
                dirs.remove(d)
        for name in sorted(names):
            if name in _RUNTIME_CACHE_NAMES or name.endswith(".pyc"):
                found.append((rel_cur / name).as_posix())
    return sorted(found)


def classify_failure(log_text: str, network_denied_by: str | None = None) -> list[FailureHint]:
    """Extract deduplicated, actionable hints from a failed run's log."""
    hints: list[FailureHint] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: Any, detail: str, suggestion: str) -> None:
        key = (kind, detail)
        if key not in seen:
            seen.add(key)
            hints.append(FailureHint(kind=kind, detail=detail, suggestion=suggestion))

    for m in _PY_MISSING_PKG.finditer(log_text):
        mod = m.group(1).split(".")[0]
        add("missing_package", f"Python module `{mod}` is not installed.",
            f"Install it (e.g. `pip install {mod}`) and declare it in `requirements.txt`.")
    for m in _R_MISSING_PKG.finditer(log_text):
        pkg_name = m.group(1)
        add("missing_package", f"R package `{pkg_name}` is not installed.",
            f"Install it (e.g. `Rscript -e 'install.packages(\"{pkg_name}\")'`) and list it in `install.R`.")
    for m in _R_MISSING_FN.finditer(log_text):
        fn = m.group(1)
        add("missing_package", f"R function `{fn}` was not found.",
            "Load the package that defines it (`library(...)`) and make sure it is installed and listed in `install.R`.")
    for m in _PY_NAME_ERR.finditer(log_text):
        add("session_state", f"Python name `{m.group(1)}` is not defined.",
            "Define or import the variable before using it in the script.")
    for m in _R_OBJ_ERR.finditer(log_text):
        add("session_state", f"R object `{m.group(1)}` was not found.",
            "Define or load the object inside the script instead of relying on interactive session state.")

    for pat, kind, detail, suggestion in _HINT_PATTERNS:
        if pat.search(log_text):
            if kind == "network" and network_denied_by:
                detail = f"The script attempted to access the network, which runs deny ({network_denied_by})."
            add(kind, detail, suggestion)
    return hints


def frozen_targets(pkg: Package) -> list[Target]:
    """The targets the code was written against (results_schema.json from the script step)."""
    if not pkg.path("results_schema.json").exists():
        raise conflict("script_missing", "create the master script first (PUT …/script)")
    return ResultsSchema.model_validate(pkg.read_json("results_schema.json")).results


def _normalize_pdf(data: bytes) -> bytes:
    """Strip benign creation/modification timestamps and object offsets from PDFs before hashing."""
    parts: list[bytes] = []
    for m in _PDF_STREAM.finditer(data):
        head, body = m.group(1), m.group(2)
        if re.search(rb"/Type\s*/XRef\b", head):
            continue
        try:
            body = zlib.decompressobj().decompress(body)
        except zlib.error:
            pass
        parts.append(_PDF_LAYOUT.sub(b"", head) + b"\n" + body)
    length_objects = set(_PDF_LENGTH_REF.findall(data))
    skeleton = _PDF_NUMBER_OBJ.sub(lambda m: b"" if m.group(1) in length_objects else m.group(0),
                                   _PDF_STREAM.sub(b"", data))
    parts.append(_PDF_LAYOUT.sub(b"", skeleton))
    return _PDF_STAMPS.sub(b"", b"\n".join(parts))


def _output_fingerprint(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        data = _normalize_pdf(data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot_outputs(root: Path) -> dict[str, str]:
    """Map package-relative paths under ``outputs/`` (excluding results.json) to normalized hashes."""
    out_dir = root / "outputs"
    if not out_dir.is_dir():
        return {}
    snap: dict[str, str] = {}
    for current, dirs, names in os.walk(out_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            p = Path(current) / name
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if rel == RESULTS:
                continue
            try:
                snap[rel] = _output_fingerprint(p)
            except OSError:
                continue
    return snap


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _looks_like_clock_num(a: float, b: float, elapsed: float) -> bool:
    for unit in (1.0, 1000.0):
        if 1.0e9 * unit <= abs(a) <= 4.0e9 * unit and 1.0e9 * unit <= abs(b) <= 4.0e9 * unit:
            if abs(a - b) <= (elapsed * 1.5 + 5.0) * unit:
                return True
    return False


def _rel_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def _compare_runs(run1_bytes: bytes | None, run1_outputs: dict[str, str],
                  run2_bytes: bytes | None, run2_outputs: dict[str, str],
                  elapsed_s: float, run2_succeeded: bool) -> DeterminismCheck:
    h1 = "sha256:" + hashlib.sha256(run1_bytes).hexdigest() if run1_bytes is not None else None
    h2 = "sha256:" + hashlib.sha256(run2_bytes).hexdigest() if run2_bytes is not None else None
    if not run2_succeeded:
        return DeterminismCheck(runs=2, status="failed", run1_results_sha256=h1, run2_results_sha256=h2,
                                summary="Run 2 failed before determinism could be verified.")
    if run1_bytes is None or run2_bytes is None:
        return DeterminismCheck(runs=2, status="failed", run1_results_sha256=h1, run2_results_sha256=h2,
                                summary="outputs/results.json was missing in one of the runs.")

    diffs: list[DeterminismDiff] = []
    if run1_bytes != run2_bytes:
        try:
            j1 = json.loads(run1_bytes.decode("utf-8"))
            j2 = json.loads(run2_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            diffs.append(DeterminismDiff(path=RESULTS, kind="parse_error", detail=f"could not parse results.json: {e}"))
        else:
            r1 = {r["id"]: r for r in j1.get("results", []) if isinstance(r, dict) and isinstance(r.get("id"), str)} if isinstance(j1, dict) else {}
            r2 = {r["id"]: r for r in j2.get("results", []) if isinstance(r, dict) and isinstance(r.get("id"), str)} if isinstance(j2, dict) else {}
            for tid in sorted(set(r1) | set(r2)):
                if tid not in r1 or tid not in r2:
                    which = "run 2" if tid not in r2 else "run 1"
                    diffs.append(DeterminismDiff(target=tid, path=RESULTS, kind="missing_in_run", detail=f"target {tid} missing in {which}"))
                    continue
                a, b = r1[tid], r2[tid]
                if a == b:
                    continue
                # Numeric or string scalar value
                if "value" in a or "value" in b:
                    va, vb = a.get("value"), b.get("value")
                    if va != vb:
                        if _is_num(va) and _is_num(vb):
                            rel = _rel_diff(float(va), float(vb))
                            clock = _looks_like_clock_num(float(va), float(vb), elapsed_s)
                            diffs.append(DeterminismDiff(
                                target=tid, path=RESULTS, kind="value_changed",
                                detail=f"{va} vs {vb} (rel diff {rel:.2e})",
                                max_rel_diff=rel, looks_like_clock=clock,
                            ))
                        else:
                            clock = bool(_TIME_OF_DAY.search(str(va)) or _TIME_OF_DAY.search(str(vb)))
                            diffs.append(DeterminismDiff(
                                target=tid, path=RESULTS, kind="value_changed",
                                detail=f"{va!r} vs {vb!r}", looks_like_clock=clock,
                            ))
                # Table comparison
                if "table" in a or "table" in b:
                    ta, tb = a.get("table"), b.get("table")
                    if ta != tb:
                        if isinstance(ta, list) and isinstance(tb, list) and len(ta) == len(tb) and all(
                            isinstance(ra, list) and isinstance(rb, list) and len(ra) == len(rb) for ra, rb in zip(ta, tb)
                        ):
                            worst = 0.0
                            only_numeric = True
                            clock = False
                            changed_cells = 0
                            for ra, rb in zip(ta, tb):
                                for ca, cb in zip(ra, rb):
                                    if ca == cb:
                                        continue
                                    changed_cells += 1
                                    if _is_num(ca) and _is_num(cb):
                                        worst = max(worst, _rel_diff(float(ca), float(cb)))
                                        clock = clock or _looks_like_clock_num(float(ca), float(cb), elapsed_s)
                                    else:
                                        only_numeric = False
                                        clock = clock or bool(_TIME_OF_DAY.search(str(ca)) or _TIME_OF_DAY.search(str(cb)))
                            diffs.append(DeterminismDiff(
                                target=tid, path=RESULTS, kind="table_changed",
                                detail=f"{changed_cells} cell(s) changed" + (f" (max rel diff {worst:.2e})" if only_numeric else ""),
                                max_rel_diff=worst if only_numeric else None, looks_like_clock=clock,
                            ))
                        else:
                            diffs.append(DeterminismDiff(target=tid, path=RESULTS, kind="table_changed", detail="table shape or type changed between runs"))
                # Figure / file path reference
                if a.get("file") != b.get("file"):
                    diffs.append(DeterminismDiff(target=tid, path=RESULTS, kind="file_changed",
                                                 detail=f"file reference changed: {a.get('file')!r} vs {b.get('file')!r}"))

    # Compare all other files produced under outputs/ (e.g. figures)
    for rel in sorted(set(run1_outputs) | set(run2_outputs)):
        if rel not in run1_outputs or rel not in run2_outputs:
            which = "run 2" if rel not in run2_outputs else "run 1"
            diffs.append(DeterminismDiff(path=rel, kind="missing_in_run", detail=f"{rel} not written in {which}"))
        elif run1_outputs[rel] != run2_outputs[rel]:
            diffs.append(DeterminismDiff(path=rel, kind="file_changed", detail=f"{rel} bytes differed between runs"))

    if not diffs:
        return DeterminismCheck(runs=2, status="identical", run1_results_sha256=h1, run2_results_sha256=h2,
                                differences=[], summary="Two runs produced identical results and output files.")
    if all(d.kind in ("value_changed", "table_changed") and d.max_rel_diff is not None and d.max_rel_diff < _CLOSE_ENOUGH and not d.looks_like_clock
           for d in diffs):
        return DeterminismCheck(runs=2, status="numerically_close", run1_results_sha256=h1, run2_results_sha256=h2,
                                differences=diffs,
                                summary=f"Two runs differed only beyond 4 significant figures ({len(diffs)} item(s)).")
    return DeterminismCheck(runs=2, status="different", run1_results_sha256=h1, run2_results_sha256=h2,
                            differences=diffs,
                            summary=f"Two runs produced different outputs ({len(diffs)} difference(s)). Check for unseeded randomness or timestamps.")


def start(pkg: Package, runs: int = 1) -> Job:
    tpl = current_template(pkg)
    if tpl is None:
        raise conflict("script_missing", "create the master script first (PUT …/script)")
    jobs.ensure_idle(pkg)
    targets = frozen_targets(pkg)
    argv = run_argv(tpl.command)  # before anything is cleaned up or committed: a missing program changes nothing
    jid = jobs.new_id(pkg)
    pkg.path(RESULTS).unlink(missing_ok=True)
    pre_outputs = set(_snapshot_outputs(pkg.root))
    pre_caches = set(_runtime_caches(pkg.root))
    files.sync(pkg)
    before_snapshot = evidence.snapshot(pkg)
    before = None
    use_git = gitrepo.available()
    if use_git:
        before = gitrepo.commit(pkg, f"Before run ({jid})") or gitrepo.head(pkg)

    scratch = Path(tempfile.mkdtemp(prefix="reprocrowd-run-"))
    clean_env, isolation = _prepare_clean_env(argv, scratch)
    exec_argv = _wrap_network(argv, isolation.network_denied_by)

    run1_state: dict[str, Any] = {"captured": False, "results_bytes": None, "outputs": {}, "t_end": 0.0}

    def between_runs() -> None:
        res_path = pkg.path(RESULTS)
        run1_state["results_bytes"] = res_path.read_bytes() if res_path.is_file() else None
        run1_state["outputs"] = _snapshot_outputs(pkg.root)
        run1_state["t_end"] = time.monotonic()
        run1_state["captured"] = True
        # Clean run-1 outputs so run 2 must regenerate everything from scratch.
        res_path.unlink(missing_ok=True)
        for rel in set(run1_state["outputs"]) - pre_outputs:
            pkg.path(rel).unlink(missing_ok=True)

    def finish(job: Job) -> RunResult:
        files.sync(pkg)
        errors: list[str] = []
        warnings: list[str] = []
        exists = pkg.path(RESULTS).exists()
        if exists:
            try:
                plan = load_plan(pkg)
                errors, warnings, _ = check_output(pkg.read_json(RESULTS), targets, plan, root=pkg.root)
                warnings += [] if plan else [NO_PLAN]
            except ValueError as e:
                errors = [f"not valid JSON: {e}"]
        new_caches = sorted(set(_runtime_caches(pkg.root)) - pre_caches)
        final_isolation = isolation.model_copy(update={"runtime_caches": new_caches})
        if new_caches:
            warnings.append(f"run left runtime cache file(s) behind: {', '.join(new_caches)}")
        hints: list[FailureHint] = []
        if job.status in ("failed", "timeout"):
            log_path = pkg.path(job.stdout)
            if log_path.is_file():
                try:
                    raw = log_path.read_bytes()[-32768:].decode("utf-8", errors="replace")
                    hints = classify_failure(raw, final_isolation.network_denied_by)
                except OSError:
                    pass
        det: DeterminismCheck | None = None
        if runs >= 2:
            if not run1_state["captured"]:
                det = DeterminismCheck(runs=runs, status="failed",
                                       summary="Run 1 failed before the second run could start.")
            else:
                r2_bytes = pkg.path(RESULTS).read_bytes() if exists else None
                r2_outputs = _snapshot_outputs(pkg.root)
                elapsed = max(0.0, time.monotonic() - run1_state["t_end"])
                det = _compare_runs(run1_state["results_bytes"], run1_state["outputs"], r2_bytes, r2_outputs,
                                    elapsed, job.status == "succeeded")
        after = gitrepo.commit(pkg, f"Run ({jid}, {job.status}, exit {job.exit_code})") if use_git else None
        return RunResult(commit_before=before, snapshot=before_snapshot, results_sha256=evidence.digest(pkg, RESULTS),
                         log_sha256=evidence.digest(pkg, job.stdout), command=tpl.command, log=job.stdout, results_file=exists,
                         output_errors=errors, output_warnings=warnings, commit_after=after, runs=runs, determinism=det,
                         isolation=final_isolation, failure_hints=hints)

    return jobs.start(pkg, jid=jid, type="run", argv=argv, exec_argv=exec_argv, stdin="", timeout_s=settings().run_timeout_s,
                      events_format="text", profile=None, request={"template": tpl.id, "command": tpl.command, "runs": runs},
                      finish=finish, merge_stderr=True, env=clean_env,
                      runs=runs, between_runs=between_runs if runs >= 2 else None,
                      cleanup=lambda: shutil.rmtree(scratch, ignore_errors=True))


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
    result = compare(targets, raw, st, root=pkg.root)
    result.basis = ComparisonBasis(results_sha256=evidence.digest(pkg, RESULTS), results_schema_sha256=evidence.digest(pkg, "results_schema.json"), created_at=now())
    plan = load_plan(pkg)
    _, warnings, _ = check_output(raw, targets, plan, root=pkg.root)
    result.output_warnings = warnings + ([] if plan else [NO_PLAN])
    return result
