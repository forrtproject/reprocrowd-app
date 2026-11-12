"""Automatic data & code acquisition from repository links in the manuscript (metacheck-style).

Scans a manuscript's text for links to research data/code repositories (Zenodo, OSF, GitHub, GitLab,
Figshare, Dryad, Dataverse), classifies each link as data or code, and downloads the files into the
package as inputs (under ``inputs/``), so users don't have to upload anything by hand.

Each repository is an adapter with ``detect(text)`` (returns candidate links) and ``fetch(pkg, link)``
(downloads the files and registers them as inputs). ``acquire()`` runs every adapter over a manuscript's
text and fetches every link it finds, subject to the configured download budget.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
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


def _get_json(url: str | None, **kw) -> dict | list:
    if not url:
        raise ApiError(502, "acquire_failed", "the repository returned an empty URL")
    try:
        with _client() as c:
            r = c.get(url, **kw)
    except httpx.HTTPError as e:
        raise ApiError(502, "acquire_failed", f"could not reach {url}: {e}")
    if r.status_code == 404:
        raise ApiError(404, "acquire_not_found", f"no such record at {url}")
    if r.status_code != 200:
        raise ApiError(502, "acquire_failed", f"{url} answered {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(502, "acquire_failed", f"{url} returned no JSON: {e}")


def _download(url: str | None, dest: Path, max_bytes: int) -> None:
    """Stream ``url`` to ``dest``, aborting once ``max_bytes`` have arrived."""
    if not url:
        raise ApiError(502, "acquire_failed", "the repository returned an empty download URL")
    with _client() as c, c.stream("GET", url) as r:
        if r.status_code == 404:
            raise ApiError(404, "acquire_not_found", f"no such file at {url}")
        if r.status_code != 200:
            raise ApiError(502, "acquire_failed", f"{url} answered {r.status_code}")
        written = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                written += len(chunk)
                if written > max_bytes:
                    raise ApiError(413, "acquire_too_large", f"{url} exceeds the download budget")
                f.write(chunk)


def _guess_kind(name: str) -> str:
    """Which input role a file most likely has, from its name/extension."""
    return inputs.guess_role(name)


class _Budget:
    """A shared byte budget for one repository's files, so many files can't together exceed the limit."""

    def __init__(self, max_bytes: int):
        self.max = max_bytes
        self.used = 0

    def remaining(self) -> int:
        return max(self.max - self.used, 0)

    def allow(self, size: int = 0) -> bool:
        return self.used + size <= self.max

    def spend(self, n: int) -> None:
        self.used += n


def _origin(url: str, repo: str, link: Link) -> FetchedOrigin:
    return FetchedOrigin(url=url, repo=repo, id=link.id, label=f"fetched from {link.label}")


def _add_file(pkg: Package, filename: str, download_url: str, link: Link, repo: str,
              role: str | None, note: str, unpack: bool, budget: _Budget, size: int = 0) -> list[Resource]:
    if not budget.allow(size):
        return []

    def write(dest: Path) -> None:
        _download(download_url, dest, budget.remaining())

    r = inputs._add_file(pkg, filename, write, _origin(download_url, repo, link),
                         role, note, unpack)
    budget.spend(sum(x.bytes for x in r))
    return r


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


def zenodo_fetch(pkg: Package, link: Link, role: str | None, note: str, unpack: bool,
                 budget: _Budget) -> list[Resource]:
    data = _get_json(f"https://zenodo.org/api/records/{link.id}")
    title = (data.get("metadata") or {}).get("title", "")
    out: list[Resource] = []
    for f in data.get("files") or []:
        name = f.get("key") or f.get("filename") or "file"
        url = (f.get("links") or {}).get("self")
        if not url:
            continue
        size = int(f["size"]) if f.get("size") else 0
        out += _add_file(pkg, name, url, link, "zenodo", role, note, unpack, budget, size)
    return out


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


def github_fetch(pkg: Package, link: Link, role: str | None, note: str, unpack: bool,
                 budget: _Budget) -> list[Resource]:
    owner, _, repo = link.id.partition("/")
    branch = _github_default_branch(owner, repo)
    data = _get_json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    tree = data.get("tree") or []
    out: list[Resource] = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not path:
            continue
        name = Path(path).name
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        size = int(entry["size"]) if entry.get("size") else 0
        out += _add_file(pkg, name, raw, link, "github", role, note, unpack, budget, size)
    return out


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


def gitlab_fetch(pkg: Package, link: Link, role: str | None, note: str, unpack: bool,
                 budget: _Budget) -> list[Resource]:
    import urllib.parse
    project = urllib.parse.quote(link.id, safe="")
    # Default branch, then the tree.
    try:
        proj = _get_json(f"https://gitlab.com/api/v4/projects/{project}")
        branch = proj.get("default_branch", "main")
    except ApiError:
        branch = "main"
    data = _get_json(f"https://gitlab.com/api/v4/projects/{project}/repository/tree?recursive=true&ref={branch}")
    out: list[Resource] = []
    for entry in data:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not path:
            continue
        name = Path(path).name
        raw = f"https://gitlab.com/{link.id}/-/raw/{branch}/{path}"
        size = int(entry["size"]) if entry.get("size") else 0
        out += _add_file(pkg, name, raw, link, "gitlab", role, note, unpack, budget, size)
    return out


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


def osf_fetch(pkg: Package, link: Link, role: str | None, note: str, unpack: bool,
              budget: _Budget) -> list[Resource]:
    def walk(folder_url: str | None, out: list[Resource]) -> None:
        data = _get_json(folder_url)
        for item in data.get("data") or []:
            attrs = item.get("attributes") or {}
            if attrs.get("kind") == "folder":
                sub = _osf_folder_url(item)
                if sub:
                    walk(sub, out)
            else:
                name = attrs.get("name") or "file"
                dl = (item.get("links") or {}).get("download") or (item.get("links") or {}).get("self")
                if not dl:
                    continue
                size = int(attrs["size"]) if attrs.get("size") else 0
                out += _add_file(pkg, name, dl, link, "osf", role, note, unpack, budget, size)
        next_url = (data.get("links") or {}).get("next")
        if next_url:
            walk(next_url, out)
    out: list[Resource] = []
    walk(f"https://api.osf.io/v2/nodes/{link.id}/files/", out)
    return out


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


def figshare_fetch(pkg: Package, link: Link, role: str | None, note: str, unpack: bool,
                   budget: _Budget) -> list[Resource]:
    data = _get_json(f"https://api.figshare.com/v2/articles/{link.id}")
    title = data.get("title", "")
    out: list[Resource] = []
    for f in data.get("files") or []:
        name = f.get("name") or "file"
        dl = f.get("download_url")
        if not dl:
            continue
        size = int(f["size"]) if f.get("size") else 0
        out += _add_file(pkg, name, dl, link, "figshare", role, note, unpack, budget, size)
    return out


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


def fetch(pkg: Package, link: Link, role: str | None = None, note: str = "",
          unpack: bool = True) -> list[Resource]:
    """Download the files of one detected link into the package as inputs."""
    if link.repo not in ADAPTERS:
        raise ApiError(400, "unknown_repository", f"no adapter for repository {link.repo!r}")
    _, fetch_fn = ADAPTERS[link.repo]
    budget = _Budget(settings().max_download_mb * 2**20)
    return fetch_fn(pkg, link, role, note, unpack, budget)


def manuscript_text(pkg: Package, fulltext: str | None = None) -> str:
    """Extract all text from a full-text resource (default: the first full text), for scanning."""
    from . import fulltexts
    m = pkg.load()
    if fulltext:
        r = fulltexts.get(pkg, fulltext)
    else:
        r = next((r for r in m.by_kind("fulltext") if r.path.lower().endswith((".xml", ".json"))), None)
        if r is None:
            raise ApiError(409, "no_fulltext", "add a full text (JATS XML) first")
    root = etree.parse(str(pkg.path(r.path))).getroot()
    return " ".join(t for t in root.itertext() if t and t.strip())


def acquire(pkg: Package, fulltext: str | None = None, role: str | None = None,
            note: str = "", unpack: bool = True, repos: Iterable[str] | None = None) -> dict:
    """Detect repository links in a manuscript and fetch them. Returns the links found, the resources added,
    and any per-repository failures."""
    text = manuscript_text(pkg, fulltext)
    links = detect(text, repos)
    resources: list[Resource] = []
    failures: list[dict[str, str]] = []
    for link in links:
        try:
            resources.extend(fetch(pkg, link, role, note, unpack))
        except ApiError as e:
            # One failing repository must not abort the whole run; it is reported instead.
            failures.append({"repo": link.repo, "id": link.id, "url": link.url, "error": e.message})
        except Exception as e:  # an adapter bug must not 500 the whole request
            failures.append({"repo": link.repo, "id": link.id, "url": link.url, "error": f"unexpected error: {e}"})
    return {"links": [l.__dict__ for l in links], "resources": resources, "failures": failures}
