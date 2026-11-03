import httpx
import respx

from conftest import FIXTURES
from test_api import problem

# The fixture TEI, with GROBID coordinates for its figure: page 1, x=20, y=30 (from the top), 100 × 50 pt.
TEI = (FIXTURES / "paper.tei.xml").read_text().replace('xml:id="fig_0"', 'xml:id="fig_0" coords="1,20,30,100,50"')


@respx.mock
def test_crop_from_converted_tei(client, pkg):
    P = f"/v1/packages/{pkg}"
    respx.post("http://grobid.test/api/processFulltextDocument").mock(return_value=httpx.Response(200, text=TEI))
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.pdf")})
    tei = client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "grobid"}).json()["name"]
    ex = client.post(P + "/extractions", json={"method": "parse", "fulltext": tei}).json()["name"]
    fig = client.post(P + "/targets", json={"type": "figure", "description": "RT plot", "refs": [{"extraction": ex, "id": "fig1.s1"}]}).json()["id"]
    num = client.post(P + "/targets", json={"type": "number", "description": "n", "expected": 1, "refs": [{"extraction": ex, "id": "sec2.p1.s1"}]}).json()["id"]

    r = client.put(f"{P}/figures/{fig}", json={"scale": 2})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["pdf"] == "ft-paper-pdf" and out["page"] == 1 and out["resource"]["path"] == f"targets/figures/{fig}.png"
    w, h = out["size"]
    assert abs(w - 2 * (100 + 8)) <= 2 and abs(h - 2 * (50 + 8)) <= 2  # the box plus a 4 pt margin, at 2 px/pt
    png = client.get(f"{P}/figures/{fig}")
    assert png.headers["content-type"] == "image/png"
    assert client.put(f"{P}/figures/{fig}", json={}).status_code == 200
    problem(client.put(f"{P}/figures/{num}", json={}), 422, "not_a_figure_target")
    assert client.put(f"{P}/figures", json={}).json() == [{"target": fig, "ok": True, "path": f"targets/figures/{fig}.png", "page": 1,
                                                            "error": None, "code": None}]
    assert client.get(P + "/validation").json()["valid"]


def test_crop_needs_coordinates_and_a_pdf(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    local = tmp_path / "local.tei.xml"
    local.write_text(TEI)
    client.post(P + "/fulltexts", json={"path": str(local)})
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.jats.xml")})
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.pdf")})
    client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-local-tei"})
    client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-paper-jats"})
    t1 = client.post(P + "/targets", json={"type": "figure", "description": "a", "refs": [{"extraction": "ex-local-tei", "id": "fig1"}]}).json()["id"]
    t2 = client.post(P + "/targets", json={"type": "figure", "description": "b", "refs": [{"extraction": "ex-paper-jats", "id": "fig1"}]}).json()["id"]
    problem(client.put(f"{P}/figures/{t1}", json={}), 409, "pdf_unknown")  # a local TEI: which PDF?
    assert client.put(f"{P}/figures/{t1}", json={"pdf": "ft-paper-pdf"}).status_code == 201
    problem(client.put(f"{P}/figures/{t2}", json={"pdf": "ft-paper-pdf"}), 422, "no_coordinates")  # JATS has none
    problem(client.put(f"{P}/figures/T999", json={}), 404, "not_found")
    outcomes = {o["target"]: o for o in client.put(f"{P}/figures", json={"pdf": "ft-paper-pdf"}).json()}
    assert outcomes[t1]["ok"] and outcomes[t2]["code"] == "no_coordinates"


def test_figure_found_by_mention(client, pkg, tmp_path):
    from reprocrowd.extractions import Scope, get, listing
    from reprocrowd.package import Registry

    P = f"/v1/packages/{pkg}"
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.pdf")})
    local = tmp_path / "mention.tei.xml"  # the Methods section mentions the figure, which sits in Results
    local.write_text(TEI.replace("Data were cleaned first.", "Data were cleaned first (Fig 1)."))
    client.post(P + "/fulltexts", json={"path": str(local)})
    ex = client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-mention-tei"}).json()["name"]
    t = client.post(P + "/targets", json={"type": "figure", "description": "RT by group", "refs": [{"extraction": ex, "id": "sec2.p1.s2"}]}).json()["id"]
    r = client.put(f"{P}/figures/{t}", json={"pdf": "ft-paper-pdf"})
    assert r.status_code == 201 and r.json()["figure"].endswith("fig1 (mentioned as Fig 1)"), r.text
    # the prompt listing of a section also shows the figures its text mentions
    doc = get(Registry().get(pkg), ex)[1]
    assert "[fig1] Figure 1" in listing(doc, Scope(sections=["sec2"]))
    assert "[fig1]" not in listing(doc, Scope(sections=["sec1"]))
