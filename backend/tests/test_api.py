import json
import shutil
from pathlib import Path

import httpx
import respx

from conftest import FIXTURES

WORK = {
    "id": "https://openalex.org/W1",
    "title": "The Extent and Consequences of P-Hacking in Science",
    "publication_year": 2015,
    "ids": {"openalex": "https://openalex.org/W1", "doi": "https://doi.org/10.1371/journal.pbio.1002106",
            "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/4359000"},
    "authorships": [{"author": {"display_name": "M. L. Head"}}],
    "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
    "locations": [{"pdf_url": "https://example.org/landing.pdf", "source": {"display_name": "Mirror"}}],
    "has_content": {"pdf": True},
}


def mock_openalex():
    respx.get(url__regex=r"https://api\.openalex\.org/works/doi:10\.1371/journal\.pbio\.1002106.*").mock(return_value=httpx.Response(200, json=WORK))
    respx.get(url__regex=r"https://api\.openalex\.org/works/doi:10\.1234/missing.*").mock(return_value=httpx.Response(404))
    respx.get("https://example.org/paper.pdf").mock(return_value=httpx.Response(200, content=(FIXTURES / "paper.pdf").read_bytes()))
    respx.get("https://example.org/landing.pdf").mock(return_value=httpx.Response(200, text="<html>landing page</html>"))
    respx.get(url__regex=r"https://www\.ebi\.ac\.uk/.*PMC4359000/fullTextXML").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "paper.jats.xml").read_bytes()))


def problem(r, status, code):
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == code and body["type"] == f"/v1/problems/{code}" and body["status"] == status
    return body


# ---------- packages and metadata ----------


@respx.mock
def test_package_and_metadata(client, tmp_path):
    mock_openalex()
    r = client.post("/v1/packages", json={"path": str(tmp_path / "p1"), "identifier": "https://doi.org/10.1371/JOURNAL.PBIO.1002106", "fetch_metadata": True})
    assert r.status_code == 201 and r.headers["location"] == "/v1/packages/p1"
    body = r.json()
    assert body["identifier"] == {"scheme": "doi", "value": "10.1371/journal.pbio.1002106"}
    assert body["identifiers"]["pmcid"] == "PMC4359000" and body["title"].startswith("The Extent")
    assert (tmp_path / "p1" / "datapackage.json").exists() and (tmp_path / "p1" / "schemas" / "document.schema.json").exists()

    problem(client.post("/v1/packages/p1/metadata", json={}), 409, "metadata_exists")
    problem(client.post("/v1/packages/p1/metadata", json={"identifier": "10.1234/other"}), 409, "metadata_exists")
    assert client.put("/v1/packages/p1/metadata", json={}).status_code == 200
    problem(client.put("/v1/packages/p1/metadata", json={"identifier": "10.1234/missing"}), 404, "openalex_not_found")
    assert client.get("/v1/packages/p1/metadata").json()["publication_year"] == 2015

    problem(client.post("/v1/packages", json={"path": str(tmp_path / "p1")}), 409, "package_exists")
    problem(client.post("/v1/packages", json={"path": str(tmp_path / "x"), "identifier": "nonsense"}), 400, "invalid_identifier")
    problem(client.get("/v1/packages/nope"), 404, "not_found")
    problem(client.post("/v1/packages", json={"path": 3, "bogus": 1}), 422, "request_invalid")

    assert client.delete("/v1/packages/p1").status_code == 204
    r = client.post("/v1/packages", json={"path": str(tmp_path / "p1"), "adopt": True})
    assert r.status_code == 201 and r.json()["title"].startswith("The Extent")


def test_metadata_needs_identifier(client, tmp_path):
    client.post("/v1/packages", json={"path": str(tmp_path / "noid")})
    problem(client.post("/v1/packages/noid/metadata", json={}), 400, "no_identifier")


# ---------- full texts ----------


