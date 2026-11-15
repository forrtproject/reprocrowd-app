"""Contract regressions shared by reports, command-line clients and benchmarks."""
import json
import zipfile

import respx

from conftest import FIXTURES
from test_api import mock_openalex, problem
from test_jobs import wait, ready_package
from test_run import SCRIPT, package_with_targets
from test_zenodo import ME
from reprocrowd.config import settings


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


def test_code_context(client, pkg, tmp_path):
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
    job = wait(client, pkg, client.post(p + "/jobs", json=body).json()["id"])
    assert job["request"]["code_files_on_disk"] == ["inputs/authors.py"]


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


def test_cli_evidence_uses_shared_api(client, pkg, monkeypatch):
    from typer.testing import CliRunner
    from reprocrowd import cli
    ready_package(client, pkg)
    def through_test_api(method, url, **kwargs):
        return client.request(method, url, json=kwargs.get("json"), params=kwargs.get("params"), headers=kwargs.get("headers"))
    monkeypatch.setattr(cli.httpx, "request", through_test_api)
    runner = CliRunner()
    evidence = runner.invoke(cli.app, ["evidence", pkg, "--json"])
    assert evidence.exit_code == 0, evidence.output
    assert json.loads(evidence.stdout) == client.get(f"/v1/packages/{pkg}/evidence").json()
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
