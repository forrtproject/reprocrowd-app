"""Automatic data & code acquisition from repository links in the manuscript (metacheck-style).

Scans a manuscript's text for links to research data/code repositories (Zenodo, OSF, GitHub, GitLab,
Figshare), classifies each link as data or code, and downloads the files into the package as inputs
(under ``inputs/``), so users don't have to upload anything by hand.

Each repository is an adapter with ``detect(text)`` (returns candidate links) and ``fetch(job)`` (lists the
record's files and hands each to ``job.add``). ``acquire()`` runs every adapter over a manuscript's text and
fetches every link it finds, subject to the configured download budget. Failures are contained: a file that
can't be downloaded is reported and skipped, a repository that can't be read is reported, and neither stops
the rest. Messages are written for the user (host, what went wrong, what to do).
"""
from __future__ import annotations

import re
from urllib.parse import quote
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import httpx
from lxml import etree

from . import inputs
from .config import settings
from .errors import ApiError
from .package import Package
from .schemas.manifest import FetchedOrigin, Resource

USER_AGENT = "reprocrowd-cli/0.1 (+https://github.com/)"


@dataclass
class Link:
    """One detected repository link in a manuscript."""

    repo: str  # "zenodo", "github", ...
    url: str  # the link as it appeared in the text
    id: str  # repository-specific identifier (DOI, owner/repo, record id, ...)
    kind: str  # "data" | "code" | "other" — best guess from the surrounding text
    label: str  # human-readable description
    title: str = ""


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _host(url: str) -> str:
    return httpx.URL(url).host or url


def _network_error(url: str, e: Exception) -> ApiError:
    """A transport failure (DNS, refused connection, timeout, dropped stream) as a message for the user."""
    host = _host(url)
    if isinstance(e, httpx.TimeoutException):
        msg = f"{host} did not answer in time; try again later"
    elif isinstance(e, httpx.ConnectError):
        msg = f"could not connect to {host}; check the internet connection or try again later"
    elif isinstance(e, (httpx.RemoteProtocolError, httpx.ReadError)):
        msg = f"the connection to {host} broke off; try again later"
    elif isinstance(e, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        msg = f"the repository returned an unusable address ({url})"
    else:
        msg = f"network error talking to {host} ({type(e).__name__}: {e})"
    return ApiError(502, "acquire_failed", msg)


def _status_error(r: httpx.Response, what: str) -> ApiError:
    """A non-200 answer as a message for the user. ``what`` is "record" or "file"."""
    host, s = _host(str(r.request.url)), r.status_code
    if s == 404 or s == 410:
        return ApiError(404, "acquire_not_found", f"{host} has no such {what} (it may have been removed or be private)")
    if s in (401, 403) and r.headers.get("x-ratelimit-remaining") == "0":
        return ApiError(502, "acquire_failed", f"{host}'s rate limit for anonymous requests is used up; try again in an hour")
    if s in (401, 403):
        return ApiError(502, "acquire_failed", f"{host} refused access to the {what} (it may be restricted or under embargo)")
    if s == 429:
        return ApiError(502, "acquire_failed", f"{host} received too many requests; try again in a few minutes")
    if s >= 500:
        return ApiError(502, "acquire_failed", f"{host} had a server error (HTTP {s}); try again later")
    return ApiError(502, "acquire_failed", f"{host} answered HTTP {s}")


def _get_json(url: str | None, expect: type = dict) -> dict | list:
    """GET a JSON document of type ``expect``; every failure is an ApiError with a readable message."""
    return _get_json_page(url, expect)[0]


def _get_json_page(url: str | None, expect: type = dict) -> tuple[dict | list, str | None]:
    """Like :func:`_get_json`, plus the next page's URL from the ``Link`` header (None on the last page)."""
    if not url:
        raise ApiError(502, "acquire_failed", "the repository returned an empty URL")
    try:
        with _client() as c:
            r = c.get(url)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise _network_error(url, e) from e
    if r.status_code != 200:
        raise _status_error(r, "record")
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, expect):
        raise ApiError(502, "acquire_failed", f"{_host(url)} gave an unexpected answer (not the JSON its API documents)")
    return data, (r.links.get("next") or {}).get("url")


