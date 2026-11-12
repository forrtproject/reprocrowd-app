from test_api import problem
from test_run import REF, package_with_targets


def test_add_atomic_claim(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    r = client.post(P + "/claims", json={"text": "t is 2.31", "refs": REF, "method": "A01", "evidence": ["T001"]})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "C1"
    items = client.get(P + "/claims").json()
    assert len(items) == 1 and items[0]["origin"]["source"] == "manual"


def test_claim_rules(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    base = {"text": "x", "refs": REF}
    sub = {"id": "C1.1", "text": "s", "refs": REF}
    bad = (
        {"mode": "paraphrase"},
        {"sub_claims": [sub], "evidence": ["T001"]},
        {"testable": False, "evidence": ["T001"]},
        {"sub_claims": [sub | {"id": "C9.1"}]},
    )
    for extra in bad:  # a POST body is checked against the request schema first, as for targets; the id rule needs the new id
        problem(client.post(P + "/claims", json=base | extra), 422, "schema_invalid" if "sub_claims" in extra and "evidence" not in extra else "request_invalid")
    assert client.post(P + "/claims", json=base).status_code == 201
    for extra in bad:  # a PATCH is merged into the stored claim and validated by the model
        problem(client.patch(P + "/claims/C1", json=extra), 422, "schema_invalid")
    assert client.post(P + "/claims", json=base | {"sub_claims": [sub | {"id": "C2.1"}]}).status_code == 201


def test_claim_refs_resolve(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    r = client.post(P + "/claims", json={"text": "x", "refs": REF, "evidence": ["T999"]})
    assert "evidence: no target 'T999'" in problem(r, 422, "invalid_reference")["errors"]
    r = client.post(P + "/claims", json={"text": "x", "refs": REF, "depends_on": ["C7"]})
    assert "depends_on: no claim 'C7'" in problem(r, 422, "invalid_reference")["errors"]


def test_old_package_without_claims_file(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "claims.json").unlink()
    import json
    mf = root / "datapackage.json"
    m = json.loads(mf.read_text())
    m["resources"] = [r for r in m["resources"] if r["name"] != "claims"]
    mf.write_text(json.dumps(m))
    r = client.get(P + "/claims")
    assert r.status_code == 200 and r.json() == []
    assert client.post(P + "/claims", json={"text": "x", "refs": REF}).status_code == 201
    assert (root / "claims.json").exists()