@respx.mock
def test_fulltexts(client, pkg, tmp_path):
    mock_openalex()
    for name in ("paper.tei.xml", "paper.jats.xml", "paper.docling.json", "paper.pdf"):
        r = client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(FIXTURES / name)})
        assert r.status_code == 201, r.text
    names = [r["name"] for r in client.get(f"/v1/packages/{pkg}/fulltexts").json()]
    assert names == ["ft-paper-tei", "ft-paper-jats", "ft-paper-docling", "ft-paper-pdf"]
    res = client.get(f"/v1/packages/{pkg}/fulltexts/ft-paper-tei").json()
    assert res["reprocrowd"]["flavor"] == "tei" and res["hash"].startswith("sha256:") and res["reprocrowd"]["check"]["passed"]

    problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(FIXTURES / "paper.pdf")}), 409, "duplicate_fulltext")
    bad = tmp_path / "bad.xml"
    bad.write_text("<TEI><text>")
    body = problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(bad)}), 422, "integrity_check_failed")
    assert "not well-formed" in body["problems"][0]
    problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(tmp_path / "nope.pdf")}), 400, "not_a_file")
    assert not list((Path(client.get(f"/v1/packages/{pkg}").json()["path"]) / "fulltext").glob(".incoming*"))

    # candidates need metadata first
    problem(client.get(f"/v1/packages/{pkg}/fulltexts/candidates"), 409, "metadata_missing")
    client.post(f"/v1/packages/{pkg}/metadata", json={})
    c = client.get(f"/v1/packages/{pkg}/fulltexts/candidates").json()
    labels = {x["label"]: x for x in c["candidates"]}
    assert set(labels) == {"best open-access location", "location: Mirror", "Europe PMC JATS (PMC4359000)"}
    assert any("API key" in n for n in c["notes"])
    landing = labels["location: Mirror"]["id"]
    problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"candidate": landing}), 422, "integrity_check_failed")
    jats = labels["Europe PMC JATS (PMC4359000)"]["id"]
    problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"candidate": jats}), 409, "duplicate_fulltext")
    client.delete(f"/v1/packages/{pkg}/fulltexts/ft-paper-jats")
    r = client.post(f"/v1/packages/{pkg}/fulltexts", json={"candidate": jats})
    assert r.status_code == 201 and r.json()["reprocrowd"]["origin"]["type"] == "url"
    assert client.get(f"/v1/packages/{pkg}/fulltexts/{r.json()['name']}/content").content.startswith(b"<?xml")


# ---------- extractions ----------


def add_fulltexts(client, pkg):
    for name in ("paper.tei.xml", "paper.pdf"):
        assert client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(FIXTURES / name)}).status_code == 201


