"""Dev-only conformance check of the app's process transports, never a live provider test.

cd backend && uv run python tests/conformance/runner.py
Overrides may adjust a fake profile's timeout, model or effort. They cannot select an
executable: this runner must never launch a real CLI or consume model credits.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from fastapi.testclient import TestClient

HERE = Path(__file__).parent
PROTOCOLS = ("text", "claude-json", "claude-stream-json", "opencode-json", "codex-json")


def run_conformance(profile_overrides: dict | None = None, protocols: tuple[str, ...] = PROTOCOLS) -> dict:
    from reprocrowd.config import settings, AgentProfile, LlmProfile
    from reprocrowd.api.app import app
    overrides = profile_overrides or {}
    if any(set(value) - {"timeout_s", "model", "effort"} for value in overrides.values()):
        raise ValueError("Conformance overrides cannot select commands or protocols")
    results = []
    with tempfile.TemporaryDirectory(prefix="scratch-reprocrowd-conformance-") as tmp:
        root = Path(tmp)
        config = root / "backend.toml"
        config.write_text(f'[server]\nregistry = "{(root / "registry.json").as_posix()}"\n[openalex]\napi_key = ""\n')
        old_config = os.environ.get("REPROCROWD_CONFIG")
        os.environ["REPROCROWD_CONFIG"] = str(config)
        settings.cache_clear()
        try:
            with TestClient(app) as client:
                pkg = client.post("/v1/packages", json={"path": str(root / "package")}).json()["id"]
                p = f"/v1/packages/{pkg}"
                fixture = HERE.parent / "fixtures" / "paper.tei.xml"
                assert client.post(p + "/fulltexts", json={"path": str(fixture)}).status_code == 201
                assert client.post(p + "/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"}).status_code == 201
                ref = [{"extraction": "ex-paper-tei", "id": "sec3.p1.s1"}]
                assert client.post(p + "/targets", json={"type": "number", "description": "fixture", "expected": "2.31", "refs": ref}).status_code == 201
                assert client.post(p + "/actions", json={"kind": "analysis", "summary": "fixture", "refs": ref, "produces": ["T001"]}).status_code == 201
                assert client.put(p + "/script", json={"template": "python"}).status_code == 201
                def record(protocol, operation, case, expected, observed):
                    results.append({"schema_version": "1", "protocol": protocol, "operation": operation, "case": case,
                                    "expected": expected, "observed": observed, "passed": expected == observed})
                def wait(jid):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        job = client.get(p + "/jobs/" + jid).json()
                        if job.get("finished_at"): return job
                        time.sleep(.025)
                    raise TimeoutError(jid)
                for protocol in protocols:
                    for case in ("success", "mixed", "malformed", "nonzero", "timeout", *(["terminal-error"] if protocol != "text" else [])):
                        argv = [sys.executable, str(HERE / f"{protocol}.py"), case]
                        fields = {"timeout_s": 1 if case == "timeout" else 10, **overrides.get(protocol, {})}
                        # A malformed log alone is not a failed coding process: no semantic
                        # completion claim is inferred from opaque text, only process status.
                        if protocol != "claude-stream-json":
                            settings().llm_profiles["fixture"] = LlmProfile(argv=argv, output=protocol, **fields)
                            response = client.post(p + "/targets/runs", json={"extraction": "ex-paper-tei", "profile": "fixture"})
                            expected = "ok" if case in ("success", "mixed") else "llm_output_invalid" if case == "malformed" else "llm_timeout" if case == "timeout" else "llm_call_failed"
                            observed = "ok" if response.is_success else response.json()["code"]
                            record(protocol, "llm", case, expected, observed)
                        if protocol != "claude-json":
                            settings().agent_profiles["fixture"] = AgentProfile(argv=argv, events=protocol, **fields)
                            response = client.post(p + "/jobs", json={"type": "codegen", "profile": "fixture"})
                            assert response.status_code == 202, response.text
                            job = wait(response.json()["id"])
                            expected = "timeout" if case == "timeout" else "failed" if case in ("nonzero", "terminal-error") else "succeeded"
                            record(protocol, "agent", case, expected, job["status"])
                            if case == "terminal-error":
                                record(protocol, "agent", "error-at-exit-zero", 0, job["exit_code"])
                    if protocol != "claude-json" and os.name == "posix":
                        pidfile = root / f"{protocol}.pid"
                        argv = [sys.executable, str(HERE / f"{protocol}.py"), "cancel", str(pidfile)]
                        settings().agent_profiles["fixture"] = AgentProfile(argv=argv, events=protocol, timeout_s=15)
                        response = client.post(p + "/jobs", json={"type": "codegen", "profile": "fixture"})
                        assert response.status_code == 202, response.text
                        deadline = time.monotonic() + 5
                        while not pidfile.exists() and time.monotonic() < deadline: time.sleep(.025)
                        assert pidfile.exists(), "fake did not start its descendant"
                        child = int(pidfile.read_text())
                        client.delete(p + "/jobs/" + response.json()["id"])
                        job = wait(response.json()["id"])
                        record(protocol, "agent", "cancel", "cancelled", job["status"])
                        try:
                            os.kill(child, 0)
                            gone = False
                        except ProcessLookupError:
                            gone = True
                        record(protocol, "agent", "cancel-descendant-posix", True, gone)
        finally:
            if old_config is None: os.environ.pop("REPROCROWD_CONFIG", None)
            else: os.environ["REPROCROWD_CONFIG"] = old_config
            settings.cache_clear()
    return {"schema_version": "1", "fake_transports_only": True, "descendant_check": "POSIX" if os.name == "posix" else "skipped (requires POSIX)",
            "cases": results, "passed": all(row["passed"] for row in results)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE.parent.parent.parent / "test-output" / "providers")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = run_conformance()
    file = args.output / ("conformance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    file.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"path": str(file.resolve()), "passed": result["passed"], "cases": len(result["cases"])}))
    raise SystemExit(0 if result["passed"] else 1)
