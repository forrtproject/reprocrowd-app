from pathlib import Path

import httpx
import pytest
import respx

from reprocrowd import acquire
from reprocrowd.errors import ApiError
from reprocrowd.package import Package, init_package


SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """No real DNS in tests: every name is public, except *.internal.test, which is on the local network."""
    real = acquire._addresses
    monkeypatch.setattr(acquire, "_addresses", lambda host, port: ["192.168.1.1"] if host.endswith(".internal.test")
                        else real(host, port) if host[:1].isdigit() or ":" in host else ["93.184.216.34"])


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
                                                "repo": "zenodo", "id": "123456", "label": "fetched from Zenodo record 123456",
                                                "path": "data.csv", "commit": None}
    assert (pkg.path(r.path)).read_bytes() == b"a,b\n1,2\n"


@respx.mock
def test_fetch_github(tmp_path):
    respx.get("https://api.github.com/repos/owner/repo").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get("https://api.github.com/repos/owner/repo/branches/main").mock(return_value=httpx.Response(200, json={"commit": {"sha": SHA}}))
    respx.get(f"https://api.github.com/repos/owner/repo/git/trees/{SHA}?recursive=1").mock(
        return_value=httpx.Response(200, json={"tree": [
            {"type": "blob", "path": "analysis.R", "size": 100},
            {"type": "tree", "path": "data"},
        ]}))
    respx.get(f"https://raw.githubusercontent.com/owner/repo/{SHA}/analysis.R").mock(
        return_value=httpx.Response(200, content=b"print('hi')\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="github", url="https://github.com/owner/repo", id="owner/repo", kind="code",
                        label="GitHub repository owner/repo")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-analysis.r"]
    r = pkg.load().get("in-analysis.r")
    assert r.reprocrowd.role == "code"
    assert r.reprocrowd.origin.commit == SHA and r.reprocrowd.origin.path == "analysis.R"  # pinned (#91)


@respx.mock
def test_fetch_gitlab(tmp_path):
    import urllib.parse
    project = urllib.parse.quote("owner/repo", safe="")
    respx.get(f"https://gitlab.com/api/v4/projects/{project}").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get(f"https://gitlab.com/api/v4/projects/{project}/repository/branches/main").mock(return_value=httpx.Response(200, json={"commit": {"id": SHA}}))
    respx.get(f"https://gitlab.com/api/v4/projects/{project}/repository/tree").mock(
        return_value=httpx.Response(200, json=[{"type": "blob", "path": "analysis.py", "size": 50}]))
    respx.get(f"https://gitlab.com/owner/repo/-/raw/{SHA}/analysis.py").mock(
        return_value=httpx.Response(200, content=b"print('hi')\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="gitlab", url="https://gitlab.com/owner/repo", id="owner/repo", kind="code",
                        label="GitLab repository owner/repo")
    res = acquire.fetch(pkg, link)
    assert [r.name for r in res] == ["in-analysis.py"]
    origin = pkg.load().get("in-analysis.py").reprocrowd.origin
    assert origin.commit == SHA and origin.path == "analysis.py"  # pinned (#91)


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


@respx.mock
def test_acquire_again_skips_files_already_fetched(tmp_path, monkeypatch):
    """Repeated acquisition is idempotent and does not download the same repository file again."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_zenodo_files("data.csv"))
    download = respx.get("https://zenodo.org/api/files/1/data.csv").mock(
        return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)

    first = acquire.acquire(pkg)
    second = acquire.acquire(pkg)

    assert [r.name for r in first["resources"]] == ["in-data.csv"]
    assert first["existing"] == []
    assert second["resources"] == []
    assert [r.name for r in second["existing"]] == ["in-data.csv"]
    assert [r.name for r in pkg.load().by_kind("input")] == ["in-data.csv"]
    assert download.call_count == 1


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
                                "code": "unexpected", "error": "unexpected error: boom"}]


@respx.mock
def test_acquire_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "Data at https://doi.org/10.5281/zenodo.999")
    respx.get("https://zenodo.org/api/records/999").mock(return_value=httpx.Response(404))
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert out["links"][0]["repo"] == "zenodo"
    assert out["resources"] == []
    assert out["failures"] == [{"repo": "zenodo", "id": "999", "url": "https://zenodo.org/records/999",
                                "code": "acquire_not_found", "error": "zenodo.org has no such record (it may have been removed or be private)"}]


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
                                "file_url": "https://zenodo.org/api/files/1/b.csv", "code": "acquire_failed",
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
    assert out["failures"] == [{"repo": "osf", "id": "ab3cd", "url": "https://osf.io/ab3cd", "code": "acquire_failed",
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


# ---------------------------------------------------------------- download limit

def _limit(monkeypatch, nbytes):
    monkeypatch.setattr(acquire, "settings", lambda: type("S", (), {"max_download_mb": nbytes / 2**20})())


def _sized(*files):
    return httpx.Response(200, json={"files": [
        {"key": n, **({"size": size} if size else {}), "links": {"self": f"https://zenodo.org/api/files/1/{n}"}}
        for n, size in files]})


def _skips(out):
    return [(f["file"], f["error"]) for f in out["failures"] if f["code"] == "acquire_too_large"]


@respx.mock
def test_limit_known_size_is_skipped_and_reported(tmp_path, monkeypatch):
    """A file whose listed size doesn't fit is reported (not silently dropped) and not requested; smaller ones still come."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    _limit(monkeypatch, 2 * 2**20)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_sized(("big.csv", 3 * 2**30), ("small.csv", 5)))
    big = respx.get("https://zenodo.org/api/files/1/big.csv")
    respx.get("https://zenodo.org/api/files/1/small.csv").mock(return_value=httpx.Response(200, content=b"small"))
    out = acquire.acquire(_make_pkg(tmp_path))
    assert [r.name for r in out["resources"]] == ["in-small.csv"]
    assert _skips(out) == [("big.csv", "skipped: 3 GB, more than the 2 MB download limit")]
    assert not big.called


@respx.mock
def test_limit_unknown_size_overrun_then_used_up(tmp_path, monkeypatch):
    """A file without a listed size that runs over is skipped with a message; once the limit is used up,
    the remaining files are skipped without being requested."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    _limit(monkeypatch, 100)
    respx.get("https://zenodo.org/api/records/1").mock(
        return_value=_sized(("a.csv", 0), ("huge.csv", 0), ("b.csv", 0), ("c.csv", 0)))
    respx.get("https://zenodo.org/api/files/1/a.csv").mock(return_value=httpx.Response(200, content=b"x" * 60))
    respx.get("https://zenodo.org/api/files/1/huge.csv").mock(return_value=httpx.Response(200, content=b"x" * 500))
    respx.get("https://zenodo.org/api/files/1/b.csv").mock(return_value=httpx.Response(200, content=b"x" * 40))
    c = respx.get("https://zenodo.org/api/files/1/c.csv")
    pkg = _make_pkg(tmp_path)
    out = acquire.acquire(pkg)
    assert [r.name for r in out["resources"]] == ["in-a.csv", "in-b.csv"]
    assert _skips(out) == [("huge.csv", "skipped: larger than the 1 KB left of the 1 KB download limit"),
                           ("c.csv", "skipped: the 1 KB download limit is used up")]
    assert not c.called
    assert not pkg.path("inputs/huge.csv").exists()


@respx.mock
def test_limit_charges_a_zip_once(tmp_path, monkeypatch):
    """Unpacked members don't count again: only the downloaded archive is charged."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.csv", "a" * 4000)
    archive = buf.getvalue()
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    _limit(monkeypatch, len(archive) + 10)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_sized(("rep.zip", 0), ("tiny.csv", 0)))
    respx.get("https://zenodo.org/api/files/1/rep.zip").mock(return_value=httpx.Response(200, content=archive))
    respx.get("https://zenodo.org/api/files/1/tiny.csv").mock(return_value=httpx.Response(200, content=b"x" * 5))
    out = acquire.acquire(_make_pkg(tmp_path))
    assert "in-tiny.csv" in [r.name for r in out["resources"]]
    assert out["failures"] == []


def test_size_text():
    assert acquire._size(0) == "0 KB"
    assert acquire._size(300) == "1 KB"
    assert acquire._size(180 * 2**20) == "180 MB"
    assert acquire._size(int(1.25 * 2**30)) == "1.2 GB"


# ---------------------------------------------------------------- GitLab pagination

@respx.mock
def test_gitlab_follows_pages(tmp_path):
    """The tree is paged; every page is read (via the Link header) and paths are URL-quoted."""
    project = "owner%2Frepo"
    tree = f"https://gitlab.com/api/v4/projects/{project}/repository/tree"
    respx.get(f"https://gitlab.com/api/v4/projects/{project}").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get(f"https://gitlab.com/api/v4/projects/{project}/repository/branches/main").mock(return_value=httpx.Response(200, json={"commit": {"id": SHA}}))
    respx.get(tree, params={"page_token": "p2"}).mock(return_value=httpx.Response(200, json=[
        {"type": "blob", "path": "data/my file.csv"}]))
    first = respx.get(tree).mock(return_value=httpx.Response(200, json=[
        {"type": "blob", "path": "analysis.py"}, {"type": "tree", "path": "data"}],
        headers={"Link": f'<{tree}?page_token=p2&per_page=100>; rel="next"'}))
    respx.get(f"https://gitlab.com/owner/repo/-/raw/{SHA}/analysis.py").mock(return_value=httpx.Response(200, content=b"1"))
    respx.get(f"https://gitlab.com/owner/repo/-/raw/{SHA}/data/my%20file.csv").mock(return_value=httpx.Response(200, content=b"2"))
    link = acquire.Link(repo="gitlab", url="https://gitlab.com/owner/repo", id="owner/repo", kind="code", label="")
    res = acquire.fetch(_make_pkg(tmp_path), link)
    assert sorted(r.name for r in res) == ["in-analysis.py", "in-my-file.csv"]
    assert first.calls[0].request.url.params["per_page"] == "100"


# ---------------------------------------------------------------- which full text is scanned

FIXTURES = Path(__file__).parent / "fixtures"


def test_manuscript_text_uses_the_jats_full_text(tmp_path):
    from reprocrowd import fulltexts
    pkg = _make_pkg(tmp_path)
    for name in ("paper.docling.json", "paper.jats.xml"):  # the JSON one first: it must not be picked
        with open(FIXTURES / name, "rb") as f:
            fulltexts.add_upload(pkg, name, f)
    text = acquire.manuscript_text(pkg)
    assert text.strip()
    # A non-XML full text (Docling JSON) is scanned too, not just the JATS XML.
    docling = pkg.load().by_kind("fulltext")[0].name
    assert acquire.manuscript_text(pkg, docling).strip()


def test_manuscript_text_without_jats(tmp_path):
    from reprocrowd import fulltexts
    pkg = _make_pkg(tmp_path)
    with open(FIXTURES / "paper.docling.json", "rb") as f:
        fulltexts.add_upload(pkg, "paper.docling.json", f)
    assert acquire.manuscript_text(pkg).strip()


def test_manuscript_text_from_local_files(tmp_path):
    """A Markdown or PDF full text added from disk is scanned for links, like the JATS XML."""
    from reprocrowd import fulltexts
    pkg = _make_pkg(tmp_path)
    md = tmp_path / "paper.md"
    md.write_text("# Methods\n\nSee the data at https://zenodo.org/records/123 and the code at https://github.com/forrt/a.\n", encoding="utf-8")
    fulltexts.add_local(pkg, str(md))
    text = acquire.manuscript_text(pkg)
    assert "zenodo.org/records/123" in text and "github.com/forrt/a" in text
    assert any(l.repo == "zenodo" for l in acquire.detect(text))


def _fulltext(pkg: Package, path: Path) -> str:
    from reprocrowd import fulltexts
    fulltexts.add_local(pkg, str(path))
    return acquire.manuscript_text(pkg)


def test_manuscript_text_jats_link_only_in_xlink_href(tmp_path):
    xml = (FIXTURES / "paper.jats.xml").read_text(encoding="utf-8").replace(
        "</body>", '<p>The data are <ext-link xmlns:xlink="http://www.w3.org/1999/xlink" ext-link-type="uri" '
        'xlink:href="https://zenodo.org/records/777">online</ext-link>.</p></body>', 1)
    p = tmp_path / "paper.jats.xml"
    p.write_text(xml, encoding="utf-8")
    text = _fulltext(_make_pkg(tmp_path), p)
    assert [(l.repo, l.id) for l in acquire.detect(text)] == [("zenodo", "777")]


def test_manuscript_text_tei_link_only_in_target(tmp_path):
    xml = (FIXTURES / "paper.tei.xml").read_text(encoding="utf-8")
    assert "</body>" in xml
    xml = xml.replace("</body>", '<p>Code: <ref type="url" target="https://github.com/forrt/b">here</ref>.</p></body>', 1)
    p = tmp_path / "paper.tei.xml"
    p.write_text(xml, encoding="utf-8")
    text = _fulltext(_make_pkg(tmp_path), p)
    assert [(l.repo, l.id) for l in acquire.detect(text)] == [("github", "forrt/b")]


def test_manuscript_text_docx_hyperlinks_and_tables(tmp_path):
    import docx
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = docx.Document()
    d.add_paragraph("A paper.")
    para = d.add_paragraph("The data are ")
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), d.part.relate_to("https://zenodo.org/records/888", RT.HYPERLINK, is_external=True))
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "on Zenodo"
    run.append(t)
    link.append(run)
    para._p.append(link)
    # An orphaned hyperlink relationship not referenced in the body must not be picked up.
    d.part.relate_to("https://zenodo.org/records/999", RT.HYPERLINK, is_external=True)
    d.add_table(rows=1, cols=2).cell(0, 1).text = "Code: https://osf.io/ab3cd"
    p = tmp_path / "paper.docx"
    d.save(str(p))
    text = _fulltext(_make_pkg(tmp_path), p)
    assert "on Zenodo" in text
    assert {(l.repo, l.id) for l in acquire.detect(text)} == {("zenodo", "888"), ("osf", "ab3cd")}


