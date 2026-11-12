from pathlib import Path
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
    # (case, POST status/code, PATCH status/code). A POST body is checked against the request schema first, as for
    # targets; the sub-claim id rule needs the new claim's id, so it is only caught by the model.
    cases = [
        ({"mode": "paraphrase"}, "request_invalid"),
        ({"sub_claims": [sub], "evidence": ["T001"]}, "request_invalid"),
        ({"testable": False, "evidence": ["T001"]}, "request_invalid"),
        ({"sub_claims": [sub | {"id": "C9.1"}]}, "schema_invalid"),
    ]
    for extra, code in cases:
        problem(client.post(P + "/claims", json=base | extra), 422, code)
    assert client.post(P + "/claims", json=base).status_code == 201
    for extra, _ in cases:  # a PATCH is merged into the stored claim and validated by the model
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


def test_delete_target_or_action_used_by_claim(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/claims", json={"text": "x", "refs": REF, "method": "A01", "evidence": ["T001"]}).status_code == 201
    assert client.post(P + "/claims", json={"text": "y", "refs": REF, "sub_claims": [
        {"id": "C2.1", "text": "s", "refs": REF, "evidence": ["T002"]}]}).status_code == 201
    assert "C1" in problem(client.delete(P + "/targets/T001"), 409, "in_use")["used_by"]
    assert "C2" in problem(client.delete(P + "/targets/T002"), 409, "in_use")["used_by"]
    assert "C1" in problem(client.delete(P + "/actions/A01"), 409, "in_use")["used_by"]
    assert client.delete(P + "/claims/C1").status_code == 204
    assert client.delete(P + "/targets/T001").status_code == 409  # still produced by A01


def _tick_c1(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/claims", json={"text": "x", "refs": REF, "method": "A01", "evidence": ["T001"]}).status_code == 201
    r = client.patch(P + "/claims/C1", json={"checked": True})
    assert r.status_code == 200 and r.json()["item"]["checked"] is True
    return P


def _checked(client, P):
    return client.get(P + "/claims").json()[0]["item"]["checked"]


def test_edit_resets_tick(client, pkg):
    P = _tick_c1(client, pkg)
    r = client.patch(P + "/claims/C1", json={"text": "changed"})
    assert r.status_code == 200 and r.json()["item"]["checked"] is False


def test_notes_keep_tick(client, pkg):
    P = _tick_c1(client, pkg)
    r = client.patch(P + "/claims/C1", json={"notes": "n"})
    assert r.status_code == 200 and r.json()["item"]["checked"] is True


def test_target_edit_unticks_claim(client, pkg):
    P = _tick_c1(client, pkg)
    assert client.patch(P + "/targets/T001", json={"expected": 2.4}).status_code == 200
    assert _checked(client, P) is False


def test_content_hash_ignores_notes(client, pkg):
    from reprocrowd.claims import content_hash
    from reprocrowd.curation import Curation
    from reprocrowd.package import Package
    P = _tick_c1(client, pkg)
    p = Package(pkg, Path(client.get(P).json()["path"]))

    def h():
        return content_hash(p, Curation(p, "claims").effective()[0][0])
    h0 = h()
    client.patch(P + "/claims/C1", json={"notes": "n"})
    assert h() == h0 and h0.startswith("sha256:")
    client.patch(P + "/targets/T001", json={"expected": 2.4})
    assert h() != h0
