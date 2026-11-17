"""GitHub pilot submissions: a reviewable, public record of a reproduction before anything goes to Zenodo.

Anyone with a GitHub account can submit; no write access to the pilots repository is needed.
  1. The run's files go to a published pre-release in the submitter's fork of the pilots repository (created on the
     first submission). The repository's owner, who can't fork it, uploads a draft release there instead.
  2. The reproduction's issue is opened on the first submission (anyone can open issues), and each run gets a comment
     whose hidden markers name the run and the repository holding its files.
  3. The pilots repository's GitHub Action (pilot_copy.py) checks the run and copies the files into an immutable
     pre-release there, numbers the run (v1, v2, …), labels the issue, updates its table of runs and replies.
The local record is ``submitted`` after step 2 and ``published`` once the copy exists (checked when records are listed).

Identity:
  reproduction  ``reproduction_id`` (repro-<10 hex>), stored in the package on the first submission. One GitHub issue
                per reproduction, found by a hidden marker; the paper's DOI is metadata, not identity, so independent
                reproductions of one paper get separate issues.
  run           ``run_key`` = sha256(archive sha256 | left-out groups)[:12]. One release per run, tagged
                <reproduction_id>-<run_key>, in the files repository and (copied) in the pilots repository; one comment
                per run on the issue.
Submitting is idempotent per run key: every step first looks for what an earlier attempt made (local record, then the
markers on GitHub) and only creates what is missing. A failure leaves a ``partial`` record; submitting again resumes.

Preconditions are those of a Zenodo deposit (``database.Prepared`` + the paper's text retrieved for its DOI): the
recorded comparison (refused when stale), a run that succeeded and an up-to-date archive. The uploaded ZIP is the
archive, or the copy without the left-out groups, built once per run key and kept under archive/ so a retry uploads
the same bytes. report.html, comparison.json, execution.json and licenses.json are copied out of that ZIP. A run's
files must total under MAX_RUN, the limit the Action enforces.

The token is passed per request and never stored.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import tempfile
import time
from time import monotonic
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx

from . import __version__, identifiers, licensing, pilot_copy
from .config import settings
from .database import Prepared
from .errors import ApiError, conflict, invalid
from .package import Package, sha256_file
from .pilot_copy import DISCLAIMER, MAX_RUN, doi_tag, doi_url, RUNS_END, RUNS_START, files_mark, issue_mark, refused_mark, release_mark, run_mark, summary_line
from .report import ARCHIVE_DIR, archive_path
from .schemas.common import now
from .schemas.manifest import GithubPilot, PilotAsset
from .zenodo import GROUPS, _check_published_paper, _group, _publication_zip, contents

BODY_LIMIT = 65_000  # GitHub's issue/comment body limit is 65,536 characters
ROW_LIMIT = 60
FORK_WAIT = 30  # × 2 s: GitHub creates forks asynchronously
ACTION_LOGIN = "github-actions[bot]"  # the pilots repository's Action comments as this account
RECHECK_S = 60  # listing records checks a submitted run on GitHub at most this often (unauthenticated reads are rate-limited)
SYNTHETIC = ("**SYNTHETIC test fixture — not a research result.** The paper, data and DOI are invented for testing "
             "the publication workflow.")
EXTRACT = {"report.html": "report.html", "comparison.json": "comparison.json", "evidence/execution.json": "execution.json",
           licensing.FILE: licensing.FILE}
TYPES = {".zip": "application/zip", ".html": "text/html", ".json": "application/json"}


def _is_run(r: dict[str, Any], tag: str) -> bool:
    """GitHub replaces a draft's tag with an ``untagged-…`` placeholder when the draft is edited without tag_name, so
    releases are also matched by the marker in their body."""
    return r.get("tag_name") == tag or release_mark(tag) in (r.get("body") or "")


# ---------- GitHub REST ----------


class GitHub:
    def __init__(self, token: str | None, api: str | None = None):
        tok = token or settings().github_token
        if not tok:
            raise ApiError(400, "github_token_missing", "a GitHub login is needed (`gh auth login`; for rc also $REPROCROWD_GITHUB_TOKEN)")
        self.api = (api or settings().github_api_url).rstrip("/")
        self.headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"ReproCrowd/{__version__}"}

    def request(self, method: str, url: str, ok: tuple[int, ...] = (200, 201, 204), **kw) -> httpx.Response:
        url = url if url.startswith("http") else self.api + url
        headers = {**self.headers, **kw.pop("headers", {})}
        try:
            r = httpx.request(method, url, headers=headers, timeout=kw.pop("timeout", 30), **kw)
        except httpx.HTTPError as e:
            raise ApiError(502, "github_unreachable", f"could not reach GitHub ({method} {url.split('?')[0]}): {e}")
        if r.status_code in ok:
            return r
        try:
            msg = r.json().get("message") or f"HTTP {r.status_code}"
        except ValueError:
            msg = f"HTTP {r.status_code}"
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in msg.lower()):
            raise ApiError(429, "github_rate_limited", f"GitHub's rate limit was reached ({msg}); try again later (reset at {r.headers.get('x-ratelimit-reset', '?')})")
        if r.status_code == 401:
            raise ApiError(403, "github_token_invalid", f"GitHub didn't accept the token ({msg})")
        if r.status_code in (403, 404):
            raise ApiError(403, "github_forbidden", f"GitHub refused {method} {url.split('?')[0].removeprefix(self.api)} ({msg}). "
                           "Submitting needs a login that can open issues and create a fork: `gh auth login`, or a classic "
                           "token with the public_repo scope")
        if r.status_code == 422:
            raise invalid("github_rejected", f"GitHub rejected {method} {url.split('?')[0].removeprefix(self.api)}: {msg}",
                          {"errors": (r.json() if r.content else {}).get("errors", [])})
        raise ApiError(502, "github_error", f"GitHub returned HTTP {r.status_code} for {method} {url.split('?')[0]}: {msg}")

    def json(self, method: str, url: str, **kw) -> Any:
        return self.request(method, url, **kw).json()

    def paged(self, url: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        params = {"per_page": 100, **(params or {})}
        while next_url:
            r = self.request("GET", next_url, params=params)
            yield from r.json()
            next_url, params = r.links.get("next", {}).get("url"), None


def check(token: str | None, repo: str | None = None) -> dict[str, Any]:
    """Can this login submit pilots to the repository? (Reads only.)"""
    repo = repo or settings().github_pilots_repo
    gh = GitHub(token)
    info = gh.json("GET", f"/repos/{repo}")
    login = gh.json("GET", "/user")["login"]
    own = info["owner"]["login"].lower() == login.lower()
    return {"repo": info.get("full_name", repo), "url": info.get("html_url"), "login": login, "private": info.get("private"),
            "can_submit": not info.get("private") and (own or info.get("allow_forking") is not False),
            "files_in": repo if own else f"{login}/{info.get('name')} (your fork, created on the first submission)"}


FIND_CACHE_S = 60  # the public search API allows 10 requests a minute without a token
_found: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


@contextmanager
def _public_errors(path: str):
    try:
        yield
    except httpx.HTTPError as e:
        raise ApiError(502, "github_unreachable", f"could not reach GitHub ({path}): {e}")
    except pilot_copy.GitHubError as e:
        if e.status in (403, 429):
            raise ApiError(429, "github_rate_limited", "GitHub's rate limit for requests without a login was reached; try again in a minute")
        raise ApiError(502, "github_error", f"GitHub returned HTTP {e.status} for {path}")


def _public(api: str) -> pilot_copy.Api:
    """GitHub's REST API without a token, for public reads."""
    return pilot_copy.Api(None, api, agent=f"ReproCrowd/{__version__}")


