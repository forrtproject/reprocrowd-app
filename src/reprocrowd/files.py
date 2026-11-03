"""Files on disk vs. resources in the manifest.

Agents and pipeline runs create and change files outside the API. ``sync`` brings the manifest up to date:
- script, code and output resources get their bytes and hash refreshed (or are dropped if the file is gone);
- new files are registered: under outputs/ as kind "output", anywhere else as kind "code".
Resources of other kinds (inputs, full texts, extractions, …) are never refreshed: a changed input must show
up as an integrity error in validation, not be silently accepted.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .package import MANIFEST, Package, sha256_file, slug
from .schemas.common import now
from .schemas.manifest import Manifest, OtherMeta

REFRESHABLE = {"script", "code", "output"}
# Top-level folders that are bookkeeping, and folder names that are tool caches.
SKIP_TOP = {"jobs", "llm-calls", "archive"}
SKIP_ANY = {"__pycache__", "_targets", "node_modules"}


def _skipped(rel: str) -> bool:
    parts = rel.split("/")
    return parts[0] in SKIP_TOP or any(p.startswith(".") or p in SKIP_ANY for p in parts) or rel == MANIFEST


def disk_files(pkg: Package) -> list[Path]:
    return sorted(p for p in pkg.root.rglob("*") if p.is_file() and not _skipped(p.relative_to(pkg.root).as_posix()))


def file_path(pkg: Package, rel: str) -> Path | None:
    """The file at a package-relative path, if it is one that ``listing`` shows (no traversal, no bookkeeping)."""
    rel = rel.strip("/")
    if not rel or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/")) or _skipped(rel):
        return None
    p = pkg.root / rel
    try:
        p.resolve().relative_to(pkg.root.resolve())
    except ValueError:
        return None
    return p if p.is_file() and not p.is_symlink() else None


TEXT_SUFFIXES = {".r", ".py", ".smk", ".md", ".txt", ".log", ".csv", ".tsv", ".json", ".toml", ".yml", ".yaml", ".do", ".sh", ".qmd", ".rmd"}


def mediatype(rel: str) -> str:
    """Code and logs as UTF-8 text (mimetypes guesses e.g. .R as nothing, .py as text/x-python)."""
    if Path(rel).suffix.lower() in TEXT_SUFFIXES or Path(rel).name in ("Snakefile", "README"):
        return "application/json" if rel.lower().endswith(".json") else "text/plain; charset=utf-8"
    return mimetypes.guess_type(rel)[0] or "application/octet-stream"


def listing(pkg: Package) -> list[dict[str, Any]]:
    """Every file with its resource (if any), most recently modified first."""
    m = pkg.load()
    by_path = {r.path: r for r in m.resources}
    out = []
    for p in disk_files(pkg):
        rel = p.relative_to(pkg.root).as_posix()
        st = p.stat()
        r = by_path.get(rel)
        out.append({"path": rel, "size": st.st_size, "modified": st.st_mtime, "resource": r.name if r else None,
                    "kind": r.reprocrowd.kind if r else None})
    return sorted(out, key=lambda f: -f["modified"])


def untracked(pkg: Package, m: Manifest) -> list[str]:
    tracked = {r.path for r in m.resources}
    return [rel for p in disk_files(pkg) if (rel := p.relative_to(pkg.root).as_posix()) not in tracked]


def sync(pkg: Package) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {"added": [], "updated": [], "removed": []}
    with pkg.edit() as m:
        for r in list(m.resources):
            if r.reprocrowd.kind not in REFRESHABLE:
                continue
            p = pkg.path(r.path)
            if not p.exists():
                pkg.remove(m, r.name, delete_file=False)
                report["removed"].append(r.path)
            elif r.hash != f"sha256:{sha256_file(p)}":
                pkg.refresh(m, r.name)
                report["updated"].append(r.path)
        for rel in untracked(pkg, m):
            kind = "output" if rel.startswith("outputs/") else "code"
            name = Package.free_name(m, ("out-" if kind == "output" else "code-") + slug(rel.removeprefix("outputs/"), 60))
            is_json = rel.lower().endswith(".json")
            ext = rel.rsplit(".", 1)[-1].lower() if "." in Path(rel).name else "bin"
            pkg.put(m, pkg.describe(name, rel, type="json" if is_json else "file", format=ext,
                                    mediatype=mimetypes.guess_type(rel)[0] or "application/octet-stream",
                                    meta=OtherMeta(kind=kind, added_at=now()),
                                    json_schema="schemas/results-output.schema.json" if rel == "outputs/results.json" else None))
            report["added"].append(rel)
    return report
