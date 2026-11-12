from pathlib import Path

import httpx
import respx

from reprocrowd import acquire
from reprocrowd.package import Package, init_package


def _make_pkg(tmp_path) -> Package:
    root = tmp_path / "pkg"
    init_package(root, "pkg", None)
    return Package("pkg", root)


def test_detect():
    text = (
        "Data are available at https://doi.org/10.5281/zenodo.123456 and "
        "https://zenodo.org/records/654321. "
        "Code lives at https://github.com/forrtproject/reprocrowd-app and "
        "https://gitlab.com/owner/repo. The OSF project is https://osf.io/ab3cd/ "
        "and the article 10.6084/m9.figshare.999 is also cited."
    )
    links = acquire.detect(text)
    got = {(l.repo, l.id) for l in links}
    assert ("zenodo", "123456") in got
    assert ("zenodo", "654321") in got
    assert ("github", "forrtproject/reprocrowd-app") in got
    assert ("gitlab", "owner/repo") in got
    assert ("osf", "ab3cd") in got
    assert ("figshare", "999") in got


def test_detect_restricted():
    text = "Data at 10.5281/zenodo.1 and code at https://github.com/o/r"
    links = acquire.detect(text, repos=["zenodo"])
    assert [(l.repo, l.id) for l in links] == [("zenodo", "1")]


@respx.mock
def test_fetch_zenodo(tmp_path):
    respx.get("https://zenodo.org/api/records/123456").mock(return_value=httpx.Response(200, json={
        "metadata": {"title": "Replication data"},
        "files": [{"key": "data.csv", "size": 11, "links": {"self": "https://zenodo.org/api/files/123/data.csv"}}],
    }))
    respx.get("https://zenodo.org/api/files/123/data.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="zenodo", url="https://zenodo.org/records/123456", id="123456", kind="data",
                        label="Zenodo record 123456")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-data.csv"]
    m = pkg.load()
    r = m.get("in-data.csv")
    assert r.reprocrowd.role == "data"
    assert r.reprocrowd.origin.model_dump() == {"type": "fetched", "url": "https://zenodo.org/api/files/123/data.csv",
                                                "repo": "zenodo", "id": "123456", "label": "fetched from Zenodo record 123456"}
    assert (pkg.path(r.path)).read_bytes() == b"a,b\n1,2\n"


@respx.mock
def test_fetch_github(tmp_path):
    respx.get("https://api.github.com/repos/owner/repo").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get("https://api.github.com/repos/owner/repo/git/trees/main?recursive=1").mock(
        return_value=httpx.Response(200, json={"tree": [
            {"type": "blob", "path": "analysis.R", "size": 100},
            {"type": "tree", "path": "data"},
        ]}))
    respx.get("https://raw.githubusercontent.com/owner/repo/main/analysis.R").mock(
        return_value=httpx.Response(200, content=b"print('hi')\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="github", url="https://github.com/owner/repo", id="owner/repo", kind="code",
                        label="GitHub repository owner/repo")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-analysis.r"]
    assert pkg.load().get("in-analysis.r").reprocrowd.role == "code"


@respx.mock
def test_fetch_gitlab(tmp_path):
    import urllib.parse
    project = urllib.parse.quote("owner/repo", safe="")
    respx.get(f"https://gitlab.com/api/v4/projects/{project}").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get(f"https://gitlab.com/api/v4/projects/{project}/repository/tree?recursive=true&ref=main").mock(
        return_value=httpx.Response(200, json=[{"type": "blob", "path": "analysis.py", "size": 50}]))
    respx.get("https://gitlab.com/owner/repo/-/raw/main/analysis.py").mock(
        return_value=httpx.Response(200, content=b"print('hi')\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="gitlab", url="https://gitlab.com/owner/repo", id="owner/repo", kind="code",
                        label="GitLab repository owner/repo")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-analysis.py"]


@respx.mock
def test_fetch_osf(tmp_path):
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/").mock(return_value=httpx.Response(200, json={
        "data": [
            {"attributes": {"kind": "folder", "name": "data"}, "links": {"self": "https://api.osf.io/v2/nodes/ab3cd/files/data/"}},
        ]}))
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/data/").mock(return_value=httpx.Response(200, json={
        "data": [
            {"attributes": {"kind": "file", "name": "raw.csv", "size": 9}, "links": {"download": "https://osf.io/download/ab3cd/"}},
        ]}))
    respx.get("https://osf.io/download/ab3cd/").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="osf", url="https://osf.io/ab3cd", id="ab3cd", kind="data", label="OSF project ab3cd")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-raw.csv"]