def find(doi: str, repo: str | None = None) -> list[dict[str, Any]]:
    """Pilot issues for the paper with this DOI in the pilots repository (public reads, no token). An issue is listed when
    it names the paper (pilot_copy.names_paper) and carries a pilot's marker; its runs are read from the repository's
    releases, as the Action reads them, so an edited issue description can't add or hide any. Other people's
    reproductions of the paper are listed too: the DOI isn't a reproduction's identity."""
    repo, doi = repo or settings().github_pilots_repo, identifiers.doi(doi)
    t = monotonic()
    if (hit := _found.get((repo, doi))) and t - hit[0] < FIND_CACHE_S:
        return hit[1]
    api = settings().github_api_url.rstrip("/")
    q = {"q": f'repo:{repo} is:issue in:title,body "{doi}"', "sort": "created", "order": "desc", "per_page": 100}
    with _public_errors("/search/issues"):  # one page of 100, the most GitHub gives: each further page costs one of the 10 searches a minute
        r = _public(api).request("GET", "/search/issues", ok=(200, 422), params=q)
    if r.status_code == 422:  # the repository doesn't exist or isn't public: nothing to find there
        return []
    issues = []
    for i in r.json().get("items") or []:
        title, body = i.get("title") or "", i.get("body") or ""
        rid = pilot_copy.ISSUE_RE.search(body)
        if "pull_request" not in i and rid and pilot_copy.names_paper(title, body, doi):
            issues.append((i, rid.group(1)))
    releases = []
    if issues:
        with _public_errors(f"/repos/{repo}/releases"):
            releases = list(_public(api).paged(f"/repos/{repo}/releases"))
    out = []
    for i, rid in issues:
        runs = [r for r in pilot_copy.copied_runs(releases, rid) if r["issue"] == i["number"]]
        labels = {lb.get("name") for lb in i.get("labels") or []}
        out.append({"number": i["number"], "title": i.get("title") or "", "url": i["html_url"], "state": i.get("state"),
                    "author": (i.get("user") or {}).get("login"), "created_at": i.get("created_at"), "reproduction_id": rid,
                    "runs": len(runs), "latest_version": runs[0]["version"] if runs else None,
                    "latest_date": runs[0]["date"] if runs else None, "latest_summary": summary_line(runs[0]) if runs else None,
                    "synthetic": "synthetic" in labels or (i.get("title") or "").startswith("[SYNTHETIC]")})
    for k in [k for k, (at, _) in _found.items() if t - at >= FIND_CACHE_S]:
        del _found[k]
    _found[(repo, doi)] = (t, out)
    return out


