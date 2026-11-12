"""A package folder, its Frictionless manifest (datapackage.json), and the registry of packages.

All changes to a package go through ``Package.edit()``: it holds the package lock, loads the manifest,
and saves it (atomically) when the block succeeds. Files are registered as resources with bytes + sha256,
so ``frictionless validate`` can check their integrity.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from .config import settings
from .errors import ApiError, conflict, not_found
from .schemas import SCHEMAS, json_schema_text, schema_path
from .schemas.common import Identifier, now
from .schemas.curation import ActionsFile, ClaimsFile, TargetsFile
from .schemas.manifest import Manifest, OtherMeta, PackageMeta, Resource

MANIFEST = "datapackage.json"
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def slug(s: str, max_len: int = 48) -> str:
    out = re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-.")
    return out[:max_len].strip("-.") or "x"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump_json(data: BaseModel | Any) -> bytes:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json", exclude_none=True)
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()


class Package:
    def __init__(self, pid: str, root: Path):
        self.id, self.root = pid, root
        with _locks_guard:
            self._lock = _locks.setdefault(str(root), threading.RLock())

    # ---------- manifest ----------

    def load(self) -> Manifest:
        try:
            return Manifest.model_validate_json((self.root / MANIFEST).read_bytes())
        except FileNotFoundError:
            raise ApiError(410, "package_missing", f"{self.root / MANIFEST} no longer exists")

    def save(self, m: Manifest) -> None:
        atomic_write(self.root / MANIFEST, dump_json(m))

    @contextmanager
    def edit(self) -> Iterator[Manifest]:
        with self._lock:
            m = self.load()
            yield m
            self.save(Manifest.model_validate(m.model_dump()))  # re-validate before writing

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    # ---------- files and resources ----------

    def path(self, rel: str) -> Path:
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or "\\" in p for p in parts):
            raise ApiError(400, "invalid_path", f"invalid package path {rel!r}")
        return self.root.joinpath(*parts)

    def free_path(self, rel: str) -> str:
        """rel, or rel with -2, -3 … before the extension if taken."""
        if not self.path(rel).exists():
            return rel
        head, _, name = rel.rpartition("/")
        stem, dot, ext = name.partition(".")
        i = 2
        while self.path(f"{head}/{stem}-{i}{dot}{ext}".lstrip("/")).exists():
            i += 1
        return f"{head}/{stem}-{i}{dot}{ext}".lstrip("/")

    @staticmethod
    def free_name(m: Manifest, base: str) -> str:
        base = slug(base)
        names = {r.name for r in m.resources}
        if base not in names:
            return base
        i = 2
        while f"{base}-{i}" in names:
            i += 1
        return f"{base}-{i}"

    def describe(self, name: str, rel: str, *, type: str, format: str, mediatype: str, meta: Any, json_schema: str | None = None) -> Resource:
        p = self.path(rel)
        return Resource(name=name, path=rel, type=type, format=format, mediatype=mediatype, bytes=p.stat().st_size,
                        hash=f"sha256:{sha256_file(p)}", jsonSchema=json_schema, reprocrowd=meta)

    @staticmethod
    def put(m: Manifest, res: Resource) -> Resource:
        """Add a resource, or replace the one with the same name."""
        m.resources = [r for r in m.resources if r.name != res.name] + [res]
        return res

    def refresh(self, m: Manifest, name: str) -> None:
        """Recompute bytes and hash after a file was rewritten."""
        r = m.get(name)
        p = self.path(r.path)
        r.bytes, r.hash = p.stat().st_size, f"sha256:{sha256_file(p)}"

    def write_json(self, m: Manifest, name: str, rel: str, data: BaseModel | Any, *, schema: str | None, meta: Any) -> Resource:
        if schema:
            self.ensure_schema(m, schema)
        atomic_write(self.path(rel), dump_json(data))
        return self.put(m, self.describe(name, rel, type="json", format="json", mediatype="application/json", meta=meta,
                                         json_schema=schema_path(schema) if schema else None))

    def ensure_schema(self, m: Manifest, schema: str) -> None:
        """The package's copy of a schema matches this version of ReproCrowd (packages made by older versions
        may have an outdated copy, or none)."""
        rel, text = schema_path(schema), json_schema_text(schema).encode()
        p = self.path(rel)
        if not p.exists() or p.read_bytes() != text or not m.get(f"schema-{schema}"):
            atomic_write(p, text)
            self.put(m, self.describe(f"schema-{schema}", rel, type="json", format="json", mediatype="application/schema+json",
                                      meta=OtherMeta(kind="schema", added_at=now())))

    def read_json(self, rel: str) -> Any:
        return json.loads(self.path(rel).read_text(encoding="utf-8"))

    def remove(self, m: Manifest, name: str, delete_file: bool = True) -> None:
        r = m.get(name)
        if r is None:
            raise not_found("resource", name)
        if delete_file:
            self.path(r.path).unlink(missing_ok=True)
        m.resources = [x for x in m.resources if x.name != name]

    def summary(self) -> dict[str, Any]:
        m = self.load()
        counts: dict[str, int] = {}
        for r in m.resources:
            counts[r.reprocrowd.kind] = counts.get(r.reprocrowd.kind, 0) + 1
        meta = m.reprocrowd
        return {"id": self.id, "path": str(self.root), "title": m.title, "identifier": meta.identifier,
                "identifiers": meta.identifiers, "metadata_fetched_at": meta.metadata_fetched_at, "resources": counts}


# ---------- creation ----------


def init_package(root: Path, name: str, identifier: Identifier | None) -> None:
    """Write the minimal package: manifest, schema copies, empty targets/actions."""
    root.mkdir(parents=True, exist_ok=True)
    m = Manifest(name=slug(name), reprocrowd=PackageMeta(created_at=now(), identifier=identifier))
    pkg = Package("-", root)
    for sname in SCHEMAS:
        rel = schema_path(sname)
        atomic_write(pkg.path(rel), json_schema_text(sname).encode())
        pkg.put(m, pkg.describe(f"schema-{sname}", rel, type="json", format="json", mediatype="application/schema+json",
                                meta=OtherMeta(kind="schema", added_at=now())))
    pkg.write_json(m, "targets", "targets.json", TargetsFile(), schema="targets", meta=OtherMeta(kind="curation", added_at=now()))
    pkg.write_json(m, "actions", "actions.json", ActionsFile(), schema="actions", meta=OtherMeta(kind="curation", added_at=now()))
    pkg.write_json(m, "claims", "claims.json", ClaimsFile(), schema="claims", meta=OtherMeta(kind="curation", added_at=now()))
    pkg.save(m)


class Registry:
    """id → package folder, stored as JSON in the service's data directory."""

    def __init__(self, path: Path | None = None):
        self.file = (path or settings().registry).expanduser()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            return json.loads(self.file.read_text())["packages"]
        except FileNotFoundError:
            return {}

    def _write(self, data: dict[str, str]) -> None:
        atomic_write(self.file, dump_json({"packages": data}))

    def list(self) -> list[Package]:
        return [Package(pid, Path(p)) for pid, p in sorted(self._read().items())]

    def get(self, pid: str) -> Package:
        p = self._read().get(pid)
        if p is None:
            raise not_found("package", pid)
        return Package(pid, Path(p))

    def create(self, root: Path, identifier: Identifier | None, adopt: bool = False) -> Package:
        root = root.expanduser().resolve()
        if not settings().path_allowed(root):
            raise ApiError(403, "path_not_allowed", f"{root} is outside the configured allowed_roots")
        with self._lock:
            data = self._read()
            existing = next((pid for pid, p in data.items() if Path(p) == root), None)
            if existing:
                raise conflict("package_exists", f"{root} is already registered as {existing!r}", {"id": existing})
            if (root / MANIFEST).exists():
                if not adopt:
                    raise conflict("package_exists", f"{root} already contains a package; pass adopt to register it")
                Package("-", root).load()  # must be a valid manifest
            elif root.exists() and any(root.iterdir()):
                raise conflict("folder_not_empty", f"{root} exists and is not empty")
            else:
                init_package(root, root.name, identifier)
            pid = slug(root.name)
            base, i = pid, 2
            while pid in data:
                pid, i = f"{base}-{i}", i + 1
            data[pid] = str(root)
            self._write(data)
        return Package(pid, root)

    def remove(self, pid: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(pid, None) is None:
                raise not_found("package", pid)
            self._write(data)
