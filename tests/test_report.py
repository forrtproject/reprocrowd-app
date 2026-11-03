import json
import zipfile
from pathlib import Path

from frictionless import validate

from test_api import problem
from test_jobs import wait
from test_run import REF, SCRIPT, package_with_targets


def test_report_and_archive(client, pkg, tmp_path):
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    client.post(P + "/targets", json={"type": "categorical", "description": "<script>alert(1)</script>", "expected": "x", "refs": REF})
    assert client.put(P + "/script", json={"template": "python"}).status_code == 200  # the target reaches results_schema.json
    problem(client.put(P + "/report", json={}), 409, "results_missing")
    (root / "analysis.py").write_text(SCRIPT)
    assert wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])["status"] == "succeeded"
    problem(client.put(P + "/archive"), 409, "report_missing")

    r = client.put(P + "/report", json={"threshold": 2})
    assert r.status_code == 201 and r.headers["location"] == f"{P}/report", r.text
    assert r.json()["reproduced"] == "3/4" and r.json()["summary"] == {"match": 3, "missing": 2}
    assert client.put(P + "/report", json={}).status_code == 200
    html = client.get(P + "/report")
    assert html.headers["content-type"].startswith("text/html")
    text = html.text
    assert "3/4</b> non-figure results reproduced" in text and "✓ match" in text and "<svg" in text and "local file" in text
    assert "<script>alert" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text  # paper/LLM text is escaped

    r = client.put(P + "/archive")
    assert r.status_code == 201, r.text
    info = r.json()
    z = root / info["path"]
    names = set(zipfile.ZipFile(z).namelist())
    manifest = client.get(P + "/manifest").json()
    assert names == {"datapackage.json"} | {res["path"] for res in manifest["resources"]}
    assert "report.html" in names and "outputs/results.json" in names and not any(n.startswith(("jobs/", "archive/", ".git")) for n in names)
    assert manifest["reprocrowd"]["archive"]["sha256"] == info["sha256"]
    extracted = tmp_path / "unzipped"
    zipfile.ZipFile(z).extractall(extracted)
    assert validate(str(extracted / "datapackage.json")).valid  # a self-contained Frictionless package
    dl = client.get(P + "/archive")
    assert dl.headers["content-type"] == "application/zip" and dl.content[:2] == b"PK"

    (root / "fulltext" / "paper.tei.xml").write_text("<TEI/>")  # tampered source
    e = problem(client.put(P + "/archive"), 409, "package_invalid")
    assert e["errors"]


def test_archive_missing(client, pkg):
    problem(client.get(f"/v1/packages/{pkg}/archive"), 409, "archive_missing")
    problem(client.get(f"/v1/packages/{pkg}/report"), 409, "report_missing")


def test_provenance_graph(client, pkg):
    import re

    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    client.post(P + "/inputs", json={"path": str(Path(__file__).parent / "fixtures" / "data.csv")})
    client.post(P + "/fulltexts", json={"path": str(Path(__file__).parent / "fixtures" / "paper.pdf")})  # used by no target
    client.post(P + "/actions", json={"kind": "transform", "summary": "Load and clean", "refs": [{"extraction": "ex-paper-tei", "id": "sec2.p1.s2"}],
                                      "inputs": ["in-data.csv"]})
    client.patch(P + "/actions/A01", json={"depends_on": ["A02"]})
    assert client.put(P + "/script", json={"template": "python", "on_modified": "overwrite"}).status_code == 200
    (root / "analysis.py").write_text(SCRIPT)
    wait(client, pkg, client.post(P + "/jobs", json={"type": "run"}).json()["id"])
    client.put(P + "/report", json={})
    html = (root / "report.html").read_text()
    svg = html[html.index("<svg"):html.index("</svg>")]
    assert 'width="1040"' in svg  # fixed width: no sideways scrolling
    layers = re.findall(r'class="layer"[^>]*>([^<]+)<', svg)
    # OUT (collect results) is pruned too: each target is attributed to the action that computes it
    assert layers == ["sources", "full texts", "extractions", "passages", "steps 1", "steps 2", "steps 3", "targets"]
    # targets are final nodes: no edge leaves a target
    # pruned: the unused PDF full text leads to no target
    assert "paper.pdf" not in svg and "paper.tei.xml" in svg
    assert 'data-from="t:' not in svg and all(f'data-node="t:T00{i}"' in svg for i in range(1, 5))
    # the trace of T001 reaches its text, the procedure, the data file and the sources
    trace = html.split("<b>T001</b>")[1].split("</details>")[0]
    assert "The effect was significant" in trace and "Load and clean" in trace and "everything" in trace
    assert "inputs/data.csv" in trace and "fulltext/paper.tei.xml" in trace and "doi:10.1371/journal.pbio.1002106" in trace
    lineage = json.loads(html.split('id="lineage">')[1].split("</script>")[0])
    assert "in:inputs/data.csv" in lineage["t:T001"] and "paper" in lineage["t:T001"]