def status() -> dict[str, Any]:
    cfg = settings()
    return {"repo": cfg.github_pilots_repo, "api_url": cfg.github_api_url, "token_configured": bool(cfg.github_token),
            "licenses": licensing.summary()}


# ---------- what a submission sends ----------


@dataclass
class Asset:
    name: str
    path: Path
    sha256: str
    bytes: int

    @property
    def content_type(self) -> str:
        return TYPES.get(self.path.suffix, "application/octet-stream")


@dataclass
class Plan:
    pkg: Package
    p: Prepared
    repo: str
    exclude: list[str]
    run_key: str
    reproduction_id: str | None
    synthetic: bool
    note: str
    files: list[Asset] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    licences: dict[str, Any] = field(default_factory=dict)  # from the archived licenses.json, not the current setting

    @property
    def tag(self) -> str:
        return f"{self.reproduction_id}-{self.run_key}"


def _plan(pkg: Package, repo: str | None, exclude: list[str] | None, note: str, synthetic: bool) -> Plan:
    _check_published_paper(pkg)
    p = Prepared(pkg)  # recorded comparison (report_stale), run succeeded, archive up to date, OpenAlex record
    c = contents(pkg, p.work)
    ex = sorted(set(c["default_exclude"] if exclude is None else [g for g in exclude if c["groups"][g]["resources"]]))
    archive_sha = p.m.reprocrowd.archive.sha256
    key = hashlib.sha256(f"{archive_sha}|{','.join(ex)}".encode()).hexdigest()[:12]
    return Plan(pkg, p, repo or settings().github_pilots_repo, ex, key, p.m.reprocrowd.reproduction_id, synthetic, note)


def _build_files(plan: Plan, tmp: Path) -> None:
    """The ZIP (kept under archive/ so that a retry uploads the same bytes) and the files copied out of it."""
    pkg, archive = plan.pkg, archive_path(plan.pkg)
    kept = pkg.path(f"{ARCHIVE_DIR}/pilot-{plan.run_key}/{archive.name}")
    if not kept.exists():
        kept.parent.mkdir(parents=True, exist_ok=True)
        built, _ = _publication_zip(pkg, archive, set(plan.exclude), tmp)
        shutil.copyfile(built, kept.with_suffix(".part"))
        kept.with_suffix(".part").replace(kept)
    plan.excluded = [r.name for r in pkg.load().resources if _group(r) in plan.exclude]
    files = [kept]
    with zipfile.ZipFile(kept) as z:
        names = set(z.namelist())
        for inner, name in EXTRACT.items():
            if inner in names:
                out = tmp / name
                out.write_bytes(z.read(inner))
                files.append(out)
    plan.files = [Asset(f.name, f, sha256_file(f), f.stat().st_size) for f in files]
    plan.licences = _archived_licences(next((f for f in files if f.name == licensing.FILE), None))
    total, limit = sum(a.bytes for a in plan.files), MAX_RUN - 1024**2  # the Action's limit includes pilot.json: leave 1 MiB for it
    if total > limit:
        raise invalid("pilot_asset_too_large", f"a pilot run's files must total at most {limit // 1024**2} MiB; these total "
                                               f"{total / 1024**2:.0f} MiB. Leave out the inputs (exclude: inputs) or deposit on Zenodo instead")