def package_files(pkg_root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in pkg_root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_previews_show_what_a_call_sends_and_store_nothing(client, pkg):
    add_fulltexts(client, pkg)
    P = f"/v1/packages/{pkg}"
    client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"})
    root = Path(client.get(P).json()["path"])
    before = package_files(root)

    body = {"extraction": "ex-paper-tei", "scope": {"sections": ["sec3"]}, "instructions": "only the main test", "profile": "structured"}
    r = client.post(P + "/targets/runs:preview", json=body)
    assert r.status_code == 200, r.text
    target_preview = r.json()
    assert target_preview["profile"] == "structured" and target_preview["model"] == "fake-1" and target_preview["effort"] == "low"
    assert target_preview["output_schema"] == "target-llm-output"
    assert "[sec3.p1.s1]" in target_preview["prompt"] and "[sec2.p1.s1]" not in target_preview["prompt"] and "only the main test" in target_preview["prompt"]

    reconcile = {"method": "reconcile", "sources": ["ft-paper-tei", "ft-paper-pdf"], "profile": "structured"}
    r = client.post(P + "/extractions:preview", json=reconcile)
    assert r.status_code == 200, r.text
    reconcile_preview = r.json()
    path = client.get(P + "/fulltexts/ft-paper-tei").json()["path"]
    assert f"- {path} (" in reconcile_preview["prompt"] and reconcile_preview["output_schema"] == "document-draft"

    action_preview = client.post(P + "/actions/runs:preview", json={"extraction": "ex-paper-tei"}).json()
    assert action_preview["profile"] == "ok" and action_preview["output_schema"] is None  # the default profile, without structured output

    assert package_files(root) == before  # no llm-calls/ folder, no run, no change to the manifest

    call = client.post(P + "/targets/runs", json=body).json()["call"]
    assert (call["prompt"], call["argv"]) == (target_preview["prompt"], target_preview["argv"])
    assert client.post(P + "/extractions", json=reconcile).status_code == 201
    calls = [json.loads(p.read_text()) for p in (root / "llm-calls").glob("reconcile-*/call.json")]
    assert [(c["prompt"], c["argv"]) for c in calls] == [(reconcile_preview["prompt"], reconcile_preview["argv"])]

    problem(client.post(P + "/targets/runs:preview", json={**body, "scope": {"items": ["sec7.p1.s1"]}}), 422, "unknown_ids")
    problem(client.post(P + "/targets/runs:preview", json={**body, "extraction": "ex-none"}), 404, "not_found")
    problem(client.post(P + "/targets/runs:preview", json={**body, "profile": "none"}), 400, "unknown_profile")
    problem(client.post(P + "/extractions:preview", json={**reconcile, "sources": ["ft-none"]}), 404, "not_found")


def test_parse_and_reconcile(client, pkg):
    add_fulltexts(client, pkg)
    r = client.post(f"/v1/packages/{pkg}/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"})
    assert r.status_code == 201 and r.json()["name"] == "ex-paper-tei"
    assert r.json()["jsonSchema"] == "schemas/document.schema.json"
    problem(client.post(f"/v1/packages/{pkg}/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"}), 409, "extraction_exists")
    assert client.put(f"/v1/packages/{pkg}/extractions/ex-paper-tei", json={"method": "parse", "fulltext": "ft-paper-tei"}).status_code == 200
    problem(client.post(f"/v1/packages/{pkg}/extractions", json={"method": "parse", "fulltext": "ft-paper-pdf"}), 422, "not_parseable")
    doc = client.get(f"/v1/packages/{pkg}/extractions/ex-paper-tei").json()
    assert doc["sections"][2]["paragraphs"][0]["sentences"][1]["id"] == "sec3.p1.s2"
    outline = client.get(f"/v1/packages/{pkg}/extractions/ex-paper-tei", params={"view": "outline"})
    assert outline.headers["content-type"].startswith("text/plain") and "[sec3.p1.s1] The effect was significant" in outline.text
    problem(client.delete(f"/v1/packages/{pkg}/fulltexts/ft-paper-tei"), 409, "in_use")

    body = {"method": "reconcile", "sources": ["ft-paper-tei", "ft-paper-pdf"]}
    r = client.post(f"/v1/packages/{pkg}/extractions", json=body)
    assert r.status_code == 201, r.text
    meta = r.json()["reprocrowd"]
    assert meta["method"] == "reconcile" and meta["fulltexts"] == ["ft-paper-tei", "ft-paper-pdf"]
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    call = json.loads((root / "llm-calls" / meta["llm_call"] / "call.json").read_text())
    assert call["model"] == "fake-1" and call["effort"] == "low" and call["template"] == "default"
    assert "fulltext/paper.tei.xml" in call["prompt"] and '"DraftSection"' in call["prompt"]
    doc = client.get(f"/v1/packages/{pkg}/extractions/{r.json()['name']}").json()
    assert doc["source"]["llm_call"] == meta["llm_call"] and doc["tables"][0]["section"] == "sec1"

    # custom prompt without placeholders: files and schema are appended
    r = client.post(f"/v1/packages/{pkg}/extractions", json=body | {"prompt": "You are reconciling. Be careful.", "name": "ex-custom"})
    assert r.status_code == 201
    call = json.loads((root / "llm-calls" / r.json()["reprocrowd"]["llm_call"] / "call.json").read_text())
    assert call["template"] == "custom" and "## files" in call["prompt"]

    # error classes
    e = problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"profile": "garbage"}), 422, "llm_output_invalid")
    assert e["stage"] == "json" and e["raw_output"].endswith("stdout.txt")
    e = problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"profile": "badschema"}), 422, "llm_output_invalid")
    assert e["stage"] == "schema" and any("sentences" in x for x in e["errors"])
    e = problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"profile": "fail"}), 502, "llm_call_failed")
    assert "overloaded" in e["stderr"]
    problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"profile": "sleep"}), 504, "llm_timeout")
    problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"profile": "nope"}), 400, "unknown_profile")
    problem(client.post(f"/v1/packages/{pkg}/extractions", json=body | {"argv": ["rm", "-rf", "/"]}), 422, "request_invalid")