def test_osf_detect_ignores_non_project_routes():
    text = (
        "Preprint at https://osf.io/preprints/psyarxiv/abcde and "
        "https://osf.io/meetings/sips2026 and https://osf.io/registries/discover "
        "and https://osf.io/search?q=test; data at https://osf.io/xy9z2."
    )
    assert [(l.repo, l.id) for l in acquire.osf_detect(text)] == [("osf", "xy9z2")]


# ---------------------------------------------------------------- preview & selection

@respx.mock
def test_preview_lists_files_with_sizes(tmp_path, monkeypatch):
    """Preview returns the files with sizes and the total, without downloading anything."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=httpx.Response(200, json={
        "files": [{"key": "data.csv", "size": 11, "links": {"self": "https://zenodo.org/api/files/1/data.csv"}},
                  {"key": "notes.md", "size": 5, "links": {"self": "https://zenodo.org/api/files/1/notes.md"}}]}))
    out = acquire.preview(_make_pkg(tmp_path))
    assert out["total_bytes"] == 16
    keys = {f["key"] for f in out["files"]}
    assert "zenodo:1:data.csv" in keys
    assert "zenodo:1:notes.md" in keys
    assert out["failures"] == []
    urls = " ".join(str(c.request.url) for c in respx.calls)
    assert "api/files/" not in urls


@respx.mock
def test_fetch_selected(tmp_path):
    """With ``selected`` only the chosen files are downloaded."""
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_zenodo_files("data.csv", "notes.md"))
    respx.get("https://zenodo.org/api/files/1/data.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="zenodo", url="https://zenodo.org/records/1", id="1", kind="data",
                        label="Zenodo record 1")
    res = acquire.fetch(pkg, link, selected=["zenodo:1:data.csv"])
    assert [r.name for r in res] == ["in-data.csv"]
    urls = " ".join(str(c.request.url) for c in respx.calls)
    assert "notes.md" not in urls


@respx.mock
def test_acquire_selected(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=_zenodo_files("data.csv", "notes.md"))
    respx.get("https://zenodo.org/api/files/1/data.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    out = acquire.acquire(_make_pkg(tmp_path), selected=["zenodo:1:data.csv"])
    assert [r.name for r in out["resources"]] == ["in-data.csv"]


@respx.mock
def test_preview_osf_same_name_in_two_folders(tmp_path, monkeypatch):
    """OSF keys use the path inside the project, so same-named files in different folders stay apart."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: "https://osf.io/ab3cd/")
    respx.get("https://api.osf.io/v2/nodes/ab3cd/files/").mock(return_value=httpx.Response(200, json={"data": [
        {"attributes": {"kind": "file", "name": "data.csv", "size": 3, "materialized_path": "/study1/data.csv"},
         "links": {"download": "https://osf.io/download/one/"}},
        {"attributes": {"kind": "file", "name": "data.csv", "size": 4, "materialized_path": "/study2/data.csv"},
         "links": {"download": "https://osf.io/download/two/"}}]}))
    out = acquire.preview(_make_pkg(tmp_path))
    assert [f["key"] for f in out["files"]] == ["osf:ab3cd:study1/data.csv", "osf:ab3cd:study2/data.csv"]
    assert out["limit_bytes"] == acquire.settings().max_download_mb * 2**20


