"""Per-claim results (pooled evidence, sub-claims beside), claim states and run records."""
import json

from test_api import problem  # noqa: F401
from test_claims_script import _claims
from test_jobs import wait

from reprocrowd import claims


def test_verdict_rule():
    assert claims.verdict(["match"] * 14 + ["deviation"] * 4) == ("partly", 14, 18)
    assert claims.verdict(["missing", "missing"]) == ("not_reproduced", 0, 0)
    assert claims.verdict(["match", "within"]) == ("reproduced", 2, 2)
    assert claims.verdict(["figure", "unparsable", "match"]) == ("reproduced", 1, 1)
    assert claims.verdict(["deviation"]) == ("not_reproduced", 0, 1)


def _targets(client, P):
    return {i["item"]["id"]: i["item"] for i in client.get(P + "/targets").json()}


def _write_claim(root, client, P, cid, claim, good):
    """claims/<cid>.py recording every evidence target of the claim: its expected value (good) or a wrong one."""
    targets = _targets(client, P)
    ids = claim["evidence"] + [t for s in claim["sub_claims"] for t in s["evidence"]]
    lines = []
    for t in ids:
        exp = targets[t]["expected"]
        if isinstance(exp, (int, float)):
            val = exp if good(t) else exp * 3 + 100
        else:
            val = exp if good(t) else "zzz"
        lines.append(f"record({t!r}, {val!r}, 'A')")
    (root / "claims" / f"{cid}.py").write_text("\n".join(lines) + "\n")


def _run(client, P, pkg):
    r = client.post(P + "/jobs", json={"type": "run"})
    assert r.status_code == 202, r.text
    job = wait(client, pkg, r.json()["id"])
    assert job["status"] == "succeeded", job
    return job


def _status(client, P):
    return {s["claim"]: s for s in client.get(P + "/claims:status").json()}


def test_pooled_with_failed_sub_claim(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=("C2",))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    c2 = claims_["C2"]
    s1, s2 = c2["sub_claims"]
    good = set(s1["evidence"]) - set(s2["evidence"]) or set(s1["evidence"])
    _write_claim(root, client, P, "C2", c2, lambda t: t in good)
    _run(client, P, pkg)
    st = _status(client, P)["C2"]
    assert st["state"] == "partly" and st["stale"] is False
    res = st["result"]
    assert res["verdict"] == "partly" and 0 < res["matched"] < res["tested"]
    assert [s["verdict"] for s in res["by_sub_claim"]] == ["reproduced", "not_reproduced"]
    assert {e["target"] for s in res["by_sub_claim"] for e in s["evidence"]} == set(s1["evidence"]) | set(s2["evidence"])


def test_status_lifecycle(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=())
    assert _status(client, P)["C1"]["state"] == "review"
    assert client.patch(P + "/claims/C1", json={"checked": True}).status_code == 200
    st = _status(client, P)["C1"]
    assert st["state"] == "ready" and st["result"] is None and st["ran_at"] is None
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C1", claims_["C1"], lambda t: True)
    job = _run(client, P, pkg)
    st = _status(client, P)["C1"]
    assert st["state"] == "reproduced" and st["stale"] is False and st["ran_at"] and st["result"]["verdict"] == "reproduced"
    ran = client.get(P + "/claims:status").status_code == 200 and (root / "claims.json").read_text()
    assert job["id"] in ran
    assert client.patch(P + "/claims/C1", json={"text": claims_["C1"]["text"] + " (edited)"}).status_code == 200
    st = _status(client, P)["C1"]
    assert st["state"] == "review" and st["stale"] is True and st["result"]["verdict"] == "reproduced"


def test_unticked_claim_keeps_its_result(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=("C1",))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C1", claims_["C1"], lambda t: True)
    _run(client, P, pkg)
    assert client.patch(P + "/claims/C1", json={"checked": False}).status_code == 200
    st = _status(client, P)["C1"]
    assert st["state"] == "review" and st["stale"] is True and st["result"]["verdict"] == "reproduced"
    assert client.patch(P + "/claims/C1", json={"checked": True}).status_code == 200
    st = _status(client, P)["C1"]
    assert st["state"] == "reproduced" and st["stale"] is False and st["result"]["matched"] == st["result"]["tested"] > 0


def test_record_run_failure_does_not_break_run(client, pkg, monkeypatch):
    P, root, claims_, _ = _claims(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C1", claims_["C1"], lambda t: True)

    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(claims, "record_run", boom)
    job = _run(client, P, pkg)
    assert job["status"] == "succeeded"


def test_not_testable_status(client, pkg):
    P, *_ = _claims(client, pkg)
    st = _status(client, P)["C3"]
    assert st["state"] == "not_testable" and st["result"] is None


def test_running_state(client, pkg):
    P, root, *_ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("import time\ntime.sleep(3)\n")
    for cid in ("C1", "C2"):
        (root / "claims" / f"{cid}.py").write_text("")
    r = client.post(P + "/jobs", json={"type": "run"})
    assert r.status_code == 202
    st = _status(client, P)
    assert st["C1"]["state"] == "running" and st["C2"]["state"] == "running" and st["C3"]["state"] == "not_testable"
    wait(client, pkg, r.json()["id"])


def test_no_claims_mode_unchanged(client, pkg):
    from test_run import SCRIPT, package_with_targets

    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])
    assert client.get(P + "/claims:status").json() == []
    assert json.loads((root / "claims.json").read_text())["ran"] == {}


