"""Contract regressions shared by reports, command-line clients and benchmarks."""
import json
from pathlib import Path
import zipfile

import respx

from conftest import FIXTURES
from test_api import mock_openalex, problem
from test_jobs import wait, ready_package
from test_run import SCRIPT, package_with_targets
from test_zenodo import ME
from reprocrowd.config import settings, AgentProfile, LlmProfile
from reprocrowd.config import expand_argv, _default_agents, _default_profiles
from reprocrowd.providers import answer, exec_restriction, terminal_error


def run(client, pkg):
    p = f"/v1/packages/{pkg}"
    return wait(client, pkg, client.post(p + "/jobs", json={"type": "run"}).json()["id"])


def test_track(client, pkg, tmp_path):
    assert client.get(f"/v1/packages/{pkg}").json()["track"] == "recreation"
    path = tmp_path / "original"
    problem(client.post("/v1/packages", json={"path": str(path), "track": "original_code"}), 422, "track_unavailable")
    assert not path.exists()
    from reprocrowd.package import Registry
    package = Registry().get(pkg)
    raw = package.read_json("datapackage.json")
    raw["reprocrowd"].pop("track")
    package.path("datapackage.json").write_text(json.dumps(raw))
    with package.edit() as manifest:
        manifest.title = "older package"
    assert client.get(f"/v1/packages/{pkg}").json()["track"] is None


def test_code_context_and_noexec(client, pkg, tmp_path):
    root = ready_package(client, pkg)
    p = f"/v1/packages/{pkg}"
    code = tmp_path / "authors.py"
    code.write_text("# original code\n")
    added = client.post(p + "/inputs", json={"path": str(code), "role": "code"})
    assert added.status_code == 201, added.text
    # Even after regenerating the plan, code paths never enter the supplied context.
    assert client.put(p + "/script", json={"template": "r"}).status_code == 200
    body = {"type": "codegen", "allow_exec": False}
    preview = client.post(p + "/jobs:preview", json=body).json()
    assert "authors.py" not in preview["prompt"] and "reuse their logic" not in preview["prompt"]
    assert "authors.py" not in (root / "analysis_plan.json").read_text()
    assert "run the pipeline once" not in preview["prompt"]
    assert "Do not run the analysis" in preview["prompt"]
    assert preview["exec_restriction"] == "tool_allowlist" and preview["available"]
    job = wait(client, pkg, client.post(p + "/jobs", json=body).json()["id"])
    assert job["request"]["code_files_on_disk"] == ["inputs/authors.py"]
    settings().agent_profiles["unrestricted"] = AgentProfile(argv=["missing-agent", "--auto"], events="opencode-json")
    body["profile"] = "unrestricted"
    count = len(client.get(p + "/jobs").json())
    problem(client.post(p + "/jobs:preview", json=body), 409, "exec_restriction_unsupported")
    problem(client.post(p + "/jobs", json=body), 409, "exec_restriction_unsupported")
    assert len(client.get(p + "/jobs").json()) == count
    body["allow_exec"] = True
    preview = client.post(p + "/jobs:preview", json=body).json()
    assert preview["available"] is False and preview["exec_restriction"] == "unsupported"


@respx.mock
def test_recorded_comparison_and_archived_evidence(client, pkg):
    mock_openalex()
    root = package_with_targets(client, pkg)
    p = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT.replace("2.3149", "2.4") + "open('Rplots.pdf','w').write('fixture')\n")
    job = run(client, pkg)
    assert job["status"] == "succeeded"
    ev = client.get(p + "/evidence").json()
    assert ev["stale"] == [] and "Rplots.pdf" in ev["added_since_run"] and ev["results_match_run"]
    assert ev["targets"][0]["step_evidence"] == "declared_matches_plan"
    result = client.put(p + "/report", json={"threshold": 10})
    assert result.status_code == 201, result.text
    assert result.json()["matched"] == 2 and result.json()["within"] == 1
    assert client.put(p + "/archive").status_code == 201
    # Use a retrieved origin just as existing deposit tests do; no remote publication.
    from reprocrowd.package import Registry
    from reprocrowd.schemas.manifest import UrlOrigin
    with Registry().get(pkg).edit() as manifest:
        for resource in manifest.by_kind("fulltext"):
            resource.reprocrowd.origin = UrlOrigin(url="https://example.org/fixture.xml", candidate="fixture", label="mocked retrieval")
    assert client.put(p + "/archive").status_code == 200
    preview = client.post(p + "/deposits:preview", json={"creators": ME})
    assert preview.status_code == 200, preview.text
    desc = preview.json()["metadata"]["description"]
    assert "2 of 3" in desc and "1 more within" in desc
    archive = Registry().get(pkg).load().reprocrowd.archive
    with zipfile.ZipFile(root / archive.path) as z:
        archived = json.loads(z.read("evidence/execution.json"))
        assert archived["log_path"] == "evidence/run.log" and archived["log_path"] in z.namelist()
        assert z.read(archived["log_path"]) == (root / job["stdout"]).read_bytes()
    (root / "analysis.py").write_text(SCRIPT.replace("2.3149", "2.5"))
    assert "analysis.py" in client.get(p + "/evidence").json()["stale"]
    run(client, pkg)
    problem(client.put(p + "/archive"), 409, "report_stale")
    problem(client.post(p + "/deposits:preview", json={"creators": ME}), 409, "report_stale")
    assert client.put(p + "/report", json={"threshold": 10}).status_code == 200
    assert client.put(p + "/archive").status_code == 200
    (root / "outputs/results.json").write_text('{"results":[]}')
    assert client.get(p + "/evidence").json()["results_match_run"] is False


