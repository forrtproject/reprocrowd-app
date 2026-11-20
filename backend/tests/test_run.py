import json
import pytest
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
    from reprocrowd import run
    # no shell (Windows has none): the Python found for the run, then the script
    assert r.status_code == 202 and r.json()["argv"] == [*run.find_program("python3"), "analysis.py"], r.json()["argv"]
    assert Path(r.json()["argv"][0]).name.startswith("py")
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


def test_child_environments_are_allowlisted():
    """Issue #91: scripts and agents don't get the app's credentials or the path of the backend's token file."""
    from reprocrowd import childenv

    env = {"PATH": "/usr/bin", "HOME": "/home/u", "LC_ALL": "C", "https_proxy": "http://proxy:3128", "R_LIBS_USER": "/r",
           "AWS_SECRET_ACCESS_KEY": "aws", "GITHUB_TOKEN": "gh", "ANTHROPIC_API_KEY": "ant", "OPENAI_API_KEY": "oai",
           "REPROCROWD_CONFIG": "/cfg.toml", "REPROCROWD_TOKEN": "t", "MY_GATEWAY_URL": "g"}
    assert set(childenv.for_script(env)) == {"PATH", "HOME", "LC_ALL", "https_proxy", "R_LIBS_USER"}
    claude = childenv.for_cli(["/usr/local/bin/claude", "-p"], env=env)
    assert {"ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "PATH"} <= set(claude) and not {"GITHUB_TOKEN", "OPENAI_API_KEY", "R_LIBS_USER"} & set(claude)
    codex = childenv.for_cli([r"C:\npm\codex.cmd", "exec"], env=env)
    assert "OPENAI_API_KEY" in codex and "ANTHROPIC_API_KEY" not in codex
    assert "MY_GATEWAY_URL" in childenv.for_cli(["mycli"], ["MY_GATEWAY_*"], env=env)
    assert not any(k.startswith("REPROCROWD_") for k in childenv.for_cli(["mycli"], ["REPROCROWD_*", "*"], env=env))


def test_template_commands_need_no_shell():
    """Commands are started without a shell (Windows has no bash): a program and its arguments only."""
    import shlex
    import tomllib
    from importlib import resources
    for t in resources.files("reprocrowd.templates").iterdir():
        if t.is_dir() and t.joinpath("template.toml").is_file():
            command = tomllib.loads(t.joinpath("template.toml").read_text(encoding="utf-8"))["command"]
            argv = shlex.split(command)
            assert not any(a in {"&&", "||", "|", ";", ">", "<", "&"} for a in argv), command
    assert shlex.split("Rscript -e 'targets::tar_make()'") == ["Rscript", "-e", "targets::tar_make()"]


def test_programs_are_found_where_windows_and_r_put_them(monkeypatch):
    from reprocrowd import run
    from reprocrowd.errors import ApiError
    on_path: dict[str, str] = {}
    monkeypatch.setattr(run.shutil, "which", lambda name, path=None: on_path.get(name))
    monkeypatch.setattr(run, "_store_stub", lambda p: "WindowsApps" in p)
    stub_runs = False
    monkeypatch.setattr(run, "_runs", lambda argv: stub_runs)
    monkeypatch.setattr(run, "_r_installs", lambda: [])
    # nothing installed: a clear error that says where to get it
    with pytest.raises(ApiError) as e:
        run.run_argv("python3 analysis.py")
    assert e.value.code == "program_not_found" and "python.org" in e.value.message
    # only the Store's stand-in: not a Python, so the same clear error
    on_path["python3"] = r"C:\Users\a\AppData\Local\Microsoft\WindowsApps\python3.exe"
    with pytest.raises(ApiError) as e:
        run.run_argv("python3 analysis.py")
    assert e.value.code == "program_not_found"
    # but a Python installed from the Store is used
    stub_runs = True
    assert run.run_argv("python3 analysis.py")[0].endswith("WindowsApps\\python3.exe")
    stub_runs = False
    del on_path["python3"]
    # Windows: no python3, but python, or only the py launcher
    on_path["py"] = r"C:\Windows\py.exe"
    assert run.run_argv("python3 analysis.py") == [r"C:\Windows\py.exe", "-3", "analysis.py"]
    on_path["python"] = r"C:\Python313\python.exe"
    assert run.run_argv("python3 analysis.py") == [r"C:\Python313\python.exe", "analysis.py"]
    # the Microsoft Store's python3 stand-in is passed over for a real Python
    on_path["python3"] = r"C:\Users\a\AppData\Local\Microsoft\WindowsApps\python3.exe"
    assert run.run_argv("python3 analysis.py")[0] == r"C:\Python313\python.exe"
    # R: not on the PATH, but where its installer put it
    with pytest.raises(ApiError) as e:
        run.run_argv("Rscript analysis.R")
    assert "r-project.org" in e.value.message
    monkeypatch.setattr(run, "_r_installs", lambda: [r"C:\Program Files\R\R-4.5.1\bin\Rscript.exe"])
    assert run.run_argv("Rscript -e 'targets::tar_make()'") == [r"C:\Program Files\R\R-4.5.1\bin\Rscript.exe", "-e", "targets::tar_make()"]
    # elsewhere, the program on the PATH as it is
    on_path["snakemake"] = "/usr/bin/snakemake"
    assert run.run_argv("snakemake --cores 1") == ["/usr/bin/snakemake", "--cores", "1"]


def test_a_missing_program_changes_nothing(client, pkg, monkeypatch):
    """Without Python, the run is refused before the old results are removed or anything is committed."""
    from reprocrowd import gitrepo, run
    root = package_with_targets(client, pkg)
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs" / "results.json").write_text("{}")
    head = gitrepo.head(run.Package(pkg, root)) if gitrepo.available() else None
    monkeypatch.setattr(run, "find_program", lambda name: None)
    problem(client.post(f"/v1/packages/{pkg}/jobs", json={"type": "run"}), 409, "program_not_found")
    assert (root / "outputs" / "results.json").read_text() == "{}"
    assert not (root / "jobs").exists() or not any((root / "jobs").iterdir())
    if gitrepo.available():
        assert gitrepo.head(run.Package(pkg, root)) == head
