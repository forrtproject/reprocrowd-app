"""Input files of the reproduction (data / code / other), under inputs/. Zip archives are unpacked; the
archive and each member are resources, members point to their archive."""
from __future__ import annotations

import mimetypes
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .config import settings
from .errors import ApiError, conflict, invalid, not_found
from .package import Package, slug
from .schemas.common import now
from .schemas.manifest import InputMeta, LocalOrigin, Manifest, Resource, Role

DATA_EXT = {"csv", "tsv", "tab", "txt", "dat", "json", "jsonl", "ndjson", "xml", "xlsx", "xls", "ods", "sav", "zsav", "por", "dta",
            "sas7bdat", "xpt", "rds", "rdata", "rda", "feather", "parquet", "arrow", "h5", "hdf5", "nc", "mat", "sqlite", "db",
            "gpkg", "shp", "geojson", "npy", "npz", "pkl", "pickle"}
CODE_EXT = {"r", "rmd", "qmd", "py", "ipynb", "do", "ado", "sas", "sps", "m", "jl", "stan", "sh", "bash", "sql", "c", "cpp",
            "h", "js", "ts", "java", "scala", "go", "rs", "f90", "pl", "rb", "smk", "nf", "wdl", "mk"}
CODE_NAMES = {"snakefile", "makefile", "dockerfile", "_targets.r", "description", "requirements.txt", "environment.yml", "renv.lock"}


def guess_role(path: str) -> Role:
    name = Path(path).name.lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if name in CODE_NAMES or ext in CODE_EXT:
        return "code"
    if ext in DATA_EXT:
        return "data"
    return "other"


def _register(pkg: Package, m: Manifest, rel: str, meta: InputMeta) -> Resource:
    ext = rel.rsplit(".", 1)[-1].lower() if "." in Path(rel).name else ""
    mediatype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    name = Package.free_name(m, "in-" + slug(rel.removeprefix("inputs/"), 60))
    return pkg.put(m, pkg.describe(name, rel, type="file", format=ext or "bin", mediatype=mediatype, meta=meta))


def add(pkg: Package, path: str, role: Role | None, note: str, unpack: bool) -> list[Resource]:
    src = Path(path).expanduser().resolve()
    if not settings().path_allowed(src):
        raise ApiError(403, "path_not_allowed", f"{src} is outside the configured allowed_roots")
    if not src.is_file():
        raise ApiError(400, "not_a_file", f"{src} is not a readable file (directories: TODO)")
    origin = LocalOrigin(path=str(src))
    with pkg.edit() as m:
        rel = pkg.free_path(f"inputs/{src.name}")
        dest = pkg.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        is_zip = unpack and zipfile.is_zipfile(dest)
        archive = _register(pkg, m, rel, InputMeta(added_at=now(), role=role or ("other" if is_zip else guess_role(rel)), note=note, origin=origin))
        if not is_zip:
            return [archive]
        try:
            members = _unpack(pkg, m, dest, rel, archive.name, origin)
        except (zipfile.BadZipFile, ApiError) as e:
            pkg.remove(m, archive.name)
            if isinstance(e, ApiError):
                raise
            raise invalid("invalid_archive", f"{src.name}: {e}")
        return [archive, *members]


def _unpack(pkg: Package, m: Manifest, zpath: Path, rel: str, archive: str, origin: LocalOrigin) -> list[Resource]:
    base = pkg.free_path(rel.rsplit(".", 1)[0])
    out: list[Resource] = []
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p not in ("", ".")]
            if info.is_dir() or not parts or parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
                continue
            if ".." in parts:  # zip slip
                shutil.rmtree(pkg.path(base), ignore_errors=True)
                raise invalid("invalid_archive", f"unsafe path in zip: {info.filename}")
            member_rel = f"{base}/{'/'.join(parts)}"
            dest = pkg.path(member_rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fsrc, dest.open("wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            out.append(_register(pkg, m, member_rel, InputMeta(added_at=now(), role=guess_role(member_rel), archive=archive, origin=origin)))
    return out


def get(m: Manifest, name: str) -> Resource:
    r = m.get(name)
    if r is None or r.metareader.kind != "input":
        raise not_found("input", name)
    return r


def patch(pkg: Package, name: str, changes: dict[str, Any]) -> Resource:
    allowed = {"role", "note", "used"}
    unknown = set(changes) - allowed
    if unknown:
        raise invalid("schema_invalid", f"cannot change {', '.join(sorted(unknown))}; patchable: role, note, used")
    with pkg.edit() as m:
        r = get(m, name)
        data = r.metareader.model_dump() | {k: ("" if k == "note" and v is None else v) for k, v in changes.items()}
        r.metareader = InputMeta.model_validate(data)
        return r


def delete(pkg: Package, name: str) -> list[str]:
    """Delete an input (an archive with all its members). Members can only be marked unused."""
    from .curation import Curation  # avoid import cycle

    with pkg.edit() as m:
        r = get(m, name)
        if r.metareader.archive:
            raise conflict("archive_member", f"{name} belongs to {r.metareader.archive}; mark it unused instead (used: false)")
        doomed = [name] + [x.name for x in m.by_kind("input") if x.metareader.archive == name]
        users = [a.id for a, _ in Curation(pkg, "actions").effective(include_disabled=True) if set(a.inputs) & set(doomed)]
        if users:
            raise conflict("in_use", f"used by actions {', '.join(users)}", {"used_by": users})
        for n in doomed:
            pkg.remove(m, n)
        for d in sorted((p for p in pkg.path("inputs").rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            if not any(d.iterdir()):  # folders left empty by unpacked members
                d.rmdir()
        return doomed
