"""A read-only file server for coding agents (MCP over stdio), confined to one folder.

Codex reads files only through its shell. When the analysis must not run, the app disables that shell and gives
Codex these tools instead, so it can still read the package it must change (it edits with apply_patch):

    python -I -m reprocrowd.mcp_files [ROOT]   # ROOT defaults to the working directory (the package)

-I (isolated mode) keeps the package folder off sys.path, so files the agent writes there are never imported.
Tools: list_files, read_file, search. Nothing here writes, runs a command or leaves ROOT (paths are resolved, so
symlinks out of ROOT are refused). Standard library only; the messages are MCP's newline-delimited JSON-RPC 2.0.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAX_BYTES = 200_000        # per read_file reply; longer files are read in parts with offset/limit
MAX_SEARCH_BYTES = 5_000_000  # files larger than this are not searched
MAX_ENTRIES = 500
SKIP = {".git", "__pycache__", "node_modules", ".venv"}

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
TOOLS = [
    {"name": "list_files", "annotations": READ_ONLY, "description": "List the files under a folder of the package (recursive, relative paths).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "folder relative to the package; default '.'"}}}},
    {"name": "read_file", "annotations": READ_ONLY,
     "description": "Read a text file of the package, optionally a range of lines (1-based offset, limit). A reply holds at most "
                    f"{MAX_BYTES} bytes; it says so when a file is longer, so read the rest with offset.",
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
    """Regular files under base, sorted, skipping excluded folders without walking into them and symlinks out of root.
    Pipes, sockets and broken symlinks are left out: opening a pipe would block a search forever."""
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(names):
            p = Path(folder) / name
            if p.resolve().is_relative_to(root) and p.is_file():
                yield p, p.relative_to(root).as_posix()


def read(path: Path, offset: int | None, limit: int | None) -> str:
    start = (offset or 1) - 1
    out, size, n = [], 0, 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            if n < start:
                continue
            if limit is not None and n >= start + limit:
                return "".join(out)
            if size + len(line) > MAX_BYTES:
                out.append(f"\n[stopped at line {n} after {size} bytes; continue with offset={n + 1}]\n")
                return "".join(out)
            out.append(line)
            size += len(line)
    return "".join(out)


def call(root: Path, name: str, args: dict) -> str:
    if name == "list_files":
        found = [rel for _, rel in files(root, inside(root, args.get("path")))]
        more = f"\n… {len(found) - MAX_ENTRIES} more" if len(found) > MAX_ENTRIES else ""
        return "\n".join(found[:MAX_ENTRIES]) + more
    if name == "read_file":
        path = inside(root, args["path"])
        if not path.is_file():
            raise Refused(f"{args['path']!r} is not a file")
        return read(path, int(args["offset"]) if args.get("offset") else None, int(args["limit"]) if args.get("limit") else None)
    if name == "search":
        pattern = re.compile(args["pattern"])
        hits = []
        for p, rel in files(root, inside(root, args.get("path"))):
            try:
                if p.stat().st_size > MAX_SEARCH_BYTES:
                    continue
                with p.open(encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        if pattern.search(line):
                            hits.append(f"{rel}:{n}: {line.rstrip()[:300]}")
                            if len(hits) >= MAX_ENTRIES:
                                break
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable
            if len(hits) >= MAX_ENTRIES:
                break
        return "\n".join(hits) or "no matches"
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
        except Exception as e:  # any failure is a tool error for the agent, never the end of the server
            result = {"content": [{"type": "text", "text": f"refused: {e}" if isinstance(e, Refused) else f"error: {type(e).__name__}: {e}"}],
                      "isError": True}
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
            msg = json.loads(line)
        except (ValueError, RecursionError):
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if not isinstance(msg, dict):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            else:
                try:
                    reply = handle(root, msg)
                except Exception as e:  # a malformed request (e.g. non-object params) gets an error reply; the server keeps running
                    reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32602, "message": f"invalid params: {type(e).__name__}: {e}"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