def _evidence(plan: Plan) -> dict[str, Any]:
    """What the archived execution evidence discloses (files changed or added after the run, results identity). Read
    from the copy in the ZIP, so a retry builds the same pilot.json."""
    path = next((a.path for a in plan.files if a.name == "execution.json"), None)
    try:
        ev = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        ev = None
    if not isinstance(ev, dict):
        return {"recorded": False}
    return {"recorded": True, "stale": ev.get("stale"), "added_since_run": ev.get("added_since_run") or [],
            "results_match_run": ev.get("results_match_run")}


def _evidence_line(plan: Plan) -> str:
    ev = _evidence(plan)
    if not ev["recorded"]:
        return "> [!NOTE]\n> No execution evidence was recorded for this run.\n"
    notes = []
    if ev["stale"]:
        notes.append(f"files changed since the recorded run: {', '.join(f'`{x}`' for x in ev['stale'][:20])}")
    if ev["added_since_run"]:
        notes.append(f"files added since the run: {', '.join(f'`{x}`' for x in ev['added_since_run'][:20])}")
    if ev["results_match_run"] is False:
        notes.append("the results file differs from the one the run wrote")
    return f"> [!IMPORTANT]\n> Execution evidence discloses {'; '.join(notes)} (see `execution.json`).\n" if notes else ""


def _archived_licences(path: Path | None) -> dict[str, Any]:
    """The licences as recorded in the archive when it was made, so the public record never contradicts it."""
    if path is None:
        raise conflict("archive_stale", "the archive was made before licences were recorded (no licenses.json); create it again (PUT …/archive)")
    lic = json.loads(path.read_text(encoding="utf-8"))
    code = sorted({f.get("license") or "?" for f in lic.get("files", []) if f.get("kind") in ("script", "code")})
    return {"policy": lic.get("policy"), "metadata": licensing.METADATA, "outputs": licensing.OUTPUTS,
            "code": code[0] if len(code) == 1 else ("none in this package" if not code else "per file, see licenses.json"),
            "third_party": "original licences retained; see licenses.json"}


def _summary(plan: Plan) -> dict[str, Any]:
    cmp = plan.p.cmp
    return {"run_key": plan.run_key, "match": cmp.summary.get("match", 0), "within": cmp.summary.get("within", 0),
            "numeric": sum(r.type != "figure" for r in cmp.rows), "threshold": cmp.settings.threshold,
            "rounding": cmp.settings.rounding, "policy": cmp.policy}