# ---------- inputs ----------


def test_inputs(client, pkg):
    r = client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(FIXTURES / "data.csv"), "note": "raw RTs"})
    assert r.status_code == 201 and r.json()[0]["reprocrowd"]["role"] == "data"
    r = client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(FIXTURES / "replication.zip")})
    rs = r.json()
    assert [x["path"] for x in rs] == ["inputs/replication.zip", "inputs/replication/replication/analysis.R", "inputs/replication/replication/data/raw.csv"]
    assert [x["reprocrowd"]["role"] for x in rs] == ["other", "code", "data"]
    member = rs[1]["name"]
    problem(client.delete(f"/v1/packages/{pkg}/inputs/{member}"), 409, "archive_member")
    r = client.patch(f"/v1/packages/{pkg}/inputs/{member}", json={"used": False, "note": "not needed"},
                     headers={"Content-Type": "application/merge-patch+json"})
    assert r.status_code == 200 and r.json()["reprocrowd"]["used"] is False
    problem(client.patch(f"/v1/packages/{pkg}/inputs/{member}", json={"path": "x"}), 422, "request_invalid")
    assert client.delete(f"/v1/packages/{pkg}/inputs/{rs[0]['name']}").status_code == 204
    left = [x["path"] for x in client.get(f"/v1/packages/{pkg}/inputs").json()]
    assert left == ["inputs/data.csv"]
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    assert not (root / "inputs" / "replication").exists()


# ---------- targets, actions, script, validation ----------


def setup_paper(client, pkg):
    add_fulltexts(client, pkg)
    client.post(f"/v1/packages/{pkg}/extractions", json={"method": "parse", "fulltext": "ft-paper-tei"})
    client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(FIXTURES / "data.csv")})