def _download(url: str | None, dest: Path, max_bytes: int) -> None:
    """Stream ``url`` to ``dest``, aborting once ``max_bytes`` have arrived."""
    if not url:
        raise ApiError(502, "acquire_failed", "the repository returned an empty download URL")
    try:
        with _client() as c, c.stream("GET", url) as r:
            if r.status_code != 200:
                raise _status_error(r, "file")
            written = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ApiError(413, "acquire_too_large", "larger than what is left of the download limit")
                    f.write(chunk)
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise _network_error(url, e) from e


def _guess_kind(name: str) -> str:
    """Which input role a file most likely has, from its name/extension."""
    return inputs.guess_role(name)


def _size(n: int) -> str:
    """Bytes for a message: "850 KB", "180 MB", "1.2 GB"."""
    for unit, f in (("GB", 2**30), ("MB", 2**20)):
        if n >= f:
            return f"{n / f:.1f} {unit}".replace(".0 ", " ")
    return f"{max(round(n / 2**10), 1)} KB" if n else "0 KB"


class _Budget:
    """The download limit for one link's files, so many files can't together exceed it.

    Only bytes actually downloaded count: an unpacked zip is charged once, for the archive.
    """

    def __init__(self, max_bytes: int):
        self.max = max_bytes
        self.used = 0

    def remaining(self) -> int:
        return max(self.max - self.used, 0)

    def allow(self, size: int = 0) -> bool:
        return self.used + size <= self.max

    def spend(self, n: int) -> None:
        self.used += n

    def skip_reason(self, size: int = 0) -> str:
        """Why a file of ``size`` bytes (0: unknown) is skipped, or "" if it may be downloaded."""
        limit = f"the {_size(self.max)} download limit"
        if self.remaining() == 0:
            return f"skipped: {limit} is used up"
        if size and not self.allow(size):
            left = "" if self.used == 0 else f"the {_size(self.remaining())} left of "
            return f"skipped: {_size(size)}, more than {left}{limit}"
        return ""


def _origin(url: str, repo: str, link: Link) -> FetchedOrigin:
    return FetchedOrigin(url=url, repo=repo, id=link.id, label=f"fetched from {link.label}")


@dataclass
class _Job:
    """One link being fetched: where files go, how they are registered, and what happened to each.

    Adapters list a repository's files and call :meth:`add` for each; a file that fails is recorded in
    ``failures`` and the adapter carries on with the next one, so one bad file doesn't lose the others.
    """

    pkg: Package
    link: Link
    role: str | None = None
    note: str = ""
    unpack: bool = True
    budget: _Budget | None = None
    resources: list[Resource] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    error: str = ""  # set when the repository itself couldn't be read (listing, API record, ...)
    error_code: str = ""

    def fail(self, filename: str, url: str, code: str, error: str) -> None:
        self.failures.append({"file": filename, "url": url, "code": code, "error": error})

    def add(self, filename: str, download_url: str, size: int = 0) -> None:
        """Download one file and register it; on any failure record it in ``failures`` and return."""
        budget = self.budget = self.budget or _Budget(settings().max_download_mb * 2**20)
        if reason := budget.skip_reason(size):
            self.fail(filename, download_url, "acquire_too_large", reason)
            return
        left = budget.remaining()
        downloaded = 0

        def write(dest: Path) -> None:
            nonlocal downloaded
            _download(download_url, dest, left)
            downloaded = dest.stat().st_size

        try:
            r = inputs._add_file(self.pkg, filename, write, _origin(download_url, self.link.repo, self.link),
                                 self.role, self.note, self.unpack)
        except ApiError as e:
            if e.code == "acquire_too_large":  # size unknown up front; found out while downloading
                left_text = "" if budget.used == 0 else f"the {_size(left)} left of "
                e.message = f"skipped: larger than {left_text}the {_size(budget.max)} download limit"
            self.fail(filename, download_url, e.code, e.message)
            return
        except Exception as e:  # noqa: BLE001 - one file's bug must not lose the rest of the repository
            self.fail(filename, download_url, "unexpected", f"unexpected error: {e}")
            return
        budget.spend(downloaded)
        self.resources += r


