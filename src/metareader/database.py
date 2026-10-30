"""Submission to the package database (AI-Assisted Reproduction Database; local simulation in
../ai-assisted-reproduction-database). Optional: with no ``[database] url`` configured, nothing here is used.

The service holds no database credentials. Submitting uploads the archive with bibliographic metadata looked
up in OpenAlex by DOI at that moment (never from local fields); the database answers with a ``finish_url``
where the user signs in and confirms. ``status`` polls whether that happened.

Preconditions: a DOI, a run of the master script that succeeded, and an archive that is up to date (its
datapackage.json lists the same resources as the package now).

A caller may pass the user's own database token (obtained by the client from the database's sign-in); it is
forwarded with the upload, which the database then publishes at once, and never stored.
"""
from __future__ import annotations

import base64
import json
import secrets
import zipfile
from typing import Any

import httpx

from . import __version__, jobs, openalex
from .config import settings
from .errors import ApiError, conflict, not_found
from .package import MANIFEST, Package
from .report import archive_path
from .run import comparison
from .schemas.common import Identifier, now
from .schemas.comparison import CompareSettings
from .schemas.manifest import Submission


def base() -> str:
    url = settings().database_url.strip().rstrip("/")
    if not url:
        raise conflict("database_not_configured", "no package database is configured ([database] url)")
    return url


def _request(method: str, url: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, url, timeout=kw.pop("timeout", 15), **kw)
    except httpx.HTTPError as e:
        raise ApiError(502, "database_unreachable", f"could not reach the package database at {base()}: {e}")


def status() -> dict[str, Any]:
    url = settings().database_url.strip().rstrip("/")
    if not url:
        return {"url": "", "configured": False, "reachable": False}
    try:
        r = httpx.get(f"{url}/api/packages", params={"doi": "0"}, timeout=4)
        return {"url": url, "configured": True, "reachable": r.status_code == 200, "error": None if r.status_code == 200 else f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        return {"url": url, "configured": True, "reachable": False, "error": str(e)}


def find(doi: str) -> list[dict[str, Any]]:
    r = _request("GET", f"{base()}/api/packages", params={"doi": doi})
    if r.status_code != 200:
        raise ApiError(502, "database_error", f"the package database returned HTTP {r.status_code}", {"body": r.text[:500]})
    return r.json().get("packages", [])


def _check_archive(pkg: Package) -> None:
    """The archive must contain the package as it is now (same resources, same hashes)."""
    path = archive_path(pkg)
    with zipfile.ZipFile(path) as z:
        archived = json.loads(z.read(MANIFEST))
    current = pkg.load()
    now_res = sorted((r.path, r.hash) for r in current.resources)
    then_res = sorted((r["path"], r["hash"]) for r in archived.get("resources", []))
    if now_res != then_res:
        raise conflict("archive_stale", "the package changed since the archive was made; create it again (PUT …/archive)")


def submit(pkg: Package, note: str = "", token: str | None = None) -> Submission:
    url = base()
    jobs.ensure_idle(pkg)
    m = pkg.load()
    doi = m.metareader.identifiers.get("doi") or (m.metareader.identifier.value if m.metareader.identifier and m.metareader.identifier.scheme == "doi" else None)
    if not doi:
        raise conflict("no_doi", "only packages for a paper with a DOI can be submitted")
    runs = [j for j in jobs.list_jobs(pkg) if j.type == "run"]
    if not runs or runs[-1].status != "succeeded":
        raise conflict("run_not_succeeded", "the last run of the master script must have succeeded (POST …/jobs {type: run})")
    _check_archive(pkg)
    cmp = comparison(pkg, CompareSettings())
    try:  # bibliographic metadata only from OpenAlex, looked up now
        work = openalex.fetch_work(Identifier(scheme="doi", value=doi))
    except ApiError as e:
        raise conflict("openalex_record_missing", f"can't submit without the OpenAlex record for doi:{doi}: {e.message}")
    if not work.get("title"):
        raise conflict("openalex_record_missing", f"the OpenAlex record for doi:{doi} has no title")
    template = next((r.metareader.template for r in m.by_kind("script")), None)
    from .scripts import templates

    tpl = templates().get(template or "")
    metadata = {
        "doi": doi,
        "openalex_id": work.get("id"),
        "title": work["title"],
        "authors": ", ".join(a.get("author", {}).get("display_name", "") for a in work.get("authorships") or [] if a.get("author")),
        "year": work.get("publication_year") if isinstance(work.get("publication_year"), int) else None,
        "venue": ((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "summary": {"statuses": cmp.summary},
        "template": tpl.label if tpl else None,
        "command": tpl.command if tpl else None,
        "note": note or None,
        "client": f"MetaReader CLI {__version__}",
    }
    header = base64.b64encode(json.dumps({k: v for k, v in metadata.items() if v is not None}).encode()).decode()
    path = archive_path(pkg)
    headers = {"Content-Type": "application/zip", "X-Package-Metadata": header}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with path.open("rb") as f:
        r = _request("POST", f"{url}/api/uploads", content=f, headers=headers, timeout=600)
    body = _json(r)
    if r.status_code == 401 and token:
        raise ApiError(403, "database_token_invalid", body.get("error") or "the package database didn't accept the token")
    if r.status_code == 403:
        raise ApiError(403, "database_forbidden", body.get("error") or "the package database refused this account")
    if r.status_code == 409:
        raise conflict("duplicate_submission", body.get("error") or "an identical package was submitted before", {"existing": body.get("existing")})
    if r.status_code == 422:
        raise ApiError(422, "submission_rejected", body.get("error") or "the database refused the package", {"problems": body.get("problems", [])})
    if r.status_code != 201:
        raise ApiError(502, "database_error", f"the package database returned HTTP {r.status_code}", {"body": r.text[:500]})
    sub = Submission(id=f"sub-{secrets.token_hex(4)}", database=url, upload_id=body["id"], finish_url=body["finish_url"],
                     status_url=body["status_url"], expires=body["expires"], status="submitted" if body.get("status") == "submitted" else "pending",
                     submitted_at=now(), archive_sha256=m.metareader.archive.sha256, note=note,
                     package_url=(body.get("package") or {}).get("url"))
    with pkg.edit() as m:
        m.metareader.submissions.append(sub)
    return sub


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def refresh(pkg: Package, sub_id: str) -> Submission:
    """Ask the database whether the upload was confirmed (pending → submitted / deleted / gone)."""
    sub = next((s for s in pkg.load().metareader.submissions if s.id == sub_id), None)
    if sub is None:
        raise not_found("submission", sub_id)
    if sub.status in ("pending",):
        if not sub.status_url.startswith(f"{sub.database}/api/uploads/"):
            raise ApiError(502, "database_error", f"unexpected status URL {sub.status_url}")
        r = _request("GET", sub.status_url, timeout=5)
        if r.status_code == 404:
            new, package = "gone", None
        elif r.status_code == 200:
            body = _json(r)
            new, package = body.get("status", "pending"), body.get("package")
        else:
            raise ApiError(502, "database_error", f"the package database returned HTTP {r.status_code}")
        with pkg.edit() as m:
            s = next(s for s in m.metareader.submissions if s.id == sub_id)
            s.status, s.checked_at = new, now()
            if package:
                s.package_url = package.get("url")
            sub = s
    return sub
