"""Claims mode of the master script: a generated runner, a shared prepare file and one file per ticked claim."""
import json
import subprocess
import sys
from pathlib import Path

from test_api import problem
from test_run import package_with_targets

from reprocrowd.schemas.exports import ReproducedOutput


def _claims(client, pkg, tick=("C1",)):
    """Package with the `ok` claims extraction (C1 atomic, C2 with two sub-claims sharing C1's method, C3 not testable)."""
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"}).status_code == 201
    for cid in tick:
        assert client.patch(f"{P}/claims/{cid}", json={"checked": True}).status_code == 200
    claims = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}
    actions = {i["item"]["id"]: i["item"] for i in client.get(P + "/actions").json()}
    return P, root, claims, actions


def _evidence(claim):
    return claim["evidence"] + [t for s in claim["sub_claims"] for t in s["evidence"]]


def test_claims_mode_files(client, pkg):
    P, root, claims, actions = _claims(client, pkg)
    r = client.put(P + "/script", json={"template": "python"})
    assert r.status_code == 200, r.text  # package_with_targets wrote the first script
    files = r.json()["files"]
    assert {"analysis.py", "prepare.py", "claims/C1.py"} <= set(files)
    assert "claims/C2.py" not in files and not (root / "claims" / "C2.py").exists()

    prep = [a for a in actions if actions[a]["kind"] == "transform"]
    plan = json.loads((root / "analysis_plan.json").read_text())
    ids = [s["id"] for s in plan["steps"]]
    assert prep[0] in ids and claims["C1"]["method"] in ids
    assert "A01" not in ids  # the manual action no ticked claim uses
    c2_only = set(_evidence(claims["C2"]))
    assert plan["steps"][-1]["produces"] == claims["C1"]["evidence"] and not c2_only & {t for s in plan["steps"] for t in s["produces"]}
    schema = json.loads((root / "results_schema.json").read_text())
    assert [t["id"] for t in schema["results"]] == claims["C1"]["evidence"]

    c1 = (root / "claims" / "C1.py").read_text()
    assert "TODO(LLM):" in c1 and claims["C1"]["text"] in c1 and f'record("{claims["C1"]["evidence"][0]}"' in c1
    assert "TODO(LLM):" in (root / "prepare.py").read_text()
    runner = (root / "analysis.py").read_text()
    assert "TODO(LLM)" not in runner and "C1" in runner
    prompt = (root / "AGENT_PROMPT.md").read_text()
    assert "claims/C1.py" in prompt and "never edit analysis.py" in prompt
    todo = {f["path"]: f["remaining"] for f in client.get(P + "/script").json()["todo"]["files"]}
    assert todo["claims/C1.py"] >= 1 and todo["prepare.py"] >= 1 and "claims/C2.py" not in todo
    assert client.get(P + "/validation").json()["valid"]


def test_snakemake_refused_in_claims_mode(client, pkg):
    P, *_ = _claims(client, pkg)
    problem(client.put(P + "/script", json={"template": "snakemake"}), 400, "template_not_claims_ready")


def test_claim_file_kept_unless_claim_changed(client, pkg):
    P, root, claims, _ = _claims(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "claims" / "C1.py").write_text("x = 1\n")
    (root / "prepare.py").write_text("y = 2\n")
    r = client.put(P + "/script", json={"template": "python"})
    assert r.status_code == 200, r.text
    assert (root / "claims" / "C1.py").read_text() == "x = 1\n" and (root / "prepare.py").read_text() == "y = 2\n"
    assert {"claims/C1.py", "prepare.py"} <= set(r.json()["kept"])

    assert client.patch(P + "/claims/C1", json={"text": "The effect was clearly significant."}).status_code == 200
    assert client.patch(P + "/claims/C1", json={"checked": True}).status_code == 200
    r = client.put(P + "/script", json={"template": "python"})
    assert r.status_code == 200 and "claims/C1.py" in r.json()["files"]
    c1 = (root / "claims" / "C1.py").read_text()
    assert "TODO(LLM):" in c1 and "clearly significant" in c1
    assert (root / "prepare.py").read_text() == "y = 2\n"


def test_unticked_claim_not_sourced(client, pkg):
    P, root, *_ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    assert (root / "claims" / "C2.py").exists() and "C2" in (root / "analysis.py").read_text()
    assert client.patch(P + "/claims/C2", json={"checked": False}).status_code == 200
    r = client.put(P + "/script", json={"template": "python"})
    assert r.status_code == 200
    assert (root / "claims" / "C2.py").exists() and "C2" not in (root / "analysis.py").read_text()
    paths = {f["path"] for f in client.get(P + "/files").json()}
    assert "claims/C2.py" in paths  # still a file of the package


