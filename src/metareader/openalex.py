"""OpenAlex metadata and full-text download candidates (URLs named in the OpenAlex record, plus Europe PMC
JATS when a PMCID is known)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings
from .errors import ApiError
from .identifiers import openalex_key
from .schemas.common import Identifier

API = "https://api.openalex.org"
CONTENT = "https://content.openalex.org"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
USER_AGENT = "metareader-cli/0.1 (+https://github.com/)"


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _auth_params() -> dict[str, str]:
    cfg = settings()
    return {k: v for k, v in {"api_key": cfg.openalex_api_key, "mailto": cfg.mailto}.items() if v}


def fetch_work(ident: Identifier) -> dict[str, Any]:
    url = f"{API}/works/{openalex_key(ident)}"
    try:
        with _client() as c:
            r = c.get(url, params=_auth_params())
    except httpx.HTTPError as e:
        raise ApiError(502, "openalex_failed", f"could not reach OpenAlex: {e}")
    if r.status_code == 404:
        raise ApiError(404, "openalex_not_found", f"OpenAlex has no work for {ident.scheme}:{ident.value}")
    if r.status_code != 200:
        raise ApiError(502, "openalex_failed", f"OpenAlex returned HTTP {r.status_code}", {"body": r.text[:500]})
    return r.json()


def identifiers_of(work: dict[str, Any]) -> dict[str, str]:
    ids = work.get("ids") or {}
    out = {}
    if ids.get("doi"):
        out["doi"] = ids["doi"].lower().removeprefix("https://doi.org/")
    if ids.get("openalex"):
        out["openalex"] = ids["openalex"].rsplit("/", 1)[-1]
    if ids.get("pmid"):
        out["pmid"] = ids["pmid"].rsplit("/", 1)[-1]
    if ids.get("pmcid"):
        v = ids["pmcid"].rsplit("/", 1)[-1].upper()
        out["pmcid"] = v if v.startswith("PMC") else f"PMC{v}"
    return out


@dataclass
class Candidate:
    id: str
    label: str
    url: str  # without secrets
    format: str  # expected format: pdf, tei, jats
    filename: str
    openalex_auth: bool = False  # add the OpenAlex API key when downloading

    def download_url(self) -> tuple[str, dict[str, str]]:
        return self.url, (_auth_params() if self.openalex_auth else {})


def _europepmc_pmcid(doi: str) -> str | None:
    try:
        with _client() as c:
            r = c.get(f"{EUROPEPMC}/search", params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"})
        return next((x["pmcid"] for x in r.json().get("resultList", {}).get("result", []) if x.get("pmcid")), None)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def candidates(work: dict[str, Any]) -> tuple[list[Candidate], list[str]]:
    """Download candidates and notes about sources that are known but unavailable."""
    out: list[Candidate] = []
    notes: list[str] = []
    ids = identifiers_of(work)

    def add(label: str, url: str | None, fmt: str, filename: str, auth: bool = False):
        if url and not any(c.url == url for c in out):
            cid = "c-" + hashlib.sha1(url.encode()).hexdigest()[:8]
            out.append(Candidate(cid, label, url, fmt, filename, auth))

    has = work.get("has_content") or {}
    wid = ids.get("openalex")
    if wid and (has.get("pdf") or has.get("grobid_xml")) and not settings().openalex_api_key:
        notes.append("OpenAlex has cached content for this work, but no OpenAlex API key is configured.")
    if wid and settings().openalex_api_key:
        if has.get("pdf"):
            add("OpenAlex content (PDF)", f"{CONTENT}/works/{wid}.pdf", "pdf", "openalex.pdf", True)
        if has.get("grobid_xml"):
            add("OpenAlex content (GROBID TEI)", f"{CONTENT}/works/{wid}.grobid-xml", "tei", "openalex.tei.xml", True)
    add("best open-access location", (work.get("best_oa_location") or {}).get("pdf_url"), "pdf", "best-oa.pdf")
    add("primary location", (work.get("primary_location") or {}).get("pdf_url"), "pdf", "primary.pdf")
    for loc in work.get("locations") or []:
        src = ((loc or {}).get("source") or {}).get("display_name") or "location"
        add(f"location: {src}", (loc or {}).get("pdf_url"), "pdf", "location.pdf")
    oa_url = (work.get("open_access") or {}).get("oa_url")
    if oa_url and "pdf" in oa_url.lower():
        add("open_access.oa_url", oa_url, "pdf", "oa-url.pdf")
    pmcid = ids.get("pmcid") or (_europepmc_pmcid(ids["doi"]) if ids.get("doi") else None)
    if pmcid:
        add(f"Europe PMC JATS ({pmcid})", f"{EUROPEPMC}/{pmcid}/fullTextXML", "jats", "europepmc.jats.xml")
    else:
        notes.append("No PMCID known: no Europe PMC JATS candidate.")
    return out, notes


def download(c: Candidate, dest, max_bytes: int) -> None:
    url, params = c.download_url()
    try:
        with _client() as client, client.stream("GET", url, params=params, headers={"Accept": "application/pdf,application/xml;q=0.9,*/*;q=0.5"}) as r:
            if r.status_code != 200:
                raise ApiError(502, "download_failed", f"{c.label}: HTTP {r.status_code}", {"url": c.url})
            n = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    n += len(chunk)
                    if n > max_bytes:
                        raise ApiError(502, "download_failed", f"{c.label}: larger than {max_bytes // 2**20} MB", {"url": c.url})
                    f.write(chunk)
    except httpx.HTTPError as e:
        raise ApiError(502, "download_failed", f"{c.label}: {e}", {"url": c.url})
