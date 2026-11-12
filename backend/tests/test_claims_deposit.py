"""Claims in the deposit: the JSON trace, the report's Claims section, the Zenodo table and new versions."""
import io
import json
import zipfile

import httpx
import respx

from test_api import mock_openalex, problem
from test_claims_results import _run, _write_claim
from test_claims_script import _claims
from test_run import SCRIPT
from test_jobs import wait
from test_zenodo import ME, Z, draft, mock_zenodo, published, uploaded_zip

ASSUMPTION = "# ASSUMPTION: age split at 3 years\n"


def _ran(client, pkg, tick=("C1",)):
    """Claims package where the ticked claims ran (all evidence reproduced), with C1's file flagging one assumption."""
    P, root, claims_, actions = _claims(client, pkg, tick=tick)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    (root / "prepare.py").write_text("")
    for cid in tick:
        _write_claim(root, client, P, cid, claims_[cid], lambda t: True)
    c1 = root / "claims" / "C1.py"
    c1.write_text(ASSUMPTION + c1.read_text())
    _run(client, P, pkg)
    return P, root, claims_


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


@respx.mock
def test_deposit_needs_a_claim_run(client, pkg):
    mock_openalex()
    P, root, *_ = _claims(client, pkg, tick=())
    (root / "analysis.py").write_text(SCRIPT)  # the monolithic script still runs; no claim is ticked, so none ran
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201
    z = mock_zenodo()
    problem(client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME}), 409, "no_claim_run")
    assert not z["create"].called


@respx.mock
def test_deposit_lists_claims(client, pkg):
    mock_openalex()
    P, root, claims_ = _ran(client, pkg)
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201
    z = mock_zenodo()
    r = client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["claims"] == ["C1"] and d["version_of"] is None
    desc = json.loads(z["create"].calls.last.request.content)["metadata"]["description"]
    n = len(claims_["C1"]["evidence"])
    assert "<table" in desc and "C1" in desc and f"Reproduced {n}/{n}" in desc
    assert "C2</td>" in desc and "Not run" in desc and "Not testable" in desc
    trace = json.loads(uploaded_zip(z["upload"]).read("claims_trace.json"))
    by_id = {c["claim_id"]: c for c in trace["claims"]}
    assert by_id["C1"]["result"]["verdict"] == "reproduced" and by_id["C1"]["status"] == "reproduced"
    assert "result" not in by_id["C2"] and by_id["C3"]["status"] == "not_testable"
    assert trace["paper"]["doi"] == "10.1371/journal.pbio.1002106"


@respx.mock
def test_redeposit_is_new_version(client, pkg):
    mock_openalex()
    P, root, claims_ = _ran(client, pkg)
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201
    mock_zenodo()
    first = client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME, "publish": True}).json()
    assert first["status"] == "published"

    # C2 is ticked and run too; a new archive, deposited again
    assert client.patch(P + "/claims/C2", json={"checked": True}).status_code == 200
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200
    _write_claim(root, client, P, "C2", claims_["C2"], lambda t: True)
    _run(client, P, pkg)
    assert client.put(P + "/archive").status_code == 200

    nv = respx.post(f"{Z}/api/deposit/depositions/101/actions/newversion").mock(
        return_value=httpx.Response(201, json=published() | {"links": {"latest_draft": f"{Z}/api/deposit/depositions/102"}}))
    old_file = {"id": "f-old", "filename": "old.zip", "links": {"self": f"{Z}/api/deposit/depositions/102/files/f-old"}}
    get_new = respx.get(f"{Z}/api/deposit/depositions/102").mock(return_value=httpx.Response(200, json=draft(102) | {"files": [old_file]}))
    del_file = respx.delete(f"{Z}/api/deposit/depositions/102/files/f-old").mock(return_value=httpx.Response(204))
    upload = respx.put(url__regex=rf"{Z}/api/files/b-102/.*").mock(return_value=httpx.Response(201, json={}))
    put_md = respx.put(f"{Z}/api/deposit/depositions/102").mock(return_value=httpx.Response(200, json=draft(102)))
    r = client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME})
    assert r.status_code == 201, r.text
    d = r.json()
    assert nv.called and get_new.called and del_file.called and upload.called and put_md.called
    assert d["version_of"] == first["id"] and d["deposition_id"] == 102 and d["status"] == "draft"
    assert d["claims"] == ["C1", "C2"] and d["doi"] == "10.5281/zenodo.102"
    md = json.loads(put_md.calls.last.request.content)["metadata"]
    assert "Not run" not in md["description"] and "C2" in md["description"]
    trace = json.loads(zipfile.ZipFile(io.BytesIO(upload.calls.last.request.content)).read("claims_trace.json"))
    assert {c["claim_id"]: c["status"] for c in trace["claims"]} == {"C1": "reproduced", "C2": "reproduced", "C3": "not_testable"}


def test_trace_has_no_tolerance_and_has_assumptions(client, pkg):
    P, root, claims_ = _ran(client, pkg)
    r = client.get(P + "/claims:trace")
    assert r.status_code == 200, r.text
    trace = r.json()
    assert "tolerance" not in set(_keys(trace))
    c1 = next(c for c in trace["claims"] if c["claim_id"] == "C1")
    assert c1["assumptions"] == ["age split at 3 years"]
    assert c1["id"] == "10.1371/journal.pbio.1002106" and c1["main_claim"] == claims_["C1"]["text"]
    ev = c1["quantitative_evidence"]
    assert [e["id"] for e in ev] == claims_["C1"]["evidence"] and all(e["label"] and e["source"] for e in ev)
    assert c1["methods"] and c1["methods"][0]["id"] == claims_["C1"]["method"]
    c2 = next(c for c in trace["claims"] if c["claim_id"] == "C2")
    assert [s["id"] for s in c2["sub_claims"]] == ["C2.1", "C2.2"] and c2["assumptions"] == []
    assert isinstance(trace["skipped"], list)


def test_report_claims_section(client, pkg):
    P, root, _ = _ran(client, pkg)
    assert client.put(P + "/report", json={}).status_code == 201
    html = (root / "report.html").read_text()
    assert "<h2>Claims</h2>" in html and "Reproduced" in html and "age split at 3 years" in html
    assert html.index("<h2>Claims</h2>") < html.index("<h2>Results comparison</h2>")
    m = client.get(P + "/manifest").json()
    tr = next(r for r in m["resources"] if r["name"] == "claims-trace")
    assert tr["path"] == "claims_trace.json" and tr["reprocrowd"]["kind"] == "report"


def test_no_claims_report_unchanged(client, pkg):
    from test_submit import ready

    root = ready(client, pkg)
    assert "<h2>Claims</h2>" not in (root / "report.html").read_text()
    assert not (root / "claims_trace.json").exists()
    assert client.get(f"/v1/packages/{pkg}/claims:trace").json()["claims"] == []
