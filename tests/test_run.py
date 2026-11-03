import json
from pathlib import Path

from conftest import FIXTURES
from test_api import problem
from test_jobs import wait

from reprocrowd.compare import compare_target, decimals_of, parse_num
from reprocrowd.schemas.comparison import CompareSettings
from reprocrowd.schemas.curation import Target
from reprocrowd.schemas.exports import OutputResult

REF = [{"extraction": "ex-paper-tei", "id": "sec3.p1.s1"}]
SCRIPT = """import json, os, sys
print("computing")
print("a warning", file=sys.stderr)
os.makedirs("outputs", exist_ok=True)
json.dump({"results": [
    {"id": "T001", "value": 2.3149, "step": "A01"},
    {"id": "T002", "value": "Yes"},
    {"id": "T003", "table": [["A", 512.38]]},
]}, open("outputs/results.json", "w"))
"""


def package_with_targets(client, pkg) -> Path:
    P = f"/v1/packages/{pkg}"
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.tei.xml")})
    client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"})
    for t in ({"type": "number", "description": "t", "expected": 2.31, "decimals": 2},
              {"type": "categorical", "description": "significant?", "expected": "yes"},
              {"type": "table", "description": "descriptives", "expected": [["Group", "M"], ["A", "512.4"]]},
              {"type": "figure", "description": "RT plot"}):
        assert client.post(P + "/targets", json=t | {"refs": REF}).status_code == 201
    client.post(P + "/actions", json={"kind": "analysis", "summary": "everything", "refs": REF, "produces": ["T001", "T002", "T003", "T004"]})
    assert client.put(P + "/script", json={"template": "python"}).status_code == 201
    return Path(client.get(P).json()["path"])


def test_run_and_compare(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    problem(client.get(P + "/comparison"), 409, "results_missing")
    (root / "analysis.py").write_text(SCRIPT)
    r = client.post(P + "/jobs", json={"type": "run"})
    assert r.status_code == 202 and r.json()["argv"] == ["bash", "-c", "python3 analysis.py"]
    job = wait(client, pkg, r.json()["id"])
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["type"] == "run" and res["results_file"] and res["output_errors"] == []
    assert res["output_warnings"] == ["T004: missing from the output"] and res["commit_after"]
    log = [e["text"] for e in client.get(f"{P}/jobs/{job['id']}/events").json()]
    assert log == ["computing", "a warning"]  # stderr is merged into the log
    files = {f["path"]: f["kind"] for f in client.get(P + "/files").json()}
    assert files["outputs/results.json"] == "output"

    c = client.get(P + "/comparison").json()
    rows = {r["target"]: r for r in c["rows"]}
    assert [rows[t]["status"] for t in ("T001", "T002", "T003", "T004")] == ["match", "match", "match", "missing"]
    assert rows["T001"]["reproduced_text"].startswith("2.31") and rows["T003"]["matched"] == 1
    assert c["summary"] == {"match": 3, "missing": 1}
    c = client.get(P + "/comparison", params={"rounding": 3, "threshold": 1}).json()
    assert c["rows"][0]["status"] == "within" and 0.2 < c["rows"][0]["deviation"] < 0.3
    assert client.get(P + "/comparison", params={"rounding": 3, "threshold": 0}).json()["rows"][0]["status"] == "deviation"
    problem(client.get(P + "/comparison", params={"rounding": "lots"}), 422, "request_invalid")
    assert client.get(P + "/validation").json()["valid"]

    # a failing run leaves no stale results behind
    (root / "analysis.py").write_text("raise SystemExit('boom')\n")
    job = wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])
    assert job["status"] == "failed" and job["exit_code"] == 1 and not job["result"]["results_file"]
    assert "boom" in client.get(f"{P}/jobs/{job['id']}/stdout").text
    problem(client.get(P + "/comparison"), 409, "results_missing")

    (root / "analysis.py").write_text("import json,os\nos.makedirs('outputs',exist_ok=True)\njson.dump({'results':[{'id':'T001','value':'n/a'}]},open('outputs/results.json','w'))\n")
    job = wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])
    assert job["result"]["output_errors"] == ["results[0] (T001): number target needs a numeric value"]


def test_run_needs_script(client, pkg):
    problem(client.post(f"/v1/packages/{pkg}/jobs", json={"type": "run"}), 409, "script_missing")


def test_compare_rules():
    st = CompareSettings()
    assert parse_num("−1,234.5%") == -1234.5 and parse_num("< .001") == 0.001 and parse_num("n.s.") is None
    assert decimals_of("0.026") == 3 and decimals_of(12) == 0

    def row(t, **out):
        return compare_target(Target(id="T001", refs=[{"extraction": "x", "id": "tab1"}], description="d", **t), OutputResult(id="T001", **out), st)

    assert row({"type": "number", "expected": 0.001, "comparator": "<"}, value=0.0004).status == "match"
    assert row({"type": "number", "expected": 0.001, "comparator": "<"}, value=0.002).status == "deviation"
    assert row({"type": "number", "expected": 12.0}, value=12.4).status == "within"  # 3.3 % ≤ 5 %
    assert row({"type": "number", "expected": 12}, value=12.4).deviation > 3
    r = row({"type": "table", "expected": [["", "M"], ["A", "1.00"], ["B", "<0.05"]]}, table=[["M"], [1.04], [0.01]])
    assert (r.status, r.matched, r.total) == ("within", 1, 2)
    assert row({"type": "categorical", "expected": "Positive"}, value=" positive ").status == "match"


def test_jobs_do_not_inherit_the_service_virtualenv(monkeypatch):
    import sys

    from reprocrowd.jobs import job_env

    monkeypatch.setenv("PATH", f"{sys.prefix}/bin:/usr/local/bin:/usr/bin")
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    env = job_env({"X": "1"})
    assert env["PATH"] == "/usr/local/bin:/usr/bin" and "VIRTUAL_ENV" not in env and env["X"] == "1"
