"""The package folder as a git repository: commits before and after agent runs and pipeline runs, so every
change made by an agent can be inspected and undone. Optional: without git, jobs still run (with a note)."""
from __future__ import annotations

import shutil
import subprocess

from .package import Package

GITIGNORE = "jobs/\narchive/\n.snakemake/\n_targets/\n__pycache__/\n.Rhistory\n.Rproj.user/\noutputs/.steps/\n"


def available() -> bool:
    return shutil.which("git") is not None


def _git(pkg: Package, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=pkg.root, capture_output=True, text=True, env=env, timeout=120)


def ensure(pkg: Package) -> None:
    if not (pkg.root / ".git").exists():
        _git(pkg, "init", "-q")
    ignore = pkg.root / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE)
    # A local identity only if none is configured globally.
    if _git(pkg, "config", "user.name").returncode != 0:
        _git(pkg, "config", "user.name", "MetaReader")
    if _git(pkg, "config", "user.email").returncode != 0:
        _git(pkg, "config", "user.email", "metareader@localhost")


def head(pkg: Package) -> str | None:
    r = _git(pkg, "rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def commit(pkg: Package, message: str) -> str | None:
    """Commit everything; returns the new short hash, or None if nothing changed."""
    ensure(pkg)
    _git(pkg, "add", "-A")
    if _git(pkg, "diff", "--cached", "--quiet").returncode == 0:
        return None
    r = _git(pkg, "commit", "-q", "-m", message)
    if r.returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
    return head(pkg)


def changed_files(pkg: Package, a: str | None, b: str | None) -> list[str]:
    if not a or not b:
        return []
    r = _git(pkg, "diff", "--name-only", a, b)
    return [line for line in r.stdout.splitlines() if line]