def test_claim_error_isolated(client, pkg):
    P, root, claims, _ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    t1 = claims["C1"]["evidence"][0]
    t21, t22 = (s["evidence"][0] for s in claims["C2"]["sub_claims"])
    method = claims["C2"]["sub_claims"][0]["method"]
    (root / "prepare.py").write_text("base = 41\n")
    (root / "claims" / "C1.py").write_text(f'record("{t1}", 1.0, "{method}")\nraise ValueError("boom")\n')
    (root / "claims" / "C2.py").write_text(f'record("{t21}", base + 1, "{method}")\n')
    p = subprocess.run([sys.executable, "analysis.py"], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "boom" in p.stderr  # the traceback is in the run log
    out = ReproducedOutput.model_validate_json((root / "outputs" / "results.json").read_text())
    rows = {r.id: r for r in out.results}
    assert rows[t1].value is None and rows[t1].note.startswith("claim C1 failed") and "boom" in rows[t1].note
    assert rows[t21].value == 42 and rows[t21].step == method
    assert rows[t22].value is None and rows[t22].note == "not recorded by claims/C2.py"


def test_r_template_renders(client, pkg):
    P, root, claims, _ = _claims(client, pkg, tick=("C1", "C2"))
    r = client.put(P + "/script", json={"template": "r"})
    assert r.status_code == 200, r.text
    assert {"analysis.R", "prepare.R", "claims/C1.R", "claims/C2.R"} <= set(r.json()["files"])
    runner = (root / "analysis.R").read_text()
    assert "tryCatch" in runner and "sys.source" in runner and "TODO(LLM)" not in runner
    assert "TODO(LLM):" in (root / "claims" / "C2.R").read_text()
    assert not (root / "analysis.py").exists() and not (root / "claims" / "C1.py").exists()


def test_no_ticked_claim(client, pkg):
    P, *_ = _claims(client, pkg, tick=())
    problem(client.put(P + "/script", json={"template": "python"}), 422, "nothing_to_script")


def test_no_claims_unchanged(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    r = client.put(P + "/script", json={"template": "python"})
    assert r.status_code == 200, r.text
    assert r.json()["files"] == ["AGENT_PROMPT.md", "README.md", "analysis.py", "analysis_plan.json", "results_schema.json"]
    assert not (root / "prepare.py").exists() and not (root / "claims").exists()
    assert client.put(P + "/script", json={"template": "snakemake"}).status_code == 200


def test_prepare_error_marks_every_claim(client, pkg):
    P, root, claims, _ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text('raise OSError("no data")\n')
    p = subprocess.run([sys.executable, "analysis.py"], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    rows = ReproducedOutput.model_validate_json((root / "outputs" / "results.json").read_text()).results
    assert len(rows) == 3 and all(r.value is None and "prepare.py failed: no data" in r.note for r in rows)


def _extract_and_tick(client, pkg, tick=("C1",)):
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"}).status_code == 201
    for cid in tick:
        assert client.patch(f"{P}/claims/{cid}", json={"checked": True}).status_code == 200
    return P


def test_edited_monolith_refused_kept_or_overwritten(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text("print('agent code')\n")
    P = _extract_and_tick(client, pkg)
    assert problem(client.put(P + "/script", json={"template": "python"}), 409, "script_modified")["files"] == ["analysis.py"]
    assert (root / "analysis.py").read_text() == "print('agent code')\n"

    r = client.put(P + "/script", json={"template": "python", "on_modified": "keep"})
    assert r.status_code == 200, r.text
    assert "analysis.pre-claims.py" in r.json()["kept"]
    assert (root / "analysis.pre-claims.py").read_text() == "print('agent code')\n"
    assert "CLAIMS = {" in (root / "analysis.py").read_text()
    files = {f["path"]: f["kind"] for f in client.get(P + "/files").json()}
    assert files["analysis.pre-claims.py"] == "code"
    # the runner itself is not "modified": a plain PUT goes through
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    assert client.get(P + "/validation").json()["valid"]


def test_edited_monolith_overwritten(client, pkg):
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text("print('agent code')\n")
    P = _extract_and_tick(client, pkg)
    r = client.put(P + "/script", json={"template": "python", "on_modified": "overwrite"})
    assert r.status_code == 200, r.text
    assert "CLAIMS = {" in (root / "analysis.py").read_text() and not (root / "analysis.pre-claims.py").exists()


def test_unedited_prepare_regenerated(client, pkg):
    P, root, *_ = _claims(client, pkg)
    REF = [{"extraction": "ex-paper-tei", "id": "sec3.p1.s1"}]
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    r = client.post(P + "/actions", json={"kind": "transform", "summary": "Winsorize reaction times", "refs": REF})
    assert r.status_code == 201, r.text
    new = r.json()["id"]
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    prep = (root / "prepare.py").read_text()
    assert f"==== {new} [transform]" in prep and f"TODO(LLM): implement {new}" in prep
    (root / "prepare.py").write_text("base = 1\n")
    r = client.put(P + "/script", json={"template": "python"})
    assert "prepare.py" in r.json()["kept"] and (root / "prepare.py").read_text() == "base = 1\n"


def test_run_uses_current_ticks(client, pkg):
    from test_jobs import wait

    P, root, claims, _ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    assert client.patch(P + "/claims/C2", json={"checked": False}).status_code == 200
    r = client.post(P + "/jobs", json={"type": "run"})
    assert r.status_code == 202, r.text
    job = wait(client, pkg, r.json()["id"])
    assert job["status"] == "succeeded", job
    runner = (root / "analysis.py").read_text()
    assert '"C1"' in runner and '"C2"' not in runner
    ids = {row["id"] for row in json.loads((root / "outputs" / "results.json").read_text())["results"]}
    assert ids == set(claims["C1"]["evidence"]) and not ids & set(_evidence(claims["C2"]))
    assert (root / "claims" / "C2.py").exists()


def test_codegen_preview_for_claim(client, pkg):
    P, root, claims, _ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    r = client.post(P + "/jobs:preview", json={"type": "codegen", "claim": "C1"})
    assert r.status_code == 200, r.text
    prompt = r.json()["prompt"]
    assert "claims/C1.py" in prompt and "Do not modify analysis.py" in prompt and "ASSUMPTION" in prompt
    assert claims["C1"]["text"] in prompt and "claims/C2.py" not in prompt
    assert "prepare.py" in prompt
    files = [ln.strip() for ln in prompt.splitlines() if ln.startswith("   - ")]
    assert files[-1] == "- claims/C1.py (claim C1): implement" and any(f.startswith("- prepare.py: ") for f in files), files
    r = client.post(P + "/jobs:preview", json={"type": "codegen", "claim": "C2"})
    p2 = r.json()["prompt"]
    assert "claims/C2.py" in p2 and "claims/C1.py" not in p2
    assert all(s["text"] in p2 for s in claims["C2"]["sub_claims"])
    whole = client.post(P + "/jobs:preview", json={"type": "codegen"}).json()["prompt"]
    assert "Do not modify analysis.py" not in whole


def test_codegen_claim_preconditions(client, pkg):
    P, *_ = _claims(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    for body, status, code in [({"claim": "C2"}, 409, "claim_not_checked"), ({"claim": "C3"}, 409, "claim_not_testable"),
                               ({"claim": "C9"}, 404, "not_found")]:
        for path in ("/jobs:preview", "/jobs"):
            r = client.post(P + path, json={"type": "codegen", **body})
            problem(r, status, code)


def test_codegen_claim_rerun_revises_its_file(client, pkg):
    P, root, claims, _ = _claims(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "claims" / "C1.py").write_text("x = 1\n")
    prompt = client.post(P + "/jobs:preview", json={"type": "codegen", "claim": "C1"}).json()["prompt"]
    line = next(ln for ln in prompt.splitlines() if "claims/C1.py (claim C1)" in ln)
    assert "implement" in line and "keep it as it is" not in line


def test_codegen_claim_job_request_and_no_evidence(client, pkg):
    P, root, claims, _ = _claims(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    job = client.post(P + "/jobs", json={"type": "codegen", "claim": "C1", "allow_exec": False})
    assert job.status_code == 202, job.text
    assert job.json()["request"]["claim"] == "C1"
    r = client.post(P + "/claims", json={"text": "no evidence", "refs": claims["C1"]["refs"]})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.patch(f"{P}/claims/{cid}", json={"checked": True}).status_code in (200, 409, 422)
    problem(client.post(P + "/jobs:preview", json={"type": "codegen", "claim": cid}), 409, "claim_no_evidence")