def _section(desc, cid):
    """The claims-table row of claim cid in a Zenodo description."""
    start = desc.index(f"<td>{cid}</td>")
    return desc[start:desc.index("</tr>", start)]


def test_ticked_but_unimplemented_claim_is_not_run(client, pkg):
    import respx

    from test_api import mock_openalex
    from test_zenodo import ME

    P, root, claims_, _ = _claims(client, pkg, tick=("C1", "C2"))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C1", claims_["C1"], lambda t: True)  # C2's file keeps its TODO(LLM): markers
    job = _run(client, P, pkg)
    runner = (root / "analysis.py").read_text()
    claims_block = runner[runner.index("CLAIMS = {"):runner.index("PENDING = {")]
    assert '"C1"' in claims_block and '"C2"' not in claims_block and '"C2"' in runner[runner.index("PENDING = {"):]
    assert job["request"]["claims"] == ["C1"]
    st = _status(client, P)
    assert st["C1"]["state"] == "reproduced" and st["C1"]["ran_at"]
    assert st["C2"]["ran_at"] is None and st["C2"]["result"] is None and st["C2"]["state"] == "ready"
    assert "C2" not in json.loads((root / "claims.json").read_text())["ran"]
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201
    with respx.mock:
        mock_openalex()
        r = client.post(P + "/deposits:preview", json={"creators": ME})
        assert r.status_code == 200, r.text
        desc = r.json()["metadata"]["description"]
    assert "Not run" in _section(desc, "C2") and "Reproduced" in _section(desc, "C1")


def test_claims_status_uses_report_settings(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=("C1",))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    targets = _targets(client, P)
    lines = [f"record({t!r}, {targets[t]['expected'] * 1.07!r}, 'A')" for t in claims_["C1"]["evidence"]]  # 7 % off
    (root / "claims" / "C1.py").write_text("\n".join(lines) + "\n")
    _run(client, P, pkg)
    assert _status(client, P)["C1"]["state"] == "not_reproduced"  # no report: the default 5 %
    assert client.put(P + "/report", json={"threshold": 10}).status_code in (200, 201)
    assert _status(client, P)["C1"]["state"] == "reproduced"  # the report's 10 %
    st = {s["claim"]: s for s in client.get(P + "/claims:status", params={"threshold": 5}).json()}
    assert st["C1"]["state"] == "not_reproduced"
    assert client.get(P + "/claims:status", params={"rounding": "x"}).status_code == 422
    trace = client.get(P + "/claims:trace", params={"threshold": 5}).json()
    assert trace["claims"][0]["result"]["verdict"] == "not_reproduced"
    _run(client, P, pkg)  # the run record's snapshot is compared with the report's settings too
    rec = json.loads((root / "claims.json").read_text())["ran"]["C1"]
    assert rec["result"]["verdict"] == "reproduced"


def test_deleted_manual_claim_id_not_reused(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=())
    c1 = claims_["C1"]
    r = client.post(P + "/claims", json={"text": "manual", "refs": c1["refs"], "method": c1["method"], "evidence": c1["evidence"]})
    assert r.status_code == 201 and r.json()["id"] == "C4"
    assert client.patch(P + "/claims/C4", json={"checked": True}).status_code == 200
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C4", {**c1, "sub_claims": []}, lambda t: True)
    _run(client, P, pkg)
    assert _status(client, P)["C4"]["ran_at"]
    assert client.delete(P + "/claims/C4").status_code == 204
    assert "C4" not in json.loads((root / "claims.json").read_text())["ran"]
    r = client.post(P + "/claims", json={"text": "another", "refs": c1["refs"]})
    assert r.status_code == 201 and r.json()["id"] == "C5"
    assert _status(client, P)["C5"]["ran_at"] is None


def test_status_words_unticked_or_changed(client, pkg):
    P, root, claims_, _ = _claims(client, pkg, tick=("C1",))
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    _write_claim(root, client, P, "C1", claims_["C1"], lambda t: True)
    _run(client, P, pkg)

    def words():
        assert client.put(P + "/report", json={}).status_code in (200, 201)
        html = (root / "report.html").read_text()
        return html[html.index("<h2>Claims</h2>"):html.index("<h2>Results comparison</h2>")]

    assert "since)" not in words()
    assert client.patch(P + "/claims/C1", json={"checked": False}).status_code == 200
    w = words()
    assert "(unticked since)" in w and "changed since" not in w
    assert client.patch(P + "/claims/C1", json={"text": claims_["C1"]["text"] + " (edited)"}).status_code == 200
    w = words()
    assert "(claim changed since)" in w and "unticked since" not in w