# --------------------------------------------------------------------------- adapters

def _zenodo_id(text: str) -> list[str]:
    """Zenodo record ids and DOIs: 10.5281/zenodo.<id>, zenodo.org/record(s)/<id>."""
    found: list[str] = []
    for m in re.finditer(r"10\.5281/zenodo\.(\d+)", text):
        found.append(m.group(1))
    for m in re.finditer(r"zenodo\.org/(?:record|records)/(\d+)", text):
        found.append(m.group(1))
    return found


def zenodo_detect(text: str) -> list[Link]:
    ids: list[str] = []
    for i in _zenodo_id(text):
        if i not in ids:
            ids.append(i)
    return [Link(repo="zenodo", url=f"https://zenodo.org/records/{i}", id=i, kind="data",
                 label=f"Zenodo record {i}", title=f"Zenodo record {i}") for i in ids]


def zenodo_fetch(job: _Job) -> None:
    data = _get_json(f"https://zenodo.org/api/records/{job.link.id}")
    for f in data.get("files") or []:
        name = f.get("key") or f.get("filename") or "file"
        url = (f.get("links") or {}).get("self")
        if not url:
            continue
        size = int(f["size"]) if f.get("size") else 0
        job.add(name, url, size)


def _github_id(text: str) -> list[tuple[str, str, str]]:
    """(owner, repo, branch|'') tuples. Only github.com/<owner>/<repo> URLs."""
    found: list[tuple[str, str, str]] = []
    for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/tree/([A-Za-z0-9_./-]+))?", text):
        owner, repo, branch = m.group(1), m.group(2).rstrip(".-"), m.group(3) or ""
        if repo:
            found.append((owner, repo, branch))
    return found


def github_detect(text: str) -> list[Link]:
    seen: set[str] = set()
    out: list[Link] = []
    for owner, repo, branch in _github_id(text):
        key = f"{owner}/{repo}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Link(repo="github", url=f"https://github.com/{key}", id=key, kind="code",
                        label=f"GitHub repository {key}", title=key))
    return out


def _github_default_branch(owner: str, repo: str) -> str:
    data = _get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return data.get("default_branch", "main")


def github_fetch(job: _Job) -> None:
    owner, _, repo = job.link.id.partition("/")
    branch = _github_default_branch(owner, repo)
    data = _get_json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    for entry in data.get("tree") or []:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not path:
            continue
        name = Path(path).name
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(branch)}/{quote(path)}"
        size = int(entry["size"]) if entry.get("size") else 0
        job.add(name, raw, size)


def _gitlab_id(text: str) -> list[tuple[str, str, str]]:
    """(owner, repo, branch|'') for gitlab.com URLs."""
    found: list[tuple[str, str, str]] = []
    for m in re.finditer(r"gitlab\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/tree/([A-Za-z0-9_./-]+))?", text):
        owner, repo, branch = m.group(1), m.group(2).rstrip(".-"), m.group(3) or ""
        if repo:
            found.append((owner, repo, branch))
    return found


def gitlab_detect(text: str) -> list[Link]:
    seen: set[str] = set()
    out: list[Link] = []
    for owner, repo, _branch in _gitlab_id(text):
        key = f"{owner}/{repo}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Link(repo="gitlab", url=f"https://gitlab.com/{key}", id=key, kind="code",
                        label=f"GitLab repository {key}", title=key))
    return out


