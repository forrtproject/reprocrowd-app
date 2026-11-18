import io
import os
import zipfile

import pytest
from conftest import FIXTURES
from test_api import problem


def test_multipart_uploads(client, pkg):
    P = f"/v1/packages/{pkg}"
    with (FIXTURES / "paper.tei.xml").open("rb") as f:
        r = client.post(P + "/fulltexts", files={"file": ("C:\\\\papers\\\\paper.tei.xml", f, "application/xml")}, data={"name": "ft-up"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "ft-up" and r.json()["reprocrowd"]["origin"] == {"type": "upload", "filename": "paper.tei.xml"}
    r = client.post(P + "/fulltexts", files={"file": ("bad.xml", b"<TEI><text>", "application/xml")})
    assert problem(r, 422, "integrity_check_failed")["problems"]
    problem(client.post(P + "/fulltexts", files={"file": (".hidden.pdf", b"%PDF-", "application/pdf")}), 400, "invalid_filename")
    problem(client.post(P + "/fulltexts", data={"name": "no file"}, files={"other": ("x", b"x")}), 422, "request_invalid")
    problem(client.post(P + "/fulltexts", content=b"{not json", headers={"Content-Type": "application/json"}), 422, "request_invalid")

    with (FIXTURES / "replication.zip").open("rb") as f:
        r = client.post(P + "/inputs", files={"file": ("replication.zip", f, "application/zip")}, data={"note": "from the journal", "role": "other"})
    assert r.status_code == 201 and len(r.json()) == 3 and r.json()[0]["reprocrowd"]["origin"]["type"] == "upload", r.text
    problem(client.post(P + "/inputs", files={"file": ("x.csv", b"a")}, data={"role": "weird"}), 422, "request_invalid")

    from reprocrowd.config import settings

    settings().max_upload_mb = 0
    problem(client.post(P + "/inputs", files={"file": ("x.csv", b"a,b\n")}), 413, "upload_too_large")
    from pathlib import Path

    assert not (Path(client.get(P).json()["path"]) / "inputs" / "x.csv").exists()  # no partial file left
    settings().max_upload_mb = 500
    assert client.get(P + "/validation").json()["valid"]


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    return buf.getvalue()


@pytest.mark.parametrize(("filename", "role"), [("data.xlsx", "data"), ("arrays.npz", "data"), ("data.ods", "data"), ("appendix.docx", "other")])
def test_zip_based_formats_stay_whole(client, pkg, tmp_path, filename, role):
    P = f"/v1/packages/{pkg}"
    r = client.post(P + "/inputs", files={"file": (filename, _zip_bytes())})
    assert r.status_code == 201, r.text
    assert [(x["path"], x["reprocrowd"]["role"]) for x in r.json()] == [(f"inputs/{filename}", role)]

    local = tmp_path / f"local-{filename}"  # a local path takes the same route
    local.write_bytes(_zip_bytes())
    r = client.post(P + "/inputs", json={"path": str(local)})
    assert [x["path"] for x in r.json()] == [f"inputs/{local.name}"]
    assert len(client.get(P + "/inputs").json()) == 2


def test_zip_extension_is_case_insensitive(client, pkg):
    r = client.post(f"/v1/packages/{pkg}/inputs", files={"file": ("PACKAGE.ZIP", _zip_bytes())})
    assert [x["path"] for x in r.json()] == ["inputs/PACKAGE.ZIP", "inputs/PACKAGE/xl/worksheets/sheet1.xml"]


def test_directory_inputs(client, pkg, tmp_path):
    P = f"/v1/packages/{pkg}"
    d = tmp_path / "replication"
    (d / "code").mkdir(parents=True)
    (d / "data.csv").write_text("a\n1\n")
    (d / "code" / "model.R").write_text("x <- 1\n")
    (d / ".git").mkdir()
    (d / ".git" / "config").write_text("secret")
    (d / ".Rhistory").write_text("history")
    os.symlink("/etc/hostname", d / "link.txt")
    r = client.post(P + "/inputs", json={"path": str(d), "note": "replication folder"})
    assert r.status_code == 201, r.text
    got = {x["path"]: x["reprocrowd"]["role"] for x in r.json()}
    assert got == {"inputs/replication/code/model.R": "code", "inputs/replication/data.csv": "data"}
    empty = tmp_path / "empty"
    empty.mkdir()
    problem(client.post(P + "/inputs", json={"path": str(empty)}), 422, "directory_empty")


def test_pagination(client, pkg):
    P = f"/v1/packages/{pkg}"
    for i in range(5):
        client.post(P + "/targets", json={"type": "number", "description": f"t{i}", "expected": i, "unreferenced": True, "note": "n"})
    r = client.get(P + "/targets")
    assert len(r.json()) == 5 and r.headers["x-total-count"] == "5" and "link" not in r.headers
    r = client.get(P + "/targets", params={"limit": 2, "offset": 2})
    assert [v["item"]["id"] for v in r.json()] == ["T003", "T004"] and r.headers["x-total-count"] == "5"
    links = dict((rel.split('rel="')[1].rstrip('"'), url.strip(" <>")) for url, rel in (part.split(";") for part in r.headers["link"].split(", ")))
    assert set(links) == {"first", "last", "next", "prev"} and "offset=4" in links["next"] and "offset=0" in links["prev"] and "offset=4" in links["last"]
    assert [v["item"]["id"] for v in client.get(links["next"]).json()] == ["T005"]
    assert "next" not in client.get(links["next"]).headers["link"]
    problem(client.get(P + "/targets", params={"limit": 0}), 422, "request_invalid")


def _zip_with(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ok.csv", "a\n1\n")
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


EVIL = 'data\nimport os; os.system("touch pwned")#.csv'  # a line break would end the comment in the master script


@pytest.mark.parametrize("member", [EVIL, "data\rimport os#.csv", "a\u2028b.csv", "../outside.txt", "D:/evil.txt", "sub/C:x.txt"])
def test_unsafe_names_in_zips_are_refused(client, pkg, member):
    """Issue #91: nothing of the archive is unpacked, whichever member is unsafe."""
    from pathlib import Path

    P = f"/v1/packages/{pkg}"
    problem(client.post(P + "/inputs", files={"file": ("rep.zip", _zip_with(member))}), 422, "invalid_archive")
    root = Path(client.get(P).json()["path"])
    assert not (root / "inputs" / "rep").exists()
    assert client.get(P + "/inputs").json() == []


def test_unsafe_file_names_are_refused(client, pkg, tmp_path):
    from pathlib import Path

    P = f"/v1/packages/{pkg}"
    from reprocrowd.errors import ApiError
    from reprocrowd.uploads import upload_name

    with pytest.raises(ApiError, match="unusable file name"):  # (httpx percent-encodes line breaks in multipart names)
        upload_name(EVIL)
    d = tmp_path / "replication"
    d.mkdir()
    (d / "good.csv").write_text("a\n1\n")
    (d / "bad\rname.csv").write_text("a\n1\n")
    problem(client.post(P + "/inputs", json={"path": str(d)}), 400, "invalid_filename")
    assert not (Path(client.get(P).json()["path"]) / "inputs" / "replication").exists()  # nothing copied
    assert client.get(P + "/inputs").json() == []


def test_package_paths_refuse_control_characters_and_drives(tmp_path):
    from reprocrowd.errors import ApiError
    from reprocrowd.package import Package

    p = Package("p", tmp_path)
    assert p.path("inputs/a b:c.csv") == tmp_path / "inputs" / "a b:c.csv"
    for bad in ("inputs/a\nb.csv", "inputs/a\x00b", "D:/x", "inputs/C:x", "inputs/../x", "inputs\\x"):
        with pytest.raises(ApiError):
            p.path(bad)
