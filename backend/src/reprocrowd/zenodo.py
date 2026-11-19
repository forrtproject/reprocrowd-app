"""Deposits on Zenodo (zenodo.org, or its sandbox for testing), through Zenodo's deposit API.

A deposit is made as a **draft**: the record with its metadata and the package ZIP, and a reserved DOI. The user
can look at it on Zenodo, and publish it there or through ``…/deposits/{id}:publish``. Publishing registers the
DOI, and a published record can't be deleted any more; that is why it is a separate, explicit step.

Preconditions are those of the package database (``database.Prepared``): a DOI, a last run that succeeded, an
up-to-date archive, and the work's OpenAlex record, from which the bibliographic metadata comes. In addition, the
paper's text must have been retrieved for the DOI (e.g. its JATS XML from Europe PMC), not taken from the user's disk:
a local file may be unpublished research (a draft, a manuscript under review), and nothing ties it to the DOI. Such
reproductions stay local (``local_fulltexts``).

What is uploaded: the package archive, or a copy of it without some groups of files, since a Zenodo record is
public and permanent:
  paper   the paper's own material (full texts, their extractions, the original figures). Only redistributable
          under an open license; by default it is left out unless OpenAlex reports a CC/public-domain license.
  inputs  the input files (the authors' data and code). Included by default.
A copy lists what was left out (with hashes) in ``excluded-files.json``, and its datapackage.json lists only the
files it contains.

The service never stores the user's Zenodo token: clients send it with each request (``zenodo_token``), and
``[zenodo] token`` in the config is only the fallback for local CLI use.
"""
from __future__ import annotations

import json
import re
import secrets
import tempfile
import zipfile
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Iterable, Literal

import httpx

from . import __version__, identifiers
from .config import settings
from .database import Prepared
from .errors import ApiError, conflict, invalid, not_found
from .package import MANIFEST, Package, sha256_file
from .report import archive_path
from .schemas.common import now
from .schemas.manifest import Resource, ZenodoCreator, ZenodoDeposit

Group = Literal["paper", "inputs"]
GROUPS: dict[str, str] = {
    "paper": "the paper's full texts, their extractions and the original figures",
    "inputs": "the input files (data and code of the original study)",
}
#: OpenAlex license ids under which the paper's material may be redistributed as it is.
OPEN_LICENSES = {"cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc0", "public-domain"}
KEYWORDS = ["ReproCrowd", "computational reproducibility", "reproduction"]


def server(sandbox: bool | None) -> tuple[str, bool]:
    cfg = settings()
    sb = cfg.zenodo_sandbox if sandbox is None else sandbox
    return (cfg.zenodo_sandbox_url if sb else cfg.zenodo_url).rstrip("/"), sb


def _token(token: str | None) -> str:
    t = token or settings().zenodo_token
    if not t:
        raise ApiError(400, "zenodo_token_missing", "a Zenodo access token is needed (zenodo_token, or [zenodo] token in the config)")
    return t


