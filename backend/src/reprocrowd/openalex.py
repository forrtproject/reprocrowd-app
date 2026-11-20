"""OpenAlex metadata and full-text download candidates (URLs named in the OpenAlex record, plus JATS XML from
Europe PMC when a PMCID is known, and from the publisher for PLOS articles)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import settings
from .errors import ApiError
from .identifiers import openalex_key
from .schemas.common import Identifier

API = "https://api.openalex.org"
CONTENT = "https://content.openalex.org"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
# PLOS serves the JATS XML of every article it publishes, PubMed Central or not; the journal in the path doesn't matter.
PLOS = "https://journals.plos.org/plosone/article/file"
USER_AGENT = "reprocrowd-cli/0.1 (+https://github.com/)"


# The JATS check must answer within the app's 60-s limit (src/main/api.ts), so it has CHECK_BUDGET seconds in all. A
# step with a fallback after it (OpenAlex, the PMCID search, Europe PMC's JATS) gets at most CHECK_TIMEOUT of that, so a
# hanging Europe PMC still leaves time to ask PLOS, which gets whatever is left.
CHECK_BUDGET = 50
CHECK_TIMEOUT = 15


_now = time.monotonic  # replaceable in tests


class Deadline:
    def __init__(self, seconds: float):
        self.end = _now() + seconds

    def left(self, cap: float | None = None) -> float:
        """Seconds left (at least one, so a request can still be made), at most ``cap``."""
        left = max(1.0, self.end - _now())
        return min(left, cap) if cap else left


def _client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _get(url: str, params: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """GET (status, body), given up after ``timeout`` seconds in all: httpx's own timeout bounds each wait, so a server
    sending a trickle could otherwise take far longer."""
    deadline = Deadline(timeout)
    with _client(timeout) as c, c.stream("GET", url, params=params) as r:
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
            if _now() > deadline.end:
                raise httpx.ReadTimeout(f"no complete answer within {timeout:.0f} s")
        return r.status_code, body


def _auth_params() -> dict[str, str]:
    cfg = settings()
    return {k: v for k, v in {"api_key": cfg.openalex_api_key, "mailto": cfg.mailto}.items() if v}


def fetch_work(ident: Identifier, timeout: float = 60) -> dict[str, Any]:
    url = f"{API}/works/{openalex_key(ident)}"
    try:
        status, body = _get(url, _auth_params(), timeout)
    except httpx.HTTPError as e:
        raise ApiError(502, "openalex_failed", f"could not reach OpenAlex: {e}")
    if status == 404:
        raise ApiError(404, "openalex_not_found", f"OpenAlex has no work for {ident.scheme}:{ident.value}")
    if status != 200:
        raise ApiError(502, "openalex_failed", f"OpenAlex returned HTTP {status}", {"body": body[:500].decode("utf-8", "replace")})
    return json.loads(body)


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


def _europepmc_pmcid(doi: str, timeout: float = CHECK_TIMEOUT) -> str | None:
    try:
        _, body = _get(f"{EUROPEPMC}/search", {"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"}, timeout)
        return next((x["pmcid"] for x in json.loads(body).get("resultList", {}).get("result", []) if x.get("pmcid")), None)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def candidates(work: dict[str, Any], pmcid_timeout: float = CHECK_TIMEOUT) -> tuple[list[Candidate], list[str]]:
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
    pmcid = ids.get("pmcid") or (_europepmc_pmcid(ids["doi"], pmcid_timeout) if ids.get("doi") else None)
    if pmcid:
        add(f"Europe PMC JATS ({pmcid})", f"{EUROPEPMC}/{pmcid}/fullTextXML", "jats", "europepmc.jats.xml")
    else:
        notes.append("No PMCID known: no Europe PMC JATS candidate.")
    if ids.get("doi", "").startswith("10.1371/journal."):  # after Europe PMC, whose archive has the published figures
        add("PLOS JATS", f"{PLOS}?{urlencode({'id': ids['doi'], 'type': 'manuscript'})}", "jats", "plos.jats.xml")
    return out, notes


def download(c: Candidate, dest, max_bytes: int) -> None:
    url, params = c.download_url()  # params=None: an empty dict would drop the query string already in the URL
    try:
        with _client() as client, client.stream("GET", url, params=params or None, headers={"Accept": "application/pdf,application/xml;q=0.9,*/*;q=0.5"}) as r:
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


def _pmcid_of(c: Candidate) -> str | None:
    return c.label.split("(")[-1].rstrip(")") if c.label.startswith("Europe PMC") else None


def _jats_head(c: Candidate, timeout: float = CHECK_TIMEOUT) -> str | None:
    """Why the candidate's JATS XML can't be had, from the start of the download; None if it can. Raises
    jats_check_failed if its source can't be reached, or doesn't answer within ``timeout`` seconds in all (httpx's own
    timeout is per wait, so a server sending a trickle could otherwise take longer)."""
    source = "Europe PMC" if _pmcid_of(c) else "PLOS"
    deadline = Deadline(timeout)
    try:
        with _client(timeout) as client, client.stream("GET", c.url, headers={"Accept": "application/xml"}) as r:
            head = b""
            if r.status_code == 200:
                for chunk in r.iter_bytes():
                    head += chunk
                    if len(head) > 65536:
                        break
                    if _now() > deadline.end:
                        raise ApiError(502, "jats_check_failed", f"{source} didn't answer within {timeout:.0f} s")
    except httpx.HTTPError as e:
        raise ApiError(502, "jats_check_failed", f"could not reach {source}: {e}")
    if r.status_code != 200:
        if _pmcid_of(c):
            return f"Europe PMC has no JATS XML for {_pmcid_of(c)}: the paper isn't in the open-access subset of PubMed Central."
        return f"PLOS has no JATS XML for this DOI (HTTP {r.status_code})."
    if b"<article" not in head:
        return f"{source}'s answer for {_pmcid_of(c) or 'this DOI'} isn't JATS XML."
    return None


def jats_availability(doi: str) -> dict[str, Any]:
    """Can the paper's JATS XML be retrieved? Asks OpenAlex and Europe PMC for its PMCID, then fetches the start of
    Europe PMC's fullTextXML: a PMCID alone isn't enough, since only the open-access subset has full-text XML. PLOS
    articles also have it from PLOS, which is tried if Europe PMC has none. A source that can't be reached doesn't stop
    the others from being tried, but if none has the XML, that is jats_check_failed: the answer isn't known."""
    deadline = Deadline(CHECK_BUDGET)
    work = fetch_work(Identifier(scheme="doi", value=doi), deadline.left(CHECK_TIMEOUT))
    cands, _ = candidates(work, deadline.left(CHECK_TIMEOUT))
    jats = [c for c in cands if c.format == "jats"]
    out: dict[str, Any] = {"doi": doi, "available": False, "source": None, "pmcid": None, "url": None, "title": work.get("title"),
                           "reason": None}
    if not jats:
        out["reason"] = "Europe PMC has no copy of this paper in PubMed Central (no PMCID)."
        return out
    reasons = [] if _pmcid_of(jats[0]) else ["Europe PMC has no copy of this paper in PubMed Central (no PMCID)."]
    unreachable: list[str] = []
    for i, c in enumerate(jats):
        try:  # the last source gets all the time left; one with a fallback after it, at most CHECK_TIMEOUT
            reason = _jats_head(c, deadline.left(None if i == len(jats) - 1 else CHECK_TIMEOUT))
        except ApiError as e:
            unreachable.append(e.message)
            continue
        if reason is None:
            return out | {"available": True, "source": "Europe PMC" if _pmcid_of(c) else "PLOS", "pmcid": _pmcid_of(c), "url": c.url}
        reasons.append(reason)
    if unreachable:
        raise ApiError(502, "jats_check_failed", "; ".join(unreachable) + (f" ({' '.join(reasons)})" if reasons else ""))
    pmcid = next((_pmcid_of(c) for c in jats if _pmcid_of(c)), None)
    return out | {"pmcid": pmcid, "url": jats[0].url, "reason": " ".join(reasons)}
