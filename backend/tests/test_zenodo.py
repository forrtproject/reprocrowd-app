import io
import json
import zipfile

import httpx
import respx

from test_api import WORK, mock_openalex, problem
from test_submit import ready

Z = "http://zenodo.test"
SB = "http://sandbox.zenodo.test"
ME = [{"name": "Doe, Jane", "orcid": "0000-0002-1825-0097", "affiliation": "TU Dortmund"}]


def draft(dep_id=101, server=Z):
    return {"id": dep_id, "submitted": False, "state": "unsubmitted",
            "links": {"bucket": f"{server}/api/files/b-{dep_id}", "html": f"{server}/deposit/{dep_id}"},
            "metadata": {"prereserve_doi": {"doi": f"10.5281/zenodo.{dep_id}", "recid": dep_id}}}


def published(dep_id=101, server=Z):
    return {"id": dep_id, "submitted": True, "state": "done", "doi": f"10.5281/zenodo.{dep_id}",
            "links": {"record_html": f"{server}/records/{dep_id}", "bucket": f"{server}/api/files/b-{dep_id}"}, "metadata": {}}


def mock_zenodo(server=Z, dep_id=101):
    routes = {
        "create": respx.post(f"{server}/api/deposit/depositions").mock(return_value=httpx.Response(201, json=draft(dep_id, server))),
        "upload": respx.put(url__regex=rf"{server}/api/files/b-{dep_id}/.*").mock(return_value=httpx.Response(201, json={"key": "x"})),
        "get": respx.get(f"{server}/api/deposit/depositions/{dep_id}").mock(return_value=httpx.Response(200, json=draft(dep_id, server))),
        "publish": respx.post(f"{server}/api/deposit/depositions/{dep_id}/actions/publish").mock(return_value=httpx.Response(202, json=published(dep_id, server))),
        "delete": respx.delete(f"{server}/api/deposit/depositions/{dep_id}").mock(return_value=httpx.Response(204)),
    }
    return routes


def ready_published(client, pkg):
    """A package ready to deposit: as ``ready``, with its full text retrieved for the DOI (as the app fetches the JATS
    XML from Europe PMC) rather than added from a local file, which Zenodo deposits refuse."""
    from reprocrowd.package import Registry
    from reprocrowd.schemas.manifest import UrlOrigin

    ready(client, pkg)
    with Registry().get(pkg).edit() as m:
        for r in m.by_kind("fulltext"):
            r.reprocrowd.origin = UrlOrigin(url=f"https://www.ebi.ac.uk/europepmc/{r.name}", candidate="c1", label="Europe PMC")
    assert client.put(f"/v1/packages/{pkg}/archive").status_code == 200  # replaces the one ready() made


def uploaded_zip(route) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(route.calls.last.request.content))


@respx.mock
def test_deposit_draft_then_publish(client, pkg):
    mock_openalex()
    P = f"/v1/packages/{pkg}"
    problem(client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME}), 409, "run_not_succeeded")
    ready_published(client, pkg)
    z = mock_zenodo()

    problem(client.post(P + "/deposits", json={"creators": ME}), 400, "zenodo_token_missing")
    problem(client.post(P + "/deposits", json={"zenodo_token": "t"}), 422, "zenodo_creators_missing")

    # preview: nothing is sent; the paper's material is left out (OpenAlex has no license for it)
    pv = client.post(P + "/deposits:preview", json={"creators": ME, "note": "all fine"}).json()
    assert pv["exclude"] == ["paper"] and pv["contents"]["paper_license"] is None
    assert set(pv["contents"]["groups"]["paper"]["resources"]) == {"ft-paper-tei", "ex-paper-tei"}
    assert not z["create"].called

    r = client.post(P + "/deposits", json={"zenodo_token": "secret", "creators": ME, "note": "all fine"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "draft" and d["doi"] == "10.5281/zenodo.101" and d["url"] == f"{Z}/deposit/101" and not d["sandbox"]
    assert d["exclude"] == ["paper"] and set(d["excluded"]) == {"ft-paper-tei", "ex-paper-tei"} and r.headers["location"].endswith(d["id"])

    req = z["create"].calls.last.request
    assert req.headers["authorization"] == "Bearer secret"
    md = json.loads(req.content)["metadata"]
    assert md["title"] == f"Computational reproduction of: {WORK['title']}"
    assert md["creators"] == ME and md["upload_type"] == "publication" and md["prereserve_doi"] is True
    assert md["related_identifiers"][0] == {"identifier": "10.1371/journal.pbio.1002106", "relation": "reviews", "resource_type": "publication-article", "scheme": "doi"}
    assert "ReproCrowd" in md["keywords"] and "all fine" in md["description"] and "3 of 4" in md["description"]
    assert "communities" not in md

    zf = uploaded_zip(z["upload"])
    names = set(zf.namelist())
    assert "excluded-files.json" in names and "report.html" in names
    paper = {x["path"] for x in client.get(P + "/manifest").json()["resources"] if x["name"] in ("ft-paper-tei", "ex-paper-tei")}
    assert len(paper) == 2 and not paper & names, names
    manifest = json.loads(zf.read("datapackage.json"))
    assert not {"ft-paper-tei", "ex-paper-tei"} & {x["name"] for x in manifest["resources"]}
    assert {x["name"] for x in json.loads(zf.read("excluded-files.json"))["files"]} == {"ft-paper-tei", "ex-paper-tei"}

    # the same archive again is refused
    assert problem(client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME}), 409, "duplicate_submission")["existing"]["id"] == d["id"]

    # refresh: still a draft; then published on the website
    assert client.post(f"{P}/deposits/{d['id']}:refresh", json={"zenodo_token": "t"}).json()["status"] == "draft"
    z["get"].mock(return_value=httpx.Response(200, json=published()))
    s = client.post(f"{P}/deposits/{d['id']}:refresh", json={"zenodo_token": "t"}).json()
    assert s["status"] == "published" and s["url"] == f"{Z}/records/101" and s["published_at"]
    problem(client.post(f"{P}/deposits/{d['id']}:discard", json={"zenodo_token": "t"}), 409, "deposit_not_draft")
    assert [x["status"] for x in client.get(P + "/deposits").json()] == ["published"]