@respx.mock
def test_fetch_osf_osfstorage(tmp_path):
    """The real OSF response: the node's files endpoint returns an ``osfstorage`` folder whose
    contents are in ``relationships.files.links.related.href``, not in ``links.self``."""
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/").mock(return_value=httpx.Response(200, json={
        "data": [
            {"attributes": {"kind": "folder", "name": "osfstorage", "path": "/"},
             "relationships": {"files": {"links": {"related": {"href": "https://api.osf.io/v2/nodes/ab3cd/files/osfstorage/"}}}},
             "links": {}},
        ]}))
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/osfstorage/").mock(return_value=httpx.Response(200, json={
        "data": [
            {"attributes": {"kind": "file", "name": "raw.csv", "size": 9},
             "links": {"download": "https://osf.io/download/ab3cd/"}},
        ]}))
    respx.get("https://osf.io/download/ab3cd/").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="osf", url="https://osf.io/ab3cd", id="ab3cd", kind="data", label="OSF project ab3cd")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-raw.csv"]
    assert (pkg.path(res[0].path)).read_bytes() == b"a,b\n1,2\n"


@respx.mock
def test_fetch_figshare(tmp_path):
    respx.get("https://api.figshare.com/v2/articles/999").mock(return_value=httpx.Response(200, json={
        "title": "Article",
        "files": [{"name": "data.csv", "size": 11, "download_url": "https://ndownloader.figshare.com/files/1"}],
    }))
    respx.get("https://ndownloader.figshare.com/files/1").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="figshare", url="https://figshare.com/articles/999", id="999", kind="data",
                        label="Figshare article 999")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-data.csv"]


@respx.mock
def test_acquire_orchestration(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "Data at https://doi.org/10.5281/zenodo.123456")
    respx.get("https://zenodo.org/api/records/123456").mock(return_value=httpx.Response(200, json={
        "metadata": {"title": "Replication data"},
        "files": [{"key": "data.csv", "size": 11, "links": {"self": "https://zenodo.org/api/files/123/data.csv"}}],
    }))
    respx.get("https://zenodo.org/api/files/123/data.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert out["links"][0]["repo"] == "zenodo" and out["failures"] == []
    assert out["resources"][0].reprocrowd.role == "data"


def test_budget():
    b = acquire._Budget(100)
    assert b.allow(100) and not b.allow(101)
    b.spend(60)
    assert b.allow(40) and not b.allow(41)
    assert b.remaining() == 40
    b.spend(40)
    assert b.remaining() == 0 and not b.allow(1)


@respx.mock
def test_osf_missing_links_is_skipped(tmp_path):
    """A folder without a self link, or a file without a download link, must not crash (None URL)."""
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/").mock(return_value=httpx.Response(200, json={
        "data": [
            {"attributes": {"kind": "folder", "name": "broken"}, "links": {}},
            {"attributes": {"kind": "file", "name": "no-dl.csv", "size": 5}, "links": {}},
        ]}))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="osf", url="https://osf.io/ab3cd", id="ab3cd", kind="data", label="OSF project ab3cd")
    res = acquire.fetch(pkg, link)
    assert res == []


@respx.mock
def test_acquire_catches_unexpected_errors(tmp_path, monkeypatch):
    """An adapter bug must not 500 the request; it is reported as a failure instead."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "Data at https://doi.org/10.5281/zenodo.123456")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setitem(acquire.ADAPTERS, "zenodo", (acquire.zenodo_detect, boom))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert out["resources"] == []
    assert out["failures"] == [{"repo": "zenodo", "id": "123456", "url": "https://zenodo.org/records/123456",
                                "error": "unexpected error: boom"}]


@respx.mock
def test_acquire_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "Data at https://doi.org/10.5281/zenodo.999")
    respx.get("https://zenodo.org/api/records/999").mock(return_value=httpx.Response(404))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert out["links"][0]["repo"] == "zenodo"
    assert out["resources"] == []
    assert out["failures"] == [{"repo": "zenodo", "id": "999", "url": "https://zenodo.org/records/999",
                                "error": "no such record at https://zenodo.org/api/records/999"}]
