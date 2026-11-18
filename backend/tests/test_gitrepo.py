"""Issue #91: a package folder's own .git (shared with the package, or written by an agent) runs no commands."""
import subprocess

import pytest

from reprocrowd import gitrepo
from reprocrowd.package import Package

pytestmark = pytest.mark.skipif(not gitrepo.available(), reason="git not installed")


def test_commands_in_the_package_git_config_never_run(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    pwned = tmp_path / "pwned"
    cmd = f"sh -c 'echo $0 >> {pwned}'"
    with (root / ".git" / "config").open("a") as f:
        f.write(f'[core]\n\tfsmonitor = "{cmd} fsmonitor"\n\tworktree = {tmp_path / "outside"}\n[filter "x"]\n\tclean = "{cmd} filter"\n\tsmudge = cat\n'
                f'[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = "{cmd} gpg"\n[diff]\n\texternal = "{cmd} diff"\n')
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho hook >> {pwned}\n")
    hook.chmod(0o755)
    (root / ".gitattributes").write_text("* filter=x\n")
    (root / "data.csv").write_text("a\n1\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("not yours")
    pkg = Package("p", root)

    first = gitrepo.commit(pkg, "one")
    (root / "data.csv").write_text("a\n2\n")
    second = gitrepo.commit(pkg, "two")
    assert first and second and gitrepo.changed_files(pkg, first, second) == ["data.csv"]
    assert not pwned.exists(), pwned.read_text()
    tracked = subprocess.run(["git", "-C", str(root), "--work-tree", str(root), "ls-files"], capture_output=True, text=True).stdout.split()
    assert "data.csv" in tracked and "secret.txt" not in tracked


def test_a_gitdir_file_is_refused(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / ".git").write_text("gitdir: ./elsewhere\n")
    with pytest.raises(RuntimeError, match="not a plain git directory"):
        gitrepo.commit(Package("p", root), "x")