def _body(r: httpx.Response) -> dict[str, Any]:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _request(method: str, url: str, token: str | None = None, ok: Iterable[int] = (200, 201, 202, 204), **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.request(method, url, headers=headers, timeout=kw.pop("timeout", 30), **kw)
    except httpx.HTTPError as e:
        raise ApiError(502, "zenodo_unreachable", f"could not reach Zenodo at {url.split('/api/')[0]}: {e}")
    if r.status_code in ok:
        return r
    body = _body(r)
    msg = body.get("message") or body.get("error") or f"HTTP {r.status_code}"
    if r.status_code in (401, 403):
        raise ApiError(403, "zenodo_token_invalid", f"Zenodo didn't accept the access token ({msg}); it needs the scopes "
                                                    "deposit:write and deposit:actions, and belongs to one server (zenodo.org or the sandbox)")
    if r.status_code == 400:
        raise invalid("zenodo_rejected", f"Zenodo refused the deposit: {msg}", {"errors": body.get("errors") or []})
    raise ApiError(502, "zenodo_error", f"Zenodo returned HTTP {r.status_code}: {msg}", {"body": r.text[:500]})


# ---------- service-level ----------


def status(sandbox: bool | None = None, probe: bool = True) -> dict[str, Any]:
    url, sb = server(sandbox)
    out: dict[str, Any] = {"url": url, "sandbox": sb, "token_configured": bool(settings().zenodo_token),
                           "community": settings().zenodo_community or None, "reachable": False if probe else None, "error": None}
    if not probe:
        return out
    try:
        r = httpx.get(f"{url}/api/records", params={"size": 1}, timeout=5)
        out["reachable"] = r.status_code == 200
        out["error"] = None if r.status_code == 200 else f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        out["error"] = str(e)
    return out


def verify_token(token: str | None, sandbox: bool | None = None) -> dict[str, Any]:
    """Is the token accepted for deposits on this server?"""
    url, sb = server(sandbox)
    _request("GET", f"{url}/api/deposit/depositions", _token(token), params={"size": 1}, timeout=10)
    return {"ok": True, "url": url, "sandbox": sb}


def find(doi: str, sandbox: bool | None = None) -> list[dict[str, Any]]:
    """Published ReproCrowd records that relate to the paper with this DOI (public search, no token)."""
    url, _ = server(sandbox)
    doi = identifiers.doi(doi)
    q = f'metadata.related_identifiers.identifier:"{doi}" AND keywords:"ReproCrowd"'
    r = _request("GET", f"{url}/api/records", params={"q": q, "size": 50, "sort": "mostrecent"}, timeout=15)
    out = []
    for h in _body(r).get("hits", {}).get("hits", []):
        md = h.get("metadata") or {}
        out.append({
            "id": h.get("id"),
            "doi": h.get("doi") or md.get("doi"),
            "title": md.get("title"),
            "creators": [c.get("name") for c in md.get("creators") or []],
            "published": md.get("publication_date"),
            "url": (h.get("links") or {}).get("self_html") or (h.get("links") or {}).get("html") or f"{url}/records/{h.get('id')}",
        })
    return out


# ---------- what goes into the upload ----------


#: Origins of full texts that come from the user's disk rather than from the published paper.
LOCAL_ORIGINS = ("local", "upload")


def local_fulltexts(pkg: Package) -> list[str]:
    """The full texts that came from the user's disk (added from a file or uploaded, or converted from such a file).
    A converted full text whose source is gone counts as local too: its origin can't be told."""
    fts = {r.name: r for r in pkg.load().by_kind("fulltext")}

    def local(r: Resource, seen: frozenset[str] = frozenset()) -> bool:
        o = r.reprocrowd.origin
        if o.type in LOCAL_ORIGINS:
            return True
        if o.type == "conversion":
            src = fts.get(o.source)
            return src is None or src.name in seen or local(src, seen | {r.name})
        return False

    return [name for name, r in fts.items() if local(r)]


def _check_published_paper(pkg: Package) -> None:
    """Refuse packages whose paper text came from the user's disk: only reproductions of published papers go to Zenodo."""
    names = local_fulltexts(pkg)
    if names:
        raise conflict("local_paper_text", "the paper's text in this package comes from a file on your computer "
                       f"({', '.join(names)}), not from the published paper; only reproductions of published papers, started "
                       "from their DOI, can be deposited on Zenodo, because a record there is public and permanent",
                       {"fulltexts": names})


def _group(r: Resource) -> str | None:
    kind = r.reprocrowd.kind
    if kind in ("fulltext", "extraction") or kind == "figure":
        return "paper"
    if kind == "input":
        return "inputs"
    return None


def paper_license(work: dict[str, Any]) -> str | None:
    for loc in (work.get("best_oa_location"), work.get("primary_location")):
        if loc and loc.get("license"):
            return str(loc["license"]).lower()
    return None


# Files that should rarely be public (#91): flagged in the deposit dialog, never left out silently.
_SECRET_NAME = re.compile(r"""(^\.env(\..*)?$|\.env$|^\.netrc$|^\.npmrc$|^\.pypirc$|^\.pgpass$|^\.git-credentials$|^\.Renviron$
    |^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx|jks|keystore|kdbx|ppk|gpg|asc)$
    |credential|secret|password|passwd|(access|auth|api|bearer)[_-]?token|apikey|api_key)""", re.I | re.X)
_SECRET_TEXT = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36}\b|\bgithub_pat_\w{40,}"
                          rb"|\bsk-ant-[\w-]{20,}|\bsk-(proj-)?[A-Za-z0-9_-]{32,}|\bxox[abpr]-[\w-]{10,}|\bAIza[\w-]{35}\b")
