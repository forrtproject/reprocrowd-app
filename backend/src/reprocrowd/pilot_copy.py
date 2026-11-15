"""Copy a submitted pilot run into the pilots repository's own release: the script of that repository's GitHub Action.

Anyone with a GitHub account can submit. The ReproCrowd app publishes a run's files as a release in the submitter's
fork of the pilots repository (the repository's owner, who can't fork it, uploads a draft release here instead) and
posts a run comment on the reproduction's issue. This script runs on that comment with the repository's GITHUB_TOKEN:
it checks the run, copies the files into an immutable pre-release here, numbers the run (v1, v2, …), labels the issue,
updates the issue's table of runs and replies.

Before anything is copied:
- the comment is by the issue's author or a maintainer, and no earlier issue claims the reproduction ID;
- the files come from the commenter's own repository (or a maintainer's draft here), found by the run's tag, never
  from a URL in the comment;
- the reproduction ID and run key agree in the issue, the comment, the tag and pilot.json;
- the run is under MAX_RUN bytes, checked before downloading;
- every file is listed in pilot.json and matches its SHA-256 there and GitHub's digest; the ZIP is readable and holds
  datapackage.json and licenses.json.
A refused run gets one reply with the reason; nothing is copied.

Source: backend/src/reprocrowd/pilot_copy.py in forrtproject/reprocrowd-app, where it is tested. The pilots repository
runs a copy as .github/scripts/pilot_copy.py. Needs Python 3.10+ and httpx; imports nothing else from ReproCrowd.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from typing import Any, Iterator

import httpx

MAX_RUN = 200 * 1024**2  # bytes per run, all files together
RID = r"repro-[0-9a-f]{10}"
ISSUE_RE = re.compile(rf"<!-- reprocrowd-pilot:({RID}) -->")
RUN_RE = re.compile(rf"<!-- reprocrowd-pilot-run:({RID}):([0-9a-f]{{12}}) -->")
FILES_RE = re.compile(r"<!-- reprocrowd-pilot-files:([A-Za-z0-9-]+/[A-Za-z0-9_.-]+) -->")
SUMMARY_RE = re.compile(r"<!-- reprocrowd-summary: (\{.*?\}) -->")
ISSUE_OF_RE = re.compile(r"<!-- reprocrowd-pilot-issue:(\d+) -->")  # in each copied release: the issue that owns the reproduction
RUNS_START, RUNS_END = "<!-- reprocrowd-runs -->", "<!-- /reprocrowd-runs -->"
DOI_RE = re.compile(r"^10\.\d{4,9}/[^\s<>\[\]()`|]+$")
POLICY_RE = re.compile(r"^reprocrowd-compare/\d+$")
CODE_LICENCES = {"MIT", "CC-BY-4.0"}
MAX_JSON = 20 * 1024**2  # comparison.json and licenses.json are read from the ZIP only up to this size
DISCLAIMER = ("**Pilot record — unreviewed.** Made with ReproCrowd by recreating the analysis from the article's text and "
              "published data. Agreement under the recorded comparison settings shows that this code produced the "
              "reported numbers from these inputs. It is not peer review, a certificate of the study's validity, or an endorsement. The authors' "
              "code was left out of the agent's context; that is not verified blinding.")
MAINTAINERS = ("OWNER", "MEMBER", "COLLABORATOR")
LABELS = {"reprocrowd-pilot": ("1d76db", "A ReproCrowd pilot record (one issue per reproduction)"),
          "unreviewed": ("fbca04", "Not reviewed: a numeric match is not a scientific certificate"),
          "synthetic": ("cccccc", "Synthetic test fixture of the publication workflow, not research")}


def issue_mark(rid: str) -> str:
    return f"<!-- reprocrowd-pilot:{rid} -->"


def run_mark(rid: str, key: str) -> str:
    return f"<!-- reprocrowd-pilot-run:{rid}:{key} -->"


def files_mark(repo: str) -> str:
    return f"<!-- reprocrowd-pilot-files:{repo} -->"


def release_mark(tag: str) -> str:
    return f"<!-- reprocrowd-pilot-release:{tag} -->"


def refused_mark(tag: str) -> str:
    return _reply_mark(tag, "refused")


def _reply_mark(tag: str, outcome: str) -> str:
    return f"<!-- reprocrowd-pilot-{outcome}:{tag} -->"


def summary_line(s: dict[str, Any]) -> str:
    if not s.get("numeric"):
        return "no results to compare automatically (figures need visual comparison)"
    rounding = s.get("rounding", "reported")
    how = "numbers at printed precision" if rounding == "reported" else f"numbers rounded to {rounding} decimals"
    return (f"{s['match']} of {s['numeric']} match ({how}; text exactly); {s['within']} more within "
            f"{s['threshold']:g} % (exploratory)")


class Refused(Exception):
    """The run is not copied; the message is the reply."""


class Api:
    def __init__(self, token: str, api: str = "https://api.github.com"):
        self.api = api.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "reprocrowd-pilot-copy"}

    def request(self, method: str, url: str, ok: tuple[int, ...] = (200, 201, 204), **kw) -> httpx.Response:
        url = url if url.startswith("http") else self.api + url
        r = httpx.request(method, url, headers={**self.headers, **kw.pop("headers", {})}, timeout=kw.pop("timeout", 120),
                          follow_redirects=True, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"GitHub returned HTTP {r.status_code} for {method} {url.split('?')[0]}: {r.text[:300]}")
        return r

    def json(self, method: str, url: str, **kw) -> Any:
        return self.request(method, url, **kw).json()

    def paged(self, url: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        params = {"per_page": 100, **(params or {})}
        while next_url:
            r = self.request("GET", next_url, params=params)
            yield from r.json()
            next_url, params = r.links.get("next", {}).get("url"), None


def handle(api: Api, repo: str, issue: dict[str, Any], comment: dict[str, Any]) -> str | None:
    """Process one comment. Returns the reply posted, or None (not a run comment, or already answered)."""
    m = RUN_RE.search(comment.get("body") or "")
    if not m or "pull_request" in issue:
        return None
    rid, key = m.groups()
    tag = f"{rid}-{key}"
    replies = [c.get("body") or "" for c in api.paged(f"/repos/{repo}/issues/{issue['number']}/comments")]
    if any(_reply_mark(tag, "copied") in b for b in replies):
        return None
    try:
        url, version, count = _copy(api, repo, issue, comment, rid, key, tag)
        body = (f"{_reply_mark(tag, 'copied')}\nRun **v{version}** is copied to [release {tag}]({url}): {count} files, each matching "
                "its SHA-256 in `pilot.json` and GitHub's digest. Published releases here are immutable.")
    except Refused as e:
        body = f"{_reply_mark(tag, 'refused')}\nRun `{tag}` was **not copied**: {e}"
        if body in replies:
            return None
    api.request("POST", f"/repos/{repo}/issues/{issue['number']}/comments", json={"body": body})
    return body


def _copy(api: Api, repo: str, issue: dict[str, Any], comment: dict[str, Any], rid: str, key: str, tag: str) -> tuple[str, int, int]:
    author = comment["user"]["login"]
    maintainer = comment.get("author_association") in MAINTAINERS
    im = ISSUE_RE.search(issue.get("body") or "")
    if not im or im.group(1) != rid:
        raise Refused(f"this issue is not the record of reproduction `{rid}`")
    if author.lower() != issue["user"]["login"].lower() and not maintainer:
        raise Refused(f"only the issue's author or a maintainer can add runs to it; submit your own reproduction as a new issue")
    ours = [r for r in api.paged(f"/repos/{repo}/releases") if (r.get("tag_name") or "").startswith(rid + "-")]
    # the issue of the reproduction's first copied run owns it (issue bodies can be edited by their authors; releases can't)
    owner = next((int(m.group(1)) for r in ours if not r.get("draft") and (m := ISSUE_OF_RE.search(r.get("body") or ""))), None)
    if owner is not None and owner != issue["number"]:
        raise Refused(f"reproduction `{rid}` belongs to #{owner}")
    fm = FILES_RE.search(comment["body"])
    src = fm.group(1) if fm else None
    here = bool(src) and src.lower() == repo.lower()
    if src is None or (here and not maintainer) or (not here and src.split("/")[0].lower() != author.lower()):
        raise Refused("the files must be in a release of the commenter's own repository")
    central = next((r for r in ours if r["tag_name"] == tag), None)
    if central and not central.get("draft"):  # copied before, but a later step or the reply failed: finish them
        pilot = json.loads(_download(api, repo, _named(central, "pilot.json")))
        _check_assets(central, pilot)
        _label(api, repo, issue["number"], pilot)
        _update_runs(api, repo, issue["number"], rid)
        return central["html_url"], _version(central) or 0, len(central["assets"])
    if here:
        if central is None:
            raise Refused(f"there is no draft release `{tag}` in this repository")
        source, src_repo = central, repo
    else:
        r = api.request("GET", f"/repos/{src}/releases/tags/{tag}", ok=(200, 404))
        if r.status_code == 404:
            raise Refused(f"{src} has no published release `{tag}`")
        source, src_repo = r.json(), src
    assets = source.get("assets") or []
    total = sum(a.get("size") or 0 for a in assets)
    if total > MAX_RUN:
        raise Refused(f"the run's files total {total:,} bytes; the limit is {MAX_RUN:,}")
    pilot = json.loads(_download(api, src_repo, _named(source, "pilot.json")))
    if (pilot.get("reproduction_id"), pilot.get("run_key"), pilot.get("tag")) != (rid, key, tag):
        raise Refused("`pilot.json` names a different reproduction or run")
    _validate(pilot)
    data = {a["name"]: _download(api, src_repo, a) for a in assets}
    _check_assets(source, pilot, data)
    summary, code = _from_zip(next(b for n, b in data.items() if n.endswith(".zip")))
    version = 1 + max((_version(r) or 0 for r in ours if not r.get("draft")), default=0)
    body = _release_body(pilot, summary | {"version": version, "run_key": key}, code, author, issue["number"],
                         [(n, len(b), hashlib.sha256(b).hexdigest()) for n, b in data.items()])
    branch = api.json("GET", f"/repos/{repo}").get("default_branch") or "main"
    keep = {"tag_name": tag, "target_commitish": branch}  # GitHub drops a draft's tag when it is edited without tag_name
    if here:
        rel = central
    else:
        if central:  # a draft left by an interrupted copy
            api.request("DELETE", f"/repos/{repo}/releases/{central['id']}", ok=(204, 404))
        rel = api.json("POST", f"/repos/{repo}/releases", json=keep | {"name": f"{rid} (copy in progress)", "body": release_mark(tag),
                                                                      "draft": True, "prerelease": True, "make_latest": "false"})
        upload = rel["upload_url"].split("{")[0]
        for name, b in data.items():
            api.request("POST", upload, params={"name": name}, content=b, timeout=600,
                        headers={"Content-Type": "application/zip" if name.endswith(".zip") else "application/json" if name.endswith(".json") else "text/html"})
    rel = api.json("GET", f"/repos/{repo}/releases/{rel['id']}")
    _check_assets(rel, pilot, data)
    rel = api.json("PATCH", f"/repos/{repo}/releases/{rel['id']}", json=keep | {
        "name": f"{rid} v{version} (pilot, unreviewed)", "body": body, "draft": False, "prerelease": True, "make_latest": "false"})
    if rel.get("tag_name") != tag:
        raise RuntimeError(f"GitHub published the release under the tag {rel.get('tag_name')!r} instead of {tag!r}")
    _label(api, repo, issue["number"], pilot)
    _update_runs(api, repo, issue["number"], rid)
    return rel["html_url"], version, len(data)


def _named(release: dict[str, Any], name: str) -> dict[str, Any]:
    a = next((a for a in release.get("assets") or [] if a["name"] == name), None)
    if a is None:
        raise Refused(f"the release has no `{name}`")
    return a


def _download(api: Api, repo: str, asset: dict[str, Any]) -> bytes:
    return api.request("GET", f"/repos/{repo}/releases/assets/{asset['id']}", headers={"Accept": "application/octet-stream"}).content


def _check_assets(release: dict[str, Any], pilot: dict[str, Any], data: dict[str, bytes] | None = None) -> None:
    want = pilot.get("assets") or {}
    have = {a["name"]: a for a in release.get("assets") or []}
    if set(have) != set(want) | {"pilot.json"}:
        raise Refused("the release's files differ from the list in `pilot.json`")
    for name, a in have.items():
        digest = a.get("digest")
        if a.get("state") != "uploaded" or not digest:
            raise Refused(f"`{name}` is incomplete or has no SHA-256 digest on GitHub")
        if data is not None and digest != "sha256:" + hashlib.sha256(data[name]).hexdigest():
            raise Refused(f"`{name}` doesn't match GitHub's digest")
        if name != "pilot.json" and digest != want[name]:
            raise Refused(f"`{name}` doesn't match its SHA-256 in `pilot.json`")
    zips = [n for n in have if n.endswith(".zip")]
    if len(zips) != 1:
        raise Refused("a run has exactly one ZIP")
    if data is not None:
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data[zips[0]])).namelist())
        except zipfile.BadZipFile:
            raise Refused("the ZIP can't be read")
        if not {"datapackage.json", "licenses.json", "comparison.json"} <= names:
            raise Refused("the ZIP lacks `datapackage.json`, `licenses.json` or `comparison.json`")


def _validate(pilot: dict[str, Any]) -> None:
    """pilot.json is the submitter's: of its values, only the DOI and the synthetic flag reach the release notes."""
    if not DOI_RE.match(str((pilot.get("paper") or {}).get("doi") or "")) or type(pilot.get("synthetic", False)) is not bool:
        raise Refused("`pilot.json` has a DOI or synthetic flag this repository doesn't accept")