def gitlab_fetch(job: _Job) -> None:
    import urllib.parse
    project = urllib.parse.quote(job.link.id, safe="")
    # Default branch, then the tree.
    try:
        proj = _get_json(f"https://gitlab.com/api/v4/projects/{project}")
        branch = proj.get("default_branch", "main")
    except ApiError:
        branch = "main"
    # The tree comes in pages (20 entries by default); keyset pagination hands out the next page's URL in
    # the Link header. Each page is read once, so a server pointing "next" back at a page can't loop.
    url: str | None = (f"https://gitlab.com/api/v4/projects/{project}/repository/tree"
                       f"?recursive=true&ref={urllib.parse.quote(branch, safe='')}&per_page=100&pagination=keyset")
    seen: set[str] = set()
    while url and url not in seen:
        seen.add(url)
        page, url = _get_json_page(url, expect=list)
        for entry in page:
            if not isinstance(entry, dict) or entry.get("type") != "blob" or not entry.get("path"):
                continue
            path = entry["path"]
            raw = f"https://gitlab.com/{job.link.id}/-/raw/{urllib.parse.quote(branch)}/{urllib.parse.quote(path)}"
            job.add(Path(path).name, raw, int(entry["size"]) if entry.get("size") else 0)


def _osf_id(text: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"osf\.io/([A-Za-z0-9]{5,})", text):
        found.append(m.group(1))
    return found


def osf_detect(text: str) -> list[Link]:
    ids: list[str] = []
    for i in _osf_id(text):
        if i not in ids:
            ids.append(i)
    return [Link(repo="osf", url=f"https://osf.io/{i}", id=i, kind="data",
                 label=f"OSF project {i}", title=f"OSF project {i}") for i in ids]


def _osf_folder_url(item: dict) -> str | None:
    """The URL listing a folder's contents.

    OSF's file folders don't put a listing URL in ``links``; they expose it as the ``files``
    relationship (``relationships.files.links.related.href``). The root ``osfstorage`` folder in
    particular has no ``links.self``, so without this the whole walk is skipped and 0 files come back.
    """
    rel = (item.get("relationships") or {}).get("files", {})
    related = (rel.get("links") or {}).get("related") or {}
    return related.get("href") or (item.get("links") or {}).get("self")


def osf_fetch(job: _Job) -> None:
    # Iterative, and each listing page once: a page whose "next" points back to itself (or a folder
    # cycle) must not loop forever.
    todo = [f"https://api.osf.io/v2/nodes/{job.link.id}/files/"]
    seen: set[str] = set()
    while todo:
        url = todo.pop(0)
        if url in seen:
            continue
        seen.add(url)
        data = _get_json(url)
        for item in data.get("data") or []:
            attrs = item.get("attributes") or {}
            if attrs.get("kind") == "folder":
                sub = _osf_folder_url(item)
                if sub:
                    todo.append(sub)
            else:
                name = attrs.get("name") or "file"
                dl = (item.get("links") or {}).get("download") or (item.get("links") or {}).get("self")
                if not dl:
                    continue
                size = int(attrs["size"]) if attrs.get("size") else 0
                job.add(name, dl, size)
        next_url = (data.get("links") or {}).get("next")
        if next_url:
            todo.append(next_url)


def _figshare_id(text: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"10\.6084/m9\.figshare\.(\d+)", text):
        found.append(m.group(1))
    for m in re.finditer(r"figshare\.com/articles/[^/]+/(\d+)", text):
        found.append(m.group(1))
    return found


def figshare_detect(text: str) -> list[Link]:
    ids: list[str] = []
    for i in _figshare_id(text):
        if i not in ids:
            ids.append(i)
    return [Link(repo="figshare", url=f"https://figshare.com/articles/{i}", id=i, kind="data",
                 label=f"Figshare article {i}", title=f"Figshare article {i}") for i in ids]


def figshare_fetch(job: _Job) -> None:
    data = _get_json(f"https://api.figshare.com/v2/articles/{job.link.id}")
    for f in data.get("files") or []:
        name = f.get("name") or "file"
        dl = f.get("download_url")
        if not dl:
            continue
        size = int(f["size"]) if f.get("size") else 0
        job.add(name, dl, size)


