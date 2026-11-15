"""Dev-only paper-through-API harness on a synthetic paper and CSV. Never deposits or uploads anything.

Offline (default; fake extraction and fake agent, no model calls):
    cd backend && uv run python tests/scenario.py
Live, with the CLI's own sign-in (explicit; uses the subscription or, for OpenCode API providers, metered credit):
    cd backend && uv run python tests/scenario.py --live --llm claude --agent claude
    cd backend && uv run python tests/scenario.py --live --llm codex --agent codex --model gpt-6.1-terra --effort low --allow-exec
    cd backend && uv run python tests/scenario.py --live --llm opencode --agent opencode --model deepseek/deepseek-v4-pro \\
        --spend-ledger ~/private/ledger.json --max-usd 0.30
Profiles come from the shipped defaults, or from a backend.toml saved through Settings (--profiles PATH).

Every run keeps its folder (package, report, archive, result.json, projection.json, diff.txt). The offline projection
is the regression contract and must equal the previous offline run's. A live projection records what the provider
did (targets and their printed tokens, statuses, checks); differences between live runs are informational. Scholarly
HTTP is mocked in every mode. The live model reads the synthetic paper and must write an analysis that computes from
inputs/data.csv: a mutation check reruns the generated script on a changed CSV and compares with stdlib means.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
import respx
from fastapi.testclient import TestClient

import live

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
DOI = "10.5555/reprocrowd-offline-fixture"
PAPER_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4359000/fullTextXML"
MUTATED_CSV = "group,rt\nA,510.0\nA,520.0\nB,500.0\nB,496.0\n"  # means 515.0 and 498.0, difference 17.0


def mutation_check(root: Path, command: str) -> dict:
    """Rerun the generated script on a CSV with several rows per group. Values copied from the paper (512.40, 14.30)
    cannot pass; values computed from the data become 515.0 / 498.0 / 17.0."""
    with tempfile.TemporaryDirectory(prefix="scratch-reprocrowd-mutation-") as tmp:
        work = Path(tmp) / "pkg"
        shutil.copytree(root, work, ignore=shutil.ignore_patterns(".git", "outputs", "jobs", "llm-calls", "report*", "evidence"))
        (work / "inputs" / "data.csv").write_text(MUTATED_CSV)
        from reprocrowd.jobs import job_env  # the user's interpreters, as a run job sees them (not this virtualenv)
        proc = subprocess.run(command, shell=True, cwd=work, capture_output=True, text=True, timeout=120, env=job_env())
        expected = {515.0, 498.0, 17.0, -17.0}
        try:
            results = json.loads((work / "outputs" / "results.json").read_text())["results"]
        except (OSError, ValueError, KeyError) as e:
            return {"passed": False, "exit_code": proc.returncode, "error": f"no results: {e}", "stderr": proc.stderr[-1000:]}
        numbers = []
        for r in results:
            if isinstance(r.get("value"), (int, float)):
                numbers.append(r["value"])
            for row in r.get("table") or []:
                numbers += [c for c in row if isinstance(c, (int, float)) and not isinstance(c, bool)]
        computed = [n for n in numbers if any(abs(n - e) < 1e-6 for e in expected)]
        stale = [n for n in numbers if any(abs(n - old) < 1e-6 for old in (512.4, 498.1, 14.3))]
        return {"passed": proc.returncode == 0 and bool(computed) and not stale, "exit_code": proc.returncode,
                "values": numbers, "computed_from_mutated_data": len(computed), "unchanged_paper_values": len(stale)}


def run_scenario(output: Path, *, llm: str = "fixture", agent: str = "fixture", profiles_from: Path | None = None,
                 model: str = "", effort: str = "", ledger: live.Ledger | None = None, job_timeout: int = 900, allow_exec: bool = False) -> dict:
    is_live = llm != "fixture" or agent != "fixture"
    if is_live:
        output = output / f"live-{llm}-{agent}"
    output.mkdir(parents=True, exist_ok=True)
    previous = [p for p in sorted(output.glob("scenario-*/result.json")) if "projection" in (r := json.loads(p.read_text())) and "error" not in r]
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
    result = {"schema_version": "2" if is_live else "1", "kind": "live-provider-scenario" if is_live else "offline-contract-scenario",
              "live_model": is_live, "folder": str(folder.resolve()), "stages": stages}
    providers: dict = {}
    killed: list[str] = []
    metered = False
    from reprocrowd.config import settings
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    try:
        os.environ["REPROCROWD_CONFIG"] = str(config.resolve())
        settings.cache_clear()
        from reprocrowd.api.app import app
        # Live provider setup inside try, so a refusal (API-key billing, spend cap) still restores the environment.
        for role, name in (("llm", llm), ("agent", agent)):
            if name == "fixture":
                continue
            lp, ap = live.profiles(name, profiles_from)
            prof = (lp if role == "llm" else ap).model_copy(update={k: v for k, v in (("model", model), ("effort", effort)) if v})
            live.refuse_api_billing(prof.argv)
            getattr(settings(), f"{role}_profiles")[name] = prof
            providers[role] = {"profile": name, "source": str(profiles_from) if profiles_from else "shipped defaults", "cli": live.cli_of(prof.argv),
                               "version": live.version(prof.argv), "protocol": prof.events if role == "agent" else prof.output, "model": prof.model or "(CLI default)",
                               "effort": prof.effort or "(CLI default)"}
            os.environ.update(live.run_env(prof.argv))
        if any(v["cli"] in live.METERED for v in providers.values()):
            ledger.check(f"live scenario {llm}/{agent}")
            metered = True
        result["providers"] = providers
        with respx.mock(assert_all_called=False) as network, TestClient(app) as client:
            work = {"id": "https://openalex.org/W1", "title": "Offline contract fixture (not a research result)",
                    "publication_year": 2026, "ids": {"doi": f"https://doi.org/{DOI}", "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/4359000"},
                    "authorships": [{"author": {"display_name": "Fixture Author"}}]}
            network.get(url__regex=r"https://api\.openalex\.org/works/doi:.*").mock(return_value=httpx.Response(200, json=work))
            network.get(PAPER_URL).mock(return_value=httpx.Response(200, content=(FIXTURES / "scenario.jats.xml").read_bytes()))
            def request(stage, method, url, body=None, expect=None):
                start = time.monotonic()
                response = client.request(method, url, json=body)
                if expect is None:
                    assert response.is_success, f"{stage}: {response.status_code} {response.text}"
                else:
                    assert response.status_code == expect, f"{stage}: {response.status_code} {response.text}"
                stages.append({"stage": stage, "status": response.status_code, "duration_ms": round((time.monotonic()-start)*1000)})
                return response.json()
            package = request("create", "POST", "/v1/packages", {"path": path(folder / "package"), "identifier": DOI, "fetch_metadata": True})
            p = f"/v1/packages/{package['id']}"
            candidates = request("candidates", "GET", p + "/fulltexts/candidates")
            candidate = next(c for c in candidates["candidates"] if "Europe PMC" in c["label"])
            fulltext = request("retrieve", "POST", p + "/fulltexts", {"candidate": candidate["id"]})
            extraction = request("parse", "POST", p + "/extractions", {"method": "parse", "fulltext": fulltext["name"]})
            request("input", "POST", p + "/inputs", {"path": path(FIXTURES / "data.csv"), "note": "synthetic fixture, one row per group"})
            body = {"extraction": extraction["name"], **({"profile": llm} if llm != "fixture" else {})}
            request("target-preview", "POST", p + "/targets/runs:preview", body)
            targets = request("targets", "POST", p + "/targets/runs", body)
            if llm == "fixture":
                assert not targets["rejected"]
            assert targets["entries"], "the extraction produced no targets"
            request("review-target", "PATCH", p + "/targets/T001", {"note": "Reviewed synthetic fixture target; no scientific judgment implied."})
            actions = request("actions", "POST", p + "/actions/runs", body)
            assert len(actions["entries"]) == 2 if llm == "fixture" else actions["entries"], actions
            request("script", "PUT", p + "/script", {"template": "python"})
            codegen = {"type": "codegen", "allow_exec": False, **({"profile": agent} if agent != "fixture" else {})}
            if agent != "fixture":
                # The permission path: a profile without an enforceable no-analysis mode must refuse it before launch.
                no_exec = client.post(p + "/jobs:preview", json=codegen)
                stages.append({"stage": "agent-preview-no-exec", "status": no_exec.status_code, "duration_ms": 0})
                assert no_exec.status_code in (200, 409), no_exec.text
                if no_exec.status_code == 409:
                    assert no_exec.json()["code"] == "exec_restriction_unsupported"
                    if not allow_exec:
                        raise RuntimeError(f"profile {agent!r} cannot run editing-only; pass --allow-exec to let the agent run code")
                    codegen["allow_exec"] = True
                else:
                    assert not any("Bash" in a for a in no_exec.json()["argv"]), "no-analysis argv still allows Bash"
            agent_preview = request("agent-preview", "POST", p + "/jobs:preview", codegen)
            def job(stage, body, deadline_s=30):
                start = time.monotonic()
                record = request(stage, "POST", p + "/jobs", body)
                deadline = time.monotonic() + deadline_s
                while time.monotonic() < deadline:
                    record = client.get(p + "/jobs/" + record["id"]).json()
                    if record.get("finished_at"):
                        stages[-1]["duration_ms"] = round((time.monotonic() - start) * 1000)
                        stages[-1]["outcome"] = record["status"]
                        assert record["status"] == "succeeded", record
                        return record
                    if metered and stage == "codegen" and record.get("stdout"):
                        spent = live.reported_cost((folder / "package" / record["stdout"]).read_text(errors="replace"))
                        if spent * 2 > ledger.run_cap:  # doubled: peak pricing may apply
                            kill(record)
                            raise RuntimeError(f"codegen cancelled at the per-run cap: reported {spent:.4f} USD")
                    time.sleep(.025 if not is_live else .5)
                kill(record)
                raise TimeoutError(stage)
            def kill(record):
                client.delete(p + "/jobs/" + record["id"])
                log = folder / "package" / str(record.get("stdout") or "jobs/none")
                if metered and not (log.is_file() and live.reported_cost(log.read_text(errors="replace"))):
                    killed.append(record["id"])  # may be billed without a reported cost
            codegen_job = job("codegen", codegen, job_timeout if agent != "fixture" else 30)
            run = job("run", {"type": "run"}, 120)
            comparison = request("compare", "GET", p + "/comparison")
            evidence = request("evidence", "GET", p + "/evidence")
            report = request("report", "PUT", p + "/report", {"threshold": 5})
            archive = request("archive", "PUT", p + "/archive")
            preview = request("deposit-preview", "POST", p + "/deposits:preview", {"creators": [{"name": "Fixture Author"}]})
            validation = request("validation", "GET", p + "/validation")
            root = Path(package["path"])
            mutation = mutation_check(root, "python3 analysis.py")
            assert validation["valid"], validation
            assert evidence["stale"] == [] and evidence["results_match_run"]
            if not is_live:
                assert comparison["summary"] == {"match": 3}
                assert report["matched"] == 3 and report["within"] == 0
                # Shared artifacts, independent of where/when a run happens. Mutable diagnostic
                # records are tested by their selected meaning rather than by byte equality.
                stable_files = ["inputs/data.csv", "analysis.py", "analysis_plan.json", "results_schema.json", "outputs/results.json"]
                hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in stable_files}
                projection = {"schema_version": "1", "track": package["track"], "stages": [{"stage": s["stage"], "status": s["status"], **({"outcome": s["outcome"]} if "outcome" in s else {})} for s in stages],
                              "hashes": hashes, "policy": comparison["policy"], "rounding_mode": comparison["rounding_mode"],
                              "summary": comparison["summary"], "rows": comparison["rows"], "settings": comparison["settings"],
                              "run": {k: run[k] for k in ("status", "exit_code")}, "stale": evidence["stale"], "results_match_run": evidence["results_match_run"],
                              "target_evidence": evidence["targets"], "valid": validation["valid"], "deposit_metadata": preview["metadata"]}
                assertions = {"all_targets_match": True, "evidence_current": True, "package_valid": True, "no_publication": True}
            else:
                printed = sorted(str(r.get("expected_text")) for r in comparison["rows"] if r.get("type") == "number")
                projection = {"schema_version": "2", "track": package["track"], "providers": {k: {f: v[f] for f in ("cli", "protocol", "model")} for k, v in providers.items()},
                              "stages": [{"stage": s["stage"], "status": s["status"], **({"outcome": s["outcome"]} if "outcome" in s else {})} for s in stages],
                              "input_sha256": hashlib.sha256((root / "inputs/data.csv").read_bytes()).hexdigest(),
                              "numeric_printed_tokens": printed, "policy": comparison["policy"], "summary": comparison["summary"],
                              "rows": [{k: r.get(k) for k in ("target", "type", "status", "expected_text", "precision", "quantum", "reproduced_text")} for r in comparison["rows"]],
                              "codegen_allow_exec": codegen["allow_exec"], "codegen_exec_restriction": agent_preview["exec_restriction"],
                              "results_match_run": evidence["results_match_run"], "valid": validation["valid"], "mutation_check": mutation["passed"],
                              "report_counts": {k: report.get(k) for k in ("matched", "within", "reproduced")}}
                assertions = {"completed": True, "evidence_current": True, "package_valid": True, "no_publication": True,
                              "computed_from_data": mutation["passed"], "printed_precision_kept": any(str(r.get("expected_text", "")).startswith("512.40") and r.get("precision") == "printed" and r.get("quantum") == -2
                                                            for r in comparison["rows"]),
                              "all_numeric_targets_match": bool(comparison["summary"]) and set(comparison["summary"]) <= {"match"}}
            result.update(projection=projection, archive=str((root / archive["path"]).resolve()), report=str((root / report["path"]).resolve()),
                          assertions=assertions, checks={"mutation": mutation},
                          git_head=subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip(),
                          git_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=HERE, capture_output=True, text=True).stdout.strip()))
            if not is_live:
                assert mutation["passed"], mutation
            assert all(call.request.method == "GET" for call in network.calls), "Only mocked metadata/fulltext reads allowed"
    except Exception as error:
        result.update(error=f"{type(error).__name__}: {error}", duration_ms=round((time.monotonic() - started) * 1000), assertions={"completed": False})
        raise
    finally:
        if is_live:
            result["usage"] = usage(folder / "package", providers)
            if metered:
                result["spend"] = ledger.record("scenario.py", f"live scenario {llm}/{agent} ({model or 'default model'})", started_at,
                                                result["usage"]["reported_usd"], result["usage"]["tokens"], str(folder.resolve()), len(killed))
        if "projection" not in result or "error" in result:  # a failed run keeps its record (and is never a baseline)
            (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
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
        if is_live and before != result["projection"]:
            diff = "Live runs may legitimately differ (model output); this diff is informational.\n" + diff
    (folder / "projection.json").write_text(stable + "\n")
    (folder / "diff.txt").write_text(diff)
    (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def usage(root: Path, providers: dict) -> dict:
    """Tokens and provider-reported cost from the recorded LLM calls and jobs of this run's package."""
    text = ""
    for f in [*root.glob("llm-calls/*/stdout.txt"), *root.glob("jobs/*/stdout*")]:
        text += f.read_text(errors="replace") + "\n"
    return {"tokens": live.tokens(text), "reported_usd": round(live.reported_cost(text), 6),
            "billing": {k: ("metered" if v["cli"] in live.METERED else "subscription") for k, v in providers.items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", "--out", type=Path, default=HERE.parent.parent / "test-output" / "paper-scenario")
    parser.add_argument("--live", action="store_true", help="required for any real CLI: model calls use your sign-in or credit")
    parser.add_argument("--llm", default="fixture", help="extraction profile: fixture (fake, default), claude, codex, opencode, or a name in --profiles")
    parser.add_argument("--agent", default="fixture", help="code-generation profile (same choices)")
    parser.add_argument("--profiles", type=Path, help="backend.toml to take the profiles from (e.g. saved in Settings); default: shipped profiles")
    parser.add_argument("--model", default="", help="model for the live profiles (as the CLI takes it, e.g. deepseek/deepseek-v4-pro)")
    parser.add_argument("--effort", default="", help="effort/variant for the live profiles")
    parser.add_argument("--spend-ledger", type=Path, help="private JSON spend ledger (required for metered OpenCode runs)")
    parser.add_argument("--max-usd", type=float, default=0.30, help="per-run cap for metered runs; the codegen job is cancelled when reached")
    parser.add_argument("--job-timeout", type=int, default=900, help="seconds to wait for a live codegen job")
    parser.add_argument("--allow-exec", action="store_true", help="let a coding agent without an editing-only mode (e.g. Codex) run code")
    args = parser.parse_args()
    if (args.llm != "fixture" or args.agent != "fixture") and not args.live:
        parser.error("real CLI profiles need --live (they make model calls)")
    result = run_scenario(args.output, llm=args.llm, agent=args.agent, profiles_from=args.profiles, model=args.model, effort=args.effort,
                          ledger=live.Ledger(args.spend_ledger.expanduser() if args.spend_ledger else None, args.max_usd), job_timeout=args.job_timeout, allow_exec=args.allow_exec)
    print(json.dumps({k: result.get(k) for k in ("folder", "report", "archive", "assertions", "usage", "spend")} | {"same_as_previous": result.get("same_as_previous")}, indent=2))
