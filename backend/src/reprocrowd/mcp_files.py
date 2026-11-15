"""A read-only file server for coding agents (MCP over stdio), confined to one folder.

Codex reads files only through its shell. When the analysis must not run, the app disables that shell and gives
Codex these tools instead, so it can still read the package it must change (it edits with apply_patch):

    python -m reprocrowd.mcp_files [ROOT]      # ROOT defaults to the working directory (the package)

Tools: list_files, read_file, search. Nothing here writes, runs a command or leaves ROOT (paths are resolved, so
symlinks out of ROOT are refused). Standard library only; the messages are MCP's newline-delimited JSON-RPC 2.0.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_BYTES = 200_000
MAX_ENTRIES = 500
SKIP = {".git", "__pycache__", "node_modules", ".venv"}

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
TOOLS = [
    {"name": "list_files", "annotations": READ_ONLY, "description": "List the files under a folder of the package (recursive, relative paths).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "folder relative to the package; default '.'"}}}},
    {"name": "read_file", "annotations": READ_ONLY, "description": "Read a text file of the package, optionally a range of lines (1-based).",
     "inputSchema": {"type": "object", "required": ["path"], "properties": {
         "path": {"type": "string"}, "offset": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1}}}},
    {"name": "search", "annotations": READ_ONLY, "description": "Search the package's text files for a regular expression; returns path:line: text.",
     "inputSchema": {"type": "object", "required": ["pattern"], "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string", "description": "folder relative to the package; default '.'"}}}},
]


class Refused(Exception):
    pass


def inside(root: Path, rel: str | None) -> Path:
    target = (root / (rel or ".")).resolve()
    if not target.is_relative_to(root) or SKIP & set(target.relative_to(root).parts):
        raise Refused(f"{rel!r} is outside the package or in an excluded folder")
    return target


def files(root: Path, base: Path):
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not SKIP & set(rel.parts) and p.resolve().is_relative_to(root):
            yield p, rel.as_posix()


def call(root: Path, name: str, args: dict) -> str:
    if name == "list_files":
        base = inside(root, args.get("path"))
        found = [rel for _, rel in files(root, base)]
        more = f"\n… {len(found) - MAX_ENTRIES} more" if len(found) > MAX_ENTRIES else ""
        return "\n".join(found[:MAX_ENTRIES]) + more
    if name == "read_file":
        path = inside(root, args["path"])
        if not path.is_file():
            raise Refused(f"{args['path']!r} is not a file")
        text = path.read_bytes()[:MAX_BYTES].decode("utf-8", errors="replace")
        if args.get("offset") or args.get("limit"):
            lines = text.splitlines(keepends=True)
            start = int(args.get("offset") or 1) - 1
            text = "".join(lines[start:start + int(args["limit"])] if args.get("limit") else lines[start:])
        return text
    if name == "search":
        pattern = re.compile(args["pattern"])
        hits = []
        for p, rel in files(root, inside(root, args.get("path"))):
            try:
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if pattern.search(line):
                        hits.append(f"{rel}:{n}: {line[:300]}")
            except (UnicodeDecodeError, OSError):
                continue
            if len(hits) >= MAX_ENTRIES:
                break
        return "\n".join(hits[:MAX_ENTRIES]) or "no matches"
    raise Refused(f"unknown tool {name!r}")


def handle(root: Path, msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification (e.g. notifications/initialized): no reply
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "reprocrowd-files", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = {"content": [{"type": "text", "text": call(root, params.get("name", ""), params.get("arguments") or {})}]}
        except (Refused, KeyError, re.error, ValueError) as e:
            result = {"content": [{"type": "text", "text": f"refused: {e}"}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = handle(root, json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
