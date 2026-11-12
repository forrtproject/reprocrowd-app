from pathlib import Path

import httpx
import pytest
import respx

from reprocrowd import acquire
from reprocrowd.errors import ApiError
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
                                "error": "zenodo.org has no such record (it may have been removed or be private)"}]


ZENODO_TEXT = "Data at https://doi.org/10.5281/zenodo.1"


def _zenodo_files(*names):
    return httpx.Response(200, json={"files": [{"key": n, "links": {"self": f"https://zenodo.org/api/files/1/{n}"}} for n in names]})


@respx.mock
def test_one_failing_file_keeps_the_others(tmp_path, monkeypatch):
    """A file that fails is reported on its own; the files before and after it are still added and returned."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_zenodo_files("a.csv", "b.csv", "c.csv"))
    respx.get("https://zenodo.org/api/files/1/a.csv").mock(return_value=httpx.Response(200, content=b"a"))
    respx.get("https://zenodo.org/api/files/1/b.csv").mock(return_value=httpx.Response(503))
    respx.get("https://zenodo.org/api/files/1/c.csv").mock(return_value=httpx.Response(200, content=b"c"))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert [r.name for r in out["resources"]] == ["in-a.csv", "in-c.csv"]
    assert out["failures"] == [{"repo": "zenodo", "id": "1", "url": "https://zenodo.org/records/1", "file": "b.csv",
                                "file_url": "https://zenodo.org/api/files/1/b.csv",
                                "error": "zenodo.org had a server error (HTTP 503); try again later"}]
    assert sorted(r.name for r in pkg.load().by_kind("input")) == ["in-a.csv", "in-c.csv"]
    assert not pkg.path("inputs/b.csv").exists()


@respx.mock
def test_download_network_errors_are_readable(tmp_path, monkeypatch):
    """Transport errors while downloading become plain messages, and leave no partial file behind."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_zenodo_files("a.csv", "b.csv"))
    respx.get("https://zenodo.org/api/files/1/a.csv").mock(side_effect=httpx.ConnectError("refused"))

    def stalled():
        yield b"x" * 10
        raise httpx.ReadTimeout("stalled")
    respx.get("https://zenodo.org/api/files/1/b.csv").mock(return_value=httpx.Response(200, content=stalled()))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert out["resources"] == []
    assert [(f["file"], f["error"]) for f in out["failures"]] == [
        ("a.csv", "could not connect to zenodo.org; check the internet connection or try again later"),
        ("b.csv", "zenodo.org did not answer in time; try again later"),
    ]
    assert not (pkg.root / "inputs").exists() or not any((pkg.root / "inputs").iterdir())


@respx.mock
def test_listing_error_keeps_files_already_added(tmp_path, monkeypatch):
    """If a later page of a listing fails, the files from earlier pages are still returned."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "see https://osf.io/ab3cd")
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/", params={"page": "2"}).mock(side_effect=httpx.ConnectError("down"))
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/").mock(return_value=httpx.Response(200, json={
        "data": [{"attributes": {"kind": "file", "name": "raw.csv"}, "links": {"download": "https://osf.io/download/1/"}}],
        "links": {"next": "https://api.osf.io/v2/nodes/ab3cd/files/?page=2"}}))
    respx.get("https://osf.io/download/1/").mock(return_value=httpx.Response(200, content=b"a,b\n"))
    out = acquire.acquire(_make_pkg(tmp_path))
    assert [r.name for r in out["resources"]] == ["in-raw.csv"]
    assert out["failures"] == [{"repo": "osf", "id": "ab3cd", "url": "https://osf.io/ab3cd",
                                "error": "could not connect to api.osf.io; check the internet connection or try again later"}]


@respx.mock
def test_status_messages(tmp_path):
    respx.get("https://api.github.com/repos/o/r").mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate limit"}))
    respx.get("https://zenodo.org/api/records/2").mock(return_value=httpx.Response(403))
    respx.get("https://zenodo.org/api/records/3").mock(return_value=httpx.Response(200, json=[1, 2]))
    respx.get("https://zenodo.org/api/records/4").mock(return_value=httpx.Response(200, text="<html>"))
    pkg = _make_pkg(tmp_path)

    def err(repo, id):
        with pytest.raises(ApiError) as e:
            acquire.fetch(pkg, acquire.Link(repo=repo, url="", id=id, kind="data", label=""))
        return e.value.message
    assert err("github", "o/r") == "api.github.com's rate limit for anonymous requests is used up; try again in an hour"
    assert err("zenodo", "2") == "zenodo.org refused access to the record (it may be restricted or under embargo)"
    assert err("zenodo", "3") == "zenodo.org gave an unexpected answer (not the JSON its API documents)"
    assert err("zenodo", "4") == "zenodo.org gave an unexpected answer (not the JSON its API documents)"


@respx.mock
def test_osf_self_referencing_next_terminates(tmp_path):
    """A listing whose "next" points back at itself is read once, not followed forever."""
    url = "https://api.osf.io/v2/nodes/ab3cd/files/"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"data": [], "links": {"next": url}}))
    link = acquire.Link(repo="osf", url="https://osf.io/ab3cd", id="ab3cd", kind="data", label="OSF project ab3cd")
    assert acquire.fetch(_make_pkg(tmp_path), link) == []
    assert route.call_count == 1
