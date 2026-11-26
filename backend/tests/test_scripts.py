"""Master-script templates: switching templates while keeping edited shared files."""
from pathlib import Path

from reprocrowd.package import Package
from reprocrowd.scripts import current_template
from test_api import problem


def _scriptable(client, pkg) -> str:
    P = f"/v1/packages/{pkg}"
    client.post(P + "/targets", json={"type": "number", "description": "t", "expected": 2.31, "unreferenced": True, "note": "test"})
    client.post(P + "/actions", json={"kind": "analysis", "summary": "Run the test", "unreferenced": True, "note": "test"})
    return P


def test_switch_template_keeping_edited_shared_files(client, pkg):
    P = _scriptable(client, pkg)
    assert client.put(P + "/script", json={"template": "r"}).status_code == 201
    root = Path(client.get(P).json()["path"])
    edited = {"README.md": b"# My own notes\n", "AGENT_PROMPT.md": b"Implement it my way.\n"}
    for name, data in edited.items():
        (root / name).write_bytes(data)

    r = client.put(P + "/script", json={"template": "python", "on_modified": "keep"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["kept"]) == ["AGENT_PROMPT.md", "README.md"]
    for name, data in edited.items():
        assert (root / name).read_bytes() == data
    assert not (root / "analysis.R").exists() and (root / "analysis.py").exists()

    tpl = current_template(Package(pkg, root))
    assert tpl is not None and (tpl.id, tpl.master) == ("python", "analysis.py")
    state = client.get(P + "/script").json()
    assert (state["template"], state["master"], state["command"]) == ("python", "analysis.py", "python3 analysis.py")
    # The kept files are still known as edited: a plain regeneration asks again.
    assert sorted(problem(client.put(P + "/script", json={"template": "python"}), 409, "script_modified")["files"]) == ["AGENT_PROMPT.md", "README.md"]


def test_current_template_ignores_stale_shared_file_metadata(client, pkg):
    """Packages written before the fix: kept shared files still name the old template and come first."""
    P = _scriptable(client, pkg)
    assert client.put(P + "/script", json={"template": "python"}).status_code == 201
    root = Path(client.get(P).json()["path"])
    p = Package(pkg, root)
    with p.edit() as m:
        shared = [r for r in m.resources if r.path in ("README.md", "AGENT_PROMPT.md")]
        for r in shared:
            r.reprocrowd = r.reprocrowd.model_copy(update={"template": "r"})
        m.resources = shared + [r for r in m.resources if r not in shared]
    assert p.load().by_kind("script")[0].reprocrowd.template == "r"
    assert current_template(p).id == "python"
    assert client.get(P + "/script").json()["command"] == "python3 analysis.py"
