import httpx
import respx

from conftest import FIXTURES
from test_api import problem


@respx.mock
def test_convert_pdf(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    grobid = respx.post("http://grobid.test/api/processFulltextDocument").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "paper.tei.xml").read_bytes()))
    assert {c["name"]: c["configured"] for c in client.get("/v1/converters").json()} == {"grobid": True, "docling": True}
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.pdf")})
    client.post(P + "/fulltexts", json={"path": str(FIXTURES / "paper.jats.xml")})

    r = client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "grobid"})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["reprocrowd"]["flavor"] == "tei" and res["path"] == "fulltext/paper.grobid.tei.xml"
    assert res["reprocrowd"]["origin"] == {"type": "conversion", "source": "ft-paper-pdf", "converter": "grobid", "detail": "http://grobid.test"}
    sent = grobid.calls.last.request.content
    assert b'name="segmentSentences"' in sent and b'name="teiCoordinates"' in sent and b"%PDF-" in sent

    r = client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "docling"})
    assert r.status_code == 201 and r.json()["reprocrowd"]["flavor"] == "docling", r.text
    assert client.post(P + "/extractions", json={"method": "parse", "fulltext": r.json()["name"]}).status_code == 201

    problem(client.post(P + "/fulltexts", json={"source": "ft-paper-jats", "converter": "grobid"}), 422, "not_convertible")
    problem(client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "ocr"}), 400, "unknown_converter")
    grobid.mock(return_value=httpx.Response(503, text="busy"))
    problem(client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "grobid"}), 502, "conversion_failed")
    grobid.mock(return_value=httpx.Response(200, text="<html>error page</html>"))
    problem(client.post(P + "/fulltexts", json={"source": "ft-paper-pdf", "converter": "grobid"}), 422, "integrity_check_failed")


def test_converter_not_configured(client, pkg):
    from reprocrowd.config import settings

    settings().grobid_url = ""
    client.post(f"/v1/packages/{pkg}/fulltexts", json={"path": str(FIXTURES / "paper.pdf")})
    problem(client.post(f"/v1/packages/{pkg}/fulltexts", json={"source": "ft-paper-pdf", "converter": "grobid"}), 409, "converter_not_configured")
