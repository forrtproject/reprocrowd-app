"""GitHub pilot submissions: a reviewable, public record of a reproduction before anything goes to Zenodo.

Identity:
  reproduction  ``reproduction_id`` (repro-<10 hex>), stored in the package on the first submission. One GitHub issue
                per reproduction, found by a hidden marker; the paper's DOI is metadata, not identity, so independent
                reproductions of one paper get separate issues.
  run           ``run_key`` = sha256(archive sha256 | left-out groups)[:12]. One pre-release per run, tagged
                <reproduction_id>-<run_key>, with the files; one comment per run on the issue. ``version`` (v1, v2, …) is
                assigned when the release is created and read back from it afterwards.
Submitting is idempotent per run key: every step first looks for what an earlier attempt made (local record, then
the issue/release/comment markers on GitHub) and only creates what is missing. A failure leaves a ``partial`` record;
submitting again resumes. Published releases are never changed or deleted; with immutable releases enabled on the
repository GitHub enforces that.

Preconditions are those of a Zenodo deposit (``database.Prepared`` + the paper's text retrieved for its DOI): the
recorded comparison (refused when stale), a run that succeeded and an up-to-date archive. The uploaded ZIP is the
archive, or the copy without the left-out groups, built once per run key and kept under archive/ so a retry uploads
the same bytes. report.html, comparison.json, execution.json and licenses.json are copied out of that ZIP.

Files go to release assets (REST upload, < 2 GiB each); GitHub has no API for issue attachments. The token is passed
per request and never stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import httpx

from . import __version__, licensing
from .config import settings
from .database import Prepared
from .errors import ApiError, conflict, invalid
from .package import Package, sha256_file
from .report import ARCHIVE_DIR, archive_path
from .schemas.common import now
from .schemas.manifest import GithubPilot, PilotAsset
from .zenodo import GROUPS, _check_published_paper, _group, _publication_zip, contents

LABEL = "reprocrowd-pilot"
MAX_ASSET = 2 * 1024**3 - 1  # GitHub: each release asset must be under 2 GiB
BODY_LIMIT = 65_000  # GitHub's issue/comment body limit is 65,536 characters
ROW_LIMIT = 60
SUMMARY_RE = re.compile(r"<!-- reprocrowd-summary: (\{.*?\}) -->")
DISCLAIMER = ("**Pilot record — unreviewed.** Made with ReproCrowd by recreating the analysis from the article's text and "
              "published data. Agreement under the recorded comparison settings shows that this code produced the "
              "reported numbers from these inputs. It is not peer review, a certificate of the study's validity, or an endorsement. The authors' "
              "code was left out of the agent's context; that is not verified blinding.")
SYNTHETIC = ("**SYNTHETIC test fixture — not a research result.** The paper, data and DOI are invented for testing "
             "the publication workflow.")
EXTRACT = {"report.html": "report.html", "comparison.json": "comparison.json", "evidence/execution.json": "execution.json",
           licensing.FILE: licensing.FILE}
TYPES = {".zip": "application/zip", ".html": "text/html", ".json": "application/json"}


def issue_mark(rid: str) -> str:
    return f"<!-- reprocrowd-pilot:{rid} -->"


def run_mark(rid: str, key: str) -> str:
    return f"<!-- reprocrowd-pilot-run:{rid}:{key} -->"


def release_mark(tag: str) -> str:
    """In every release body: GitHub replaces a draft's tag with an ``untagged-…`` placeholder when the draft is
    edited without tag_name, so releases are also matched by this marker, not only by their tag."""
    return f"<!-- reprocrowd-pilot-release:{tag} -->"


def _is_run(r: dict[str, Any], tag: str) -> bool:
    return r.get("tag_name") == tag or release_mark(tag) in (r.get("body") or "")


def _of_reproduction(r: dict[str, Any], rid: str) -> bool:
    return (r.get("tag_name") or "").startswith(f"{rid}-") or f"<!-- reprocrowd-pilot-release:{rid}-" in (r.get("body") or "")


# ---------- GitHub REST ----------


class GitHub:
    def __init__(self, token: str | None, api: str | None = None):
        tok = token or settings().github_token
        if not tok:
            raise ApiError(400, "github_token_missing", "a GitHub token is needed (github_token; for rc: $REPROCROWD_GITHUB_TOKEN or `gh auth login`)")
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
                           "Submitting needs write access to the repository; a fine-grained token needs Contents: write, "
                           "Issues: write and Metadata: read for it")
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
    """Can this token submit pilots to the repository? (One read; nothing is written.)"""
    repo = repo or settings().github_pilots_repo
    gh = GitHub(token)
    info = gh.json("GET", f"/repos/{repo}")
    perms = info.get("permissions") or {}
    return {"repo": info.get("full_name", repo), "url": info.get("html_url"), "private": info.get("private"),
            "can_submit": bool(perms.get("push")), "default_branch": info.get("default_branch"),
            "immutable_releases": _immutable(gh, repo)}


def _immutable(gh: GitHub, repo: str) -> bool | None:
    try:
        return bool(gh.json("GET", f"/repos/{repo}/immutable-releases").get("enabled"))
    except ApiError:
        return None


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
    branch: str = "main"
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
    big = [a.name for a in plan.files if a.bytes > MAX_ASSET]
    if big:
        raise invalid("pilot_asset_too_large", f"GitHub release assets must be under 2 GiB; too large: {', '.join(big)}. "
                                               "Leave out the inputs (exclude: inputs) or deposit on Zenodo instead")


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


def _summary(plan: Plan, version: int | None) -> dict[str, Any]:
    cmp = plan.p.cmp
    numeric = sum(r.type != "figure" for r in cmp.rows)
    return {"version": version, "run_key": plan.run_key, "match": cmp.summary.get("match", 0), "within": cmp.summary.get("within", 0),
            "numeric": numeric, "threshold": cmp.settings.threshold, "rounding": cmp.settings.rounding, "policy": cmp.policy}


def _summary_line(s: dict[str, Any]) -> str:
    if not s["numeric"]:
        return "no results to compare automatically (figures need visual comparison)"
    rounding = s.get("rounding", "reported")  # older release notes don't record it
    how = "numbers at printed precision" if rounding == "reported" else f"numbers rounded to {rounding} decimals"
    return (f"{s['match']} of {s['numeric']} match ({how}; text exactly); {s['within']} more within "
            f"{s['threshold']:g} % (exploratory)")


def _pilot_json(plan: Plan, version: int) -> bytes:
    """The machine-readable record of the run (CC0). Deterministic for a run key and version, so a retry matches."""
    cmp, m = plan.p.cmp, plan.p.m
    record = {
        "schema": "reprocrowd-pilot/1",
        "status": "pilot-unreviewed",
        "statement": DISCLAIMER.replace("**", ""),
        "synthetic": plan.synthetic,
        "reproduction_id": plan.reproduction_id,
        "version": version,
        "run_key": plan.run_key,
        "tag": plan.tag,
        "paper": {"doi": plan.p.doi},
        "track": m.reprocrowd.track,
        "comparison": {"policy": cmp.policy, "rounding_mode": cmp.rounding_mode, "settings": cmp.settings.model_dump(mode="json"),
                       "summary": cmp.summary, "numeric_targets": _summary(plan, version)["numeric"]},
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
    title = f"Pilot reproduction: {plan.p.work['title']} (doi:{plan.p.doi})"
    return ("[SYNTHETIC] " if plan.synthetic else "") + title[:240]


def _banner(plan: Plan) -> str:
    return (f"> [!CAUTION]\n> {SYNTHETIC}\n\n" if plan.synthetic else "") + f"> [!WARNING]\n> {DISCLAIMER}\n"


def _release_body(plan: Plan, version: int, assets: list[Asset]) -> str:
    s = _summary(plan, version)
    return _clip(f"{_banner(plan)}\n**Run v{version}** of reproduction `{plan.reproduction_id}` (run key `{plan.run_key}`) of "
                 f"https://doi.org/{plan.p.doi}\n\n{_summary_line(s)}. Policy `{s['policy']}`.\n\n{_evidence_line(plan)}\n{_assets_table(assets)}\n\n"
                 f"{_licence_lines(plan)}\n\n{release_mark(plan.tag)}\n<!-- reprocrowd-summary: {json.dumps(s, sort_keys=True)} -->\n")


def _comment_body(plan: Plan, rec: GithubPilot) -> str:
    s = _summary(plan, rec.version)
    note = f"\n**Submitter's note:** {' '.join(plan.note.split())[:2000]}\n" if plan.note else ""
    return _clip(f"{run_mark(plan.reproduction_id, plan.run_key)}\n### Run v{rec.version} · `{plan.run_key}`\n\n"
                 f"{_summary_line(s)}. Policy `{s['policy']}`, rounding `{plan.p.cmp.rounding_mode}`.\n\n{_evidence_line(plan)}{note}\n"
                 f"Files: [release {rec.tag}]({rec.release_url}) (pre-release; earlier runs stay in their own releases).\n\n"
                 f"{_assets_table(rec.assets)}\n\n<details><summary>Comparison ({len(plan.p.cmp.rows)} targets)</summary>\n\n"
                 f"{_comparison_table(plan)}\n\n</details>\n\n{_licence_lines(plan)}\n\n_Status: pilot, unreviewed. Reviewers: "
                 "reply here, or open a new issue for a separate reproduction._\n")


def _issue_body(plan: Plan, runs: list[dict[str, Any]], repo_url: str) -> str:
    latest = max(runs, key=lambda r: r["version"]) if runs else None
    lines = [issue_mark(plan.reproduction_id), _banner(plan),
             f"## Reproduction of: {plan.p.work['title']}", "",
             f"Paper: https://doi.org/{plan.p.doi} · Reproduction ID: `{plan.reproduction_id}` · Track: "
             f"{plan.p.m.reprocrowd.track or 'unspecified (older package)'} · Status: **pilot, unreviewed**", ""]
    if latest:
        lines += [f"### Latest run: v{latest['version']} ({latest['date']})", "", f"{_summary_line(latest)}. Policy `{latest['policy']}`.",
                  f"Files and SHA-256 hashes: [{latest['tag']}]({latest['url']}) · machine-readable record: `pilot.json`.", ""]
    lines += ["### All runs", "", "| Run | Date | Exact | Within | Release |", "|---|---|---:|---:|---|"]
    for r in sorted(runs, key=lambda r: -r["version"])[:100]:
        lines.append(f"| v{r['version']} | {r['date']} | {r['match']}/{r['numeric']} | {r['within']} | [{r['tag']}]({r['url']}) |")
    lines += ["", "Each run is a separate pre-release; earlier runs are kept and never changed. A comment below records each run.", "",
              "### Licences", "", "Each run's `licenses.json` lists the licence of every file. For the run submitted last:", "", _licence_lines(plan), "",
              f"_Updated by ReproCrowd {__version__}. Releases: {repo_url}/releases?q={plan.reproduction_id}_"]
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
            "issue_title": _issue_title(plan), "labels": _labels(plan), "summary": _summary_line(_summary(plan, None)),
            "assets": [{"name": a.name, "bytes": a.bytes, "sha256": a.sha256} for a in plan.files] + [{"name": "pilot.json", "bytes": None, "sha256": None}],
            "exclude": plan.exclude, "excluded": plan.excluded, "contents": contents(pkg, plan.p.work),
            "licenses": plan.licences, "existing": existing.model_dump(mode="json") if existing else None}


def _labels(plan: Plan) -> list[str]:
    return [LABEL, "unreviewed"] + (["synthetic"] if plan.synthetic else [])


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
        plan.branch = info.get("default_branch") or "main"
        if info.get("private"):
            raise ApiError(403, "github_forbidden", f"{plan.repo} is private; pilots are public records, so submit them to a public repository")
        if not (info.get("permissions") or {}).get("push"):
            raise ApiError(403, "github_forbidden", f"this GitHub account can't write to {plan.repo}; pilot submissions create issues and "
                                                    "releases, which needs write access (ask the maintainers, or configure your own repository)")
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
            _issue(gh, plan, rec, pkg)
            _release(gh, plan, rec, pkg, Path(tmp))
            _comment(gh, plan, rec, pkg)
            runs = _runs(gh, plan)
            gh.request("PATCH", f"/repos/{plan.repo}/issues/{rec.issue_number}", json={"body": _issue_body(plan, runs, info.get("html_url", ""))})
        except Exception as e:
            rec.error = f"{e.code}: {e.message}" if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
            _save(pkg, rec)
            raise
        rec.status = "published"
        return _save(pkg, rec)


def _issue(gh: GitHub, plan: Plan, rec: GithubPilot, pkg: Package) -> None:
    mark = issue_mark(plan.reproduction_id)
    known = rec.issue_number or next((x.issue_number for x in pkg.load().reprocrowd.pilots if x.repo == plan.repo and x.issue_number), None)
    issue = None
    if known:
        r = gh.request("GET", f"/repos/{plan.repo}/issues/{known}", ok=(200, 404, 410))
        issue = r.json() if r.status_code == 200 and mark in (r.json().get("body") or "") else None
    if issue is None:
        issue = next((i for i in gh.paged(f"/repos/{plan.repo}/issues", {"labels": LABEL, "state": "all"})
                      if "pull_request" not in i and mark in (i.get("body") or "")), None)
    if issue is None:
        _ensure_labels(gh, plan)
        body = f"{mark}\n{_banner(plan)}\n_The first run is being uploaded; this description is replaced when it is complete._\n"
        issue = gh.json("POST", f"/repos/{plan.repo}/issues", json={"title": _issue_title(plan), "body": body, "labels": _labels(plan)})
    rec.issue_number, rec.issue_url = issue["number"], issue["html_url"]
    _save(pkg, rec)


LABELS = {LABEL: ("1d76db", "A ReproCrowd pilot record (one issue per reproduction)"),
          "unreviewed": ("fbca04", "Not reviewed: a numeric match is not a scientific certificate"),
          "synthetic": ("cccccc", "Synthetic test fixture of the publication workflow, not research")}


def _ensure_labels(gh: GitHub, plan: Plan) -> None:
    """A configured repository of one's own may lack the labels; create those that are missing."""
    for name in _labels(plan):
        if gh.request("GET", f"/repos/{plan.repo}/labels/{name}", ok=(200, 404)).status_code == 404:
            color, desc = LABELS[name]
            gh.request("POST", f"/repos/{plan.repo}/labels", ok=(201, 422), json={"name": name, "color": color, "description": desc})