def test_conflicting_step_is_warning(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text(SCRIPT.replace('"A01"', '"L01"'))
    job = run(client, pkg)
    assert job["result"]["output_errors"] == []
    assert any("does not produce" in warning for warning in job["result"]["output_warnings"])
    ev = client.get(f"/v1/packages/{pkg}/evidence").json()
    assert ev["targets"][0]["step_evidence"] == "declared_conflicts_plan"


def test_terminal_errors_are_not_tool_errors():
    assert exec_restriction(AgentProfile(argv=["cli", "{tools}"], tools=[]), ["Bash"]) == "unsupported"
    assert terminal_error('{"type":"error","error":{"message":"offline"}}', "opencode-json") == "offline"
    assert terminal_error('{"type":"result","is_error":true,"result":"refused"}', "claude-stream-json") == "refused"
    assert terminal_error('{"type":"tool_result","is_error":true}', "claude-stream-json") is None
    assert terminal_error('[]', "opencode-json") is None


def test_codex_errors_and_answer():
    reconnect = '{"type":"error","message":"Reconnecting... 2/5"}'
    done = '{"type":"turn.completed","usage":{}}'
    warning = '{"type":"item.completed","item":{"type":"error","message":"fallback metadata"}}'
    assert terminal_error("\n".join([reconnect, warning, done]), "codex-json") is None
    assert terminal_error("\n".join([done, reconnect]), "codex-json") == "Reconnecting... 2/5"  # no completed turn after it
    assert terminal_error('{"type":"turn.failed","error":{"message":"model not supported"}}', "codex-json") == "model not supported"
    msgs = [f'{{"type":"item.completed","item":{{"type":"agent_message","text":"{t}"}}}}' for t in ("reading", "final")]
    assert answer("\n".join(msgs + [done]), "codex-json") == "final"
    assert answer("not json", "codex-json") == ""
    import io
    stream = io.StringIO("\n".join([reconnect, done, reconnect]) + "\n")  # a log file is read line by line
    assert terminal_error(stream, "codex-json") == "Reconnecting... 2/5"


def test_shipped_argv_expansion_unchanged():
    """The empty-placeholder rule drops `-c key={effort}`; shipped profiles expand as before."""
    values = {"model": "m", "effort": "e", "tools": "Read"}
    for p in [*_default_agents().values(), *_default_profiles().values()]:
        assert expand_argv(p.argv, values) == [a.replace("{model}", "m").replace("{effort}", "e").replace("{tools}", "Read") for a in p.argv]
    assert expand_argv(["claude", "--model", "{model}", "--effort", "{effort}"], {"model": "", "effort": ""}) == ["claude"]
    assert expand_argv(["cli", "--verbose", "--model={model}"], {"model": ""}) == ["cli", "--verbose"]  # a flag=value drops only itself
    codex = _default_agents()["codex"].argv
    assert "-c" not in expand_argv(codex, {"model": "", "effort": "", "tools": ""}) and "-m" not in expand_argv(codex, {"model": "", "effort": ""})
    assert expand_argv(codex, {"model": "gpt", "effort": "low"})[-6:] == ["-m", "gpt", "-c", "model_reasoning_effort=low", "-s", "workspace-write"]


def test_cli_settings_restriction(client, pkg, monkeypatch):
    """OpenCode runs editing-only through declared CLI settings; Codex (no read tool without its shell) and a profile without settings are refused."""
    for name in ("codex", "opencode"):
        monkeypatch.setitem(settings().agent_profiles, name, _default_agents()[name])
    monkeypatch.setitem(settings().agent_profiles, "bare", _default_agents()["opencode"].model_copy(update={"no_exec_env": {}}))
    ready_package(client, pkg)
    p = f"/v1/packages/{pkg}"
    problem(client.post(p + "/jobs:preview", json={"type": "codegen", "profile": "codex", "allow_exec": False}), 409, "exec_restriction_unsupported")
    allowed = client.post(p + "/jobs:preview", json={"type": "codegen", "profile": "codex", "allow_exec": True}).json()
    assert ["--disable", "apps"] == allowed["argv"][6:8] and allowed["env"] == {}  # connectors off in every mode
    oc = client.post(p + "/jobs:preview", json={"type": "codegen", "profile": "opencode", "allow_exec": False}).json()
    perm = json.loads(oc["env"]["OPENCODE_PERMISSION"])
    assert perm["*"] == "deny" and perm["edit"] == "allow" and "bash" not in perm
    assert client.post(p + "/jobs:preview", json={"type": "codegen", "profile": "opencode", "allow_exec": True}).json()["env"] == {}
    problem(client.post(p + "/jobs:preview", json={"type": "codegen", "profile": "bare", "allow_exec": False}), 409, "exec_restriction_unsupported")
    problem(client.post(p + "/jobs", json={"type": "codegen", "profile": "bare", "allow_exec": False}), 409, "exec_restriction_unsupported")
    views = {v["name"]: v for v in client.get("/v1/agent-profiles").json()}
    assert views["opencode"]["exec_restriction"] == "cli_settings" and views["codex"]["exec_restriction"] == views["bare"]["exec_restriction"] == "unsupported"


def test_config_is_read_only_over_http(client):
    spec = client.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        if "profiles" in path or "config" in path:
            assert set(methods) <= {"get"}, path


def test_identical_rerun_requires_fresh_report(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text(SCRIPT)
    p = f"/v1/packages/{pkg}"
    run(client, pkg)
    assert client.put(p + "/report", json={}).status_code == 201
    run(client, pkg)
    problem(client.put(p + "/archive"), 409, "report_stale")


def test_timeout_evidence_is_process_status(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text("import time; time.sleep(60)\n")
    settings().run_timeout_s = 1
    job = run(client, pkg)
    assert job["status"] == "timeout"
    evidence = client.get(f"/v1/packages/{pkg}/evidence").json()
    assert evidence["run"]["status"] == "timeout" and evidence["results_match_run"] is False
    assert all(row["level"] == "process" for row in evidence["targets"])


def test_missing_run_log_is_not_an_archive_link(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text(SCRIPT)
    job = run(client, pkg)
    (root / job["stdout"]).unlink()
    p = f"/v1/packages/{pkg}"
    assert client.put(p + "/report", json={}).status_code == 201
    evidence = json.loads((root / "evidence/execution.json").read_text())
    assert evidence.get("log_path") is None
    html = (root / "report.html").read_text()
    assert 'href="evidence/run.log"' not in html and "Run log unavailable" in html
    assert client.put(p + "/archive").status_code == 201


def test_cli_previews_and_evidence_use_shared_api(client, pkg, monkeypatch):
    from typer.testing import CliRunner
    from reprocrowd import cli
    ready_package(client, pkg)
    def through_test_api(method, url, **kwargs):
        return client.request(method, url, json=kwargs.get("json"), params=kwargs.get("params"), headers=kwargs.get("headers"))
    monkeypatch.setattr(cli.httpx, "request", through_test_api)
    runner = CliRunner()
    agent = runner.invoke(cli.app, ["agent", "preview", pkg, "--no-exec"])
    assert agent.exit_code == 0, agent.output
    data = json.loads(agent.stdout)
    assert data["exec_restriction"] == "tool_allowlist" and "Do not run the analysis" in data["prompt"]
    llm = runner.invoke(cli.app, ["llm", "preview", pkg, "ex-paper-tei"])
    assert llm.exit_code == 0, llm.output
    data = json.loads(llm.stdout)
    assert data["available"] and data["output_protocol"] == "text" and data["timeout_s"] == 30
    evidence = runner.invoke(cli.app, ["evidence", pkg, "--json"])
    assert evidence.exit_code == 0, evidence.output
    assert json.loads(evidence.stdout) == client.get(f"/v1/packages/{pkg}/evidence").json()
    human = runner.invoke(cli.app, ["evidence", pkg])
    assert human.exit_code == 0 and "changed since run: unknown (no run)" in human.stdout and "{" not in human.stdout, human.output
    assert client.get(f"/v1/packages/{pkg}/jobs").json() == []


def test_cli_sends_printed_numbers_unchanged(client, pkg, monkeypatch):
    from typer.testing import CliRunner
    from reprocrowd import cli
    def through_test_api(method, url, **kwargs):
        return client.request(method, url, json=kwargs.get("json"), params=kwargs.get("params"), headers=kwargs.get("headers"))
    monkeypatch.setattr(cli.httpx, "request", through_test_api)
    runner = CliRunner()
    added = runner.invoke(cli.app, ["target", "add", pkg, "-f", "type=number", "-f", "description=value",
                                    "-f", "expected=0.1700", "--unreferenced", "--note", "fixture"])
    assert added.exit_code == 0, added.output
    item = json.loads(added.stdout)
    assert (item["printed"], item["decimals"]) == ("0.1700", 4)
    edited = runner.invoke(cli.app, ["target", "edit", pkg, item["id"], "--set", "expected=0.170"])
    assert edited.exit_code == 0, edited.output
    item = json.loads(edited.stdout)["item"]
    assert (item["printed"], item["decimals"]) == ("0.170", 3)


@respx.mock
def test_legacy_comparison_tolerates_float_formatting(client, pkg):
    from reprocrowd.package import Registry
    def save(data):  # as an older app version wrote it, with the manifest hash kept current
        (root / "comparison.json").write_text(json.dumps(data))
        package = Registry().get(pkg)
        with package.edit() as manifest:
            package.refresh(manifest, "comparison")
    mock_openalex()
    root = package_with_targets(client, pkg)
    p = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT.replace("2.3149", "2.4"))
    run(client, pkg)
    assert client.put(p + "/report", json={"threshold": 10}).status_code == 201
    saved = json.loads((root / "comparison.json").read_text())
    assert any(row["status"] == "within" for row in saved["rows"])
    # An older comparison.json: no basis/policy/new row metadata, and float deviations
    # computed by the previous formula, which differ only in the last bits.
    for key in ("basis", "policy", "rounding_mode"):
        saved.pop(key)
    def legacy(value):
        if isinstance(value, dict):
            return {k: (v * (1 + 1e-13) if k == "deviation" and isinstance(v, float) else legacy(v))
                    for k, v in value.items() if k not in {"refs", "precision", "quantum", "printed", "comparator"}}
        return [legacy(v) for v in value] if isinstance(value, list) else value
    saved["rows"] = legacy(saved["rows"])
    save(saved)
    archived = client.put(p + "/archive")
    assert archived.status_code == 201, archived.text
    # An older comparison that agrees on recomputation is reported under the current policy, not a default.
    from reprocrowd.report import recorded_comparison
    assert recorded_comparison(Registry().get(pkg)).policy == "reprocrowd-compare/3"
    assert json.loads((root / "comparison.json").read_text()).get("policy") is None
    within = next(row for row in saved["rows"] if row["status"] == "within")
    within["status"] = "match"
    save(saved)
    problem(client.put(p + "/archive"), 409, "report_stale")


def test_archive_refuses_evidence_that_no_longer_describes_scripts(client, pkg):
    root = package_with_targets(client, pkg)
    p = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    run(client, pkg)
    assert client.put(p + "/report", json={}).status_code == 201
    # E.g. a later coding-agent job edits the script; its sync keeps the manifest hashes current.
    (root / "analysis.py").write_text(SCRIPT + "# edited after the report\n")
    from reprocrowd import files
    from reprocrowd.package import Registry
    files.sync(Registry().get(pkg))
    problem(client.put(p + "/archive"), 409, "report_stale")
    assert client.put(p + "/report", json={}).status_code == 200
    assert json.loads((root / "evidence/execution.json").read_text())["stale"] == ["analysis.py"]
    assert "Changed inputs/scripts: analysis.py" in (root / "report.html").read_text()
    archived = client.put(p + "/archive")
    assert archived.status_code == 201, archived.text


def test_run_records_drop_tool_outputs():
    """targets.json/actions.json leave the machine; an agent's file reads must not."""
    from reprocrowd.providers import recorded_output
    secret = '{"type":"item.completed","item":{"type":"command_execution","command":"cat ~/private.md","aggregated_output":"PRIVATE","exit_code":0}}'
    answer_line = '{"type":"item.completed","item":{"type":"agent_message","text":"{\\"targets\\": []}"}}'
    full = "\n".join(['{"type":"thread.started","thread_id":"x"}', secret, answer_line, '{"type":"turn.completed","usage":{}}'])
    kept = recorded_output(full, "codex-json")
    assert "PRIVATE" not in kept and answer(kept, "codex-json") == answer(full, "codex-json") and terminal_error(kept, "codex-json") is None
    tool = '{"type":"tool_use","part":{"tool":"read","state":{"status":"completed","output":"PRIVATE"}}}'
    oc = "\n".join([tool, '{"type":"text","part":{"text":"{}"}}', '{"type":"error","error":{"message":"boom"}}'])
    assert "PRIVATE" not in recorded_output(oc, "opencode-json") and terminal_error(recorded_output(oc, "opencode-json"), "opencode-json") == "boom"
    assert recorded_output("plain answer", "text") == "plain answer"


def test_no_exec_env_reaches_the_agent(client, pkg, monkeypatch):
    import sys
    script = "import os, pathlib, sys; sys.stdin.read(); pathlib.Path('env-probe.txt').write_text(os.environ.get('RC_NO_EXEC', 'missing') + ' ' + ' '.join(sys.argv[1:])); pathlib.Path('pwd.txt').write_text(os.environ['PWD'])"
    monkeypatch.setitem(settings().agent_profiles, "probe", AgentProfile(argv=[sys.executable, "-c", script], events="text", tools=[], exec_tools=[],
                                                                          no_exec_argv=["--no-shell"], no_exec_env={"RC_NO_EXEC": "1"}))
    root = ready_package(client, pkg)
    job = wait(client, pkg, client.post(f"/v1/packages/{pkg}/jobs", json={"type": "codegen", "profile": "probe", "allow_exec": False}).json()["id"])
    assert job["status"] == "succeeded" and job["request"]["env"] == {"RC_NO_EXEC": "1"} and job["request"]["exec_restriction"] == "cli_settings"
    assert (root / "env-probe.txt").read_text() == "1 --no-shell"
    assert Path((root / "pwd.txt").read_text()).resolve() == root.resolve()  # OpenCode resolves relative paths from $PWD
    job = wait(client, pkg, client.post(f"/v1/packages/{pkg}/jobs", json={"type": "codegen", "profile": "probe", "allow_exec": True}).json()["id"])
    assert (root / "env-probe.txt").read_text() == "missing "


def test_app_default_config_matches_backend_defaults():
    """New installs get backend.toml from src/main/backend.ts; its shipped agent/LLM profiles must equal the backend's."""
    import re, tomllib
    from pathlib import Path
    ts = (Path(__file__).parents[2] / "src/main/backend.ts").read_text()
    app_cfg = tomllib.loads(re.search(r"DEFAULT_USER_CONFIG = `([^`]*)`", ts).group(1))
    for name, prof in _default_agents().items():
        got = AgentProfile.model_validate(app_cfg["agent"]["profiles"][name])
        assert (got.argv, got.no_exec_argv, got.no_exec_env, got.events) == (prof.argv, prof.no_exec_argv, prof.no_exec_env, prof.events), name
    for name in ("claude", "opencode", "codex"):
        assert LlmProfile.model_validate(app_cfg["llm"]["profiles"][name]).argv == _default_profiles()[name].argv, name


def test_evidence_survives_bad_inputs_and_rejects_edited_logs(client, pkg):
    root = package_with_targets(client, pkg)
    p = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    job = run(client, pkg)
    assert client.get(p + "/evidence").json()["log_path"] == job["stdout"]
    # A log changed after the run is no longer offered as that run's log.
    with open(root / job["stdout"], "a") as log:
        log.write("edited later\n")
    assert client.get(p + "/evidence").json()["log_path"] is None
    # Malformed output and a broken plan stay reportable instead of failing with 500.
    (root / "outputs/results.json").write_text('{"results": [{"id": ["T001"], "step": ["A01"]}, {"id": "T001", "step": {"x": 1}, "value": 2.31}]}')
    (root / "analysis_plan.json").write_text("not json")
    ev = client.get(p + "/evidence")
    assert ev.status_code == 200 and ev.json()["targets"][0]["step_evidence"] == "none"
    (root / "outputs/results.json").write_text('{"results": [{"id": "T001", "value": 2.31, "step": "A01"}]}')
    assert client.get(p + "/evidence").json()["targets"][0]["step_evidence"] == "declared_unchecked"
    cmp = client.get(p + "/comparison")
    assert cmp.status_code == 200 and any("analysis_plan.json is missing or invalid" in w for w in cmp.json()["output_warnings"])


def test_numeric_expected_edit_replaces_printed_precision(client, pkg):
    url = f"/v1/packages/{pkg}/targets"
    base = dict(type="number", description="value", unreferenced=True, note="test")
    assert client.post(url, json=base | {"expected": "0.170"}).status_code == 201
    r = client.patch(url + "/T001", json={"expected": 0.18})
    assert r.status_code == 200, r.text
    assert (r.json()["item"]["printed"], r.json()["item"]["decimals"]) == (None, None)
