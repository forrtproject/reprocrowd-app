import base64
import json

import httpx
import respx

from test_api import WORK, mock_openalex, problem
from test_jobs import wait
from test_run import SCRIPT, package_with_targets

UPLOAD = {"id": "up1", "finish_url": "http://db.test/submit/up1", "status_url": "http://db.test/api/uploads/up1", "expires": "2026-11-01T00:00:00Z"}


def ready(client, pkg):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    (root / "analysis.py").write_text(SCRIPT)
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"
    assert client.put(P + "/report", json={}).status_code == 201
    assert client.put(P + "/archive").status_code == 201, client.put(P + "/archive").text
    return root


@respx.mock
def test_submit(client, pkg):
    mock_openalex()
    P = f"/v1/packages/{pkg}"
    problem(client.post(P + "/submissions", json={}), 409, "run_not_succeeded")
    root = ready(client, pkg)
    inputs = json.loads((root / "inputs.json").read_text())
    assert inputs == {"inputs": []}  # written with the archive, in the database's format
    schema = json.loads((root / "results_schema.json").read_text())
    assert schema["paper"]["doi"] == "10.1371/journal.pbio.1002106" and len(schema["results"]) == 4

    upload = respx.post("http://db.test/api/uploads").mock(return_value=httpx.Response(201, json=UPLOAD))
    r = client.post(P + "/submissions", json={"note": "first try"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["status"] == "pending" and sub["finish_url"] == UPLOAD["finish_url"] and r.headers["location"].endswith(sub["id"])
    req = upload.calls.last.request
    meta = json.loads(base64.b64decode(req.headers["x-package-metadata"]))
    assert meta["doi"] == "10.1371/journal.pbio.1002106" and meta["title"] == WORK["title"] and meta["authors"] == "M. L. Head"
    assert meta["summary"] == {"statuses": {"match": 3, "missing": 1}} and meta["template"] == "Monolithic Python script" and meta["note"] == "first try"
    assert req.headers["content-type"] == "application/zip" and req.content[:2] == b"PK"

    status = respx.get("http://db.test/api/uploads/up1").mock(return_value=httpx.Response(200, json={"status": "pending", "package": None}))
    assert client.get(f"{P}/submissions/{sub['id']}").json()["status"] == "pending"
    status.mock(return_value=httpx.Response(200, json={"status": "submitted", "package": {"url": "http://db.test/packages/p1"}}))
    s = client.get(f"{P}/submissions/{sub['id']}").json()
    assert s["status"] == "submitted" and s["package_url"] == "http://db.test/packages/p1" and s["checked_at"]
    assert [x["status"] for x in client.get(P + "/submissions").json()] == ["submitted"]

    upload.mock(return_value=httpx.Response(409, json={"error": "identical to an earlier submission", "existing": {"id": "p1"}}))
    assert problem(client.post(P + "/submissions", json={}), 409, "duplicate_submission")["existing"] == {"id": "p1"}
    upload.mock(return_value=httpx.Response(422, json={"error": "not complete", "problems": ["missing report.html"]}))
    assert problem(client.post(P + "/submissions", json={}), 422, "submission_rejected")["problems"] == ["missing report.html"]
    upload.mock(side_effect=httpx.ConnectError("refused"))
    problem(client.post(P + "/submissions", json={}), 502, "database_unreachable")

    # any change after the archive makes it stale
    client.post(P + "/inputs", json={"path": str(root / "results_schema.json")})
    problem(client.post(P + "/submissions", json={}), 409, "archive_stale")


@respx.mock
def test_database_lookup(client):
    respx.get("http://db.test/api/packages").mock(return_value=httpx.Response(200, json={"packages": [{"id": "p1", "doi": "10.1/x"}]}))
    assert client.get("/v1/database/packages", params={"doi": "10.1/x"}).json() == [{"id": "p1", "doi": "10.1/x"}]
    assert client.get("/v1/database").json() == {"url": "http://db.test", "configured": True, "reachable": True, "error": None}


def test_database_not_configured(client, pkg):
    from reprocrowd.config import settings

    settings().database_url = ""
    problem(client.post(f"/v1/packages/{pkg}/submissions", json={}), 409, "database_not_configured")
    assert client.get("/v1/database").json()["configured"] is False


@respx.mock
def test_submit_with_database_token(client, pkg):
    """With the user's database token the upload is published at once; the token is forwarded, never stored."""
    mock_openalex()
    P = f"/v1/packages/{pkg}"
    root = ready(client, pkg)
    published = {**UPLOAD, "status": "submitted", "package": {"id": "p9", "url": "http://db.test/packages/p9"}}
    upload = respx.post("http://db.test/api/uploads").mock(return_value=httpx.Response(201, json=published))
    r = client.post(P + "/submissions", json={"note": "", "database_token": "tok-123"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "submitted" and r.json()["package_url"] == "http://db.test/packages/p9"
    assert upload.calls.last.request.headers["authorization"] == "Bearer tok-123"
    assert "tok-123" not in (root / "datapackage.json").read_text()

    upload.mock(return_value=httpx.Response(401, json={"error": "invalid or expired token"}))
    problem(client.post(P + "/submissions", json={"database_token": "old"}), 403, "database_token_invalid")
    upload.mock(return_value=httpx.Response(403, json={"error": "This account is suspended"}))
    problem(client.post(P + "/submissions", json={"database_token": "tok-123"}), 403, "database_forbidden")


def test_file_download(client, pkg):
    P = f"/v1/packages/{pkg}"
    root = package_with_targets(client, pkg)
    (root / "analysis.py").write_text(SCRIPT)
    r = client.get(P + "/files/analysis.py")
    assert r.status_code == 200 and r.text == SCRIPT and r.headers["content-type"].startswith("text/plain")
    assert client.get(P + "/files/targets.json").json()["kind"] == "targets"
    for bad in ["datapackage.json", "../x", "jobs/x.json", ".git/config", "nope.txt"]:
        assert client.get(f"{P}/files/{bad}").status_code == 404, bad
