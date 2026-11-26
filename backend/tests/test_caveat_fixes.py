"""Figure files checked on disk, differing table text as deviations, second edits to stale files, unrounded R output."""
import json
import re

import pytest

from test_api import problem
from test_jobs import wait
from test_run import REF, SCRIPT, package_with_targets

from reprocrowd.compare import check_output, compare, compare_target
from reprocrowd.schemas.comparison import CompareSettings
from reprocrowd.schemas.curation import Target
from reprocrowd.schemas.exports import OutputResult

FIG = Target(id="T004", type="figure", description="RT plot", refs=REF)


@pytest.mark.parametrize("setup, file, problem_text", [
    (None, "outputs/figures/T004.png", "figure file not found"),
    (b"", "outputs/figures/T004.png", "figure file is empty"),
    (None, "../outside.png", "figure path outside package"),
    (None, "/etc/hosts", "figure path outside package"),
    (None, "outputs/figures/bad\x00.png", "invalid figure path"),
    ("dir", "outputs/figures", "figure file not found"),
])
def test_figure_file_problems(tmp_path, setup, file, problem_text):
    root = tmp_path / "pkg"
    (root / "outputs" / "figures").mkdir(parents=True)
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")  # exists, but outside the package
    if isinstance(setup, bytes):
        (root / file).write_bytes(setup)
    row = compare_target(FIG, OutputResult(id="T004", file=file, note="drawn with ggplot"), CompareSettings(), root)
    assert row.status == "missing" and row.note == f"drawn with ggplot; {problem_text}"
    _, warnings, _ = check_output({"results": [{"id": "T004", "file": file}]}, [FIG], root=root)
    assert warnings == [f"results[0] (T004): {problem_text}: {file}"]
    result = compare([FIG], {"results": [{"id": "T004", "file": file}]}, CompareSettings(), root=root)
    assert result.summary == {"missing": 1} and result.output_warnings == warnings


def test_figure_file_present(tmp_path):
    (tmp_path / "outputs" / "figures").mkdir(parents=True)
    (tmp_path / "outputs" / "figures" / "T004.png").write_bytes(b"\x89PNG")
    row = compare_target(FIG, OutputResult(id="T004", file="outputs/figures/T004.png"), CompareSettings(), tmp_path)
    assert row.status == "figure" and row.note is None and row.reproduced_text == "outputs/figures/T004.png"
    assert check_output({"results": [{"id": "T004", "file": "outputs/figures/T004.png"}]}, [FIG], root=tmp_path)[1] == []
    # without a package folder (pure comparison), a file name is taken as given
    assert compare_target(FIG, OutputResult(id="T004", file="nowhere.png"), CompareSettings()).status == "figure"


def test_figure_missing_after_run(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT.replace('{"id": "T003"', '{"id": "T004", "file": "outputs/figures/T004.png"},\n    {"id": "T003"'))
    job = wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])
    assert "results[2] (T004): figure file not found: outputs/figures/T004.png" in job["result"]["output_warnings"]
    c = client.get(P + "/comparison").json()
    row = next(r for r in c["rows"] if r["target"] == "T004")
    assert row["status"] == "missing" and row["note"] == "figure file not found"
    assert "results[2] (T004): figure file not found: outputs/figures/T004.png" in c["output_warnings"]


def test_figure_result_without_file_warns(tmp_path):
    _, warnings, _ = check_output({"results": [{"id": "T004", "value": None}]}, [FIG], root=tmp_path)
    assert warnings == ['results[0] (T004): figure target has no "file"']
    res = compare([FIG], {"results": [{"id": "T004", "value": None}]}, CompareSettings(), root=tmp_path)
    assert res.rows[0].status == "missing" and res.output_warnings == warnings


def test_table_text_cells():
    t = Target(id="T003", type="table", description="d", refs=REF, expected=[["Group", "Effect"], ["A", "Positive"], ["B", "Negative"], ["C", "Neutral"]])
    row = compare_target(t, OutputResult(id="T003", table=[["A", "Negative"], ["B"], ["C", ""]]), CompareSettings())
    assert [[c.status for c in r] for r in row.cells] == [["match", "match"], ["match", "deviation"], ["match", "missing"], ["match", "missing"]]
    assert row.status == "deviation"
    ok = compare_target(t, OutputResult(id="T003", table=[["A", " positive "], ["B", "NEGATIVE"], ["C", "neutral"]]), CompareSettings())
    assert ok.status == "match"


def test_second_edit_to_stale_script(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201
    assert json.loads((root / "evidence" / "execution.json").read_text())["stale_hashes"] == {}

    (root / "analysis.py").write_text(SCRIPT + "# first edit\n")
    assert client.post(P + ":sync").status_code == 200  # an editor saved it
    problem(client.put(P + "/archive"), 409, "report_stale")
    assert client.put(P + "/report", json={}).status_code == 200  # the report now records analysis.py as stale
    published = json.loads((root / "evidence" / "execution.json").read_text())
    assert published["stale"] == ["analysis.py"] and published["stale_hashes"]["analysis.py"].startswith("sha256:")
    r = client.put(P + "/archive")
    assert r.status_code == 200, r.text

    (root / "analysis.py").write_text(SCRIPT + "# second edit\n")  # still stale, but not the file the report describes
    assert client.post(P + ":sync").status_code == 200
    problem(client.put(P + "/archive"), 409, "report_stale")
    assert client.put(P + "/report", json={}).status_code == 200
    assert client.put(P + "/archive").status_code == 200


@pytest.mark.parametrize("template, master", [("r", "analysis.R"), ("targets", "_targets.R")])
def test_r_templates_write_unrounded_json(client, pkg, template, master):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.put(P + "/script", json={"template": template}).status_code == 200
    script = (root / master).read_text()
    assert "write_reprocrowd_results <- function(results, path = \"outputs/results.json\")" in script
    assert 'jsonlite::write_json(list(results = I(norm)), path, auto_unbox = TRUE, digits = NA, null = "null", pretty = TRUE)' in script
    assert "dir.create(dirname(path), recursive = TRUE" in script
    markers = len(re.findall(r"TODO\(LLM\):", script))
    st = client.get(P + "/script").json()
    assert st["todo"]["remaining"] == markers and markers >= 2
    prompt = (root / "AGENT_PROMPT.md").read_text()
    assert f"`write_reprocrowd_results(results)` helper defined in {master}" in prompt and "digits = NA" in prompt


def test_deleted_stale_file_does_not_block_archive(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    (root / "helpers.py").write_text("x = 1\n")
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"
    (root / "helpers.py").unlink()
    assert client.post(P + ":sync").status_code == 200
    assert client.put(P + "/report", json={}).status_code == 201
    published = json.loads((root / "evidence" / "execution.json").read_text())
    assert published["stale"] == ["helpers.py"] and published["stale_hashes"] == {"helpers.py": None}
    r = client.put(P + "/archive")
    assert r.status_code == 201, r.text