_SCAN_BYTES = 2 * 1024**2  # per file: the start of it
_SCAN_TOTAL = 256 * 1024**2  # per preview: small files first; the rest is reported as not checked
_KEY_SECRET = "contains what looks like a private key or access token"
# Archive member hash → whether its start looks like a secret. The archive only holds files whose content matched
# their recorded hash when it was made (validation), so the hash names exactly the bytes that are uploaded.
_scanned: dict[str, bool] = {}


def _name_flags(path: str) -> list[str]:
    parts = path.split("/")
    return (["hidden file"] if any(p.startswith(".") for p in parts) else []) + \
        (["the name suggests keys or credentials"] if _SECRET_NAME.search(parts[-1]) else [])


def _read_start(pkg: Package, path: str, z: zipfile.ZipFile | None) -> bytes | None:
    """The first bytes of what is uploaded: the archive's member when there is an archive, else the file on disk.
    None when it can't be read."""
    try:
        if z is not None:
            with z.open(path) as f:
                return f.read(_SCAN_BYTES)
        with pkg.path(path).open("rb") as f:
            return f.read(_SCAN_BYTES)
    except (OSError, KeyError, zipfile.BadZipFile, ApiError):
        return None


def _looks_secret(pkg: Package, path: str, digest: str | None, z: zipfile.ZipFile | None = None) -> bool | None:
    """Whether the start of the file looks like a key or token; None when it couldn't be read (not checked)."""
    if digest and digest in _scanned:
        return _scanned[digest]
    data = _read_start(pkg, path, z)
    if data is None:
        return None
    found = bool(_SECRET_TEXT.search(data))
    if digest and z is not None:
        _scanned[digest] = found
    return found


def file_flags(pkg: Package, r: Resource) -> list[str]:
    """Why a file of the package may not belong in a public record: its name, or what looks like a key or token in it."""
    return _name_flags(r.path) + ([_KEY_SECRET] if _looks_secret(pkg, r.path, None) else [])


def _open_archive(pkg: Package) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(archive_path(pkg))
    except (ApiError, OSError, zipfile.BadZipFile):
        return None


def contents(pkg: Package, work: dict[str, Any] | None = None) -> dict[str, Any]:
    """The groups that can be left out, their files, which are left out by default, and every file of the upload
    (group null: always uploaded) with the reasons it may not belong in a public record. The contents are read from the
    archive, which is what is uploaded, for their first 2 MB; ``checked`` is false for a file that wasn't read (past
    the per-preview limit, or unreadable)."""
    m = pkg.load()
    groups: dict[str, dict[str, Any]] = {g: {"description": d, "resources": [], "bytes": 0} for g, d in GROUPS.items()}
    found: dict[str, bool | None] = {}
    z = _open_archive(pkg)
    try:
        budget = _SCAN_TOTAL
        for r in sorted(m.resources, key=lambda r: r.bytes or 0):
            if r.hash in _scanned or budget > 0:
                budget -= 0 if r.hash in _scanned else min(r.bytes or 0, _SCAN_BYTES)
                found[r.path] = _looks_secret(pkg, r.path, r.hash, z)
        found[MANIFEST] = _looks_secret(pkg, MANIFEST, None, z)  # small, and it holds the notes people type
    finally:
        if z is not None:
            z.close()
    entry = lambda path, size, g: {"path": path, "bytes": size, "group": g, "checked": found.get(path) is not None,  # noqa: E731
                                   "flags": _name_flags(path) + ([_KEY_SECRET] if found.get(path) else [])}
    files = [entry(MANIFEST, pkg.path(MANIFEST).stat().st_size, None)]
    for r in sorted(m.resources, key=lambda r: r.path):
        g = _group(r)
        if g:
            groups[g]["resources"].append(r.name)
            groups[g]["bytes"] += r.bytes or 0
        files.append(entry(r.path, r.bytes, g))
    lic = paper_license(work) if work else None
    default = [] if lic in OPEN_LICENSES else ["paper"]
    return {"groups": groups, "paper_license": lic, "default_exclude": [g for g in default if groups[g]["resources"]], "files": files}


