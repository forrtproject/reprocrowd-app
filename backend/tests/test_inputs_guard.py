"""Inputs must come from outside the package: adding the package's own folder copied it into itself."""
from pathlib import Path

from test_api import problem


def _root(client, pkg) -> Path:
    return Path(client.get(f"/v1/packages/{pkg}").json()["path"])


def test_package_folder_refused(client, pkg):
    root = _root(client, pkg)
    body = problem(client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(root)}), 400, "path_in_package")
    assert str(root) in body["detail"]
    assert client.get(f"/v1/packages/{pkg}/inputs").json() == []


def test_file_or_folder_inside_package_refused(client, pkg):
    root = _root(client, pkg)
    problem(client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(root / "datapackage.json")}), 400, "path_in_package")
    problem(client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(root / "schemas")}), 400, "path_in_package")


def test_folder_containing_package_refused(client, pkg):
    parent = _root(client, pkg).parent
    problem(client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(parent)}), 400, "path_in_package")


def test_folder_next_to_package_allowed(client, pkg, tmp_path):
    data = tmp_path / "study-data"
    data.mkdir()
    (data / "S1_Data.csv").write_text("id,mouth\n1,17\n")
    r = client.post(f"/v1/packages/{pkg}/inputs", json={"path": str(data)})
    assert r.status_code == 201, r.text
    assert [x["path"] for x in r.json()] == ["inputs/study-data/S1_Data.csv"]