def _from_zip(zipped: bytes) -> tuple[dict[str, Any], str]:
    """The comparison summary and the new code's licence, from the archived comparison.json and licenses.json (never
    from pilot.json), checked before they reach the release notes."""
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as z:
            members = set(z.namelist())
            if any(z.getinfo(n).file_size > MAX_JSON for n in ("comparison.json", "licenses.json")):
                raise Refused(f"`comparison.json` or `licenses.json` in the ZIP is over {MAX_JSON // 1024**2} MiB")
            cmp, lic = json.loads(z.read("comparison.json")), json.loads(z.read("licenses.json"))
    except (KeyError, ValueError, zipfile.BadZipFile):
        raise Refused("the ZIP lacks a readable `comparison.json` or `licenses.json`")
    if not isinstance(cmp, dict) or not isinstance(lic, dict):
        raise Refused("`comparison.json` and `licenses.json` must be JSON objects")
    s, st, rows, files = cmp.get("summary"), cmp.get("settings") or {}, cmp.get("rows"), lic.get("files")
    rounding = st.get("rounding", "reported") if isinstance(st, dict) else None
    ok = (isinstance(s, dict) and all(type(v) is int for v in s.values()) and isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
          and isinstance(st, dict) and type(st.get("threshold", 0)) in (int, float) and (rounding == "reported" or type(rounding) is int)
          and (cmp.get("policy") is None or bool(POLICY_RE.match(str(cmp["policy"]))))
          and lic.get("policy") == "reprocrowd-licensing/1" and isinstance(files, list) and all(isinstance(f, dict) for f in files))
    if not ok:
        raise Refused("the archived `comparison.json` or `licenses.json` has values this repository doesn't accept")
    code = sorted({str(f.get("license")) for f in files if f.get("kind") in ("script", "code")})
    wrong = [str(f.get("path")) for f in files if f.get("license") != _policy_licence(f, code)]
    unlisted = members - {str(f.get("path")) for f in files} - {"excluded-files.json"}
    if not set(code) <= CODE_LICENCES or wrong or unlisted:
        raise Refused("`licenses.json` doesn't follow policy `reprocrowd-licensing/1` or doesn't list every file "
                      f"({', '.join(f'`{p}`' for p in sorted(wrong + list(unlisted))[:5]) or 'code licence'})")
    summary = {"match": s.get("match", 0), "within": s.get("within", 0), "numeric": sum(r.get("type") != "figure" for r in rows),
               "threshold": st.get("threshold", 0), "rounding": rounding, "policy": cmp.get("policy")}
    return summary, code[0] if len(code) == 1 else ("none in this package" if not code else "per file, see `licenses.json`")


