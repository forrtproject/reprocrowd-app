"""GitHub pilot submissions against a stateful fake GitHub (offline; respx): the app's side (a fork release, the issue
and the run comment, by an account without write access) and the pilots repository's Action (pilot_copy.py)."""
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from test_api import mock_openalex, problem
from test_submit import SCRIPT
from test_zenodo import ready_published

API = "http://api.github.test"
UP = "http://uploads.github.test"
OWNER = "forrtproject"
REPO = f"{OWNER}/reprocrowd-pilots"
USER = "alice"  # the submitter: no write access to REPO
FORK = f"{USER}/reprocrowd-pilots"
TOKEN = "ghp_secret_test_token"
ACTION = "ghs_action_token"  # the Action's GITHUB_TOKEN
PAGE = 2  # small pages, so that pagination is exercised


class FakeGitHub:
    """Just enough of the REST API for both sides: repositories and forks, issues, comments, labels, releases and assets,
    with pagination, permissions (only a repository's owner, and the Action in REPO, may write releases) and failures."""

    def __init__(self):
        self.tokens = {TOKEN: USER, ACTION: "github-actions[bot]"}
        self.private = False
        self.repos = {REPO: {"owner": OWNER}}
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.releases: dict[str, list[dict]] = {REPO: []}
        self.blobs: dict[int, bytes] = {}
        self.labels = {"reprocrowd-pilot", "unreviewed", "synthetic"}
        self.writes: list[str] = []
        self.downloads: list[str] = []
        self.tag_reads = 0
        self.fail: dict[str, int] = {}  # step name → fail on the nth call (1-based), once
        self.calls: dict[str, int] = {}
        self.next_id = 1000
        self.digests = True  # GitHub reports a SHA-256 digest per asset
        self.search_limited = False

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
        headers = {}
        if page * PAGE < len(items):
            params = {k: v[0] for k, v in q.items() if k != "page"} | {"page": str(page + 1)}
            headers["Link"] = f'<{str(request.url).split("?")[0]}?{"&".join(f"{k}={v}" for k, v in params.items())}>; rel="next"'
        return httpx.Response(200, json=items[(page - 1) * PAGE: page * PAGE], headers=headers)

    def _repo_view(self, name):
        info = self.repos[name]
        view = {"full_name": name, "name": name.split("/")[1], "owner": {"login": info["owner"]}, "html_url": f"https://github.test/{name}",
                "private": self.private if name == REPO else False, "default_branch": "main", "allow_forking": True}
        return view | ({"fork": True, "parent": {"full_name": REPO}} if name != REPO else {})

    def _release_view(self, repo, r):
        return {**r, "assets": [dict(a) for a in r["assets"]], "html_url": f"https://github.test/{repo}/releases/tag/{r['tag_name']}",
                "upload_url": f"{UP}/repos/{repo}/releases/{r['id']}/assets{{?name,label}}"}

    def _can_write(self, who, repo):
        return who == self.repos[repo]["owner"] or (repo == REPO and who == "github-actions[bot]")

    def _user(self, login):
        return {"login": login}

    def _association(self, login):
        return "OWNER" if login == OWNER else "NONE"

    def handle(self, request: httpx.Request):
        auth = request.headers.get("authorization", "")
        who = self.tokens.get(auth.removeprefix("Bearer ")) if auth else None
        path, method = urlparse(str(request.url)).path, request.method
        if path == "/search/issues":  # public: no token needed
            return self._search(request)
        if who is None and not (method == "GET" and re.fullmatch(r"/repos/[^/]+/[^/]+/(releases|releases/tags/.+|issues/\d+/comments)", path)):
            return httpx.Response(401, json={"message": "Bad credentials"})
        upload = path.endswith("/assets") and method == "POST"
        body = json.loads(request.content) if request.content and method in ("POST", "PATCH") and not upload else None
        if method != "GET":
            self.writes.append(f"{who} {method} {path}")
        if path == "/user":
            return httpx.Response(200, json={"login": who})
        if m := re.fullmatch(r"/repos/([^/]+/[^/]+)(/.*)?", path):
            repo, rest = m.group(1), m.group(2) or ""
        else:
            raise AssertionError(f"unexpected GitHub call {method} {path}")
        if repo not in self.repos:
            return httpx.Response(404, json={"message": "Not Found"})
        if rest == "":
            return httpx.Response(200, json=self._repo_view(repo))
        if rest == "/forks" and method == "POST":
            if (f := self._maybe_fail("fork")):
                return f
            name = f"{who}/{repo.split('/')[1]}"
            self.repos.setdefault(name, {"owner": who})
            self.releases.setdefault(name, [])
            return httpx.Response(202, json=self._repo_view(name))
        if m := re.fullmatch(r"/branches/(.+)", rest):
            return httpx.Response(200 if m.group(1) == "main" else 404, json={"name": m.group(1)})
        if rest.startswith("/releases"):
            return self._releases(request, method, repo, rest, body, who, upload)
        assert repo == REPO, f"issues only in {REPO}"
        if m := re.fullmatch(r"/labels(?:/(.+))?", rest):
            if method == "GET":
                return httpx.Response(200 if m.group(1) in self.labels else 404, json={"name": m.group(1)})
            assert self._can_write(who, repo)
            self.labels.add(body["name"])
            return httpx.Response(201, json=body)
        if rest == "/issues" and method == "GET":
            q = parse_qs(urlparse(str(request.url)).query)
            assert q.get("state") == ["all"]
            items = [i for i in self.issues if ("labels" not in q or q["labels"][0] in [l["name"] for l in i["labels"]])
                     and ("creator" not in q or q["creator"][0] == i["user"]["login"])]
            return self._page(request, items)
        if rest == "/issues" and method == "POST":
            if (f := self._maybe_fail("issue")):
                return f
            n = len(self.issues) + 1
            labels = body.get("labels", []) if self._can_write(who, repo) else []  # GitHub drops them silently otherwise
            issue = {"number": n, "id": self._id(), "title": body["title"], "body": body["body"], "user": self._user(who),
                     "html_url": f"https://github.test/{REPO}/issues/{n}", "labels": [{"name": l} for l in labels], "state": "open"}
            self.issues.append(issue)
            self.comments[n] = []
            return httpx.Response(201, json=issue)
        if m := re.fullmatch(r"/issues/(\d+)", rest):
            issue = next((i for i in self.issues if i["number"] == int(m.group(1))), None)
            if issue is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "PATCH":
                assert who == issue["user"]["login"] or self._can_write(who, repo)
                if (f := self._maybe_fail("issue_body")):
                    return f
                issue.update(body)
            return httpx.Response(200, json=issue)
        if m := re.fullmatch(r"/issues/(\d+)/labels", rest):
            assert self._can_write(who, repo) and set(body["labels"]) <= self.labels
            issue = next(i for i in self.issues if i["number"] == int(m.group(1)))
            issue["labels"] = [{"name": l} for l in dict.fromkeys([l["name"] for l in issue["labels"]] + body["labels"])]
            return httpx.Response(200, json=issue["labels"])
        if m := re.fullmatch(r"/issues/(\d+)/comments", rest):
            n = int(m.group(1))
            if method == "GET":
                return self._page(request, self.comments[n])
            if (f := self._maybe_fail("comment")):
                return f
            c = {"id": self._id(), "body": body["body"], "user": self._user(who), "author_association": self._association(who)}
            c["html_url"] = f"https://github.test/{REPO}/issues/{n}#issuecomment-{c['id']}"
            self.comments[n].append(c)
            return httpx.Response(201, json=c)
        raise AssertionError(f"unexpected GitHub call {method} {path}")

    def _search(self, request):
        """Issue search: repo:, is:issue and a quoted phrase, matched case-insensitively in titles and descriptions."""
        self.calls["search"] = self.calls.get("search", 0) + 1
        if self.search_limited:
            return httpx.Response(403, json={"message": "API rate limit exceeded for 203.0.113.1."}, headers={"x-ratelimit-remaining": "0"})
        q = parse_qs(urlparse(str(request.url)).query)["q"][0]
        repo, phrase = re.search(r"repo:(\S+)", q).group(1), re.search(r'"([^"]+)"', q).group(1).lower()
        assert "is:issue" in q and "in:title,body" in q
        if repo not in self.repos:
            return httpx.Response(422, json={"message": "Validation Failed"})
        items = [i for i in self.issues if phrase in (i["title"] + " " + i["body"]).lower()] if repo == REPO else []
        return httpx.Response(200, json={"total_count": len(items), "items": items[::-1]})

    def _releases(self, request, method, repo, rest, body, who, upload):
        rels = self.releases[repo]
        writer = self._can_write(who, repo)
        if method != "GET" and not writer:
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        if rest == "/releases" and method == "GET":
            return self._page(request, [self._release_view(repo, r) for r in reversed(rels) if writer or not r["draft"]])
        if rest == "/releases" and method == "POST":
            assert body["draft"] is True and body["prerelease"] is True and body["target_commitish"] == "main"
            if any(r["tag_name"] == body["tag_name"] for r in rels):
                return httpx.Response(422, json={"message": "Validation Failed", "errors": [{"code": "already_exists"}]})
            r = {"id": self._id(), "tag_name": body["tag_name"], "name": body["name"], "body": body["body"], "draft": True,
                 "prerelease": True, "published_at": None, "assets": []}
            rels.append(r)
            return httpx.Response(201, json=self._release_view(repo, r))
        if m := re.fullmatch(r"/releases/tags/(.+)", rest):
            self.tag_reads += 1
            r = next((r for r in rels if r["tag_name"] == m.group(1) and not r["draft"]), None)
            return httpx.Response(200, json=self._release_view(repo, r)) if r else httpx.Response(404, json={"message": "Not Found"})
        if m := re.fullmatch(r"/releases/(\d+)", rest):
            r = next((r for r in rels if r["id"] == int(m.group(1))), None)
            if r is None or (r["draft"] and not writer):
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "DELETE":
                assert r["draft"], "never delete a published release"
                rels.remove(r)
                return httpx.Response(204)
            if method == "PATCH":
                if (f := self._maybe_fail("publish" if body.get("draft") is False else "release_body")):
                    return f
                if not r["draft"] and set(body) - {"body", "name"}:
                    return httpx.Response(422, json={"message": "immutable release"})
                if r["draft"] and "tag_name" not in body:  # as GitHub does: editing a draft without tag_name drops its tag
                    r["tag_name"] = f"untagged-{self._id():020x}"
                r.update({k: v for k, v in body.items() if k not in ("target_commitish", "make_latest")})
                if body.get("draft") is False:
                    r["published_at"] = "2026-11-15T12:00:00Z"
                    for a in r["assets"]:
                        a["browser_download_url"] = f"https://github.test/{repo}/releases/download/{r['tag_name']}/{a['name']}"
            return httpx.Response(200, json=self._release_view(repo, r))
        if m := re.fullmatch(r"/releases/assets/(\d+)", rest):
            aid = int(m.group(1))
            r = next(r for r in rels if any(a["id"] == aid for a in r["assets"]))
            if method == "GET":
                assert request.headers["accept"] == "application/octet-stream"
                self.downloads.append(f"{repo}:{aid}")
                return httpx.Response(200, content=self.blobs[aid])
            assert r["draft"], "never delete assets of a published release"
            r["assets"] = [a for a in r["assets"] if a["id"] != aid]
            return httpx.Response(204)
        if upload:
            r = next(r for r in rels if r["id"] == int(rest.split("/")[2]))
            name = parse_qs(urlparse(str(request.url)).query)["name"][0]
            if any(a["name"] == name for a in r["assets"]):
                return httpx.Response(422, json={"message": "Validation Failed", "errors": [{"code": "already_exists", "field": "name"}]})
            data = request.content
            a = {"id": self._id(), "name": name, "size": len(data), "state": "uploaded",
                 "digest": "sha256:" + hashlib.sha256(data).hexdigest() if self.digests else None,
                 "content_type": request.headers["content-type"], "browser_download_url": f"https://github.test/{repo}/releases/download/untagged/{name}"}
            if (f := self._maybe_fail("upload")):
                r["assets"].append({**a, "state": "starter", "size": len(data) // 2, "digest": None})  # a broken partial upload
                return f
            r["assets"].append(a)
            self.blobs[a["id"]] = data
            return httpx.Response(201, json=a)
        raise AssertionError(f"unexpected release call {method} {repo}{rest}")

    # ---------- helpers for assertions ----------

    def counts(self, repo=None):
        rels = [r for name, rs in self.releases.items() if repo in (None, name) for r in rs]
        return {"issues": len(self.issues), "comments": sum(len(c) for c in self.comments.values()), "releases": len(rels),
                "assets": sum(len(r["assets"]) for r in rels)}

    def blob(self, repo, tag, name):
        r = next(r for r in self.releases[repo] if r["tag_name"] == tag)
        return self.blobs[next(a["id"] for a in r["assets"] if a["name"] == name)]

    def run_action(self):
        """The pilots repository's Action, run once over every comment (as a manual dispatch would)."""
        from reprocrowd import pilot_copy
        api = pilot_copy.Api(ACTION, API)
        return [r for i in list(self.issues) for c in list(self.comments[i["number"]]) if (r := pilot_copy.handle(api, REPO, dict(i), dict(c)))]


@pytest.fixture
def gh(client, monkeypatch):
    from reprocrowd import pilots
    from reprocrowd.config import settings

    cfg = Path(os.environ["REPROCROWD_CONFIG"])
    cfg.write_text(cfg.read_text() + f'\n[github]\napi_url = "{API}"\npilots_repo = "{REPO}"\n')
    settings.cache_clear()
    monkeypatch.setattr(pilots, "time", SimpleNamespace(sleep=lambda s: None))  # forks are ready at once here
    monkeypatch.setattr(pilots, "RECHECK_S", 0)
    pilots._found.clear()
    pilots._releases.clear()
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
    assert pv["issue_title"].startswith("[SYNTHETIC] Pilot reproduction: ")
    names = [a["name"] for a in pv["assets"]]
    assert names[0].endswith("-reproduction.zip") and names[1:] == ["report.html", "comparison.json", "execution.json", "licenses.json", "pilot.json"]
    assert pv["licenses"]["metadata"] == "CC0-1.0" and pv["licenses"]["outputs"] == "CC-BY-4.0"
    assert gh.writes == [] and gh.calls == {}
    root = Path(client.get(P).json()["path"])
    assert len(list((root / "archive").glob(f"pilot-{pv['run_key']}/*.zip"))) == 1  # the ZIP the submission will upload, built once


def test_check_needs_no_write_access(client, gh):
    r = client.post("/v1/github-pilots:check", json={"github_token": TOKEN}).json()
    assert r["can_submit"] is True and r["login"] == USER and r["files_in"].startswith(FORK) and gh.writes == []
    gh.private = True
    assert client.post("/v1/github-pilots:check", json={"github_token": TOKEN}).json()["can_submit"] is False


def test_anyone_submits_through_a_fork_and_the_action_copies_each_run(client, pkg, gh, tmp_path):
    P = f"/v1/packages/{pkg}"
    root = Path(client.get(P).json()["path"])
    ready_published(client, pkg)
    problem(client.post(P + "/pilots", json={}), 400, "github_token_missing")
    gh.labels = set()  # a new pilots repository: the Action creates the labels

    r = client.post(P + "/pilots", json=body(note="first pilot", synthetic=True))
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["status"] == "submitted" and v1["version"] is None and v1["files_repo"] == FORK and v1["issue_number"] == 1
    assert v1["tag"] == f"{v1['reproduction_id']}-{v1['run_key']}" and re.fullmatch(r"repro-[0-9a-f]{10}", v1["reproduction_id"])
    # the app wrote only to the submitter's own fork and opened an issue and a comment, which anyone may
    assert not [w for w in gh.writes if f"/repos/{REPO}/releases" in w or "/labels" in w]
    assert gh.counts(FORK) == {"issues": 1, "comments": 1, "releases": 1, "assets": 6} and gh.counts(REPO)["releases"] == 0
    fork_rel = gh.releases[FORK][0]
    assert not fork_rel["draft"] and fork_rel["prerelease"]
    for a in v1["assets"]:
        assert hashlib.sha256(gh.blob(FORK, v1["tag"], a["name"])).hexdigest() == a["sha256"]
    pilot = json.loads(gh.blob(FORK, v1["tag"], "pilot.json"))
    assert pilot["status"] == "pilot-unreviewed" and pilot["synthetic"] and pilot["comparison"]["summary"] == {"match": 3, "missing": 1}
    zipped = zipfile.ZipFile(io.BytesIO(gh.blob(FORK, v1["tag"], v1["assets"][0]["name"])))
    assert "excluded-files.json" in zipped.namelist() and zipped.read("report.html") == gh.blob(FORK, v1["tag"], "report.html")
    issue = gh.issues[0]
    assert issue["title"].startswith("[SYNTHETIC]") and issue["labels"] == [] and issue["user"]["login"] == USER
    assert f"<!-- reprocrowd-pilot:{v1['reproduction_id']} -->" in issue["body"] and "Pilot record — unreviewed" in issue["body"]
    comment = gh.comments[1][0]["body"]
    assert "first pilot" in comment and v1["assets"][0]["sha256"] in comment and f"<!-- reprocrowd-pilot-files:{FORK} -->" in comment

    # the Action copies the run into an immutable pre-release of the pilots repository, labels the issue and replies
    replies = gh.run_action()
    assert len(replies) == 1 and "Run **v1** is copied" in replies[0]
    rel = gh.releases[REPO][0]
    assert not rel["draft"] and "v1" in rel["name"] and rel["tag_name"] == v1["tag"]
    assert {a["name"]: a["digest"] for a in rel["assets"]} == {a["name"]: a["digest"] for a in fork_rel["assets"]}
    assert {l["name"] for l in gh.issues[0]["labels"]} == {"reprocrowd-pilot", "unreviewed", "synthetic"}
    assert "### Latest run: v1" in gh.issues[0]["body"] and "3 of 3 match (numbers at printed precision; text exactly)" in gh.issues[0]["body"]
    listed = client.get(P + "/pilots").json()
    assert listed[0]["status"] == "published" and listed[0]["version"] == 1 and listed[0]["release_url"].endswith(v1["tag"])
    assert listed[0]["assets"][0]["url"].startswith(f"https://github.test/{REPO}/releases/download/")

    # the same archive again: nothing new on GitHub, the same local record; the Action answers nothing twice
    before = gh.counts()
    again = client.post(P + "/pilots", json=body(note="first pilot", synthetic=True)).json()
    assert gh.counts() == before and again["id"] == v1["id"] and again["version"] == 1
    assert gh.run_action() == [] and gh.counts() == before

    # a new run makes the report stale: refused before any GitHub write
    rerun(client, pkg, root, "2.5")
    writes = len(gh.writes)
    problem(client.post(P + "/pilots", json=body(synthetic=True)), 409, "report_stale")
    assert len(gh.writes) == writes
    assert client.put(P + "/report", json={}).status_code in (200, 201) and client.put(P + "/archive").status_code == 200

    v2 = client.post(P + "/pilots", json=body(note="after a rerun", synthetic=True)).json()
    assert v2["status"] == "submitted" and v2["issue_number"] == 1 and v2["run_key"] != v1["run_key"]
    assert "Run **v2** is copied" in gh.run_action()[0]
    assert len(gh.releases[REPO]) == 2 and gh.releases[REPO][0]["tag_name"] == v1["tag"] and not gh.releases[REPO][0]["draft"]
    assert "### Latest run: v2" in gh.issues[0]["body"] and "| v1 |" in gh.issues[0]["body"] and "| v2 |" in gh.issues[0]["body"]
    assert gh.issues[0]["body"].count("<!-- reprocrowd-runs -->") == 1
    assert [(x["status"], x["version"]) for x in client.get(P + "/pilots").json()] == [("published", 1), ("published", 2)]

    # the token is never stored or echoed, in the package or anywhere else the app writes
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert TOKEN.encode() not in f.read_bytes(), f


def test_the_owner_uploads_a_draft_that_the_action_publishes(client, pkg, gh):
    """GitHub doesn't let an account fork its own repository: its owner's files go to a draft release there."""
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.tokens[TOKEN] = OWNER
    rec = client.post(P + "/pilots", json=body()).json()
    assert rec["status"] == "submitted" and rec["files_repo"] == REPO and len(gh.repos) == 1
    assert gh.releases[REPO][0]["draft"]
    assert "Run **v1** is copied" in gh.run_action()[0]
    assert not gh.releases[REPO][0]["draft"] and len(gh.releases[REPO]) == 1
    assert client.get(P + "/pilots").json()[0]["status"] == "published"


@pytest.mark.parametrize("step", ["fork", "issue", "upload", "release_body", "publish", "comment"])
def test_retry_after_a_failure_resumes(client, pkg, gh, step):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.fail[step] = 3 if step == "upload" else 1
    r = client.post(P + "/pilots", json=body())
    assert r.status_code == 502 and r.json()["code"] == "github_error"
    rec = client.get(P + "/pilots").json()
    assert len(rec) == 1 and rec[0]["status"] == "partial" and "github_error" in rec[0]["error"]
    done = client.post(P + "/pilots", json=body())
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "submitted" and done.json()["error"] is None
    assert gh.counts(FORK) == {"issues": 1, "comments": 1, "releases": 1, "assets": 6}
    assert all(a["state"] == "uploaded" for a in gh.releases[FORK][0]["assets"])
    assert len(client.get(P + "/pilots").json()) == 1


def test_an_interrupted_copy_resumes(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    client.post(P + "/pilots", json=body())
    gh.fail["upload"] = gh.calls["upload"] + 2  # the Action's second upload
    with pytest.raises(RuntimeError):
        gh.run_action()
    assert gh.releases[REPO][0]["draft"]  # a draft left behind, never published
    assert "Run **v1** is copied" in gh.run_action()[0]
    assert len(gh.releases[REPO]) == 1 and not gh.releases[REPO][0]["draft"] and len(gh.releases[REPO][0]["assets"]) == 6


def test_issue_is_found_on_github_when_the_local_record_is_lost(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    for n in (1, 2, 3):
        gh.issues.append({"number": n, "title": "other", "body": "unrelated", "labels": [], "user": {"login": USER}, "html_url": "x"})
        gh.comments[n] = []
    gh.fail["comment"] = 1
    client.post(P + "/pilots", json=body())
    from reprocrowd.package import Registry
    with Registry().get(pkg).edit() as m:
        m.reprocrowd.pilots = []  # e.g. the package folder was copied before the record was written
    done = client.post(P + "/pilots", json=body()).json()
    assert done["issue_number"] == 4 and gh.counts()["issues"] == 4 and gh.counts(FORK)["releases"] == 1


def test_published_release_with_other_files_is_refused_not_changed(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    client.post(P + "/pilots", json=body())
    gh.releases[FORK][0]["assets"][1]["digest"] = "sha256:" + "0" * 64
    deletes = [w for w in gh.writes if " DELETE " in w]
    err = problem(client.post(P + "/pilots", json=body()), 409, "pilot_release_mismatch")
    assert "report.html" in err["files"] and [w for w in gh.writes if " DELETE " in w] == deletes


def _tamper(gh, tag, name, data):
    a = next(a for a in next(r for r in gh.releases[FORK] if r["tag_name"] == tag)["assets"] if a["name"] == name)
    gh.blobs[a["id"]] = data
    a["digest"], a["size"] = "sha256:" + hashlib.sha256(data).hexdigest(), len(data)


@pytest.mark.parametrize("case", ["other_commenter", "files_of_another_account", "changed_file", "too_large", "claimed_by_earlier_issue",
                                  "unexpected_pilot_json", "malformed_pilot_json", "pilot_json_not_an_object"])
def test_the_action_refuses_and_copies_nothing(client, pkg, gh, monkeypatch, case):
    from reprocrowd import pilot_copy
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    rec = client.post(P + "/pilots", json=body()).json()
    comment = gh.comments[1][0]
    if case == "other_commenter":  # someone else posts a copy of the run comment on this issue
        gh.comments[1].append({**comment, "id": 1, "user": {"login": "mallory"}, "author_association": "NONE"})
        gh.comments[1].remove(comment)
    elif case == "files_of_another_account":
        comment["body"] = comment["body"].replace(f"reprocrowd-pilot-files:{FORK}", "reprocrowd-pilot-files:mallory/reprocrowd-pilots")
    elif case == "changed_file":  # changed in the fork after the comment (the fork's releases may not be immutable)
        _tamper(gh, rec["tag"], "report.html", b"<p>changed</p>")
    elif case == "too_large":
        monkeypatch.setattr(pilot_copy, "MAX_RUN", 100)
    elif case == "claimed_by_earlier_issue":  # a run of this reproduction ID was already copied for another issue
        gh.releases[REPO].append({"id": 1, "tag_name": f"{rec['reproduction_id']}-{'0' * 12}", "name": f"{rec['reproduction_id']} v1 (pilot, unreviewed)",
                                  "body": "<!-- reprocrowd-pilot-issue:9 -->", "draft": False, "prerelease": True, "published_at": "2026-11-15T12:00:00Z", "assets": []})
    elif case == "unexpected_pilot_json":  # pilot.json is the submitter's: its text must not reach the release notes unchecked
        pilot = json.loads(gh.blob(FORK, rec["tag"], "pilot.json"))
        pilot["paper"]["doi"] = "10.1/x <a href=https://evil.example>"
        _tamper(gh, rec["tag"], "pilot.json", json.dumps(pilot).encode())
    elif case == "malformed_pilot_json":  # refused with a reply, not a failed workflow
        _tamper(gh, rec["tag"], "pilot.json", b"{not json")
    elif case == "pilot_json_not_an_object":
        _tamper(gh, rec["tag"], "pilot.json", b"[]")
    replies = gh.run_action()
    assert len(replies) == 1 and "was **not copied**" in replies[0], replies
    assert not [r for r in gh.releases[REPO] if r["tag_name"] == rec["tag"]] and gh.issues[-1]["labels"] == []
    if case == "too_large":
        assert gh.downloads == []  # refused before downloading anything
    assert gh.run_action() == []  # the same refusal is not repeated
    listed = client.get(P + "/pilots").json()[0]
    assert listed["status"] == "submitted" and listed["error"].startswith("the pilots repository refused this run: ")
    assert listed["error"].split(": ", 1)[1] in replies[0]


def test_a_forged_refusal_is_ignored(client, pkg, gh):
    from reprocrowd.pilot_copy import refused_mark
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    rec = client.post(P + "/pilots", json=body()).json()
    gh.comments[1].append({"id": 5, "body": f"{refused_mark(rec['tag'])}\nRun was **not copied**: forged", "user": {"login": "mallory"},
                           "author_association": "NONE", "html_url": "x"})
    assert client.get(P + "/pilots").json()[0]["error"] is None


def test_a_forged_copy_reply_doesn_t_stop_the_copy(client, pkg, gh):
    """Anyone can paste the Action's reply marker into a comment; only the Action's own replies count."""
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    rec = client.post(P + "/pilots", json=body()).json()
    gh.comments[1].append({"id": 5, "body": f"<!-- reprocrowd-pilot-copied:{rec['tag']} -->\nRun **v1** is copied", "user": {"login": "mallory"},
                           "author_association": "NONE", "html_url": "x"})
    assert "Run **v1** is copied" in gh.run_action()[0]
    assert client.get(P + "/pilots").json()[0]["status"] == "published"
    assert gh.run_action() == []  # the Action's own reply does count


def test_resuming_from_another_github_account_is_refused_before_writing(client, pkg, gh):
    """The run's comment names the first account's fork and is never rewritten, so another login can't resume it."""
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    assert client.post(P + "/pilots", json=body()).json()["files_repo"] == FORK
    gh.tokens[TOKEN] = "bob"
    writes = list(gh.writes)
    err = problem(client.post(P + "/pilots", json=body()), 409, "pilot_other_login")
    assert err["files_repo"] == FORK and gh.writes == writes and "bob/reprocrowd-pilots" not in gh.repos
    gh.tokens[TOKEN] = USER
    assert client.post(P + "/pilots", json=body()).status_code == 200


def test_the_size_limit_counts_pilot_json(client, pkg, gh, monkeypatch):
    from reprocrowd import pilots
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    rec = client.post(P + "/pilots", json=body()).json()
    files = sum(a["bytes"] or 0 for a in client.post(P + "/pilots:preview", json={}).json()["assets"])
    total = files + len(gh.blob(FORK, rec["tag"], "pilot.json"))  # the Action's limit counts every asset
    monkeypatch.setattr(pilots, "MAX_RUN", total)  # exactly at the limit: accepted (no fixed reserve)
    assert client.post(P + "/pilots:preview", json={}).status_code == 200
    monkeypatch.setattr(pilots, "MAX_RUN", total - 1)
    problem(client.post(P + "/pilots:preview", json={}), 422, "pilot_asset_too_large")


def _zip(**members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name.replace("__", "."), data if isinstance(data, (bytes, str)) else json.dumps(data))
    return buf.getvalue()


def test_the_action_checks_the_archived_comparison_and_licences():
    from reprocrowd.pilot_copy import MAX_JSON, Refused, _from_zip
    cmp = {"policy": "reprocrowd-compare/3", "settings": {"threshold": 5, "rounding": "reported"}, "summary": {"match": 1}, "rows": [{"type": "number"}]}
    files = [{"path": "comparison.json", "kind": "report", "rights": "ours", "license": "CC-BY-4.0"},
             {"path": "licenses.json", "kind": "export", "rights": "ours", "license": "CC0-1.0"},
             {"path": "analysis.py", "kind": "script", "rights": "ours", "license": "MIT"}]
    lic = {"policy": "reprocrowd-licensing/1", "files": files}
    good = {"comparison__json": cmp, "licenses__json": lic, "analysis__py": "print(1)", "excluded-files__json": []}
    assert _from_zip(_zip(**good)) == ({"match": 1, "within": 0, "numeric": 1, "threshold": 5, "rounding": "reported", "policy": "reprocrowd-compare/3"}, "MIT")
    output_cc0 = {**lic, "files": [{**files[0], "license": "CC0-1.0"}, *files[1:]]}
    ours_as_third_party = {**lic, "files": [{**files[0], "rights": "third-party", "license": None}, *files[1:]]}
    relabelled = {**lic, "files": [{**files[0], "kind": "figure", "rights": "third-party", "license": None}, *files[1:]]}
    odd_kind = {**lic, "files": [*files, {"path": "x.txt", "kind": ["figure"], "rights": "ours", "license": "CC0-1.0"}]}
    for bad in ({"comparison__json": [], "licenses__json": lic},  # not an object
                {**good, "licenses__json": output_cc0},  # contradicts the policy
                {**good, "licenses__json": ours_as_third_party},  # our output claimed as third-party
                {**good, "licenses__json": relabelled},  # a file the app writes, given another kind
                {**good, "licenses__json": odd_kind, "x__txt": "x"},  # a kind that isn't a string
                {**good, "notes__txt": "unlisted"},  # a file licenses.json doesn't list
                {**good, "comparison__json": " " * (MAX_JSON + 1)}):  # too large to read
        with pytest.raises(Refused):
            _from_zip(_zip(**bad))


def test_release_notes_use_the_repository_s_own_statement(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    rec = client.post(P + "/pilots", json=body()).json()
    pilot = json.loads(gh.blob(FORK, rec["tag"], "pilot.json"))
    pilot["statement"] = "Certified correct by FORRT."
    pilot["licenses"] = {"metadata": "CC0-1.0", "outputs": "CC0-1.0", "code": ["CC0-1.0"]}  # ignored: licences come from the ZIP
    pilot["comparison"]["summary"] = {"match": 1000}  # ignored: counts come from the ZIP's comparison.json
    _tamper(gh, rec["tag"], "pilot.json", json.dumps(pilot).encode())
    assert "Run **v1** is copied" in gh.run_action()[0]
    notes = gh.releases[REPO][0]["body"]
    assert "Certified" not in notes and "Pilot record — unreviewed" in notes and "<!-- reprocrowd-pilot-issue:1 -->" in notes
    assert "new analysis code MIT" in notes and "outputs CC-BY-4.0" in notes and "3 of 3 match" in notes and "1000" not in notes


def test_listing_asks_github_at_most_once_a_minute(client, pkg, gh, monkeypatch):
    from reprocrowd import pilots
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    client.post(P + "/pilots", json=body())
    monkeypatch.setattr(pilots, "RECHECK_S", 60)
    reads = gh.tag_reads
    for _ in range(3):
        assert client.get(P + "/pilots").json()[0]["status"] == "submitted"
    assert gh.tag_reads - reads <= 1


def test_a_copy_that_failed_after_publishing_is_finished_on_the_next_run(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    client.post(P + "/pilots", json=body(synthetic=True))
    gh.fail["issue_body"] = 1  # the runs table can't be written
    with pytest.raises(RuntimeError):
        gh.run_action()
    assert not gh.releases[REPO][0]["draft"] and "### Latest run" not in gh.issues[0]["body"]
    assert "Run **v1** is copied" in gh.run_action()[0]
    assert "### Latest run: v1" in gh.issues[0]["body"] and {l["name"] for l in gh.issues[0]["labels"]} >= {"reprocrowd-pilot", "synthetic"}
    assert len(gh.releases[REPO]) == 1


def test_private_repository_is_refused_before_writing(client, pkg, gh):
    ready_published(client, pkg)
    gh.private = True
    err = problem(client.post(f"/v1/packages/{pkg}/pilots", json=body()), 403, "github_forbidden")
    assert "public" in err["detail"] and gh.writes == []


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
    assert rec["status"] == "submitted" and TOKEN not in sub.output
    again = runner.invoke(cli.app, ["pilot", "submit", pkg, "--synthetic", "--note", "from rc"])
    assert json.loads(again.stdout)["id"] == rec["id"] and gh.counts(FORK) == {"issues": 1, "comments": 1, "releases": 1, "assets": 6}


def test_evidence_disclosures_reach_the_record(client, pkg, gh):
    """A file added after the run (and the report regenerated) is published, but disclosed, not hidden."""
    P = f"/v1/packages/{pkg}"
    root = Path(client.get(P).json()["path"])
    ready_published(client, pkg)
    (root / "notes.py").write_text("# added after the run\n")
    client.post(P + ":sync")
    assert client.put(P + "/report", json={}).status_code in (200, 201) and client.put(P + "/archive").status_code == 200
    rec = client.post(P + "/pilots", json=body()).json()
    assert rec["status"] == "submitted", rec
    assert "notes.py" in json.loads(gh.blob(FORK, rec["tag"], "pilot.json"))["evidence"]["added_since_run"]
    assert "files added since the run: `notes.py`" in gh.comments[1][0]["body"]


def test_records_state_the_archived_licences_not_the_current_setting(client, pkg, gh):
    from reprocrowd.config import settings
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)  # archived with the default code licence (MIT)
    cfg = Path(os.environ["REPROCROWD_CONFIG"])
    cfg.write_text(cfg.read_text() + '\n[licensing]\ncode = "CC-BY-4.0"\n')
    settings.cache_clear()
    assert client.post(P + "/pilots:preview", json={}).json()["licenses"]["code"] == "MIT"
    rec = client.post(P + "/pilots", json=body()).json()
    archived = {f["path"]: f for f in json.loads(gh.blob(FORK, rec["tag"], "licenses.json"))["files"]}
    assert json.loads(gh.blob(FORK, rec["tag"], "pilot.json"))["licenses"]["code"] == archived["analysis.py"]["license"] == "MIT"
    assert "New analysis code: MIT" in gh.comments[1][0]["body"]
    assert client.put(P + "/archive").status_code == 200  # a new archive under the new setting records CC-BY-4.0
    rec2 = client.post(P + "/pilots", json=body()).json()
    assert json.loads(gh.blob(FORK, rec2["tag"], "pilot.json"))["licenses"]["code"] == "CC-BY-4.0"


def test_assets_without_a_digest_are_not_accepted(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    gh.digests = False
    problem(client.post(P + "/pilots", json=body()), 409, "pilot_release_mismatch")
    assert all(r["draft"] for r in gh.releases[FORK])  # never published unverified


def test_retry_keeps_the_note_of_the_published_comment(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    first = client.post(P + "/pilots", json=body(note="original note")).json()
    again = client.post(P + "/pilots", json=body(note="a different note")).json()
    assert first["id"] == again["id"] and again["note"] == "original note" and "original note" in gh.comments[1][0]["body"]


def test_repeated_left_out_groups_give_the_same_run(client, pkg, gh):
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    once = client.post(P + "/pilots:preview", json={"exclude": ["paper"]}).json()
    twice = client.post(P + "/pilots:preview", json={"exclude": ["paper", "paper"]}).json()
    assert once["exclude"] == ["paper"] and twice["run_key"] == once["run_key"] and twice["exclude"] == ["paper"]


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
    assert by_path["analysis.py"]["license"] == "MIT" and by_path["report.html"]["license"] == "CC-BY-4.0"
    assert "quote" in by_path["report.html"]["note"]
    third = [f for f in lic["files"] if f["rights"] == "third-party"]
    assert third and all(f["license"] is None for f in third) and {f["kind"] for f in third} >= {"fulltext", "extraction"}


def test_archive_without_licences_is_refused(client, pkg, gh):
    import zipfile as zf
    from reprocrowd.package import Registry
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    p = Registry().get(pkg)
    path = p.path(p.load().reprocrowd.archive.path)
    with zf.ZipFile(path) as src:
        items = [(i, src.read(i)) for i in src.infolist() if i.filename != "licenses.json"]
    with zf.ZipFile(path, "w") as out:
        for i, data in items:
            out.writestr(i, data)
    problem(client.post(P + "/pilots:preview", json={}), 409, "archive_stale")
    assert gh.writes == []


def test_archive_from_before_licences_is_refused_for_every_submission(client, pkg, gh):
    """An archive (and package) made before licenses.json existed: the shared archive check refuses it."""
    import zipfile as zf
    from reprocrowd.package import Registry
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    p = Registry().get(pkg)
    with p.edit() as m:
        m.resources = [r for r in m.resources if r.path != "licenses.json"]
    path = p.path(p.load().reprocrowd.archive.path)
    with zf.ZipFile(path) as src:
        items = [(i, src.read(i)) for i in src.infolist() if i.filename != "licenses.json"]
    with zf.ZipFile(path, "w") as out:
        for i, data in items:
            if i.filename == "datapackage.json":
                dp = json.loads(data)
                dp["resources"] = [r for r in dp["resources"] if r["path"] != "licenses.json"]
                data = json.dumps(dp).encode()
            out.writestr(i, data)
    for url in ("/pilots:preview", "/submissions"):
        err = problem(client.post(P + url, json={}), 409, "archive_stale")
        assert "licenses.json" in err["detail"], url
    assert gh.writes == []


def test_license_entries_must_match_their_rights():
    from pydantic import ValidationError
    from reprocrowd.schemas.exports import LicenseEntry
    with pytest.raises(ValidationError):
        LicenseEntry(path="inputs/data.csv", kind="input", rights="third-party", license="CC-BY-4.0")
    with pytest.raises(ValidationError):
        LicenseEntry(path="analysis.py", kind="script", rights="ours", license=None)


def test_exported_licence_schema_states_the_same_rule():
    import jsonschema
    from reprocrowd.schemas import json_schema
    schema = json_schema("licenses")
    good = {"policy": "reprocrowd-licensing/1", "rules": {}, "files": [
        {"path": "inputs/data.csv", "kind": "input", "rights": "third-party", "license": None},
        {"path": "analysis.py", "kind": "script", "rights": "ours", "license": "MIT"}]}
    jsonschema.validate(good, schema)
    for bad in ({"path": "inputs/data.csv", "kind": "input", "rights": "third-party", "license": "CC-BY-4.0"},
                {"path": "analysis.py", "kind": "script", "rights": "ours", "license": None},
                {"path": "analysis.py", "kind": "script", "rights": "ours"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(good | {"files": [bad]}, schema)



def test_records_of_a_paper_are_found_by_its_doi(client, pkg, gh, monkeypatch):
    from reprocrowd import pilots
    P, doi = f"/v1/packages/{pkg}", "10.1371/journal.pbio.1002106"
    find = lambda d=doi: client.get("/v1/github-pilots/records", params={"doi": d})
    assert find().json() == []
    monkeypatch.setattr(pilots, "FIND_CACHE_S", 0)
    ready_published(client, pkg)
    v1 = client.post(P + "/pilots", json=body(synthetic=True)).json()

    def add(n, title, text, who="bob"):
        gh.issues.append({"number": n, "id": n, "title": title, "body": text, "user": {"login": who},
                          "html_url": f"https://github.test/{REPO}/issues/{n}", "labels": [], "state": "open"})
        gh.comments[n] = []
    # not listed: an issue that mentions the paper without a pilot's marker, and a pilot of a longer DOI sharing the prefix
    add(9, f"Question about doi:{doi}", f"see https://doi.org/{doi}")
    add(10, f"Pilot reproduction: X (doi:{doi}5)", f"<!-- reprocrowd-pilot:repro-0000000000 -->\nPaper: https://doi.org/{doi}5 ·")
    [r] = find().json()
    assert r["number"] == v1["issue_number"] and r["reproduction_id"] == v1["reproduction_id"] and r["author"] == USER
    assert (r["runs"], r["latest_version"], r["latest_summary"], r["synthetic"]) == (0, None, None, True)
    for form in (doi.upper(), f"https://doi.org/{doi}", f"http://dx.doi.org/{doi}", f"doi:{doi}"):
        assert [x["number"] for x in find(form).json()] == [r["number"]], form
    problem(find("W2164005910"), 400, "invalid_identifier")
    problem(find('10.1000/a"b'), 400, "invalid_identifier")  # can't be put in the search's quoted phrase

    gh.run_action()
    [r] = find().json()
    assert (r["runs"], r["latest_version"]) == (1, 1) and r["latest_summary"].startswith("3 of 3 match") and r["latest_date"]
    # runs come from the releases, not the issue's description: a copy of the marker with a forged table of runs gets none,
    # and editing the real issue's table changes nothing
    forged = f"<!-- reprocrowd-pilot:{v1['reproduction_id']} -->\nPaper: https://doi.org/{doi} ·\n<!-- reprocrowd-runs -->\n### Latest run: v3 (2026-01-01)\n<!-- /reprocrowd-runs -->"
    add(11, f"Pilot reproduction: Y (doi:{doi})", forged)
    gh.issues[0]["body"] = gh.issues[0]["body"].replace("### Latest run: v1", "### Latest run: v9")
    got = {x["number"]: (x["runs"], x["latest_version"]) for x in find().json()}
    assert got == {11: (0, None), r["number"]: (1, 1)}
    # a title cut before its DOI is still found by the paper's link in the description
    gh.issues[0]["title"] = "Pilot reproduction: " + "a very long title " * 20
    assert r["number"] in [x["number"] for x in find().json()]
    assert find("10.9999/none").json() == [] and client.get("/v1/github-pilots/records", params={"doi": doi, "repo": "nobody/none"}).json() == []


def test_doi_search_is_cached_and_reports_the_rate_limit(client, pkg, gh):
    ready_published(client, pkg)
    client.post(f"/v1/packages/{pkg}/pilots", json=body())
    reads = lambda: sum(1 for c in respx.calls if c.request.url.path == f"/repos/{REPO}/releases")
    gh.issues.append({"number": 5, "id": 5, "title": "Pilot reproduction: A (doi:10.1000/a)", "body": "<!-- reprocrowd-pilot:repro-0000000000 -->",
                      "user": {"login": "bob"}, "html_url": f"https://github.test/{REPO}/issues/5", "labels": [], "state": "open"})
    for d in ("10.1371/journal.pbio.1002106", "10.1371/journal.pbio.1002106", "10.1000/a"):
        assert client.get("/v1/github-pilots/records", params={"doi": d}).json()
    assert gh.calls["search"] == 2 and reads() == 1  # the releases are read once a minute for all papers
    gh.search_limited = True
    problem(client.get("/v1/github-pilots/records", params={"doi": "10.1000/b"}), 429, "github_rate_limited")
