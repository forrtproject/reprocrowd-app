"""The read-only file server given to Codex in no-analysis mode: MCP over stdio, confined to the package."""
import json
import subprocess
import sys

from reprocrowd.mcp_files import handle


def call(root, name, **args):
    r = handle(root, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})["result"]
    return r["content"][0]["text"], r.get("isError", False)


def test_tools_are_read_only_and_confined(tmp_path):
    root = tmp_path / "pkg"
    (root / "inputs").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("SECRET-CONTENT")
    (root / "analysis.py").write_text("a\nb\nc\n")
    (root / "inputs" / "data.csv").write_text("group,rt\nA,1\n")
    (tmp_path / "outside.txt").write_text("PRIVATE-CONTENT")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    root = root.resolve()
    tools = handle(root, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_files", "read_file", "search"}
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)
    listing, _ = call(root, "list_files")
    assert listing.splitlines() == ["analysis.py", "inputs/data.csv"]  # no .git, no symlink out of the package
    assert call(root, "read_file", path="analysis.py", offset=2, limit=1) == ("b\n", False)
    for path in ("../outside.txt", str(tmp_path / "outside.txt"), "link.txt", ".git/config"):
        text, error = call(root, "read_file", path=path)
        assert error and text.startswith("refused") and "PRIVATE-CONTENT" not in text and "SECRET-CONTENT" not in text
    assert call(root, "search", pattern="A,")[0] == "inputs/data.csv:2: A,1"
    assert call(root, "search", pattern="(")[1]  # a bad pattern is an error result, not a crash
    assert handle(root, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle(root, {"jsonrpc": "2.0", "id": 2, "method": "resources/list"})["error"]["code"] == -32601


def test_stdio_round_trip(tmp_path):
    (tmp_path / "x.txt").write_text("hello")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "x.txt"}}}]
    (tmp_path / "json.py").write_text("raise SystemExit('package code ran in the server')")  # -I keeps it off sys.path
    out = subprocess.run([sys.executable, "-I", "-m", "reprocrowd.mcp_files"], cwd=tmp_path, capture_output=True, text=True, timeout=30,
                         input="".join(json.dumps(m) + "\n" for m in msgs)).stdout.splitlines()
    replies = [json.loads(line) for line in out]
    assert replies[0]["result"]["serverInfo"]["name"] == "reprocrowd-files" and len(replies) == 2
    assert replies[1]["result"]["content"][0]["text"] == "hello"


def test_bounded_reads_and_errors(tmp_path, monkeypatch):
    import reprocrowd.mcp_files as m
    root = tmp_path.resolve()
    monkeypatch.setattr(m, "MAX_BYTES", 30)
    (root / "long.txt").write_text("".join(f"line {i:02d}\n" for i in range(10)))  # 8 bytes per line
    first, _ = call(root, "read_file", path="long.txt")
    assert first.startswith("line 00\n") and "continue with offset=4" in first  # never silently truncated
    assert call(root, "read_file", path="long.txt", offset=4, limit=2)[0] == "line 03\nline 04\n"
    monkeypatch.setattr(m, "MAX_SEARCH_BYTES", 10)
    assert call(root, "search", pattern="line")[0] == "no matches"  # oversized files are not searched
    monkeypatch.setattr(m, "call", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    text, error = call(root, "list_files")
    assert error and "RuntimeError" in text  # any failure is a tool error, not the end of the server
