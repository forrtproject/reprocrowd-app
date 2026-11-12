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


REF2 = [{"extraction": "ex-paper-tei", "id": "sec3.p1.s2"}]


def _tick_sub(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    sub = {"id": "C1.1", "text": "s", "refs": REF, "evidence": ["T001"]}
    assert client.post(P + "/claims", json={"text": "x", "refs": REF, "sub_claims": [sub]}).status_code == 201
    assert client.patch(P + "/claims/C1", json={"checked": True}).json()["item"]["checked"] is True
    return P, sub


def test_sub_claim_refs_keep_tick_but_text_resets(client, pkg):
    P, sub = _tick_sub(client, pkg)
    r = client.patch(P + "/claims/C1", json={"sub_claims": [sub | {"refs": REF2}]})
    assert r.status_code == 200 and r.json()["item"]["checked"] is True
    r = client.patch(P + "/claims/C1", json={"sub_claims": [sub | {"text": "other"}]})
    assert r.json()["item"]["checked"] is False


def test_sub_claim_evidence_resets_tick(client, pkg):
    P, sub = _tick_sub(client, pkg)
    r = client.patch(P + "/claims/C1", json={"sub_claims": [sub | {"evidence": ["T002"]}]})
    assert r.status_code == 200 and r.json()["item"]["checked"] is False


def test_noop_patch_keeps_tick(client, pkg):
    P = _tick_c1(client, pkg)
    r = client.patch(P + "/claims/C1", json={"text": "x"})
    assert r.status_code == 200 and r.json()["item"]["checked"] is True


def test_action_edit_unticks_claim(client, pkg):
    P = _tick_c1(client, pkg)
    assert client.patch(P + "/actions/A01", json={"summary": "changed"}).status_code == 200
    assert _checked(client, P) is False


def test_drop_overlay_unticks_claim(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/targets/runs", json={"extraction": "ex-paper-tei", "scope": {"sections": ["sec3"]}}).status_code == 201
    llm = next(t["item"]["id"] for t in client.get(P + "/targets").json() if t["origin"]["source"] == "llm")
    assert client.patch(P + f"/targets/{llm}", json={"description": "edited"}).status_code == 200
    assert client.post(P + "/claims", json={"text": "x", "refs": REF, "evidence": [llm]}).status_code == 201
    assert client.patch(P + "/claims/C1", json={"checked": True}).json()["item"]["checked"] is True
    assert client.delete(P + f"/targets/{llm}/edit").status_code == 200
    assert _checked(client, P) is False


def test_malformed_sub_claims_patch_is_422(client, pkg):
    P = _tick_c1(client, pkg)
    before = client.get(P + "/claims/C1").json()
    # the route's request schema rejects a non-list or a list of non-objects; null reaches the model
    problem(client.patch(P + "/claims/C1", json={"sub_claims": ["x"]}), 422, "request_invalid")
    problem(client.patch(P + "/claims/C1", json={"sub_claims": "x"}), 422, "request_invalid")
    problem(client.patch(P + "/claims/C1", json={"sub_claims": None}), 422, "schema_invalid")
    assert client.get(P + "/claims/C1").json() == before


def test_malformed_sub_claims_reach_model_as_schema_invalid(client, pkg):
    """Curation.patch itself (below the route's request check) must report malformed sub_claims as schema_invalid."""
    from reprocrowd.curation import Curation
    from reprocrowd.errors import ApiError
    from reprocrowd.package import Package
    P = _tick_c1(client, pkg)
    p = Package(pkg, Path(client.get(P).json()["path"]))
    for bad in (["x"], None, "x"):
        try:
            Curation(p, "claims").patch("C1", {"sub_claims": bad})
        except ApiError as e:
            assert e.code == "schema_invalid", e
        else:
            raise AssertionError("expected ApiError")
    assert Curation(p, "claims").effective()[0][0].checked is True


def _extract(client, pkg):
    package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    r = client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"})
    assert r.status_code == 201, r.text
    return P, r.json()


def test_extract_claims(client, pkg):
    P, run = _extract(client, pkg)
    claims = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}
    assert list(claims) == ["C1", "C2", "C3"]
    subs = claims["C2"]["sub_claims"]
    assert [s["id"] for s in subs] == ["C2.1", "C2.2"] and subs[0]["method"] == subs[1]["method"] is not None
    assert claims["C3"]["testable"] is False and claims["C3"]["depends_on"] == ["C1", "C2"]
    actions = [i["item"] for i in client.get(P + "/actions").json()]
    prep = [a for a in actions if a["kind"] == "transform"]
    assert len(prep) == 1 and prep[0]["summary"] == "Exclude incomplete records"
    llm_analysis = [a for a in actions if a["kind"] == "analysis" and a["summary"] == "Run the t-test"]
    assert len(llm_analysis) == 1 and llm_analysis[0]["depends_on"] == [prep[0]["id"]]
    assert claims["C1"]["method"] == subs[0]["method"] == llm_analysis[0]["id"]
    assert len(llm_analysis[0]["produces"]) == 3
    raw = client.get(P + "/claims?view=raw").json()
    assert raw["skipped"]["run1"] == [{"ref": {"extraction": "ex-paper-tei", "id": "sec2.p1.s2"}, "reason": "study_design"}]


def test_extract_keeps_good_parts(client, pkg):
    P, _ = _extract(client, pkg)
    runs = client.get(P + "/targets/runs").json()
    tr = [r for r in runs if r["request"].get("via") == "claims"][0]
    assert tr["request"]["claims_run"] == "run1"
    assert len(tr["rejected"]) == 1 and any("sec9.p1.s1" in e for e in tr["rejected"][0]["errors"])
    c1 = client.get(P + "/claims/C1").json()["item"]
    assert len(c1["evidence"]) == 1


def test_extract_rejects_claim_with_bad_refs(client, pkg, monkeypatch):
    package_with_targets(client, pkg)
    from reprocrowd import claims as cl
    orig = cl.llm.output_json

    def fake(call):
        out = orig(call)
        out["claims"][0]["refs"] = ["sec9.p9.s9"]
        return out

    monkeypatch.setattr(cl.llm, "output_json", fake)
    P = f"/v1/packages/{pkg}"
    run = client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"}).json()
    assert len(run["rejected"]) == 1 and len(run["entries"]) == 2


def test_extract_output_invalid(client, pkg):
    package_with_targets(client, pkg)
    r = client.post(f"/v1/packages/{pkg}/claims/runs", json={"extraction": "ex-paper-tei", "profile": "badschema"})
    problem(r, 422, "llm_output_invalid")


def test_reextract_arrives_disabled(client, pkg):
    P, _ = _extract(client, pkg)
    before = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}
    r = client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"})
    assert r.status_code == 201 and r.json()["id"] == "run2" and r.json()["disabled"] is True
    for kind in ("targets", "actions"):
        mine = [x for x in client.get(f"{P}/{kind}/runs").json() if x["request"].get("via") == "claims"]
        assert [x["disabled"] for x in mine] == [False, True]
    after = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}
    assert after == before
    ids = [i["item"]["id"] for i in client.get(P + "/claims?include_disabled=true").json()]
    assert ids == ["C1", "C2", "C3", "C4", "C5", "C6"]