ADAPTERS: dict[str, tuple[Callable[[str], list[Link]], Callable]] = {
    "zenodo": (zenodo_detect, zenodo_fetch),
    "github": (github_detect, github_fetch),
    "gitlab": (gitlab_detect, gitlab_fetch),
    "osf": (osf_detect, osf_fetch),
    "figshare": (figshare_detect, figshare_fetch),
}


def detect(text: str, repos: Iterable[str] | None = None) -> list[Link]:
    """Detect repository links in ``text``. ``repos`` limits which adapters run (default: all)."""
    names = list(repos) if repos else list(ADAPTERS)
    out: list[Link] = []
    for name in names:
        detect_fn, _ = ADAPTERS[name]
        out.extend(detect_fn(text))
    return out


def _run(pkg: Package, link: Link, role: str | None, note: str, unpack: bool) -> _Job:
    """Fetch one link. Never raises: a failure to read the repository itself goes to ``job.error``,
    single files that fail go to ``job.failures``, and files added before either are kept."""
    job = _Job(pkg, link, role, note, unpack, _Budget(settings().max_download_mb * 2**20))
    if link.repo not in ADAPTERS:
        job.error, job.error_code = f"no adapter for repository {link.repo!r}", "unknown_repository"
        return job
    _, fetch_fn = ADAPTERS[link.repo]
    try:
        fetch_fn(job)
    except ApiError as e:
        job.error, job.error_code = e.message, e.code
    except Exception as e:  # noqa: BLE001 - an adapter bug must not 500 the whole request
        job.error, job.error_code = f"unexpected error: {e}", "unexpected"
    return job


def fetch(pkg: Package, link: Link, role: str | None = None, note: str = "",
          unpack: bool = True) -> list[Resource]:
    """Download the files of one detected link into the package as inputs; raises if nothing could be added."""
    job = _run(pkg, link, role, note, unpack)
    if job.error and not job.resources:
        raise ApiError(502, job.error_code if job.error_code.startswith("acquire_") else "acquire_failed", job.error)
    return job.resources


def manuscript_text(pkg: Package, fulltext: str | None = None) -> str:
    """Extract all text from a full-text resource (default: the JATS XML), for scanning."""
    from . import fulltexts
    m = pkg.load()
    if fulltext:
        r = fulltexts.get(pkg, fulltext)
    else:
        r = next((r for r in m.by_kind("fulltext") if r.reprocrowd.flavor == "jats"), None)
        if r is None:
            raise ApiError(409, "no_fulltext", "add a full text (JATS XML) first")
    try:
        root = etree.parse(str(pkg.path(r.path))).getroot()
    except etree.XMLSyntaxError as e:
        raise ApiError(409, "no_fulltext", f"{r.name} is not XML and can't be scanned for links; use the JATS full text") from e
    return " ".join(t for t in root.itertext() if t and t.strip())


def acquire(pkg: Package, fulltext: str | None = None, role: str | None = None,
            note: str = "", unpack: bool = True, repos: Iterable[str] | None = None) -> dict:
    """Detect repository links in a manuscript and fetch them. Returns the links found, the resources added,
    and the failures: one per link whose repository couldn't be read (no ``file``), and one per file that
    couldn't be downloaded or was skipped for the download limit (with ``file`` and ``file_url``; ``code``
    ``acquire_too_large`` for skips). A failure never stops the other links or files."""
    text = manuscript_text(pkg, fulltext)
    links = detect(text, repos)
    resources: list[Resource] = []
    failures: list[dict[str, str]] = []
    for link in links:
        job = _run(pkg, link, role, note, unpack)
        resources += job.resources
        where = {"repo": link.repo, "id": link.id, "url": link.url}
        if job.error:
            failures.append({**where, "code": job.error_code, "error": job.error})
        for f in job.failures:
            failures.append({**where, "file": f["file"], "file_url": f["url"], "code": f["code"], "error": f["error"]})
    return {"links": [l.__dict__ for l in links], "resources": resources, "failures": failures}