def _version(release: dict[str, Any]) -> int | None:
    m = re.search(r"\bv(\d+)\b", release.get("name") or "")
    return int(m.group(1)) if m else None


def _policy_licence(f: dict[str, Any], code: list[str]) -> str | None:
    """The licence policy reprocrowd-licensing/1 gives this entry (any listed code licence for code)."""
    if f.get("rights") != "ours":
        return None
    if f.get("kind") in ("script", "code"):
        return f.get("license") if f.get("license") in code else None
    return "CC-BY-4.0" if f.get("kind") in ("output", "report") else "CC0-1.0"


def _release_body(pilot: dict[str, Any], s: dict[str, Any], code: str, author: str, issue: int, files: list[tuple[str, int, str]]) -> str:
    synthetic = "> [!CAUTION]\n> **SYNTHETIC test fixture — not a research result.**\n\n" if pilot.get("synthetic") else ""
    rows = "\n".join(f"| `{n}` | {size} | `{sha}` |" for n, size, sha in files)
    return (f"{synthetic}> [!WARNING]\n> {DISCLAIMER}\n\n"
            f"**Run v{s['version']}** of reproduction `{pilot['reproduction_id']}` (run key `{pilot['run_key']}`) of "
            f"https://doi.org/{pilot['paper']['doi']}, submitted by [{author}](https://github.com/{author}) in #{issue}.\n\n"
            f"{summary_line(s)}. Policy `{s['policy']}`.\n\n| File | Bytes | SHA-256 |\n|---|---:|---|\n{rows}\n\n"
            f"Licences (policy `reprocrowd-licensing/1`, each file listed in `licenses.json`): metadata and `pilot.json` CC0-1.0; "
            f"report, comparison, evidence and outputs CC-BY-4.0; new analysis code {code}. The article's material and the input "
            "data and code keep their original licences.\n\n"
            f"{release_mark(pilot['tag'])}\n<!-- reprocrowd-pilot-issue:{issue} -->\n<!-- reprocrowd-summary: {json.dumps(s, sort_keys=True)} -->\n")


