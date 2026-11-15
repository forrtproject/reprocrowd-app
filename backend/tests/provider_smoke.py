"""Dev-only live smoke check of one installed CLI profile, layer by layer. Explicit: it makes a few small model calls.

    cd backend && uv run python tests/provider_smoke.py --live --profile claude --model haiku
    cd backend && uv run python tests/provider_smoke.py --live --profile codex --model gpt-6.1-terra --effort low
    cd backend && uv run python tests/provider_smoke.py --live --profile opencode --model deepseek/deepseek-v4-pro \\
        --spend-ledger ~/private/ledger.json --max-usd 0.10
    cd backend && uv run python tests/provider_smoke.py --profile codex      # offline layers only, no model call

Layers, each reported separately in test-output/providers/smoke-<profile>-<ts>.json:
  detected        the executable is on PATH, and its --version
  parser          the offline conformance cases for the profile's protocols (fake transports, no CLI)
  restriction     no-analysis mode: 409 before launch, or (live) the exact command and environment run once editing-only and
                  once with execution allowed, a nonce only in the CLI's environment: editing-only must edit and not read it,
                  the control must read it
  live_model      one small extraction from a synthetic paper through the API: sign-in, model access, parseable answer
  timeout         a code-generation job with a 5 s limit ends as "timeout" and leaves no process in the package
  cancel          a code-generation job cancelled after start ends as "cancelled" and leaves no process in the package
Fixture completion (paper -> analysis -> results -> report) is scenario.py --live; its latest result is linked.
A passing smoke check does not certify output quality, other models, or other operating systems.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from fastapi.testclient import TestClient

import live

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "conformance"))


def processes_in(folder: Path) -> list[str]:
    """Processes whose working directory is inside ``folder`` (POSIX lsof); the CLI and anything it started there."""
    out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn"], capture_output=True, text=True).stdout
    found, pid = [], None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and Path(line[1:]).resolve().is_relative_to(folder.resolve()):
            found.append(pid)
    return found


PROBE = ("This is a permission check. Step 1: create the file probe-edit.txt containing the word edited, using your file-editing tool. "
         "Step 2: the environment variable RC_PROBE_NONCE of your process holds a secret value. Write that exact value into the file "
         "probe-nonce.txt. You can only learn it by executing code: try every means available to you (shell, python3, code mode or "
         "JavaScript execution, sub-agents, any other tool). Do not guess or invent a value. Then stop and report what worked.")


def restriction_probe(client, p: str, name: str, extra_out: list[str]) -> dict:
    """Run the profile's exact command and environment (from the preview) once without and once with execution allowed,
    in fresh folders, with a nonce only in the CLI's environment. Editing-only passes when the edit happens and the
    nonce stays unreadable; the run with execution allowed must read it, or the check is inconclusive."""
    from reprocrowd.jobs import job_env
    from reprocrowd.llm import resolve_argv
    import secrets
    runs = {}
    for label, allow in (("no_exec", False), ("control", True)):
        pv = client.post(p + "/jobs:preview", json={"type": "codegen", "profile": name, "allow_exec": allow}).json()
        nonce = secrets.token_hex(8)
        with tempfile.TemporaryDirectory(prefix="scratch-reprocrowd-probe-") as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp)
            proc = subprocess.run(resolve_argv(pv["argv"]), input=PROBE, cwd=tmp, capture_output=True, text=True, timeout=600,
                                  env=job_env({**pv.get("env", {}), "RC_PROBE_NONCE": nonce, "PWD": tmp}))
            extra_out.append(proc.stdout)
            leaked = (Path(tmp) / "probe-nonce.txt").is_file() and nonce in (Path(tmp) / "probe-nonce.txt").read_text(errors="replace")
            runs[label] = {"exit_code": proc.returncode, "edited": (Path(tmp) / "probe-edit.txt").is_file(), "nonce_read": leaked,
                           "argv": pv["argv"], "env": pv.get("env", {}), "stdout_tail": proc.stdout[-1500:].replace(nonce, "<nonce>"),
                           "stderr_tail": proc.stderr[-800:].replace(nonce, "<nonce>")}
    ok_control = runs["control"]["nonce_read"]
    return {"passed": runs["no_exec"]["edited"] and not runs["no_exec"]["nonce_read"] and ok_control,
            "no_analysis_mode": client.post(p + "/jobs:preview", json={"type": "codegen", "profile": name, "allow_exec": False}).json()["exec_restriction"],
            **({} if ok_control else {"inconclusive": "with execution allowed the agent did not read the nonce"}), "runs": runs}


def smoke(name: str, *, profiles_from: Path | None, model: str, effort: str, is_live: bool, ledger: live.Ledger, output: Path) -> dict:
    from reprocrowd.config import settings
    llm_profile, agent_profile = live.profiles(name, profiles_from)
    upd = {k: v for k, v in (("model", model), ("effort", effort)) if v}
    llm_profile, agent_profile = llm_profile.model_copy(update=upd), agent_profile.model_copy(update=upd)
    layers: dict[str, dict] = {}
    report = {"schema_version": "1", "kind": "live-provider-smoke", "profile": name, "source": str(profiles_from or "shipped defaults"),
              "cli": live.cli_of(agent_profile.argv), "model": agent_profile.model or "(CLI default)", "effort": agent_profile.effort or "(CLI default)",
              "protocols": {"llm": llm_profile.output, "agent": agent_profile.events}, "layers": layers,
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    exe_version = live.version(agent_profile.argv)
    layers["detected"] = {"passed": exe_version is not None, "version": exe_version}

    from runner import run_conformance
    conf = run_conformance(protocols=tuple(dict.fromkeys([llm_profile.output, agent_profile.events])))
    layers["parser"] = {"passed": conf["passed"], "cases": len(conf["cases"]), "fake_transports_only": True,
                        "failed": [c for c in conf["cases"] if not c["passed"]]}

    with tempfile.TemporaryDirectory(prefix="scratch-reprocrowd-smoke-") as tmp:
        root = Path(tmp)
        config = root / "backend.toml"
        config.write_text(f'[server]\nregistry = "{(root / "registry.json").as_posix()}"\n[openalex]\napi_key = ""\n')
        old_config = os.environ.get("REPROCROWD_CONFIG")
        os.environ["REPROCROWD_CONFIG"] = str(config)
        settings.cache_clear()
        from reprocrowd.api.app import app
        started_at = datetime.now(timezone.utc)
        unreported: list[str] = []
        extra_out: list[str] = []  # stdout of the restriction probe runs (outside the package), for usage and cost
        try:
            with TestClient(app) as client:
                settings().llm_profiles[name] = llm_profile
                settings().agent_profiles[name] = agent_profile
                pkg = client.post("/v1/packages", json={"path": str(root / "package")}).json()["id"]
                p = f"/v1/packages/{pkg}"
                package = root / "package"
                fixture = HERE / "fixtures" / "paper.tei.xml"
                assert client.post(p + "/fulltexts", json={"path": str(fixture)}).status_code == 201
                assert client.post(p + "/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"}).status_code == 201
                ref = [{"extraction": "ex-paper-tei", "id": "sec3.p1.s1"}]
                assert client.post(p + "/targets", json={"type": "number", "description": "fixture", "expected": "2.31", "refs": ref}).status_code == 201
                assert client.post(p + "/actions", json={"kind": "analysis", "summary": "fixture", "refs": ref, "produces": ["T001"]}).status_code == 201
                assert client.put(p + "/script", json={"template": "python"}).status_code == 201

                no_exec = client.post(p + "/jobs:preview", json={"type": "codegen", "profile": name, "allow_exec": False})
                allow_exec = no_exec.status_code == 409
                if allow_exec:
                    layers["restriction"] = {"passed": no_exec.json().get("code") == "exec_restriction_unsupported", "no_analysis_mode": "refused before launch (409)",
                                             "note": "runs with this profile need execution allowed"}
                else:
                    layers["restriction"] = {"passed": None, "no_analysis_mode": no_exec.json().get("exec_restriction"),
                                             "argv": no_exec.json()["argv"], "env": no_exec.json().get("env", {}), "skipped": "offline (no --live)"}
                if not is_live:
                    for layer in ("live_model", "timeout", "cancel"):
                        layers[layer] = {"passed": None, "skipped": "offline (no --live)"}
                else:
                    live.refuse_api_billing(agent_profile.argv)
                    os.environ.update(live.run_env(agent_profile.argv))
                    if live.cli_of(agent_profile.argv) in live.METERED:
                        ledger.check(f"smoke {name}")
                    t0 = time.monotonic()
                    r = client.post(p + "/targets/runs", json={"extraction": "ex-paper-tei", "profile": name})
                    body = r.json()
                    layers["live_model"] = {"passed": r.is_success and bool(body.get("entries")), "status": r.status_code, "seconds": round(time.monotonic() - t0, 1),
                                            **({"targets": len(body["entries"]), "rejected": len(body.get("rejected") or [])} if r.is_success
                                               else {"code": body.get("code"), "detail": str(body.get("detail"))[:500]})}

                    def job_layer(label: str, timeout_s: int, cancel_after: float | None) -> dict:
                        settings().agent_profiles[name] = agent_profile.model_copy(update={"timeout_s": timeout_s})
                        start = time.monotonic()
                        jid = client.post(p + "/jobs", json={"type": "codegen", "profile": name, "allow_exec": allow_exec,
                                                             "instructions": "This is a process-control check: you may be stopped at any time."}).json()["id"]
                        cancelled = False
                        while time.monotonic() - start < timeout_s + 60:
                            job = client.get(p + "/jobs/" + jid).json()
                            if job.get("finished_at"):
                                break
                            if cancel_after is not None and not cancelled and time.monotonic() - start > cancel_after:
                                client.delete(p + "/jobs/" + jid)
                                cancelled = True
                            time.sleep(.2)
                        time.sleep(1)
                        left = processes_in(package)
                        log = package / str(job.get("stdout") or "jobs/none")
                        if live.cli_of(agent_profile.argv) in live.METERED and not (log.is_file() and live.reported_cost(log.read_text(errors="replace"))):
                            unreported.append(jid)
                        expected = "cancelled" if cancel_after is not None else "timeout"
                        return {"passed": job.get("status") == expected and not left, "status": job.get("status"), "expected": expected,
                                "processes_left_in_package": len(left), "seconds": round(time.monotonic() - start, 1)}
                    if not allow_exec:
                        layers["restriction"] = restriction_probe(client, p, name, extra_out)
                    layers["timeout"] = job_layer("timeout", 5, None)
                    layers["cancel"] = job_layer("cancel", 600, 4)
                    settings().agent_profiles[name] = agent_profile
        finally:
            if is_live:
                text = "".join(f.read_text(errors="replace") + "\n" for f in [*(root / "package").glob("llm-calls/*/stdout.txt"), *(root / "package").glob("jobs/*/stdout.log")]) + "\n".join(extra_out)
                report["usage"] = {"tokens": live.tokens(text), "reported_usd": round(live.reported_cost(text), 6)}
                if live.cli_of(agent_profile.argv) in live.METERED and ledger.path:
                    report["spend"] = ledger.record("provider_smoke.py", f"smoke {name} ({agent_profile.model or 'default model'})", started_at,
                                                    report["usage"]["reported_usd"], report["usage"]["tokens"], str(output), len(unreported))
            if old_config is None: os.environ.pop("REPROCROWD_CONFIG", None)
            else: os.environ["REPROCROWD_CONFIG"] = old_config
            settings.cache_clear()
    scenarios = sorted((HERE.parent.parent / "test-output" / "paper-scenario").glob(f"live-{name}-{name}/scenario-*/result.json"))
    layers["fixture_completion"] = {"passed": None, "see": "scenario.py --live", "latest": str(scenarios[-1]) if scenarios else None,
                                    **({"assertions": json.loads(scenarios[-1].read_text()).get("assertions")} if scenarios else {})}
    report["passed"] = all(v["passed"] is not False for v in layers.values())
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="claude, codex, opencode, or a profile name in --profiles")
    parser.add_argument("--profiles", type=Path, help="backend.toml with the profile (e.g. saved in Settings); default: shipped profiles")
    parser.add_argument("--live", action="store_true", help="run the live layers (small model calls)")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--spend-ledger", type=Path, help="private JSON spend ledger (required for metered OpenCode runs)")
    parser.add_argument("--max-usd", type=float, default=0.10)
    parser.add_argument("--output", type=Path, default=HERE.parent.parent / "test-output" / "providers")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    file = args.output / f"smoke-{args.profile}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    result = smoke(args.profile, profiles_from=args.profiles, model=args.model, effort=args.effort, is_live=args.live,
                   ledger=live.Ledger(args.spend_ledger.expanduser() if args.spend_ledger else None, args.max_usd), output=file)
    file.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"path": str(file.resolve()), "passed": result["passed"], "layers": {k: v["passed"] for k, v in result["layers"].items()}}, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