@respx.mock
def test_preview_marks_files_already_fetched(tmp_path, monkeypatch):
    """Files fetched before are marked ``present`` and left out of the total, by the same check acquire skips them with:
    same repository record and download URL. A same-named file added by hand doesn't count, and neither does a file
    whose input was deleted."""
    monkeypatch.setattr(acquire, "manuscript_text", lambda pkg, fulltext=None: ZENODO_TEXT)
    respx.get("https://zenodo.org/api/records/1").mock(return_value=httpx.Response(200, json={
        "files": [{"key": "data.csv", "size": 11, "links": {"self": "https://zenodo.org/api/files/1/data.csv"}},
                  {"key": "notes.md", "size": 5, "links": {"self": "https://zenodo.org/api/files/1/notes.md"}}]}))
    respx.get("https://zenodo.org/api/files/1/data.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
    pkg = _make_pkg(tmp_path)
    upload = tmp_path / "notes.md"
    upload.write_text("my own notes\n")
    from reprocrowd import inputs
    inputs.add(pkg, str(upload), None, "", True)

    before = acquire.preview(pkg)
    assert [f["present"] for f in before["files"]] == [False, False]
    assert before["total_bytes"] == 16 and before["present_bytes"] == 0

    acquire.acquire(pkg, selected=["zenodo:1:data.csv"])
    after = acquire.preview(pkg)
    assert {f["key"]: f["present"] for f in after["files"]} == {"zenodo:1:data.csv": True, "zenodo:1:notes.md": False}
    assert after["total_bytes"] == 5 and after["present_bytes"] == 11

    inputs.delete(pkg, "in-data.csv")
    assert not any(f["present"] for f in acquire.preview(pkg)["files"])


@respx.mock
@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/x", "http://[::1]/x", "http://[::ffff:10.0.0.1]/x", "http://169.254.169.254/latest",
                                 "http://router.internal.test/admin"])