def test_targets(client, pkg):
    setup_paper(client, pkg)
    T = f"/v1/packages/{pkg}/targets"
    ref = {"extraction": "ex-paper-tei", "id": "sec3.p1.s2"}
    # the body is validated against the target schema before anything else: request_invalid
    e = problem(client.post(T, json={"type": "number", "description": "mean RT", "expected": 512.4}), 422, "request_invalid")
    assert any("unreferenced" in x["msg"] for x in e["errors"])
    e = problem(client.post(T, json={"type": "number", "description": "x", "expected": 1, "unreferenced": True}), 422, "request_invalid")
    assert any("note" in x["msg"] for x in e["errors"])
    problem(client.post(T, json={"type": "number", "description": "x", "expected": "a lot", "refs": [ref]}), 422, "request_invalid")
    e = problem(client.post(T, json={"type": "number", "description": "x", "expected": 1, "refs": [{"extraction": "ex-paper-tei", "id": "sec9.p1.s1"}]}), 422, "invalid_reference")
    r = client.post(T, json={"type": "number", "description": "mean RT", "expected": 512.4, "decimals": 1, "unit": "ms", "refs": [ref]})
    assert r.status_code == 201 and r.json()["id"] == "T001" and r.headers["location"].endswith("/targets/T001")
    r = client.post(T, json={"type": "categorical", "description": "from the authors", "expected": "yes", "unreferenced": True, "note": "email from the authors"})
    assert r.json()["id"] == "T002"

    run = client.post(T + "/runs", json={"extraction": "ex-paper-tei", "scope": {"sections": ["sec3"]}})
    assert run.status_code == 201, run.text
    run = run.json()
    assert run["id"] == "run1" and [e["base"]["id"] for e in run["entries"]] == ["T003", "T004", "T005"]
    assert len(run["rejected"]) == 1 and "sec9.p1.s1" in run["rejected"][0]["errors"][0]
    assert run["entries"][0]["base"]["expected"] == 2.31 and run["entries"][0]["base"]["decimals"] == 2
    assert run["entries"][1]["base"].get("comparator") is None
    call = run["call"]
    assert call["profile"] == "ok" and call["model"] == "fake-1" and call["effort"] == "low" and "[sec3.p1.s1]" in call["prompt"]
    assert "[sec2.p1.s1]" not in call["prompt"]  # out of scope
    problem(client.post(T + "/runs", json={"extraction": "ex-paper-tei", "scope": {"items": ["sec7.p1.s1"]}}), 422, "unknown_ids")
    problem(client.post(T + "/runs", json={"extraction": "ex-paper-tei", "profile": "badschema"}), 422, "llm_output_invalid")

    # non-destructive edits
    r = client.patch(T + "/T003", json={"description": "t(38)", "unit": "t"})
    assert r.json()["item"]["description"] == "t(38)" and r.json()["origin"] == {"source": "llm", "run": "run1", "edited": True, "disabled": False}
    raw = client.get(T, params={"view": "raw"}).json()
    entry = raw["llm_runs"][0]["entries"][0]
    assert entry["base"]["description"] == "t statistic" and entry["edit"] == {"description": "t(38)", "unit": "t"}
    problem(client.patch(T + "/T003", json={"expected": "x"}), 422, "schema_invalid")
    client.patch(T + "/T004", json={"disabled": True})
    ids = [v["item"]["id"] for v in client.get(T).json()]
    assert ids == ["T001", "T002", "T003", "T005"]
    assert len(client.get(T, params={"include_disabled": True}).json()) == 5
    r = client.delete(T + "/T003/edit")
    assert r.status_code == 200 and r.json()["description"] == "t statistic"
    problem(client.delete(T + "/T003"), 409, "llm_entry")
    problem(client.patch(T + "/T001", json={"disabled": True}), 409, "manual_item")
    problem(client.delete(T + "/T001/edit"), 409, "manual_item")
    assert client.patch(T + "/T001", json={"note": "checked"}).json()["item"]["note"] == "checked"

    client.patch(T + "/runs/run1", json={"disabled": True})
    assert [v["item"]["id"] for v in client.get(T).json()] == ["T001", "T002"]
    client.patch(T + "/runs/run1", json={"disabled": False})
    assert client.delete(T + "/T002").status_code == 204
    problem(client.get(T + "/T002"), 404, "not_found")
    # ids are never reused
    assert client.post(T, json={"type": "figure", "description": "fig", "refs": [{"extraction": "ex-paper-tei", "id": "fig1"}]}).json()["id"] == "T006"