def _publication_zip(pkg: Package, archive: Path, exclude: set[str], out_dir: Path) -> tuple[Path, list[Resource]]:
    """The archive without the excluded groups (the archive itself if nothing is left out)."""
    m = pkg.load()
    left_out = [r for r in m.resources if _group(r) in exclude]
    if not left_out:
        return archive, []
    out = out_dir / archive.name
    names = {r.name for r in left_out}
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = json.loads(src.read(MANIFEST))
        manifest["resources"] = [r for r in manifest.get("resources", []) if r.get("name") not in names]
        z.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
        skip = {r.path for r in left_out}
        for info in src.infolist():
            if info.filename != MANIFEST and info.filename not in skip:
                z.writestr(info, src.read(info))
        listing = [{"name": r.name, "path": r.path, "kind": r.reprocrowd.kind, "group": _group(r), "bytes": r.bytes, "hash": r.hash,
                    "origin": r.reprocrowd.origin.model_dump(exclude_none=True) if getattr(r.reprocrowd, "origin", None) else None}
                   for r in left_out]
        z.writestr("excluded-files.json", json.dumps({"note": "Files of the package that this copy leaves out; their hashes "
                                                               "identify them.", "files": listing}, indent=2, ensure_ascii=False))
    return out, left_out


# ---------- metadata ----------


def _creators(given: list[ZenodoCreator] | None) -> list[ZenodoCreator]:
    creators = given or [ZenodoCreator.model_validate(c) for c in settings().zenodo_creators]
    if not creators:
        raise invalid("zenodo_creators_missing", "name at least one creator (you, as you want to appear on the record)")
    return creators


def metadata(p: Prepared, creators: list[ZenodoCreator], note: str, excluded: list[str], community: str | None) -> dict[str, Any]:
    work, doi = p.work, p.doi
    summary = p.cmp.summary
    total = sum(row.type != "figure" for row in p.cmp.rows)
    authors = ", ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")
    cite = f"{escape(authors)}{f' ({p.year})' if p.year else ''}. <i>{escape(work['title'])}</i>.{f' {escape(p.venue)}.' if p.venue else ''}"
    parts = [
        f"<p>A computational reproduction of {cite} <a href=\"https://doi.org/{escape(doi)}\">https://doi.org/{escape(doi)}</a>, "
        "based on its published data and the procedures described in the text.</p>",
        f"<p><b>Result:</b> {summary.get('match', 0)} of {total} match "
        f"({'numbers at printed precision' if p.cmp.settings.rounding == 'reported' else f'numbers rounded to {p.cmp.settings.rounding} decimals'}; text exactly); "
        f"{summary.get('within', 0)} more within {p.cmp.settings.threshold} % (exploratory). "
        f"Policy {p.cmp.policy}, rounding {p.cmp.rounding_mode}. "
        f"({', '.join(f'{escape(k)}: {v}' for k, v in summary.items())}).</p>" if total else "",
        f"<p><b>Master script:</b> {escape(p.template.label)} (<code>{escape(p.template.command)}</code>).</p>" if p.template else "",
        f"<p><b>Note:</b> {escape(note)}</p>" if note else "",
        "<p>The ZIP is a Frictionless data package (<code>datapackage.json</code>) made with ReproCrowd: the targets taken "
        "from the paper, the analysis plan, the master script and its outputs, the comparison, and <code>report.html</code> "
        "with the provenance of every target.</p>",
        "<p><b>Licences:</b> Zenodo shows one licence per record; it applies to the report and analysis outputs. Each file's "
        "licence is listed in <code>licenses.json</code> and takes precedence: metadata CC0-1.0, new analysis code as listed "
        "there (MIT by default), and the article's material and the input data and code keep their original licences.</p>",
        f"<p>Left out of this upload: {escape('; '.join(GROUPS[g] for g in excluded))}. <code>excluded-files.json</code> lists "
        "them with their hashes.</p>" if excluded else "",
    ]
    md: dict[str, Any] = {
        "title": f"Computational reproduction of: {work['title']}",
        "upload_type": "publication",
        "publication_type": "report",
        "publication_date": date.today().isoformat(),
        "description": "\n".join(x for x in parts if x),
        "creators": [{k: v for k, v in c.model_dump().items() if v} for c in creators],
        "access_right": "open",
        "license": settings().zenodo_license,
        "keywords": KEYWORDS,
        "related_identifiers": [{"identifier": doi, "relation": "reviews", "resource_type": "publication-article", "scheme": "doi"}],
        "notes": f"Made with ReproCrowd {__version__}.",
        "prereserve_doi": True,
    }
    if community:
        md["communities"] = [{"identifier": community}]
    return md


