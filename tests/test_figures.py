import hashlib
import json

import httpx
import respx

from conftest import FIXTURES
from test_api import problem

# The fixture TEI, with GROBID coordinates for its figure: page 1, x=20, y=30 (from the top), 100 × 50 pt.
TEI = (FIXTURES / "paper.tei.xml").read_text().replace(
    '<figure xml:id="fig_0">', '<figure xml:id="fig_0" coords="1,20,30,100,50"><graphic coords="1,20,30,100,50" type="bitmap"/>')


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
                                                            "method": "crop", "error": None, "code": None}]
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


CAPTION = "Box plot of mouth opening at admission and discharge in all patients"


def pdf_with(*pages: str) -> bytes:
    """A PDF with one line of text per page (the size of the fixture's, 200 × 200 pt)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for text in pages:
        n = len(objs) + 1
        kids.append(f"{n} 0 R")
        stream = f"BT /F1 4 Tf 5 100 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents {n + 1} 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def tei(figures: str, md5: str = "") -> str:
    """The fixture TEI with its figure replaced, as GROBID writes them."""
    start, end = TEI.index("<figure xml:id"), TEI.index('<figure type="table"')
    body = TEI[:start] + figures + TEI[end:]
    return body.replace("<teiHeader>", f'<teiHeader><fileDesc><sourceDesc><biblStruct><idno type="MD5">{md5.upper()}</idno></biblStruct></sourceDesc></fileDesc>', 1) if md5 else body


def docling(pictures: list[dict], binary_hash: int | None = None) -> str:
    d = json.loads((FIXTURES / "paper.docling.json").read_text())
    d["texts"] = [*d["texts"], *[{"self_ref": f"#/texts/{900 + i}", "label": "caption", "text": p["caption"]} for i, p in enumerate(pictures) if p.get("caption")]]
    d["pictures"] = [{"self_ref": f"#/pictures/{i}", "label": "picture", "captions": [{"$ref": f"#/texts/{900 + i}"}] if p.get("caption") else [],
                      "prov": [{"page_no": p["page"], "bbox": {"l": 20, "t": 170, "r": 120, "b": 120, "coord_origin": "BOTTOMLEFT"}}]} for i, p in enumerate(pictures)]
    d["body"]["children"] = [*d["body"].get("children", []), *[{"$ref": f"#/pictures/{i}"} for i in range(len(pictures))]]
    d["origin"] = {"binary_hash": binary_hash} if binary_hash is not None else {}
    return json.dumps(d)


def docling_hash(pdf: bytes) -> int:
    return int.from_bytes(hashlib.sha256(pdf).digest()[-8:], "big")


def jats_figure(client, P, tmp_path, caption: str) -> str:
    """A target on a figure of an extraction without coordinates, as with JATS or a reconciled extraction."""
    jats = tmp_path / "pub.jats.xml"
    jats.write_text((FIXTURES / "paper.jats.xml").read_text().replace("<p>Reaction times.</p>", f"<p>{caption}</p>"))
    client.post(P + "/fulltexts", json={"path": str(jats)})
    ex = client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-pub-jats"}).json()["name"]
    return client.post(P + "/targets", json={"type": "figure", "description": "plot", "refs": [{"extraction": ex, "id": "fig1"}]}).json()["id"]


def add(client, P, tmp_path, name: str, content) -> None:
    f = tmp_path / name
    f.write_bytes(content) if isinstance(content, bytes) else f.write_text(content)
    r = client.post(P + "/fulltexts", json={"path": str(f)})
    assert r.status_code == 201, r.text


def test_crop_by_caption_from_a_full_text_of_the_same_pdf(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION)
    pdf = pdf_with("no figure here", "still nothing")
    add(client, P, tmp_path, "paper.pdf", pdf)
    add(client, P, tmp_path, "dl.docling.json", docling([{"caption": f"Fig 1. {CAPTION}.", "page": 2}, {"page": 1}], docling_hash(pdf)))
    r = client.put(f"{P}/figures/{t}", json={}).json()
    assert (r["method"], r["page"], r["source"], r["pdf"]) == ("crop", 2, "ft-dl-docling", "ft-paper-pdf"), r
    assert client.get(f"{P}/figures").json() == [{"target": t, "path": f"targets/figures/{t}.png", "method": "crop", "page": 2}]
    report = {x["format"]: x for x in client.get(f"{P}/coordinates").json()}
    assert report["jats"] == {"fulltext": "ft-pub-jats", "format": "jats", "has_coordinates": False, "figures": 0, "tables": 0, "pdf": None, "pdf_match": None}
    assert report["docling"] == {"fulltext": "ft-dl-docling", "format": "docling", "has_coordinates": True, "figures": 1, "tables": 0,
                                 "pdf": "ft-paper-pdf", "pdf_match": "verified"}


def test_full_page_when_no_full_text_locates_the_figure(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION)
    add(client, P, tmp_path, "paper.pdf", pdf_with("some text", f"Fig 1. {CAPTION}."))
    r = client.put(f"{P}/figures/{t}", json={"scale": 1}).json()
    assert (r["method"], r["page"], r["size"], r["source"]) == ("page", 2, [200, 200], None), r
    assert client.get(f"{P}/figures").json()[0]["method"] == "page"
    assert client.put(f"{P}/figures", json={}).json()[0]["method"] == "page"
    assert client.get(P + "/validation").json()["valid"]


def test_grobid_figure_without_graphic_is_not_located(client, pkg, tmp_path):
    """GROBID tags a paragraph quoting the caption as a figure, with coordinates on the running text."""
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION)
    pdf = pdf_with("first", f"Fig 1. {CAPTION}.")
    add(client, P, tmp_path, "paper.pdf", pdf)
    add(client, P, tmp_path, "g.tei.xml", tei(f'<figure xml:id="fig_0" coords="1,10,10,100,10"><figDesc>{CAPTION}</figDesc></figure>', hashlib.md5(pdf).hexdigest()))
    r = client.put(f"{P}/figures/{t}", json={}).json()
    assert (r["method"], r["page"]) == ("page", 2), r
    report = next(x for x in client.get(f"{P}/coordinates").json() if x["format"] == "tei")
    assert report["figures"] == 0 and report["has_coordinates"] and report["pdf_match"] == "verified"


def test_docling_uncaptioned_picture_is_not_located(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    pdf = pdf_with("logo page")
    add(client, P, tmp_path, "paper.pdf", pdf)
    add(client, P, tmp_path, "dl.docling.json", docling([{"page": 1}], docling_hash(pdf)))
    ex = client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-dl-docling"}).json()["name"]
    doc = client.get(f"{P}/extractions/{ex}").json()
    t = client.post(P + "/targets", json={"type": "figure", "description": "plot", "refs": [{"extraction": ex, "id": doc["figures"][0]["id"]}]}).json()["id"]
    r = client.put(f"{P}/figures/{t}", json={}).json()
    assert (r["method"], r["page"]) == ("page", 1), r  # no caption: its box still tells the page, but it isn't a crop
    assert client.get(f"{P}/coordinates").json()[0]["figures"] == 0


def test_ambiguous_captions_are_not_guessed(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION)
    add(client, P, tmp_path, "paper.pdf", pdf_with(f"Fig 1. {CAPTION}.", f"Fig 2. {CAPTION}."))
    problem(client.put(f"{P}/figures/{t}", json={}), 422, "no_coordinates")


def test_near_identical_captions_are_told_apart(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION + " at the first hospital of the study in the northern part of the country, measured at admission")
    twin = CAPTION + " at the first hospital of the study in the northern part of the country, measured at discharge"
    add(client, P, tmp_path, "paper.pdf", pdf_with(f"Fig 2. {twin}.", f"Fig 1. {CAPTION} at the first hospital of the study in the northern part of the country, measured at admission."))
    assert client.put(f"{P}/figures/{t}", json={}).json()["page"] == 2


def test_hash_finds_the_pdf_of_an_uploaded_full_text(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    pdf = pdf_with("page one")
    add(client, P, tmp_path, "other.pdf", pdf_with("another paper"))
    add(client, P, tmp_path, "paper.pdf", pdf)
    add(client, P, tmp_path, "g.tei.xml", tei(f'<figure xml:id="fig_0" coords="1,20,30,100,50"><graphic coords="1,20,30,100,50"/><figDesc>{CAPTION}</figDesc></figure>', hashlib.md5(pdf).hexdigest()))
    ex = client.post(P + "/extractions", json={"method": "parse", "fulltext": "ft-g-tei"}).json()["name"]
    t = client.post(P + "/targets", json={"type": "figure", "description": "plot", "refs": [{"extraction": ex, "id": "fig1"}]}).json()["id"]
    r = client.put(f"{P}/figures/{t}", json={}).json()
    assert (r["method"], r["pdf"]) == ("crop", "ft-paper-pdf"), r


def test_a_hash_of_another_pdf_is_reported_and_not_cropped_from(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    t = jats_figure(client, P, tmp_path, CAPTION)
    add(client, P, tmp_path, "paper.pdf", pdf_with(f"Fig 1. {CAPTION}."))
    add(client, P, tmp_path, "g.tei.xml", tei(f'<figure xml:id="fig_0" coords="1,20,30,100,50"><graphic coords="1,20,30,100,50"/><figDesc>{CAPTION}</figDesc></figure>', "0" * 32))
    r = client.put(f"{P}/figures/{t}", json={}).json()
    assert (r["method"], r["page"]) == ("page", 1), r
    report = next(x for x in client.get(f"{P}/coordinates").json() if x["format"] == "tei")
    assert (report["pdf"], report["pdf_match"]) == (None, "mismatch")
