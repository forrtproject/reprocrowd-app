"""The package folder as a git repository: commits before and after agent runs and pipeline runs, so every
change made by an agent can be inspected and undone. Optional: without git, jobs still run (with a note)."""
from __future__ import annotations

import os
import shutil
import subprocess

from .package import Package

GITIGNORE = "jobs/\narchive/\n.snakemake/\n_targets/\n__pycache__/\n.Rhistory\n.Rproj.user/\noutputs/.steps/\n"


def available() -> bool:
    return shutil.which("git") is not None


# A package folder may come from someone else, .git included, and an agent may write to .git/config: git must not run any
# command named there (fsmonitor, hooks, a gpg program). Command-line -c wins over every config file; clean/smudge
# filters are switched off by ATTRIBUTES, as their names in .git/config are free.
SAFE_CONFIG = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgSign=false",
               "-c", "tag.gpgSign=false", "-c", "core.attributesFile=" + os.devnull)
#: .git/info/attributes, which takes precedence over every .gitattributes in the tree.
ATTRIBUTES = "# written by ReproCrowd: no filter commands\n* -filter\n"


def _git(pkg: Package, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *SAFE_CONFIG, *args], cwd=pkg.root, capture_output=True, text=True, env=env, timeout=120)


def ensure(pkg: Package) -> None:
    dot = pkg.root / ".git"
    if not dot.exists() and not dot.is_symlink():
        _git(pkg, "init", "-q")
    elif dot.is_symlink() or not dot.is_dir():  # a "gitdir:" file or a link would point git at a config of anyone's choosing
        raise RuntimeError(f"{dot} is not a plain git directory; remove it to let ReproCrowd track changes with git")
    info = dot / "info"
    if info.is_symlink():
        info.unlink()
    info.mkdir(exist_ok=True)
    (info / "attributes").unlink(missing_ok=True)  # not through a symlink planted there
    (info / "attributes").write_text(ATTRIBUTES)
    ignore = pkg.root / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE)
    # A local identity only if none is configured globally.
    if _git(pkg, "config", "user.name").returncode != 0:
        _git(pkg, "config", "user.name", "ReproCrowd")
    if _git(pkg, "config", "user.email").returncode != 0:
        _git(pkg, "config", "user.email", "reprocrowd@localhost")


def head(pkg: Package) -> str | None:
    r = _git(pkg, "rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def commit(pkg: Package, message: str) -> str | None:
    """Commit everything; returns the new short hash, or None if nothing changed."""
    ensure(pkg)
    _git(pkg, "add", "-A")
    if _git(pkg, "diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv").returncode == 0:
        return None
    r = _git(pkg, "commit", "-q", "-m", message)
    if r.returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
    return head(pkg)


def changed_files(pkg: Package, a: str | None, b: str | None) -> list[str]:
    if not a or not b:
        return []
    r = _git(pkg, "diff", "--name-only", "--no-ext-diff", "--no-textconv", a, b)
    return [line for line in r.stdout.splitlines() if line]
