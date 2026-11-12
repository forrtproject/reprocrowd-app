"""Deposits on Zenodo (zenodo.org, or its sandbox for testing), through Zenodo's deposit API.

A deposit is made as a **draft**: the record with its metadata and the package ZIP, and a reserved DOI. The user
can look at it on Zenodo, and publish it there or through ``…/deposits/{id}:publish``. Publishing registers the
DOI, and a published record can't be deleted any more; that is why it is a separate, explicit step.

Preconditions are those of the package database (``database.Prepared``): a DOI, a last run that succeeded, an
up-to-date archive, and the work's OpenAlex record, from which the bibliographic metadata comes.

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
import secrets
import tempfile
import zipfile
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Iterable, Literal

import httpx

from . import __version__
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


def status(sandbox: bool | None = None) -> dict[str, Any]:
    url, sb = server(sandbox)
    out: dict[str, Any] = {"url": url, "sandbox": sb, "token_configured": bool(settings().zenodo_token),
                           "community": settings().zenodo_community or None, "reachable": False, "error": None}
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
    doi = doi.lower().removeprefix("https://doi.org/")
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


def contents(pkg: Package, work: dict[str, Any] | None = None) -> dict[str, Any]:
    """The groups that can be left out, their files, and which are left out by default."""
    m = pkg.load()
    groups: dict[str, dict[str, Any]] = {g: {"description": d, "resources": [], "bytes": 0} for g, d in GROUPS.items()}
    for r in m.resources:
        g = _group(r)
        if g:
            groups[g]["resources"].append(r.name)
            groups[g]["bytes"] += r.bytes or 0
    lic = paper_license(work) if work else None
    default = [] if lic in OPEN_LICENSES else ["paper"]
    return {"groups": groups, "paper_license": lic, "default_exclude": [g for g in default if groups[g]["resources"]]}


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
    total = sum(summary.values())
    authors = ", ".join(p.authors[:6]) + (" et al." if len(p.authors) > 6 else "")
    cite = f"{escape(authors)}{f' ({p.year})' if p.year else ''}. <i>{escape(work['title'])}</i>.{f' {escape(p.venue)}.' if p.venue else ''}"
    parts = [
        f"<p>A computational reproduction of {cite} <a href=\"https://doi.org/{escape(doi)}\">https://doi.org/{escape(doi)}</a></p>",
        f"<p><b>Result:</b> {summary.get('match', 0)} of {total} target results reproduced "
        f"({', '.join(f'{escape(k)}: {v}' for k, v in summary.items())}).</p>" if total else "",
        f"<p><b>Master script:</b> {escape(p.template.label)} (<code>{escape(p.template.command)}</code>).</p>" if p.template else "",
        f"<p><b>Note:</b> {escape(note)}</p>" if note else "",
        "<p>The ZIP is a Frictionless data package (<code>datapackage.json</code>) made with ReproCrowd: the targets taken "
        "from the paper, the analysis plan, the master script and its outputs, the comparison, and <code>report.html</code> "
        "with the provenance of every target.</p>",
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
    r = _request("GET", f"{d.server}/api/deposit/depositions/{d.deposition_id}", _token(token), ok=(200, 404, 410), timeout=15)
    if r.status_code in (404, 410):
        return _update(pkg, dep_id, status="gone")
    dep = _body(r)
    page, doi = _links(d.server, dep)
    if dep.get("submitted"):
        return _update(pkg, dep_id, status="published", url=page, doi=doi or d.doi, published_at=d.published_at or now())
    return _update(pkg, dep_id, url=page, doi=doi or d.doi)


def publish_deposit(pkg: Package, dep_id: str, token: str | None) -> ZenodoDeposit:
    d = _find(pkg, dep_id)
    if d.status == "published":
        return d
    if d.status != "draft":
        raise conflict("deposit_not_draft", f"the deposit is {d.status}; only drafts can be published")
    r = _request("POST", f"{d.server}/api/deposit/depositions/{d.deposition_id}/actions/publish", _token(token), timeout=120)
    dep = _body(r)
    page, doi = _links(d.server, {**dep, "submitted": True})
    return _update(pkg, dep_id, status="published", url=page, doi=doi or d.doi, published_at=now())


def discard(pkg: Package, dep_id: str, token: str | None) -> ZenodoDeposit:
    """Delete a draft on Zenodo (published records can't be deleted)."""
    d = _find(pkg, dep_id)
    if d.status != "draft":
        raise conflict("deposit_not_draft", f"the deposit is {d.status}; only drafts can be discarded")
    _request("DELETE", f"{d.server}/api/deposit/depositions/{d.deposition_id}", _token(token), ok=(201, 202, 204, 404, 410), timeout=30)
    return _update(pkg, dep_id, status="discarded")
