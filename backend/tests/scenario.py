"""Dev-only offline paper-through-API harness; never invokes a real agent or deposits anything.

Run from the project root: cd backend && uv run python tests/scenario.py
The stable projection is the regression contract; timestamps, absolute paths, job ids,
LLM timings and report bytes are intentionally absent. Every run retains its artifacts.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import respx
from fastapi.testclient import TestClient

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
DOI = "10.5555/reprocrowd-offline-fixture"
PAPER_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4359000/fullTextXML"


def run_scenario(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    previous = [p for p in sorted(output.glob("scenario-*/result.json")) if "projection" in json.loads(p.read_text())]
    folder = output / ("scenario-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir()
    exe = sys.executable.replace("\\", "/")
    def path(p): return p.resolve().as_posix()
    config = folder / "backend.toml"
    config.write_text(f'''[server]
registry = "{path(folder / 'registry.json')}"
[openalex]
api_key = ""
[llm]
default_profile = "fixture"
[llm.profiles.fixture]
argv = ["{exe}", "{path(HERE / 'fake_llm.py')}", "scenario"]
[agent]
default_profile = "fixture"
[agent.profiles.fixture]
argv = ["{exe}", "{path(HERE / 'fake_agent.py')}", "python-ok", "--tools", "{{tools}}"]
''')
    old_config = os.environ.get("REPROCROWD_CONFIG")
    stages = []
    result = {"schema_version": "1", "kind": "offline-contract-scenario", "live_model": False,
              "folder": str(folder.resolve()), "stages": stages}
    started = time.monotonic()
    from reprocrowd.config import settings
    try:
        os.environ["REPROCROWD_CONFIG"] = str(config.resolve())
        settings.cache_clear()
        from reprocrowd.api.app import app
        with respx.mock(assert_all_called=False) as network, TestClient(app) as client:
            work = {"id": "https://openalex.org/W1", "title": "Offline contract fixture (not a research result)",
                    "publication_year": 2026, "ids": {"doi": f"https://doi.org/{DOI}", "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/4359000"},
                    "authorships": [{"author": {"display_name": "Fixture Author"}}]}
            network.get(url__regex=r"https://api\.openalex\.org/works/doi:.*").mock(return_value=httpx.Response(200, json=work))
            network.get(PAPER_URL).mock(return_value=httpx.Response(200, content=(FIXTURES / "scenario.jats.xml").read_bytes()))
            def request(stage, method, url, body=None):
                start = time.monotonic()
                response = client.request(method, url, json=body)
                assert response.is_success, f"{stage}: {response.status_code} {response.text}"
                stages.append({"stage": stage, "status": response.status_code, "duration_ms": round((time.monotonic()-start)*1000)})
                return response.json()
            package = request("create", "POST", "/v1/packages", {"path": path(folder / "package"), "identifier": DOI, "fetch_metadata": True})
            p = f"/v1/packages/{package['id']}"
            candidates = request("candidates", "GET", p + "/fulltexts/candidates")
            candidate = next(c for c in candidates["candidates"] if "Europe PMC" in c["label"])
            fulltext = request("retrieve", "POST", p + "/fulltexts", {"candidate": candidate["id"]})
            extraction = request("parse", "POST", p + "/extractions", {"method": "parse", "fulltext": fulltext["name"]})
            request("input", "POST", p + "/inputs", {"path": path(FIXTURES / "data.csv"), "note": "synthetic fixture, one row per group"})
            body = {"extraction": extraction["name"]}
            request("target-preview", "POST", p + "/targets/runs:preview", body)
            targets = request("targets", "POST", p + "/targets/runs", body)
            assert not targets["rejected"]
            request("review-target", "PATCH", p + "/targets/T001", {"note": "Reviewed synthetic fixture target; no scientific judgment implied."})
            actions = request("actions", "POST", p + "/actions/runs", body)
            assert len(actions["entries"]) == 2
            request("script", "PUT", p + "/script", {"template": "python"})
            request("agent-preview", "POST", p + "/jobs:preview", {"type": "codegen", "allow_exec": False})
            def job(stage, body):
                start = time.monotonic()
                record = request(stage, "POST", p + "/jobs", body)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    record = client.get(p + "/jobs/" + record["id"]).json()
                    if record.get("finished_at"):
                        stages[-1]["duration_ms"] = round((time.monotonic() - start) * 1000)
                        stages[-1]["outcome"] = record["status"]
                        assert record["status"] == "succeeded", record
                        return record
                    time.sleep(.025)
                raise TimeoutError(stage)
            job("codegen", {"type": "codegen", "allow_exec": False})
            run = job("run", {"type": "run"})
            comparison = request("compare", "GET", p + "/comparison")
            evidence = request("evidence", "GET", p + "/evidence")
            report = request("report", "PUT", p + "/report", {"threshold": 5})
            archive = request("archive", "PUT", p + "/archive")
            preview = request("deposit-preview", "POST", p + "/deposits:preview", {"creators": [{"name": "Fixture Author"}]})
            validation = request("validation", "GET", p + "/validation")
            assert validation["valid"], validation
            assert comparison["summary"] == {"match": 3} and evidence["stale"] == [] and evidence["results_match_run"]
            assert report["matched"] == 3 and report["within"] == 0
            root = Path(package["path"])
            # Shared artifacts, independent of where/when a run happens. Mutable diagnostic
            # records are tested by their selected meaning rather than by byte equality.
            stable_files = ["inputs/data.csv", "analysis.py", "analysis_plan.json", "results_schema.json", "outputs/results.json"]
            hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in stable_files}
            projection = {"schema_version": "1", "track": package["track"], "stages": [{"stage": s["stage"], "status": s["status"], **({"outcome": s["outcome"]} if "outcome" in s else {})} for s in stages],
                          "hashes": hashes, "policy": comparison["policy"], "rounding_mode": comparison["rounding_mode"],
                          "summary": comparison["summary"], "rows": comparison["rows"], "settings": comparison["settings"],
                          "run": {k: run[k] for k in ("status", "exit_code")}, "stale": evidence["stale"], "results_match_run": evidence["results_match_run"],
                          "target_evidence": evidence["targets"], "valid": validation["valid"], "deposit_metadata": preview["metadata"]}
            result.update(projection=projection, archive=str((root / archive["path"]).resolve()), report=str((root / report["path"]).resolve()),
                          assertions={"all_targets_match": True, "evidence_current": True, "package_valid": True, "no_publication": True},
                          git_head=subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip(),
                          git_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=HERE, capture_output=True, text=True).stdout.strip()))
            assert all(call.request.method == "GET" for call in network.calls), "Only mocked metadata/fulltext reads allowed"
    except Exception as error:
        result.update(error=f"{type(error).__name__}: {error}", duration_ms=round((time.monotonic() - started) * 1000), assertions={"completed": False})
        (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        raise
    finally:
        if old_config is None:
            os.environ.pop("REPROCROWD_CONFIG", None)
        else:
            os.environ["REPROCROWD_CONFIG"] = old_config
        settings.cache_clear()
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    stable = json.dumps(result["projection"], indent=2, sort_keys=True)
    result["projection_sha256"] = hashlib.sha256(stable.encode()).hexdigest()
    diff = "First run: no previous projection.\n"
    if previous:
        before = json.loads(previous[-1].read_text())["projection"]
        result["previous"] = str(previous[-1].resolve())
        result["same_as_previous"] = before == result["projection"]
        diff = "".join(difflib.unified_diff((json.dumps(before, indent=2, sort_keys=True) + "\n").splitlines(True), (stable+"\n").splitlines(True), fromfile=str(previous[-1]), tofile="current")) if before != result["projection"] else "Stable projection unchanged.\n"
    (folder / "projection.json").write_text(stable + "\n")
    (folder / "diff.txt").write_text(diff)
    (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "--out", type=Path, default=HERE.parent.parent / "test-output" / "paper-scenario")
    args = parser.parse_args()
    result = run_scenario(args.output)
    print(json.dumps({k: result[k] for k in ("folder", "report", "archive", "assertions")} | {"same_as_previous": result.get("same_as_previous")}, indent=2))