# ---------- deposits ----------


def _links(server_url: str, dep: dict[str, Any]) -> tuple[str, str | None]:
    """The page to show (draft: the upload form; published: the record) and the DOI."""
    links = dep.get("links") or {}
    if dep.get("submitted"):
        url = links.get("record_html") or links.get("html") or f"{server_url}/records/{dep['id']}"
        return url, dep.get("doi") or (dep.get("metadata") or {}).get("doi")
    url = links.get("html") or f"{server_url}/uploads/{dep['id']}"
    return url, ((dep.get("metadata") or {}).get("prereserve_doi") or {}).get("doi")


def preview(pkg: Package, exclude: list[str] | None, note: str, creators: list[ZenodoCreator] | None, community: str | None) -> dict[str, Any]:
    """What a deposit would send, without sending anything."""
    _check_published_paper(pkg)
    p = Prepared(pkg)
    c = contents(pkg, p.work)
    ex = c["default_exclude"] if exclude is None else [g for g in exclude if c["groups"][g]["resources"]]
    try:
        cr = _creators(creators)
    except ApiError:
        cr = [ZenodoCreator(name="(you)")]
    return {"metadata": metadata(p, cr, note, ex, community if community is not None else settings().zenodo_community or None),
            "contents": c, "exclude": ex}


def create(pkg: Package, *, token: str | None, sandbox: bool | None, exclude: list[str] | None, note: str,
           creators: list[ZenodoCreator] | None, community: str | None, publish: bool) -> ZenodoDeposit:
    url, sb = server(sandbox)
    _check_published_paper(pkg)
    tok = _token(token)
    creators = _creators(creators)
    p = Prepared(pkg)
    c = contents(pkg, p.work)
    ex = c["default_exclude"] if exclude is None else [g for g in exclude if c["groups"][g]["resources"]]
    community = community if community is not None else settings().zenodo_community or None
    archive_sha = p.m.reprocrowd.archive.sha256
    for d in p.m.reprocrowd.deposits:
        if d.server == url and d.archive_sha256 == archive_sha and sorted(d.exclude) == sorted(ex) and d.status in ("draft", "published"):
            raise conflict("duplicate_submission", f"this archive is already on Zenodo ({d.status}, {d.url}); change the package "
                                                   "and create the archive again for a new deposit", {"existing": d.model_dump(mode="json")})
    md = metadata(p, creators, note, ex, community)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path, left_out = _publication_zip(pkg, archive_path(pkg), set(ex), Path(tmp))
        sha = sha256_file(zip_path)
        r = _request("POST", f"{url}/api/deposit/depositions", tok, json={"metadata": md})
        dep = _body(r)
        try:
            bucket = (dep.get("links") or {}).get("bucket")
            if not bucket:
                raise ApiError(502, "zenodo_error", "Zenodo's answer has no file bucket for the new deposit")
            with zip_path.open("rb") as f:
                _request("PUT", f"{bucket}/{zip_path.name}", tok, content=f, headers={"Content-Type": "application/octet-stream"}, timeout=3600)
        except ApiError:
            # Don't leave an empty draft behind.
            try:
                _request("DELETE", f"{url}/api/deposit/depositions/{dep['id']}", tok, timeout=15)
            except ApiError:
                pass
            raise
    page, doi = _links(url, dep)
    deposit = ZenodoDeposit(id=f"zen-{secrets.token_hex(4)}", server=url, sandbox=sb, deposition_id=int(dep["id"]), status="draft",
                            title=md["title"], doi=doi, url=page, file=zip_path.name, sha256=sha, archive_sha256=archive_sha,
                            exclude=ex, excluded=[r.name for r in left_out], community=community, creators=creators, note=note, created_at=now())
    with pkg.edit() as m:
        m.reprocrowd.deposits.append(deposit)
    if publish:
        deposit = publish_deposit(pkg, deposit.id, tok)
    return deposit