def test_actions_script_validation(client, pkg):
    setup_paper(client, pkg)
    P = f"/v1/packages/{pkg}"
    ref = [{"extraction": "ex-paper-tei", "id": "sec2.p1.s1"}]
    client.post(P + "/targets", json={"type": "number", "description": "t", "expected": 2.31, "refs": [{"extraction": "ex-paper-tei", "id": "sec3.p1.s1"}]})
    problem(client.put(P + "/script", json={"template": "r"}), 422, "nothing_to_script")

    run = client.post(P + "/actions/runs", json={"extraction": "ex-paper-tei"}).json()
    assert [e["base"]["id"] for e in run["entries"]] == ["A01", "A02"]
    assert run["entries"][0]["base"]["inputs"] == ["in-data.csv"]
    assert run["entries"][1]["base"]["depends_on"] == ["A01"] and run["entries"][1]["base"]["produces"] == ["T001"]
    assert "A9" in run["rejected"][0]["errors"][0]

    problem(client.post(P + "/actions", json={"kind": "analysis", "summary": "x", "refs": ref, "inputs": ["in-nope"]}), 422, "invalid_reference")
    problem(client.post(P + "/actions", json={"kind": "analysis", "summary": "x", "refs": ref, "produces": ["T999"]}), 422, "invalid_reference")
    r = client.post(P + "/actions", json={"kind": "analysis", "summary": "Robustness check", "refs": ref, "depends_on": ["A02"]})
    assert r.json()["id"] == "A03"
    e = problem(client.patch(P + "/actions/A01", json={"depends_on": ["A03"]}), 422, "invalid_reference")
    assert "cycle" in e["errors"][0]
    problem(client.delete(P + "/targets/T001"), 409, "in_use")
    problem(client.delete(P + "/inputs/in-data.csv"), 409, "in_use")

    # script
    templates = {t["id"] for t in client.get("/v1/templates").json()}
    assert templates == {"r", "python", "targets", "snakemake"}
    r = client.put(P + "/script", json={"template": "r"})
    assert r.status_code == 201, r.text
    root = Path(client.get(P).json()["path"])
    script = (root / "analysis.R").read_text()
    assert "# ==== L01 [load]: Load inputs/data.csv ====" in script and "# ==== A02 [analysis]: Run the t-test ====" in script
    assert "TODO(LLM): implement OUT" in script
    plan = json.loads((root / "analysis_plan.json").read_text())
    assert [s["id"] for s in plan["steps"]] == ["L01", "A01", "A02", "A03", "OUT"]
    assert plan["steps"][1]["depends_on"] == ["L01"] and plan["steps"][-1]["depends_on"] == ["A03"]
    assert client.put(P + "/script", json={"template": "snakemake"}).status_code == 200
    assert not (root / "analysis.R").exists()
    snake = (root / "Snakefile").read_text()
    assert 'rule a02:\n    input: "outputs/.steps/A01.done"' in snake
    for t in ("targets", "python"):
        assert client.put(P + "/script", json={"template": t}).status_code == 200
    (root / "analysis.py").write_text("print('implemented')\n")
    e = problem(client.put(P + "/script", json={"template": "python"}), 409, "script_modified")
    assert e["files"] == ["analysis.py"]
    assert client.put(P + "/script", json={"template": "python", "on_modified": "overwrite"}).status_code == 200

    report = client.get(P + "/validation").json()
    assert report["valid"], report
    # tampering is caught by the frictionless integrity check
    (root / "targets.json").write_text((root / "targets.json").read_text().replace("2.31", "9.99"))
    (root / "stray.txt").write_text("x")
    report = client.get(P + "/validation").json()
    assert not report["valid"]
    assert {(i["stage"], i["resource"]) for i in report["errors"]} >= {("frictionless", "targets")}
    assert any("untracked file stray.txt" in w["message"] for w in report["warnings"]) or report["errors"]


def test_etag_and_auth(client, pkg, tmp_path, monkeypatch):
    r = client.get(f"/v1/packages/{pkg}")
    tag = r.headers["etag"]
    assert r.headers["x-request-id"]
    body = {"type": "number", "description": "x", "expected": 1, "unreferenced": True, "note": "n"}
    r = client.post(f"/v1/packages/{pkg}/targets", json=body, headers={"If-Match": tag})
    assert r.status_code == 201 and r.headers["etag"] != tag
    problem(client.post(f"/v1/packages/{pkg}/targets", json=body, headers={"If-Match": tag}), 412, "precondition_failed")

    from reprocrowd.config import settings

    settings().token = "s3cret"
    try:
        problem(client.get("/v1/packages"), 401, "unauthorized")
        assert client.get("/v1/packages", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/v1/health").status_code == 200
    finally:
        settings().token = ""


def test_problem_catalog_complete(client):
    """Every code raised in the code base is documented in the catalog."""
    import re

    from reprocrowd.api.problems import CATALOG

    src = Path(__file__).parent.parent / "src" / "reprocrowd"
    codes = set()
    for p in src.rglob("*.py"):
        text = p.read_text()
        codes |= set(re.findall(r'ApiError\(\d+, "([a-z_]+)"', text))
        codes |= set(re.findall(r'(?:conflict|invalid)\("([a-z_]+)"', text))
    assert codes - set(CATALOG) == set()
    assert client.get("/v1/problems/llm_output_invalid").json()["status"] == 422


def test_openapi_docs(client):
    assert client.get("/docs").status_code == 200 and client.get("/redoc").status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "application/problem+json" in spec["paths"]["/v1/packages/{pkg}/fulltexts"]["post"]["responses"]["422"]["content"]


def test_outdated_schema_copies_are_refreshed(client, pkg):
    """Packages made by an older version may have stale schema copies; writing a resource updates its schema."""
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "schemas" / "targets.schema.json").write_text('{"type": "object", "required": ["old"]}')
    client.post(f"/v1/packages/{pkg}/targets", json={"type": "number", "description": "x", "expected": 1, "unreferenced": True, "note": "n"})
    assert client.get(f"/v1/packages/{pkg}/validation").json()["valid"]