def _pilot_json(plan: Plan) -> bytes:
    """The machine-readable record of the run (CC0). Deterministic for a run key, so a retry matches."""
    cmp, m = plan.p.cmp, plan.p.m
    record = {
        "schema": "reprocrowd-pilot/1",
        "status": "pilot-unreviewed",
        "statement": DISCLAIMER.replace("**", ""),
        "synthetic": plan.synthetic,
        "reproduction_id": plan.reproduction_id,
        "run_key": plan.run_key,
        "tag": plan.tag,
        "paper": {"doi": plan.p.doi},
        "track": m.reprocrowd.track,
        "comparison": {"policy": cmp.policy, "rounding_mode": cmp.rounding_mode, "settings": cmp.settings.model_dump(mode="json"),
                       "summary": cmp.summary, "numeric_targets": _summary(plan)["numeric"]},
        "evidence": _evidence(plan),
        "archive": {"sha256": m.reprocrowd.archive.sha256, "exclude": plan.exclude, "excluded": plan.excluded},
        "assets": {a.name: f"sha256:{a.sha256}" for a in plan.files},
        "licenses": plan.licences | {"this_file": licensing.METADATA},
    }
    return (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode()


def _comparison_table(plan: Plan) -> str:
    rows = plan.p.cmp.rows
    lines = ["| Target | Status | Printed | Reproduced |", "|---|---|---|---|"]
    for r in rows[:ROW_LIMIT]:
        lines.append(f"| `{r.target}` | {r.status} | {_cell(r.expected_text)} | {_cell(r.reproduced_text)} |")
    if len(rows) > ROW_LIMIT:
        lines.append(f"| … | {len(rows) - ROW_LIMIT} more rows in `comparison.json` | | |")
    return "\n".join(lines)


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")[:80]


def _assets_table(assets: list[PilotAsset] | list[Asset]) -> str:
    lines = ["| File | Bytes | SHA-256 |", "|---|---:|---|"]
    for a in assets:
        url = getattr(a, "url", None)
        lines.append(f"| {f'[{a.name}]({url})' if url else a.name} | {a.bytes} | `{a.sha256}` |")
    return "\n".join(lines)


def _licence_lines(plan: Plan) -> str:
    lic = plan.licences
    excluded = f" Left out of the ZIP: {'; '.join(GROUPS[g] for g in plan.exclude)} (listed with hashes in `excluded-files.json`)." if plan.exclude else ""
    return (f"Metadata and `pilot.json`: {lic['metadata']}. Report, comparison, evidence and outputs: {lic['outputs']}. "
            f"New analysis code: {lic['code']}. The article's material and the input data and code keep their original "
            f"licences; `licenses.json` lists every file.{excluded}")


def _clip(body: str) -> str:
    return body if len(body) <= BODY_LIMIT else body[:BODY_LIMIT - 200] + "\n\n… (shortened to GitHub's limit; see the release files)\n"


def _issue_title(plan: Plan) -> str:
    title = f"Pilot reproduction: {plan.p.work['title']} {doi_tag(plan.p.doi)}"
    return ("[SYNTHETIC] " if plan.synthetic else "") + title[:240]


def _banner(plan: Plan) -> str:
    return (f"> [!CAUTION]\n> {SYNTHETIC}\n\n" if plan.synthetic else "") + f"> [!WARNING]\n> {DISCLAIMER}\n"


def _release_body(plan: Plan, assets: list[Asset]) -> str:
    return _clip(f"{_banner(plan)}\nRun `{plan.run_key}` of reproduction `{plan.reproduction_id}` of {doi_url(plan.p.doi)}, "
                 f"submitted as a pilot to {plan.repo}; the copy there is the record.\n\n{summary_line(_summary(plan))}.\n\n"
                 f"{_evidence_line(plan)}\n{_assets_table(assets)}\n\n{_licence_lines(plan)}\n\n{release_mark(plan.tag)}\n")


def _comment_body(plan: Plan, rec: GithubPilot) -> str:
    s = _summary(plan)
    note = f"\n**Submitter's note:** {' '.join(plan.note.split())[:2000]}\n" if plan.note else ""
    return _clip(f"{run_mark(plan.reproduction_id, plan.run_key)}\n{files_mark(rec.files_repo)}\n### Run `{plan.run_key}`\n\n"
                 f"{summary_line(s)}. Policy `{s['policy']}`, rounding `{plan.p.cmp.rounding_mode}`.\n\n{_evidence_line(plan)}{note}\n"
                 f"Files: [release {rec.tag}]({rec.files_url}) in {rec.files_repo}. This repository's Action checks them, copies them "
                 "into its own immutable pre-release, numbers the run and replies below.\n\n"
                 f"{_assets_table(rec.assets)}\n\n<details><summary>Comparison ({len(plan.p.cmp.rows)} targets)</summary>\n\n"
                 f"{_comparison_table(plan)}\n\n</details>\n\n{_licence_lines(plan)}\n\n_Status: pilot, unreviewed. Reviewers: "
                 "reply here, or open a new issue for a separate reproduction._\n")


def _issue_body(plan: Plan) -> str:
    lines = [issue_mark(plan.reproduction_id), _banner(plan), f"## Reproduction of: {plan.p.work['title']}", "",
             f"Paper: {doi_url(plan.p.doi)} · Reproduction ID: `{plan.reproduction_id}` · Track: "
             f"{plan.p.m.reprocrowd.track or 'unspecified (older package)'} · Status: **pilot, unreviewed**", "",
             RUNS_START, "_No run has been copied into this repository yet._", RUNS_END, "",
             "Each run is a comment below and an immutable pre-release in this repository; earlier runs are never changed.", "",
             "### Licences", "", "Each run's `licenses.json` lists the licence of every file. For the first run:", "", _licence_lines(plan), "",
             f"_Opened by ReproCrowd {__version__}._"]
    return _clip("\n".join(lines) + "\n")


# ---------- preview and submit ----------


def preview(pkg: Package, repo: str | None, exclude: list[str] | None, note: str, synthetic: bool) -> dict[str, Any]:
    """What a submission would send; contacts no GitHub endpoint."""
    plan = _plan(pkg, repo, exclude, note, synthetic)
    plan.reproduction_id = plan.reproduction_id or "repro-(assigned on first submission)"
    with tempfile.TemporaryDirectory() as tmp:
        _build_files(plan, Path(tmp))  # builds the ZIP once per run key; a later preview or the submission reuses it
    existing = next((x for x in pkg.load().reprocrowd.pilots if x.run_key == plan.run_key and x.repo == plan.repo), None)
    return {"repo": plan.repo, "reproduction_id": plan.reproduction_id, "run_key": plan.run_key, "tag": plan.tag,
            "issue_title": _issue_title(plan), "summary": summary_line(_summary(plan)),
            "assets": [{"name": a.name, "bytes": a.bytes, "sha256": a.sha256} for a in plan.files] + [{"name": "pilot.json", "bytes": None, "sha256": None}],
            "exclude": plan.exclude, "excluded": plan.excluded, "contents": contents(pkg, plan.p.work),
            "licenses": plan.licences, "existing": existing.model_dump(mode="json") if existing else None}


def _save(pkg: Package, rec: GithubPilot) -> GithubPilot:
    with pkg.edit() as m:
        m.reprocrowd.pilots = [x for x in m.reprocrowd.pilots if not (x.run_key == rec.run_key and x.repo == rec.repo)] + [rec]
    return rec


def submit(pkg: Package, *, token: str | None, repo: str | None, exclude: list[str] | None, note: str, synthetic: bool) -> GithubPilot:
    plan = _plan(pkg, repo, exclude, note, synthetic)
    gh = GitHub(token)
    with tempfile.TemporaryDirectory() as tmp:
        _build_files(plan, Path(tmp))  # size checks before anything is written on GitHub
        info = gh.json("GET", f"/repos/{plan.repo}")
        if info.get("private"):
            raise ApiError(403, "github_forbidden", f"{plan.repo} is private; pilots are public records, so submit them to a public repository")
        login = gh.json("GET", "/user")["login"]
        if plan.reproduction_id is None:
            with pkg.edit() as m:
                m.reprocrowd.reproduction_id = m.reprocrowd.reproduction_id or f"repro-{secrets.token_hex(5)}"
                plan.reproduction_id = m.reprocrowd.reproduction_id
        rec = next((x for x in pkg.load().reprocrowd.pilots if x.run_key == plan.run_key and x.repo == plan.repo), None)
        if rec is None:
            rec = GithubPilot(id=f"pil-{secrets.token_hex(4)}", repo=plan.repo, reproduction_id=plan.reproduction_id, run_key=plan.run_key,
                              status="partial", tag=plan.tag, archive_sha256=plan.p.m.reprocrowd.archive.sha256, exclude=plan.exclude,
                              excluded=plan.excluded, synthetic=synthetic, note=note, created_at=now())
        if rec.comment_url:  # the run's comment exists and is not rewritten: keep the note it shows
            plan.note = rec.note
        rec.note, rec.error = plan.note, None
        try:
            files_repo, branch = _files_repo(gh, info, login)
            rec.files_repo = files_repo
            _issue(gh, plan, rec, pkg)
            _release(gh, plan, rec, pkg, Path(tmp), files_repo, branch)
            _comment(gh, plan, rec, pkg)
        except Exception as e:
            rec.error = f"{e.code}: {e.message}" if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
            _save(pkg, rec)
            raise
        if rec.status == "partial":
            rec.status = "submitted"
        _save(pkg, rec)
    return refresh(pkg, [rec])[0]


def _files_repo(gh: GitHub, info: dict[str, Any], login: str) -> tuple[str, str]:
    """Where the run's files go: the submitter's fork (created if needed), or the repository itself for its owner."""
    central, branch = info["full_name"], info.get("default_branch") or "main"
    if info["owner"]["login"].lower() == login.lower():
        return central, branch
    r = gh.request("GET", f"/repos/{login}/{info['name']}", ok=(200, 404))
    fork = r.json() if r.status_code == 200 and (r.json().get("parent") or {}).get("full_name", "").lower() == central.lower() else None
    if fork is None:
        fork = gh.json("POST", f"/repos/{central}/forks", ok=(202,), json={"default_branch_only": True})
    name, branch = fork["full_name"], fork.get("default_branch") or branch
    for _ in range(FORK_WAIT):
        if gh.request("GET", f"/repos/{name}/branches/{branch}", ok=(200, 404)).status_code == 200:
            return name, branch
        time.sleep(2)
    raise ApiError(502, "github_error", f"GitHub is still creating the fork {name}; submit again in a minute")


def _issue(gh: GitHub, plan: Plan, rec: GithubPilot, pkg: Package) -> None:
    mark = issue_mark(plan.reproduction_id)
    known = rec.issue_number or next((x.issue_number for x in pkg.load().reprocrowd.pilots if x.repo == plan.repo and x.issue_number), None)
    issue = None
    if known:
        r = gh.request("GET", f"/repos/{plan.repo}/issues/{known}", ok=(200, 404, 410))
        issue = r.json() if r.status_code == 200 and mark in (r.json().get("body") or "") else None
    if issue is None:  # all issues: one is unlabelled until the Action has copied a run, and may be another login's
        issue = next((i for i in gh.paged(f"/repos/{plan.repo}/issues", {"state": "all"})
                      if "pull_request" not in i and mark in (i.get("body") or "")), None)
    if issue is None:
        issue = gh.json("POST", f"/repos/{plan.repo}/issues", json={"title": _issue_title(plan), "body": _issue_body(plan)})
    rec.issue_number, rec.issue_url = issue["number"], issue["html_url"]
    _save(pkg, rec)


def _release(gh: GitHub, plan: Plan, rec: GithubPilot, pkg: Package, tmp: Path, repo: str, branch: str) -> None:
    """The run's release in the files repository: published in a fork; a draft, for the Action to publish, in the pilots
    repository itself."""
    rel = next((r for r in gh.paged(f"/repos/{repo}/releases") if _is_run(r, plan.tag)), None)
    if rel is None:
        rel = gh.json("POST", f"/repos/{repo}/releases", json={
            "tag_name": plan.tag, "target_commitish": branch, "name": f"{plan.reproduction_id} run {plan.run_key} (pilot, unreviewed)",
            "body": f"Upload in progress.\n\n{release_mark(plan.tag)}\n", "draft": True, "prerelease": True, "make_latest": "false"})
    pj = tmp / "pilot.json"
    pj.write_bytes(_pilot_json(plan))
    files = plan.files + [Asset("pilot.json", pj, sha256_file(pj), pj.stat().st_size)]
    if rel.get("draft"):
        have = {a["name"]: a for a in rel.get("assets") or []}
        for a in files:
            old = have.get(a.name)
            if old and old.get("state") == "uploaded" and _same(old, a):
                continue
            if old:  # incomplete or different upload from an earlier attempt: replace it
                gh.request("DELETE", f"/repos/{repo}/releases/assets/{old['id']}", ok=(204, 404))
            with a.path.open("rb") as f:
                gh.request("POST", rel["upload_url"].split("{")[0], params={"name": a.name}, content=f, timeout=3600,
                           headers={"Content-Type": a.content_type, "Content-Length": str(a.bytes)})
        keep = {"tag_name": plan.tag, "target_commitish": branch}  # else GitHub drops the tag
        gh.request("PATCH", f"/repos/{repo}/releases/{rel['id']}", json=keep | {"body": _release_body(plan, files)})
        _verify(gh.json("GET", f"/repos/{repo}/releases/{rel['id']}"), files, plan.tag)
        if repo != plan.repo:
            rel = gh.json("PATCH", f"/repos/{repo}/releases/{rel['id']}", json=keep | {"draft": False})
            if rel.get("tag_name") != plan.tag:
                raise ApiError(502, "github_error", f"GitHub published the release under the tag {rel.get('tag_name')!r} instead of {plan.tag!r}")
    rel = gh.json("GET", f"/repos/{repo}/releases/{rel['id']}")
    urls = _verify(rel, files, plan.tag)
    rec.files_url = rel["html_url"]
    if rec.status != "published":  # once copied, the links point at the pilots repository's release
        rec.assets = [PilotAsset(name=a.name, bytes=a.bytes, sha256=a.sha256, url=urls[a.name]) for a in files]
    _save(pkg, rec)


def _same(remote: dict[str, Any], a: Asset) -> bool:
    """Only GitHub's SHA-256 digest counts as verification; an asset without one is not accepted."""
    return remote.get("size") == a.bytes and remote.get("digest") == f"sha256:{a.sha256}"


def _verify(rel: dict[str, Any], files: list[Asset], tag: str) -> dict[str, str]:
    have = {x["name"]: x for x in rel.get("assets") or []}
    bad = [a.name for a in files if a.name not in have or have[a.name].get("state") != "uploaded" or not _same(have[a.name], a)]
    if bad:
        state = "draft" if rel.get("draft") else "published"
        raise conflict("pilot_release_mismatch", f"the {state} release {tag} on GitHub doesn't hold the same files ({', '.join(bad)} missing "
                       "or different). Published releases are never changed: create the archive again (a new run key) and submit that",
                       {"release": rel.get("html_url"), "files": bad})
    return {a.name: have[a.name].get("browser_download_url") for a in files}


def _time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _comment(gh: GitHub, plan: Plan, rec: GithubPilot, pkg: Package) -> None:
    mark = run_mark(plan.reproduction_id, plan.run_key)
    found = next((c for c in gh.paged(f"/repos/{plan.repo}/issues/{rec.issue_number}/comments") if mark in (c.get("body") or "")), None)
    if found is None:
        found = gh.json("POST", f"/repos/{plan.repo}/issues/{rec.issue_number}/comments", json={"body": _comment_body(plan, rec)})
    rec.comment_url = found["html_url"]
    _save(pkg, rec)


def refresh(pkg: Package, recs: list[GithubPilot] | None = None) -> list[GithubPilot]:
    """Mark submitted runs ``published`` once the pilots repository's Action has copied them, or record its refusal
    (public reads, no token)."""
    recs = pkg.load().reprocrowd.pilots if recs is None else recs
    api = settings().github_api_url.rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"ReproCrowd/{__version__}"}
    for rec in recs:
        if rec.status != "submitted" or (rec.checked_at and (now() - rec.checked_at).total_seconds() < RECHECK_S):
            continue
        rec.checked_at = now()
        _save(pkg, rec)
        try:
            r = httpx.get(f"{api}/repos/{rec.repo}/releases/tags/{rec.tag}", timeout=10, headers=headers)
            if r.status_code != 200 and rec.issue_number and (reason := _refusal(api, rec, headers)) and reason != rec.error:
                rec.error = reason
                _save(pkg, rec)
        except httpx.HTTPError:
            continue
        if r.status_code != 200 or r.json().get("draft"):
            continue
        rel = r.json()
        urls = {a["name"]: a.get("browser_download_url") for a in rel.get("assets") or []}
        version = next((int(w[1:]) for w in (rel.get("name") or "").split() if w[:1] == "v" and w[1:].isdigit()), None)
        rec.status, rec.version, rec.release_url, rec.error = "published", version, rel.get("html_url"), None
        rec.published_at = _time(rel.get("published_at"))
        rec.assets = [a.model_copy(update={"url": urls.get(a.name, a.url)}) for a in rec.assets]
        _save(pkg, rec)
    return recs


def _refusal(api: str, rec: GithubPilot, headers: dict[str, str]) -> str | None:
    """The Action's reply refusing this run, if any: only comments by the Action, made since the run was submitted."""
    since = rec.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url: str | None = f"{api}/repos/{rec.repo}/issues/{rec.issue_number}/comments?per_page=100&since={since}"
    for _ in range(5):
        if url is None:
            return None
        r = httpx.get(url, timeout=10, headers=headers)
        if r.status_code != 200:
            return None
        for c in r.json():
            body = c.get("body") or ""
            if refused_mark(rec.tag) in body and (c.get("user") or {}).get("login") == ACTION_LOGIN:
                return "the pilots repository refused this run: " + body.split("**not copied**: ", 1)[-1].strip()
        url = r.links.get("next", {}).get("url")
    return None
