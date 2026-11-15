"""Dev-only LIVE demo of GitHub pilot submissions with the SYNTHETIC scenario fixture. It writes to GitHub.

  cd backend && uv run python tests/pilot_demo.py --repo forrtproject/reprocrowd-pilots

Builds the offline scenario package (synthetic paper, CSV and DOI 10.5555/…; scholarly HTTP mocked, no model calls),
then through the real API: submit (v1) → submit again (no duplicates) → rerun → refused as report_stale → new report
and ZIP → submit (v2) → submit again (no duplicates) → download every published file and check its SHA-256.
Only api.github.com, uploads.github.com and the public download URLs pass through. The token comes from
$REPROCROWD_GITHUB_TOKEN or `gh auth token`; it is sent with requests only and never written anywhere.
Writes test-output/pilot-demo/demo-<time>/result.json with the links, counts and hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from scenario import DOI, run_scenario  # noqa: E402


def token() -> str:
    t = os.environ.get("REPROCROWD_GITHUB_TOKEN") or subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not t:
        sys.exit("no GitHub token: set REPROCROWD_GITHUB_TOKEN or run `gh auth login`")
    return t


def github_state(repo: str, tok: str, rid: str | None) -> dict:
    """Counts of what the reproduction has on GitHub (issues with its marker, their comments, releases, assets)."""
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    issues = [i for i in httpx.get(f"https://api.github.com/repos/{repo}/issues", params={"labels": "reprocrowd-pilot", "state": "all", "per_page": 100}, headers=h).json()
              if rid and f"<!-- reprocrowd-pilot:{rid} -->" in (i.get("body") or "")]
    comments = sum(len(httpx.get(i["comments_url"], params={"per_page": 100}, headers=h).json()) for i in issues)
    releases = [r for r in httpx.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 100}, headers=h).json()
                if rid and r["tag_name"].startswith(rid + "-")]
    return {"issues": len(issues), "comments": comments, "releases": len(releases), "assets": sum(len(r["assets"]) for r in releases)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--output", type=Path, default=HERE.parent.parent / "test-output" / "pilot-demo")
    args = ap.parse_args()
    tok = token()
    folder = args.output / ("demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    scenario = run_scenario(folder / "scenario")
    os.environ["REPROCROWD_CONFIG"] = str(Path(scenario["folder"]) / "backend.toml")
    from reprocrowd.config import settings
    settings.cache_clear()
    from reprocrowd.api.app import app
    log: list[dict] = []
    out = {"schema_version": "1", "kind": "live-github-pilot-demo", "synthetic": True, "repo": args.repo, "doi": DOI, "steps": log}
    work = {"id": "https://openalex.org/W1", "title": "Offline contract fixture (not a research result)", "publication_year": 2026,
            "ids": {"doi": f"https://doi.org/{DOI}"}, "authorships": [{"author": {"display_name": "Fixture Author"}}]}
    with respx.mock(assert_all_called=False) as net, TestClient(app) as client:
        net.get(url__regex=r"https://api\.openalex\.org/works/doi:.*").mock(return_value=httpx.Response(200, json=work))
        for host in ("api.github.com", "uploads.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"):
            net.route(host=host).pass_through()
        pid = client.get("/v1/packages").json()[0]["id"]
        P = f"/v1/packages/{pid}"
        body = {"github_token": tok, "repo": args.repo, "synthetic": True}

        def step(name, method, url, payload=None, expect=200):
            t = time.monotonic()
            r = client.request(method, url, json=payload)
            assert r.status_code == expect, f"{name}: {r.status_code} {r.text}"
            data = r.json()
            rid = client.get(P + "/manifest").json()["reprocrowd"].get("reproduction_id")
            log.append({"step": name, "status": r.status_code, "seconds": round(time.monotonic() - t, 1),
                        **({"code": data.get("code")} if expect >= 400 else {}), "github": github_state(args.repo, tok, rid)})
            print(json.dumps(log[-1]), flush=True)
            return data

        v1 = step("submit v1", "POST", P + "/pilots", body | {"note": "Synthetic demo: first run."})
        again = step("retry v1 (same archive)", "POST", P + "/pilots", body | {"note": "Synthetic demo: first run."})
        assert again["id"] == v1["id"] and log[-1]["github"] == log[-2]["github"]
        job = client.post(P + "/jobs", json={"type": "run"}).json()
        deadline = time.monotonic() + 120
        while not (job := client.get(f"{P}/jobs/{job['id']}").json()).get("finished_at"):
            assert time.monotonic() < deadline, "the rerun did not finish within 120 s"
            time.sleep(.05)
        assert job["status"] == "succeeded", job
        refused = step("submit after a new run without a new report", "POST", P + "/pilots", body, expect=409)
        assert refused["code"] == "report_stale", refused
        assert log[-1]["github"] == log[-2]["github"]
        client.put(P + "/report", json={"threshold": 5})
        client.put(P + "/archive")
        v2 = step("submit v2", "POST", P + "/pilots", body | {"note": "Synthetic demo: second run (re-executed analysis)."})
        again = step("retry v2 (same archive)", "POST", P + "/pilots", body | {"note": "Synthetic demo: second run (re-executed analysis)."})
        assert again["id"] == v2["id"] and log[-1]["github"] == log[-2]["github"]
        assert v2["issue_number"] == v1["issue_number"] and (v1["version"], v2["version"]) == (1, 2)
        checked = []
        for rec in (v1, v2):
            for a in rec["assets"]:
                data = httpx.get(a["url"], follow_redirects=True, timeout=120).content
                checked.append({"url": a["url"], "sha256": a["sha256"], "ok": hashlib.sha256(data).hexdigest() == a["sha256"]})
        assert all(c["ok"] for c in checked), checked
        out.update(issue=v1["issue_url"], runs=[{k: r[k] for k in ("version", "run_key", "tag", "release_url", "comment_url", "archive_sha256")} for r in (v1, v2)],
                   downloads_verified=len(checked), downloads=checked)
    (folder / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("issue", "runs", "downloads_verified")}, indent=2))


if __name__ == "__main__":
    main()
