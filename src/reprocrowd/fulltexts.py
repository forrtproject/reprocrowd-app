"""Full-text files: added from a local path or downloaded from an OpenAlex candidate, accepted only after
the format's integrity check."""
from __future__ import annotations

import secrets
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from . import formats, metadata, openalex
from .config import settings
from .errors import ApiError, conflict, invalid, not_found
from .package import Package, sha256_file, slug
from .schemas.common import now
from .converters import CONVERTERS
from .schemas.manifest import CheckResult, ConvertedOrigin, FulltextMeta, LocalOrigin, Resource, UploadOrigin, UrlOrigin
from .uploads import copy_limited, upload_name


def _stem(filename: str) -> str:
    name = Path(filename).name
    for ext in (".tei.xml", ".jats.xml", ".docling.json", ".nxml", ".xml", ".json", ".pdf"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return Path(name).stem


def _accept(pkg: Package, tmp: Path, filename: str, origin, name: str | None) -> Resource:
    """Check the file in ``tmp`` (inside the package) and register it, or delete it and raise."""
    try:
        fmt, report = formats.check(tmp)
        if not report.passed:
            raise invalid("integrity_check_failed", f"{filename} failed the integrity check",
                          {"format": fmt.name if fmt else None, "problems": report.problems})
        digest = f"sha256:{sha256_file(tmp)}"
        with pkg.edit() as m:
            dup = next((r for r in m.by_kind("fulltext") if r.hash == digest), None)
            if dup:
                raise conflict("duplicate_fulltext", f"identical to {dup.name}", {"resource": dup.name})
            stem = slug(_stem(filename), 40)
            rel = pkg.free_path(f"fulltext/{stem}{fmt.extension}")
            rname = Package.free_name(m, name or f"ft-{stem}-{fmt.name}")
            shutil.move(tmp, pkg.path(rel))
            meta = FulltextMeta(added_at=now(), flavor=fmt.name, origin=origin,
                                check=CheckResult(passed=True, checked_at=now(), notes=report.notes))
            return pkg.put(m, pkg.describe(rname, rel, type=fmt.resource_type, format=fmt.frictionless_format,
                                           mediatype=fmt.mediatype, meta=meta))
    finally:
        tmp.unlink(missing_ok=True)


def _incoming(pkg: Package) -> Path:
    d = pkg.path("fulltext")
    d.mkdir(exist_ok=True)
    return d / f".incoming-{secrets.token_hex(6)}"


def add_local(pkg: Package, path: str, name: str | None = None) -> Resource:
    src = Path(path).expanduser().resolve()
    if not settings().path_allowed(src):
        raise ApiError(403, "path_not_allowed", f"{src} is outside the configured allowed_roots")
    if not src.is_file():
        raise ApiError(400, "not_a_file", f"{src} is not a readable file")
    tmp = _incoming(pkg)
    shutil.copyfile(src, tmp)
    return _accept(pkg, tmp, src.name, LocalOrigin(path=str(src)), name)


def add_upload(pkg: Package, filename: str, data: BinaryIO, name: str | None = None) -> Resource:
    """An uploaded file (multipart), checked like a local one."""
    clean = upload_name(filename)
    tmp = _incoming(pkg)
    try:
        copy_limited(data, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _accept(pkg, tmp, clean, UploadOrigin(filename=clean), name)


def convert(pkg: Package, source: str, converter: str, name: str | None = None) -> Resource:
    """Convert a PDF full text with a configured converter; the result is checked like any other full text."""
    conv = CONVERTERS.get(converter)
    if conv is None:
        raise ApiError(400, "unknown_converter", f"no converter {converter!r}; available: {', '.join(CONVERTERS)}")
    if not conv.configured():
        raise conflict("converter_not_configured", f"{converter} is not configured in the service ([converters.{converter}])")
    src = get(pkg, source)
    if src.reprocrowd.flavor != "pdf":
        raise invalid("not_convertible", f"{source} is {src.reprocrowd.flavor}; converters take PDFs")
    tmp = _incoming(pkg)
    try:
        conv.run(pkg.path(src.path), tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tag = "" if converter in conv.output else f".{converter}"  # x.grobid.tei.xml, but x.docling.json
    filename = f"{_stem(src.path)}{tag}{conv.output}"
    return _accept(pkg, tmp, filename, ConvertedOrigin(source=src.name, converter=converter, detail=conv.detail()), name)


def list_candidates(pkg: Package) -> dict:
    work = _work(pkg)
    cands, notes = openalex.candidates(work)
    return {"candidates": [asdict(c) for c in cands], "notes": notes}


def add_candidate(pkg: Package, candidate: str, name: str | None = None) -> Resource:
    cands, _ = openalex.candidates(_work(pkg))
    c = next((x for x in cands if x.id == candidate), None)
    if c is None:
        raise not_found("candidate", candidate)
    tmp = _incoming(pkg)
    try:
        openalex.download(c, tmp, settings().max_download_mb * 2**20)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _accept(pkg, tmp, c.filename, UrlOrigin(url=c.url, candidate=c.id, label=c.label), name)


def _work(pkg: Package) -> dict:
    try:
        return metadata.load(pkg)
    except ApiError:
        raise conflict("metadata_missing", "retrieve the OpenAlex metadata first") from None


def get(pkg: Package, name: str) -> Resource:
    r = pkg.load().get(name)
    if r is None or r.reprocrowd.kind != "fulltext":
        raise not_found("full text", name)
    return r


def delete(pkg: Package, name: str) -> None:
    with pkg.edit() as m:
        get(pkg, name)
        users = [r.name for r in m.by_kind("extraction") if name in r.reprocrowd.fulltexts]
        if users:
            raise conflict("in_use", f"{name} is the source of {', '.join(users)}; delete those first", {"used_by": users})
        pkg.remove(m, name)