def test_claims_prompt_preview(client, pkg):
    package_with_targets(client, pkg)
    r = client.post(f"/v1/packages/{pkg}/claims/runs:preview", json={"extraction": "ex-paper-tei"})
    assert r.status_code == 200
    assert "CLAIMS" in r.json()["prompt"] and "sec3.p1.s1" in r.json()["prompt"]


def test_extract_is_all_or_nothing(client, pkg, monkeypatch):
    package_with_targets(client, pkg)
    from reprocrowd import claims as cl

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cl, "SubClaim", boom)
    P = f"/v1/packages/{pkg}"
    n = {k: len(client.get(f"{P}/{k}/runs").json()) for k in ("targets", "actions", "claims")}
    try:
        client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"})
    except RuntimeError:
        pass
    assert {k: len(client.get(f"{P}/{k}/runs").json()) for k in n} == n


def test_extract_save_failure_rolls_back(client, pkg, monkeypatch):
    package_with_targets(client, pkg)
    from reprocrowd.curation import Curation
    orig = Curation._save

    def flaky(self, f):
        if self.spec.kind == "claims":
            raise RuntimeError("disk full")
        return orig(self, f)

    monkeypatch.setattr(Curation, "_save", flaky)
    P = f"/v1/packages/{pkg}"
    n = {k: len(client.get(f"{P}/{k}/runs").json()) for k in ("targets", "actions")}
    try:
        client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"})
    except RuntimeError:
        pass
    assert {k: len(client.get(f"{P}/{k}/runs").json()) for k in n} == n


def test_claims_run_toggle_cascades(client, pkg):
    P, _ = _extract(client, pkg)
    client.post(P + "/claims/runs", json={"extraction": "ex-paper-tei"})

    def linked(run):
        return [r["disabled"] for k in ("targets", "actions") for r in client.get(f"{P}/{k}/runs").json()
                if r["request"].get("claims_run") == run]
    assert linked("run2") == [True, True]
    assert client.patch(P + "/claims/runs/run2", json={"disabled": False}).status_code == 200
    assert linked("run2") == [False, False] and linked("run1") == [False, False]
    client.patch(P + "/claims/runs/run2", json={"disabled": True})
    assert linked("run2") == [True, True]


def test_toggling_a_targets_run_unticks_its_claims(client, pkg):
    from test_claims_script import _claims

    P, root, claims, _ = _claims(client, pkg, tick=("C1",))
    t_run = next(r["id"] for r in client.get(P + "/targets/runs").json() if r["request"].get("via") == "claims")
    assert client.patch(f"{P}/targets/runs/{t_run}", json={"disabled": True}).status_code == 200
    c1 = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}["C1"]
    assert c1["checked"] is False
    assert client.patch(f"{P}/targets/runs/{t_run}", json={"disabled": False}).status_code == 200
    a_run = next(r["id"] for r in client.get(P + "/actions/runs").json() if r["request"].get("via") == "claims")
    assert client.patch(P + "/claims/C1", json={"checked": True}).status_code == 200
    assert client.patch(f"{P}/actions/runs/{a_run}", json={"disabled": True}).status_code == 200
    assert client.patch(f"{P}/actions/runs/{a_run}", json={"disabled": False}).status_code == 200
    c1 = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}["C1"]
    assert c1["checked"] is False


def test_claims_run_cascade_keeps_ticks(client, pkg):
    from test_claims_script import _claims

    P, *_ = _claims(client, pkg, tick=("C1",))
    assert client.patch(P + "/claims/runs/run1", json={"disabled": True}).status_code == 200
    assert client.patch(P + "/claims/runs/run1", json={"disabled": False}).status_code == 200
    c1 = {i["item"]["id"]: i["item"] for i in client.get(P + "/claims").json()}["C1"]
    assert c1["checked"] is True
