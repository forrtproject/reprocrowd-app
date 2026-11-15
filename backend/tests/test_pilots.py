"""GitHub pilot submissions against a stateful fake GitHub (offline; respx)."""
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from test_api import mock_openalex, problem
from test_submit import SCRIPT
from test_zenodo import ready_published

API = "http://api.github.test"
UP = "http://uploads.github.test"
REPO = "forrtproject/reprocrowd-pilots"
TOKEN = "ghp_secret_test_token"
PAGE = 2  # small pages, so that pagination is exercised


class FakeGitHub:
    """Just enough of the REST API: repository, issues, comments, releases and assets, with pagination and failures."""

    def __init__(self, push=True):
        self.push = push
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.releases: list[dict] = []
        self.writes: list[str] = []
        self.fail: dict[str, int] = {}  # step name → fail on the nth call (1-based), once
        self.calls: dict[str, int] = {}
        self.next_id = 1000

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _maybe_fail(self, step):
        self.calls[step] = self.calls.get(step, 0) + 1
        if self.fail.get(step) == self.calls[step]:
            del self.fail[step]
            return httpx.Response(502, json={"message": "Server Error"})
        return None

    def _page(self, request, items):
        q = parse_qs(urlparse(str(request.url)).query)
        page = int(q.get("page", ["1"])[0])
        chunk = items[(page - 1) * PAGE: page * PAGE]
        headers = {}
        if page * PAGE < len(items):
            base = str(request.url).split("?")[0]
            params = {k: v[0] for k, v in q.items() if k != "page"}
            params["page"] = str(page + 1)
            headers["Link"] = f'<{base}?{"&".join(f"{k}={v}" for k, v in params.items())}>; rel="next"'
        return httpx.Response(200, json=chunk, headers=headers)

    def _release_view(self, r):
        return {**r, "assets": [dict(a) for a in r["assets"]], "html_url": f"https://github.test/{REPO}/releases/tag/{r['tag_name']}",
                "upload_url": f"{UP}/repos/{REPO}/releases/{r['id']}/assets{{?name,label}}"}

    def handle(self, request: httpx.Request):
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        path, method = urlparse(str(request.url)).path, request.method
        body = json.loads(request.content) if request.content and method in ("POST", "PATCH") and not path.endswith("/assets") else None
        if method != "GET":
            self.writes.append(f"{method} {path}")
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO, "html_url": f"https://github.test/{REPO}", "private": False,
                                             "default_branch": "main", "permissions": {"push": self.push, "pull": True}})
        if path == f"/repos/{REPO}/immutable-releases":
            return httpx.Response(200, json={"enabled": True})
        if path == f"/repos/{REPO}/issues" and method == "GET":
            q = parse_qs(urlparse(str(request.url)).query)
            assert q.get("state") == ["all"]
            items = [i for i in self.issues if q.get("labels", [""])[0] in [l["name"] for l in i["labels"]]]
            return self._page(request, items)
        if path == f"/repos/{REPO}/issues" and method == "POST":
            if (f := self._maybe_fail("issue")):
                return f
            n = len(self.issues) + 1
            issue = {"number": n, "id": self._id(), "title": body["title"], "body": body["body"], "html_url": f"https://github.test/{REPO}/issues/{n}",
                     "labels": [{"name": l} for l in body.get("labels", [])], "state": "open"}
            self.issues.append(issue)
            self.comments[n] = []
            return httpx.Response(201, json=issue)
        if m := re.fullmatch(rf"/repos/{REPO}/issues/(\d+)", path):
            issue = next((i for i in self.issues if i["number"] == int(m.group(1))), None)
            if issue is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "PATCH":
                if (f := self._maybe_fail("issue_body")):
                    return f
                issue.update(body)
            return httpx.Response(200, json=issue)
        if m := re.fullmatch(rf"/repos/{REPO}/issues/(\d+)/comments", path):
            n = int(m.group(1))
            if method == "GET":
                return self._page(request, self.comments[n])
            if (f := self._maybe_fail("comment")):
                return f
            c = {"id": self._id(), "body": body["body"]}
            c["html_url"] = f"https://github.test/{REPO}/issues/{n}#issuecomment-{c['id']}"
            self.comments[n].append(c)
            return httpx.Response(201, json=c)
        if path == f"/repos/{REPO}/releases" and method == "GET":
            return self._page(request, [self._release_view(r) for r in reversed(self.releases)])
        if path == f"/repos/{REPO}/releases" and method == "POST":
            assert body["draft"] is True and body["prerelease"] is True
            if any(r["tag_name"] == body["tag_name"] for r in self.releases):
                return httpx.Response(422, json={"message": "Validation Failed", "errors": [{"code": "already_exists"}]})
            r = {"id": self._id(), "tag_name": body["tag_name"], "name": body["name"], "body": body["body"], "draft": True,
                 "prerelease": True, "published_at": None, "assets": []}
            self.releases.append(r)
            return httpx.Response(201, json=self._release_view(r))
        if m := re.fullmatch(rf"/repos/{REPO}/releases/(\d+)", path):
            r = next((r for r in self.releases if r["id"] == int(m.group(1))), None)
            if r is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "PATCH":
                if (f := self._maybe_fail("publish" if body.get("draft") is False else "release_body")):
                    return f
                if not r["draft"] and set(body) - {"body", "name"}:
                    return httpx.Response(422, json={"message": "immutable release"})
                r.update(body)
                if body.get("draft") is False:
                    r["published_at"] = "2026-11-15T12:00:00Z"
                    for a in r["assets"]:
                        a["browser_download_url"] = f"https://github.test/{REPO}/releases/download/{r['tag_name']}/{a['name']}"
            return httpx.Response(200, json=self._release_view(r))
        if m := re.fullmatch(rf"/repos/{REPO}/releases/assets/(\d+)", path):
            for r in self.releases:
                if any(a["id"] == int(m.group(1)) for a in r["assets"]):
                    assert r["draft"], "never delete assets of a published release"
                    r["assets"] = [a for a in r["assets"] if a["id"] != int(m.group(1))]
            return httpx.Response(204)
        if m := re.fullmatch(rf"/repos/{REPO}/releases/(\d+)/assets", path):
            r = next(r for r in self.releases if r["id"] == int(m.group(1)))
            name = parse_qs(urlparse(str(request.url)).query)["name"][0]
            if any(a["name"] == name for a in r["assets"]):
                return httpx.Response(422, json={"message": "Validation Failed", "errors": [{"code": "already_exists", "field": "name"}]})
            data = request.content
            a = {"id": self._id(), "name": name, "size": len(data), "state": "uploaded", "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                 "content_type": request.headers["content-type"], "browser_download_url": f"https://github.test/{REPO}/releases/download/untagged/{name}"}
            self.blobs = getattr(self, "blobs", {})
            if (f := self._maybe_fail("upload")):
                r["assets"].append({**a, "state": "starter", "size": len(data) // 2, "digest": None})  # a broken partial upload
                return f
            r["assets"].append(a)
            self.blobs[(r["tag_name"], name)] = data
            return httpx.Response(201, json=a)
        raise AssertionError(f"unexpected GitHub call {method} {path}")

    def counts(self):
        return {"issues": len(self.issues), "comments": sum(len(c) for c in self.comments.values()), "releases": len(self.releases),
                "assets": sum(len(r["assets"]) for r in self.releases)}


@pytest.fixture
def gh(client):
    from reprocrowd.config import settings

    cfg = Path(os.environ["REPROCROWD_CONFIG"])
    cfg.write_text(cfg.read_text() + f'\n[github]\napi_url = "{API}"\npilots_repo = "{REPO}"\n')
    settings.cache_clear()
    fake = FakeGitHub()
    with respx.mock:
        respx.route(host="api.github.test").mock(side_effect=fake.handle)
        respx.route(host="uploads.github.test").mock(side_effect=fake.handle)
        mock_openalex()
        yield fake


def body(**kw):
    return {"github_token": TOKEN, **kw}


def rerun(client, pkg, root, value):
    from test_jobs import wait
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT.replace("2.3149", value))
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"


def test_preview_sends_nothing(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    problem(client.post(P + "/pilots:preview", json={}), 409, "run_not_succeeded")
    ready_published(client, pkg)
    pv = client.post(P + "/pilots:preview", json={"synthetic": True}).json()
    assert pv["repo"] == REPO and pv["exclude"] == ["paper"] and set(pv["excluded"]) == {"ft-paper-tei", "ex-paper-tei"}
    assert pv["issue_title"].startswith("[SYNTHETIC] Pilot reproduction: ") and pv["labels"] == ["reprocrowd-pilot", "unreviewed", "synthetic"]
    names = [a["name"] for a in pv["assets"]]
    assert names[0].endswith("-reproduction.zip") and names[1:] == ["report.html", "comparison.json", "execution.json", "licenses.json", "pilot.json"]
    assert pv["licenses"]["metadata"] == "CC0-1.0" and pv["licenses"]["outputs"] == "CC-BY-4.0"
    assert gh.writes == [] and gh.calls == {}
    root = Path(client.get(P).json()["path"])
    assert not list((root / "archive").glob("pilot-*"))  # preview keeps nothing


def test_create_update_and_retries_do_not_duplicate(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    root = Path(client.get(P).json()["path"])
    ready_published(client, pkg)
    problem(client.post(P + "/pilots", json={}), 400, "github_token_missing")

    r = client.post(P + "/pilots", json=body(note="first pilot", synthetic=True))
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["status"] == "published" and v1["version"] == 1 and v1["issue_number"] == 1 and v1["error"] is None
    assert v1["tag"] == f"{v1['reproduction_id']}-{v1['run_key']}" and re.fullmatch(r"repro-[0-9a-f]{10}", v1["reproduction_id"])
    assert gh.counts() == {"issues": 1, "comments": 1, "releases": 1, "assets": 6}
    rel = gh.releases[0]
    assert not rel["draft"] and rel["prerelease"] and "v1" in rel["name"]
    # every recorded hash is the hash of the bytes GitHub holds, and the links point at the published tag
    for a in v1["assets"]:
        assert gh.blobs[(rel["tag_name"], a["name"])] and hashlib.sha256(gh.blobs[(rel["tag_name"], a["name"])]).hexdigest() == a["sha256"]
        assert a["url"].endswith(f"/download/{rel['tag_name']}/{a['name']}")
    pilot = json.loads(gh.blobs[(rel["tag_name"], "pilot.json")])
    assert pilot["status"] == "pilot-unreviewed" and pilot["synthetic"] and pilot["version"] == 1 and pilot["comparison"]["summary"] == {"match": 3, "missing": 1}
    zipped = zipfile.ZipFile(io.BytesIO(gh.blobs[(rel["tag_name"], v1["assets"][0]["name"])]))
    assert "excluded-files.json" in zipped.namelist() and "licenses.json" in zipped.namelist()
    assert zipped.read("report.html") == gh.blobs[(rel["tag_name"], "report.html")]  # copied out of the ZIP: same bytes
    issue = gh.issues[0]
    assert issue["title"].startswith("[SYNTHETIC]") and {l["name"] for l in issue["labels"]} == {"reprocrowd-pilot", "unreviewed", "synthetic"}
    assert f"<!-- reprocrowd-pilot:{v1['reproduction_id']} -->" in issue["body"] and "Pilot record — unreviewed" in issue["body"]
    assert "### Latest run: v1" in issue["body"] and "3 of 3 numeric targets match" in issue["body"]
    assert "first pilot" in gh.comments[1][0]["body"] and v1["assets"][0]["sha256"] in gh.comments[1][0]["body"]

    # the same archive again: nothing new on GitHub, the same local record
    before = gh.counts()
    again = client.post(P + "/pilots", json=body(note="first pilot", synthetic=True)).json()
    assert gh.counts() == before and again["id"] == v1["id"] and again["version"] == 1
    assert [x["id"] for x in client.get(P + "/pilots").json()] == [v1["id"]]

    # a new run makes the report stale: refused before any GitHub write
    rerun(client, pkg, root, "2.5")
    writes = len(gh.writes)
    problem(client.post(P + "/pilots", json=body(synthetic=True)), 409, "report_stale")
    problem(client.post(P + "/pilots:preview", json={}), 409, "report_stale")
    assert len(gh.writes) == writes
    assert client.put(P + "/report", json={}).status_code in (200, 201) and client.put(P + "/archive").status_code == 200

    v2 = client.post(P + "/pilots", json=body(note="after a rerun", synthetic=True)).json()
    assert v2["status"] == "published" and v2["version"] == 2 and v2["issue_number"] == 1 and v2["run_key"] != v1["run_key"]
    assert gh.counts() == {"issues": 1, "comments": 2, "releases": 2, "assets": 12}
    assert gh.releases[0]["tag_name"] == v1["tag"] and not gh.releases[0]["draft"]  # v1 kept, unchanged
    assert "### Latest run: v2" in gh.issues[0]["body"] and "| v1 |" in gh.issues[0]["body"] and "| v2 |" in gh.issues[0]["body"]
    assert "2 of 3 numeric targets match" in gh.issues[0]["body"]
    assert [x["version"] for x in client.get(P + "/pilots").json()] == [1, 2]

    # the token is never stored or echoed
    for f in root.rglob("*"):
        if f.is_file():
            assert TOKEN.encode() not in f.read_bytes(), f


@pytest.mark.parametrize("step", ["issue", "upload", "release_body", "publish", "comment", "issue_body"])
def test_retry_after_a_failure_resumes(client, pkg, gh, step):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.fail[step] = 3 if step == "upload" else 1
    r = client.post(P + "/pilots", json=body())
    assert r.status_code == 502 and r.json()["code"] == "github_error"
    rec = client.get(P + "/pilots").json()
    if step != "issue":
        assert len(rec) == 1 and rec[0]["status"] == "partial" and "github_error" in rec[0]["error"]
    done = client.post(P + "/pilots", json=body())
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "published" and done.json()["error"] is None
    assert gh.counts() == {"issues": 1, "comments": 1, "releases": 1, "assets": 6}
    assert all(a["state"] == "uploaded" for a in gh.releases[0]["assets"])
    assert len(client.get(P + "/pilots").json()) == 1


def test_issue_is_found_on_github_when_the_local_record_is_lost(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.issues += [{"number": n, "title": "other", "body": "unrelated", "labels": [{"name": "reprocrowd-pilot"}], "html_url": "x"} for n in (1, 2, 3)]
    for n in (1, 2, 3):
        gh.comments[n] = []
    gh.fail["comment"] = 1
    client.post(P + "/pilots", json=body())
    from reprocrowd.package import Registry
    with Registry().get(pkg).edit() as m:
        m.reprocrowd.pilots = []  # e.g. the package folder was copied before the record was written
    done = client.post(P + "/pilots", json=body()).json()
    assert done["issue_number"] == 4 and gh.counts()["issues"] == 4 and gh.counts()["releases"] == 1


def test_published_release_with_other_files_is_refused_not_changed(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    client.post(P + "/pilots", json=body())
    gh.releases[0]["assets"][1]["digest"] = "sha256:" + "0" * 64
    deletes = [w for w in gh.writes if w.startswith("DELETE")]
    err = problem(client.post(P + "/pilots", json=body()), 409, "pilot_release_mismatch")
    assert "report.html" in err["files"] and [w for w in gh.writes if w.startswith("DELETE")] == deletes


def test_no_write_access_is_refused_before_writing(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.push = False
    assert client.post("/v1/github-pilots:check", json={"github_token": TOKEN}).json()["can_submit"] is False
    err = problem(client.post(P + "/pilots", json=body()), 403, "github_forbidden")
    assert "write access" in err["detail"] and gh.writes == []


def test_licenses_json_covers_every_archived_file(client, pkg, gh):
    ready_published(client, pkg)
    from reprocrowd.package import Registry
    p = Registry().get(pkg)
    with zipfile.ZipFile(p.path(p.load().reprocrowd.archive.path)) as z:
        lic = json.loads(z.read("licenses.json"))
        names = set(z.namelist())
    by_path = {f["path"]: f for f in lic["files"]}
    assert names == set(by_path)
    assert by_path["datapackage.json"]["license"] == "CC0-1.0" and by_path["targets.json"]["license"] == "CC0-1.0"
    assert by_path["analysis.py"]["license"] == "CC-BY-4.0" and by_path["report.html"]["license"] == "CC-BY-4.0"
    assert "quote" in by_path["report.html"]["note"]
    third = [f for f in lic["files"] if f["rights"] == "third-party"]
    assert third and all(f["license"] is None for f in third) and {f["kind"] for f in third} >= {"fulltext", "extraction"}


def test_cli_submits_through_the_same_service(client, pkg, gh, monkeypatch):
    from typer.testing import CliRunner
    from reprocrowd import cli
    ready_published(client, pkg)
    real = httpx.request

    def route(method, url, **kw):  # rc → the test service; the service's own GitHub calls stay on the fake
        if url.startswith(cli._state["url"]):
            return client.request(method, url, json=kw.get("json"), params=kw.get("params"), headers=kw.get("headers"))
        return real(method, url, **kw)
    monkeypatch.setattr(cli.httpx, "request", route)
    monkeypatch.setenv("REPROCROWD_GITHUB_TOKEN", TOKEN)
    runner = CliRunner()
    pv = runner.invoke(cli.app, ["pilot", "preview", pkg, "--synthetic"])
    assert pv.exit_code == 0, pv.output
    assert gh.writes == []
    sub = runner.invoke(cli.app, ["pilot", "submit", pkg, "--synthetic", "--note", "from rc"])
    assert sub.exit_code == 0, sub.output
    rec = json.loads(sub.stdout)
    assert rec["status"] == "published" and TOKEN not in sub.output
    again = runner.invoke(cli.app, ["pilot", "submit", pkg, "--synthetic", "--note", "from rc"])
    assert json.loads(again.stdout)["id"] == rec["id"] and gh.counts() == {"issues": 1, "comments": 1, "releases": 1, "assets": 6}