def _versions(release: dict[str, Any]) -> int | None:
    m = re.search(r"\bv(\d+)\b", release.get("name") or "")
    return int(m.group(1)) if m else None


def _release(gh: GitHub, plan: Plan, rec: GithubPilot, pkg: Package, tmp: Path) -> None:
    mine = [r for r in gh.paged(f"/repos/{plan.repo}/releases") if _of_reproduction(r, plan.reproduction_id)]
    rel = next((r for r in mine if _is_run(r, plan.tag)), None)
    if rel is None:
        version = 1 + max((_versions(r) or 0 for r in mine), default=0)
        rel = gh.json("POST", f"/repos/{plan.repo}/releases", json={
            "tag_name": plan.tag, "target_commitish": plan.branch, "name": f"{plan.reproduction_id} v{version} (pilot, unreviewed)",
            "body": f"Upload in progress.\n\n{release_mark(plan.tag)}\n", "draft": True, "prerelease": True, "make_latest": "false"})
    version = _versions(rel) or rec.version
    if version is None:
        raise conflict("pilot_release_mismatch", f"release {plan.tag} exists but its name has no version; fix it on GitHub")
    rec.version, rec.release_id = version, rel["id"]
    _save(pkg, rec)
    pj = tmp / "pilot.json"
    pj.write_bytes(_pilot_json(plan, version))
    files = plan.files + [Asset("pilot.json", pj, sha256_file(pj), pj.stat().st_size)]
    if rel.get("draft"):
        have = {a["name"]: a for a in rel.get("assets") or []}
        for a in files:
            old = have.get(a.name)
            if old and old.get("state") == "uploaded" and _same(old, a):
                continue
            if old:  # incomplete or different upload from an earlier attempt: replace it
                gh.request("DELETE", f"/repos/{plan.repo}/releases/assets/{old['id']}", ok=(204, 404))
            upload = rel["upload_url"].split("{")[0]
            with a.path.open("rb") as f:
                gh.request("POST", upload, params={"name": a.name}, content=f, timeout=3600,
                           headers={"Content-Type": a.content_type, "Content-Length": str(a.bytes)})
        keep = {"tag_name": plan.tag, "target_commitish": plan.branch}  # else GitHub drops the tag
        gh.request("PATCH", f"/repos/{plan.repo}/releases/{rel['id']}", json=keep | {"body": _release_body(plan, version, files)})
        _verify(gh.json("GET", f"/repos/{plan.repo}/releases/{rel['id']}"), files, plan.tag)
        rel = gh.json("PATCH", f"/repos/{plan.repo}/releases/{rel['id']}", json=keep | {"draft": False})
        if rel.get("tag_name") != plan.tag:
            raise ApiError(502, "github_error", f"GitHub published the release under the tag {rel.get('tag_name')!r} instead of {plan.tag!r}")
    rel = gh.json("GET", f"/repos/{plan.repo}/releases/{rel['id']}")
    urls = _verify(rel, files, plan.tag)
    rec.release_url = rel["html_url"]
    rec.published_at = _time(rel.get("published_at")) or rec.published_at
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


def _runs(gh: GitHub, plan: Plan) -> list[dict[str, Any]]:
    """The published runs of this reproduction, from GitHub (so runs submitted elsewhere count too)."""
    out = []
    for r in gh.paged(f"/repos/{plan.repo}/releases"):
        if r.get("draft") or not _of_reproduction(r, plan.reproduction_id):
            continue
        m = SUMMARY_RE.search(r.get("body") or "")
        s = json.loads(m.group(1)) if m else {}
        version = _versions(r)
        if version is None:
            continue
        out.append({"match": 0, "within": 0, "numeric": 0, "threshold": 0, "policy": "?", **s, "version": version,
                    "tag": r["tag_name"], "url": r["html_url"], "date": (r.get("published_at") or "")[:10]})
    return out