def test_structured_output(client, pkg):
    """Profiles with json_schema_argv get the task's output schema; the envelope's structured_output is used."""
    setup_paper(client, pkg)
    P = f"/v1/packages/{pkg}"
    run = client.post(P + "/targets/runs", json={"extraction": "ex-paper-tei", "profile": "structured"})
    assert run.status_code == 201, run.text
    call = run.json()["call"]
    assert call["output_format"] == "claude-json" and call["output_schema"] == "target-llm-output"
    passed = json.loads(call["argv"][-1])
    assert call["argv"][-2] == "--json-schema" and passed["title"].startswith("TargetLlmOutput")
    assert "$schema" not in passed and "$id" not in passed  # Claude Code's validator rejects the 2020-12 meta-schema URI
    assert len(run.json()["entries"]) == 3
    r = client.post(P + "/extractions", json={"method": "reconcile", "sources": ["ft-paper-tei"], "profile": "structured"})
    assert r.status_code == 201, r.text
    e = problem(client.post(P + "/targets/runs", json={"extraction": "ex-paper-tei", "profile": "structured-error"}), 502, "llm_call_failed")
    assert e["subtype"] == "error_max_turns" and "ran out of turns" in e["result"]


# ---------- JATS availability (before a package exists) ----------

JATS_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4359000/fullTextXML"


@respx.mock
def test_jats_available(client):
    mock_openalex()
    respx.get(JATS_URL).mock(return_value=httpx.Response(200, content=(FIXTURES / "paper.jats.xml").read_bytes()))
    r = client.get("/v1/jats", params={"doi": "https://doi.org/10.1371/journal.pbio.1002106"})
    assert r.status_code == 200, r.text
    assert r.json() | {"title": None} == {"doi": "10.1371/journal.pbio.1002106", "available": True, "pmcid": "PMC4359000", "url": JATS_URL,
                                          "title": None, "reason": None}


@respx.mock
def test_jats_unavailable(client):
    xml = respx.get(JATS_URL).mock(return_value=httpx.Response(404))  # before mock_openalex's route for the same URL
    mock_openalex()
    j = client.get("/v1/jats", params={"doi": "10.1371/journal.pbio.1002106"}).json()
    assert not j["available"] and "open-access subset" in j["reason"]
    xml.mock(return_value=httpx.Response(200, text="<html>error</html>"))
    assert "isn't JATS" in client.get("/v1/jats", params={"doi": "10.1371/journal.pbio.1002106"}).json()["reason"]
    xml.mock(side_effect=httpx.ConnectError("down"))
    problem(client.get("/v1/jats", params={"doi": "10.1371/journal.pbio.1002106"}), 502, "jats_check_failed")
    problem(client.get("/v1/jats", params={"doi": "10.1234/missing"}), 404, "openalex_not_found")
    problem(client.get("/v1/jats", params={"doi": "W123"}), 400, "invalid_identifier")


@respx.mock
def test_jats_no_pmcid(client):
    work = WORK | {"ids": {"openalex": "https://openalex.org/W2", "doi": "https://doi.org/10.1234/nopmc"}}
    respx.get(url__regex=r"https://api\.openalex\.org/works/doi:10\.1234/nopmc.*").mock(return_value=httpx.Response(200, json=work))
    respx.get(url__regex=r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*").mock(return_value=httpx.Response(200, json={"resultList": {"result": []}}))
    r = client.get("/v1/jats", params={"doi": "10.1234/nopmc"})
    j = r.json()
    assert r.status_code == 200, r.text
    assert not j["available"] and j["pmcid"] is None and "no PMCID" in j["reason"]