def test_download_refuses_local_addresses(tmp_path, url):
    """Issue #91: a repository's download URL can't point the app at the user's own network."""
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"secret"))
    with pytest.raises(ApiError, match="local or private address"):
        acquire._download(url, tmp_path / "f", 1000)
    assert not route.called and not (tmp_path / "f").exists()


@respx.mock
def test_redirects_to_local_addresses_are_refused(tmp_path):
    respx.get("https://figshare.com/ndownloader/files/1").mock(return_value=httpx.Response(302, headers={"Location": "http://192.168.0.1/cfg"}))
    local = respx.get("http://192.168.0.1/cfg").mock(return_value=httpx.Response(200, content=b"router config"))
    with pytest.raises(ApiError, match="local or private address"):
        acquire._download("https://figshare.com/ndownloader/files/1", tmp_path / "f", 1000)
    assert not local.called


@respx.mock
def test_github_refetch_at_a_new_commit_skips_known_files(tmp_path):
    """The download URL names the commit, so a file fetched before is recognised by its path, also when it was
    fetched by a version that recorded the branch URL and no path."""
    respx.get("https://api.github.com/repos/owner/repo").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    branch = respx.get("https://api.github.com/repos/owner/repo/branches/main")
    for sha in (SHA, "b" * 40):
        respx.get(f"https://api.github.com/repos/owner/repo/git/trees/{sha}?recursive=1").mock(
            return_value=httpx.Response(200, json={"tree": [{"type": "blob", "path": "analysis.R", "size": 2}]}))
        respx.get(f"https://raw.githubusercontent.com/owner/repo/{sha}/analysis.R").mock(return_value=httpx.Response(200, content=b"1\n"))
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="github", url="https://github.com/owner/repo", id="owner/repo", kind="code", label="GitHub repository owner/repo")
    branch.mock(return_value=httpx.Response(200, json={"commit": {"sha": SHA}}))
    assert [r.name for r in acquire.fetch(pkg, link)] == ["in-analysis.r"]
    branch.mock(return_value=httpx.Response(200, json={"commit": {"sha": "b" * 40}}))
    assert acquire.fetch(pkg, link) == []
    with pkg.edit() as m:  # as an older version recorded it
        o = m.get("in-analysis.r").reprocrowd.origin
        o.url, o.path, o.commit = "https://raw.githubusercontent.com/owner/repo/main/analysis.R", None, None
    assert acquire.fetch(pkg, link) == []
    assert len(pkg.load().by_kind("input")) == 1


@respx.mock
def test_new_url_for_a_name_outside_git_is_a_new_file(tmp_path):
    """Matching by path is for commit-pinned git fetches only: a record whose file got a new URL is fetched again."""
    record = respx.get("https://zenodo.org/api/records/123456")
    pkg = _make_pkg(tmp_path)
    link = acquire.Link(repo="zenodo", url="https://zenodo.org/records/123456", id="123456", kind="data", label="Zenodo record 123456")
    for n in (1, 2):
        record.mock(return_value=httpx.Response(200, json={"metadata": {"title": "t"}, "files": [
            {"key": "data.csv", "size": 4, "links": {"self": f"https://zenodo.org/api/files/{n}/data.csv"}}]}))
        respx.get(f"https://zenodo.org/api/files/{n}/data.csv").mock(return_value=httpx.Response(200, content=f"v{n}\n".encode()))
        assert len(acquire.fetch(pkg, link)) == 1
    assert len(pkg.load().by_kind("input")) == 2