def _find(pkg: Package, dep_id: str) -> ZenodoDeposit:
    d = next((d for d in pkg.load().reprocrowd.deposits if d.id == dep_id), None)
    if d is None:
        raise not_found("deposit", dep_id)
    return d


def _server_of(d: ZenodoDeposit) -> str:
    """The configured server the deposit is on. The record's own ``server`` comes from datapackage.json, which a shared
    package can fill in at will: the token is only ever sent to the configured zenodo.org or sandbox URL."""
    url, _ = server(d.sandbox)
    if d.server.rstrip("/") != url:
        raise conflict("deposit_server_unknown", f"the deposit is recorded on {d.server}, which isn't the configured "
                                                 f"{'Zenodo sandbox' if d.sandbox else 'Zenodo'} server ({url}); it can't be checked or changed from here",
                       {"server": d.server, "configured": url})
    return url


def _update(pkg: Package, dep_id: str, **changes: Any) -> ZenodoDeposit:
    with pkg.edit() as m:
        d = next(d for d in m.reprocrowd.deposits if d.id == dep_id)
        for k, v in changes.items():
            setattr(d, k, v)
        d.checked_at = now()
        return d


def refresh(pkg: Package, dep_id: str, token: str | None) -> ZenodoDeposit:
    """Ask Zenodo about the deposit (it may have been published or deleted on the website)."""
    d = _find(pkg, dep_id)
    if d.status in ("discarded", "gone"):
        return d
    url = _server_of(d)
    r = _request("GET", f"{url}/api/deposit/depositions/{d.deposition_id}", _token(token), ok=(200, 404, 410), timeout=15)
    if r.status_code in (404, 410):
        return _update(pkg, dep_id, status="gone")
    dep = _body(r)
    page, doi = _links(url, dep)
    if dep.get("submitted"):
        return _update(pkg, dep_id, status="published", url=page, doi=doi or d.doi, published_at=d.published_at or now())
    return _update(pkg, dep_id, url=page, doi=doi or d.doi)


def publish_deposit(pkg: Package, dep_id: str, token: str | None) -> ZenodoDeposit:
    d = _find(pkg, dep_id)
    if d.status == "published":
        return d
    if d.status != "draft":
        raise conflict("deposit_not_draft", f"the deposit is {d.status}; only drafts can be published")
    url = _server_of(d)
    r = _request("POST", f"{url}/api/deposit/depositions/{d.deposition_id}/actions/publish", _token(token), timeout=120)
    dep = _body(r)
    page, doi = _links(url, {**dep, "submitted": True})
    return _update(pkg, dep_id, status="published", url=page, doi=doi or d.doi, published_at=now())


def discard(pkg: Package, dep_id: str, token: str | None) -> ZenodoDeposit:
    """Delete a draft on Zenodo (published records can't be deleted)."""
    d = _find(pkg, dep_id)
    if d.status != "draft":
        raise conflict("deposit_not_draft", f"the deposit is {d.status}; only drafts can be discarded")
    _request("DELETE", f"{_server_of(d)}/api/deposit/depositions/{d.deposition_id}", _token(token), ok=(201, 202, 204, 404, 410), timeout=30)
    return _update(pkg, dep_id, status="discarded")