@respx.mock
def test_deposit_options(client, pkg):
    mock_openalex()
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    z = mock_zenodo(SB, 7)

    # everything included, sandbox, published at once, with a community
    r = client.post(P + "/deposits", json={"zenodo_token": "t", "creators": [{"name": "Roe, R"}], "exclude": [], "sandbox": True,
                                            "publish": True, "community": "reprocrowd"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["sandbox"] and d["server"] == SB and d["status"] == "published" and d["url"] == f"{SB}/records/7" and d["excluded"] == []
    assert json.loads(z["create"].calls.last.request.content)["metadata"]["communities"] == [{"identifier": "reprocrowd"}]
    names = uploaded_zip(z["upload"]).namelist()
    paper = {x["path"] for x in client.get(P + "/manifest").json()["resources"] if x["name"] in ("ft-paper-tei", "ex-paper-tei")}
    assert "excluded-files.json" not in names and paper <= set(names)
    assert z["publish"].called


@respx.mock
def test_deposit_failures(client, pkg):
    mock_openalex()
    P = f"/v1/packages/{pkg}"
    ready_published(client, pkg)
    z = mock_zenodo()
    body = {"zenodo_token": "t", "creators": ME}

    z["create"].mock(return_value=httpx.Response(401, json={"message": "The server could not verify that you are authorized"}))
    problem(client.post(P + "/deposits", json=body), 403, "zenodo_token_invalid")
    z["create"].mock(return_value=httpx.Response(400, json={"message": "Validation error.", "errors": [{"field": "metadata.creators", "message": "bad"}]}))
    assert problem(client.post(P + "/deposits", json=body), 422, "zenodo_rejected")["errors"][0]["field"] == "metadata.creators"
    z["create"].mock(side_effect=httpx.ConnectError("refused"))
    problem(client.post(P + "/deposits", json=body), 502, "zenodo_unreachable")

    # a failed file upload deletes the empty draft and records nothing
    z["create"].mock(return_value=httpx.Response(201, json=draft()))
    z["upload"].mock(return_value=httpx.Response(500, text="oops"))
    problem(client.post(P + "/deposits", json=body), 502, "zenodo_error")
    assert z["delete"].called and client.get(P + "/deposits").json() == []

    # discard a draft
    z["upload"].mock(return_value=httpx.Response(201, json={}))
    d = client.post(P + "/deposits", json=body).json()
    assert client.post(f"{P}/deposits/{d['id']}:discard", json={"zenodo_token": "t"}).json()["status"] == "discarded"
    problem(client.post(f"{P}/deposits/{d['id']}:publish", json={"zenodo_token": "t"}), 409, "deposit_not_draft")
    problem(client.post(f"{P}/deposits/zen-00:refresh", json={"zenodo_token": "t"}), 404, "not_found")


@respx.mock
def test_open_license_includes_paper(client, pkg):
    mock_openalex()
    respx.get(url__regex=r"https://api\.openalex\.org/works/doi:10\.1371/journal\.pbio\.1002106.*").mock(
        return_value=httpx.Response(200, json=WORK | {"best_oa_location": {"pdf_url": "https://example.org/paper.pdf", "license": "cc-by"}}))
    ready_published(client, pkg)
    pv = client.post(f"/v1/packages/{pkg}/deposits:preview", json={}).json()
    assert pv["contents"]["paper_license"] == "cc-by" and pv["exclude"] == []
    assert pv["metadata"]["creators"] == [{"name": "(you)"}]


@respx.mock
def test_zenodo_service(client):
    search = respx.get(f"{Z}/api/records").mock(return_value=httpx.Response(200, json={"hits": {"hits": [
        {"id": 5, "doi": "10.5281/zenodo.5", "metadata": {"title": "Computational reproduction of: X", "creators": [{"name": "Doe, Jane"}],
                                                          "publication_date": "2026-11-01"}, "links": {"self_html": f"{Z}/records/5"}}]}}))
    recs = client.get("/v1/zenodo/records", params={"doi": "https://doi.org/10.1/X"}).json()
    assert recs == [{"id": 5, "doi": "10.5281/zenodo.5", "title": "Computational reproduction of: X", "creators": ["Doe, Jane"],
                     "published": "2026-11-01", "url": f"{Z}/records/5"}]
    q = search.calls.last.request.url.params["q"]
    assert '"10.1/x"' in q and 'keywords:"ReproCrowd"' in q

    st = client.get("/v1/zenodo").json()
    assert st == {"url": Z, "sandbox": False, "token_configured": False, "community": None, "reachable": True, "error": None}
    respx.get(f"{SB}/api/records").mock(return_value=httpx.Response(503))
    st = client.get("/v1/zenodo", params={"sandbox": True}).json()
    assert st["url"] == SB and st["sandbox"] and not st["reachable"] and st["error"] == "HTTP 503"

    respx.get(f"{SB}/api/deposit/depositions").mock(return_value=httpx.Response(200, json=[]))
    assert client.post("/v1/zenodo:verify-token", json={"zenodo_token": "t", "sandbox": True}).json() == {"ok": True, "url": SB, "sandbox": True}
    respx.get(f"{Z}/api/deposit/depositions").mock(return_value=httpx.Response(403, json={"message": "no"}))
    problem(client.post("/v1/zenodo:verify-token", json={"zenodo_token": "t"}), 403, "zenodo_token_invalid")


@respx.mock
def test_local_paper_is_not_deposited(client, pkg, tmp_path):
    """A package whose paper text came from the user's disk (New from file, an upload, or a conversion of such a file)
    may be unpublished research: it can't be deposited, whatever DOI it has."""
    from reprocrowd.package import Registry
    from reprocrowd.schemas.manifest import ConvertedOrigin, UrlOrigin

    from test_api import FIXTURES

    mock_openalex()
    P = f"/v1/packages/{pkg}"
    ready(client, pkg)  # ft-paper-tei added from a local file, though the package has a DOI
    z = mock_zenodo()
    body = problem(client.post(P + "/deposits:preview", json={}), 409, "local_paper_text")
    assert body["fulltexts"] == ["ft-paper-tei"]
    problem(client.post(P + "/deposits", json={"zenodo_token": "t", "creators": ME}), 409, "local_paper_text")
    assert not z["create"].called

    # a conversion of a local PDF is local too
    with Registry().get(pkg).edit() as m:
        m.get("ft-paper-tei").reprocrowd.origin = ConvertedOrigin(source="ft-paper-pdf", converter="grobid")
    assert problem(client.post(P + "/deposits:preview", json={}), 409, "local_paper_text")["fulltexts"] == ["ft-paper-tei"]  # source gone
    with Registry().get(pkg).edit() as m:
        m.get("ft-paper-tei").reprocrowd.origin = UrlOrigin(url="https://example.org/paper.tei.xml", candidate="c1", label="OpenAlex")
    assert client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.pdf")}).status_code == 201
    with Registry().get(pkg).edit() as m:
        m.get("ft-paper-tei").reprocrowd.origin = ConvertedOrigin(source="ft-paper-pdf", converter="grobid")
    assert sorted(problem(client.post(P + "/deposits:preview", json={}), 409, "local_paper_text")["fulltexts"]) == ["ft-paper-pdf", "ft-paper-tei"]

    # a package without a DOI (New from file) is refused for its local text, not just for the missing DOI
    r = client.post("/v1/packages", json={"path": str(tmp_path / "local")})
    local = r.json()["id"]
    md = tmp_path / "paper.md"
    md.write_text("# A paper\n\n## Methods\n\nWe ran a test.\n", encoding="utf-8")
    assert client.post(f"/v1/packages/{local}/fulltexts", json={"path": str(md)}).status_code == 201
    problem(client.post(f"/v1/packages/{local}/deposits:preview", json={}), 409, "local_paper_text")
