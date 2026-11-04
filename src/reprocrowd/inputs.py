"""Input files of the reproduction (data / code / other), under inputs/. Zip archives are unpacked; the
archive and each member are resources, members point to their archive."""
from __future__ import annotations

import mimetypes
import shutil
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .config import settings
from .errors import ApiError, conflict, invalid, not_found
from .package import Package, slug
from .schemas.common import now
from .schemas.manifest import InputMeta, LocalOrigin, Manifest, Resource, Role, UploadOrigin
from .uploads import copy_limited, upload_name

DATA_EXT = {"csv", "tsv", "tab", "txt", "dat", "json", "jsonl", "ndjson", "xml", "xlsx", "xls", "ods", "sav", "zsav", "por", "dta",
            "sas7bdat", "xpt", "rds", "rdata", "rda", "feather", "parquet", "arrow", "h5", "hdf5", "nc", "mat", "sqlite", "db",
            "gpkg", "shp", "geojson", "npy", "npz", "pkl", "pickle"}
CODE_EXT = {"r", "rmd", "qmd", "py", "ipynb", "do", "ado", "sas", "sps", "m", "jl", "stan", "sh", "bash", "sql", "c", "cpp",
            "h", "js", "ts", "java", "scala", "go", "rs", "f90", "pl", "rb", "smk", "nf", "wdl", "mk"}
CODE_NAMES = {"snakefile", "makefile", "dockerfile", "_targets.r", "description", "requirements.txt", "environment.yml", "renv.lock"}
# What gets unpacked is decided by file type, not content: xlsx, docx, ods, npz, jar... are zip files inside too, but
# they are single files that stay whole.
ARCHIVE_SUFFIXES = {".zip"}


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


MAX_DIRECTORY_FILES = 5000


def _source(path: str) -> Path:
    src = Path(path).expanduser().resolve()
    if not settings().path_allowed(src):
        raise ApiError(403, "path_not_allowed", f"{src} is outside the configured allowed_roots")
    return src


def add(pkg: Package, path: str, role: Role | None, note: str, unpack: bool) -> list[Resource]:
    """A local file (zip archives unpacked) or a whole directory."""
    src = _source(path)
    if src.is_dir():
        return add_directory(pkg, src, role, note)
    if not src.is_file():
        raise ApiError(400, "not_a_file", f"{src} is not a readable file or directory")
    return _add_file(pkg, src.name, lambda dest: shutil.copyfile(src, dest), LocalOrigin(path=str(src)), role, note, unpack)


def add_upload(pkg: Package, filename: str, data: BinaryIO, role: Role | None, note: str, unpack: bool) -> list[Resource]:
    """An uploaded file (multipart): same as a local file."""
    name = upload_name(filename)
    return _add_file(pkg, name, lambda dest: copy_limited(data, dest), UploadOrigin(filename=name), role, note, unpack)


def _add_file(pkg: Package, filename: str, write: Callable[[Path], None], origin, role: Role | None, note: str, unpack: bool) -> list[Resource]:
    with pkg.edit() as m:
        rel = pkg.free_path(f"inputs/{filename}")
        dest = pkg.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            write(dest)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        is_zip = unpack and dest.suffix.lower() in ARCHIVE_SUFFIXES and zipfile.is_zipfile(dest)
        archive = _register(pkg, m, rel, InputMeta(added_at=now(), role=role or ("other" if is_zip else guess_role(rel)), note=note, origin=origin))
        if not is_zip:
            return [archive]
        try:
            members = _unpack(pkg, m, dest, rel, archive.name, origin)
        except (zipfile.BadZipFile, ApiError) as e:
            pkg.remove(m, archive.name)
            if isinstance(e, ApiError):
                raise
            raise invalid("invalid_archive", f"{filename}: {e}")
        return [archive, *members]


def add_directory(pkg: Package, src: Path, role: Role | None, note: str) -> list[Resource]:
    """Every regular file below ``src`` (hidden files and folders and symlinks are skipped; zips stay packed)."""
    files = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part.startswith(".") for part in rel.parts) or p.is_symlink() or not p.is_file():
            continue
        files.append(rel)
        if len(files) > MAX_DIRECTORY_FILES:
            raise invalid("directory_too_large", f"{src} has more than {MAX_DIRECTORY_FILES} files; zip the part you need")
    if not files:
        raise invalid("directory_empty", f"{src} contains no regular files")
    out = []
    with pkg.edit() as m:
        base = pkg.free_path(f"inputs/{src.name}")
        origin = LocalOrigin(path=str(src))
        for rel in files:
            member_rel = f"{base}/{rel.as_posix()}"
            dest = pkg.path(member_rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, dest)
            out.append(_register(pkg, m, member_rel, InputMeta(added_at=now(), role=role or guess_role(member_rel), note=note, origin=origin)))
    return out


def _unpack(pkg: Package, m: Manifest, zpath: Path, rel: str, archive: str, origin) -> list[Resource]:
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
    if r is None or r.reprocrowd.kind != "input":
        raise not_found("input", name)
    return r


def patch(pkg: Package, name: str, changes: dict[str, Any]) -> Resource:
    allowed = {"role", "note", "used"}
    unknown = set(changes) - allowed
    if unknown:
        raise invalid("schema_invalid", f"cannot change {', '.join(sorted(unknown))}; patchable: role, note, used")
    with pkg.edit() as m:
        r = get(m, name)
        data = r.reprocrowd.model_dump() | {k: ("" if k == "note" and v is None else v) for k, v in changes.items()}
        r.reprocrowd = InputMeta.model_validate(data)
        return r


def delete(pkg: Package, name: str) -> list[str]:
    """Delete an input (an archive with all its members). Members can only be marked unused."""
    from .curation import Curation  # avoid import cycle

    with pkg.edit() as m:
        r = get(m, name)
        if r.reprocrowd.archive:
            raise conflict("archive_member", f"{name} belongs to {r.reprocrowd.archive}; mark it unused instead (used: false)")
        doomed = [name] + [x.name for x in m.by_kind("input") if x.reprocrowd.archive == name]
        users = [a.id for a, _ in Curation(pkg, "actions").effective(include_disabled=True) if set(a.inputs) & set(doomed)]
        if users:
            raise conflict("in_use", f"used by actions {', '.join(users)}", {"used_by": users})
        for n in doomed:
            pkg.remove(m, n)
        for d in sorted((p for p in pkg.path("inputs").rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            if not any(d.iterdir()):  # folders left empty by unpacked members
                d.rmdir()
        return doomed