def _label(api: Api, repo: str, number: int, pilot: dict[str, Any]) -> None:
    names = ["reprocrowd-pilot", "unreviewed"] + (["synthetic"] if pilot.get("synthetic") else [])
    for name in names:
        if api.request("GET", f"/repos/{repo}/labels/{name}", ok=(200, 404)).status_code == 404:
            color, desc = LABELS[name]
            api.request("POST", f"/repos/{repo}/labels", ok=(201, 422), json={"name": name, "color": color, "description": desc})
    api.request("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": names})


def _update_runs(api: Api, repo: str, number: int, rid: str) -> None:
    runs = []
    for r in api.paged(f"/repos/{repo}/releases"):
        m = SUMMARY_RE.search(r.get("body") or "")
        if r.get("draft") or not (r.get("tag_name") or "").startswith(rid + "-") or not m or _version(r) is None:
            continue
        runs.append({**json.loads(m.group(1)), "version": _version(r), "tag": r["tag_name"], "url": r["html_url"],
                     "date": (r.get("published_at") or "")[:10]})
    runs.sort(key=lambda r: -r["version"])
    lines = [RUNS_START]
    if runs:
        latest = runs[0]
        lines += [f"### Latest run: v{latest['version']} ({latest['date']})", "", f"{summary_line(latest)}. Policy `{latest['policy']}`.", "",
                  "### All runs", "", "| Run | Date | Exact | Within | Release |", "|---|---|---:|---:|---|"]
        lines += [f"| v{r['version']} | {r['date']} | {r['match']}/{r['numeric']} | {r['within']} | [{r['tag']}]({r['url']}) |" for r in runs[:100]]
    lines.append(RUNS_END)
    issue = api.json("GET", f"/repos/{repo}/issues/{number}")
    old = issue.get("body") or ""
    section = "\n".join(lines)
    if RUNS_START in old and RUNS_END in old:
        new = old[:old.index(RUNS_START)] + section + old[old.index(RUNS_END) + len(RUNS_END):]
    else:
        new = old.rstrip() + "\n\n" + section + "\n"
    if len(new) > 65_000:  # GitHub's limit; cutting could drop the end marker. The runs stay listed in the releases and comments.
        print(f"The table of runs doesn't fit in #{number}'s description; left unchanged.")
        return
    api.request("PATCH", f"/repos/{repo}/issues/{number}", json={"body": new})


def main() -> None:
    """The Action: on a new comment, or on a manual run for one issue (workflow_dispatch with inputs.issue)."""
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    repo = os.environ["GITHUB_REPOSITORY"]
    api = Api(os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    if "comment" in event:
        pairs = [(event["issue"], event["comment"])]
    else:
        number = int(event["inputs"]["issue"])
        issue = api.json("GET", f"/repos/{repo}/issues/{number}")
        pairs = [(issue, c) for c in api.paged(f"/repos/{repo}/issues/{number}/comments")]
    for issue, comment in pairs:
        reply = handle(api, repo, issue, comment)
        if reply:
            print(reply.split("\n", 1)[1])


if __name__ == "__main__":
    main()
