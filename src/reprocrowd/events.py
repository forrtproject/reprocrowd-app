"""Reading a job's stdout as a stream of events (what the agent says, which tools it uses, their results).

One parser per output format, selected by the agent profile's ``events`` setting:
- claude-stream-json: Claude Code's ``--output-format stream-json`` (one JSON object per line);
- text: every line is a "log" event (pipeline runs, other agents).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from .schemas.jobs import JobEvent

Raw = tuple[str, str, str | None, bool | None]  # kind, text, tool, is_error
TOOL_RESULT_MAX = 2000


def _tool_summary(name: str, inp: dict) -> str:
    for key in ("file_path", "command", "pattern", "path", "url", "description"):
        if isinstance(inp.get(key), str):
            return f"{name} {inp[key]}"
    return name


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def parse_claude(line: str) -> Iterable[Raw]:
    try:
        ev = json.loads(line)
    except ValueError:
        yield ("log", line, None, None)
        return
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        yield ("start", f"session started (model {ev.get('model') or 'default'})", None, None)
    elif t == "assistant":
        for c in (ev.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip():
                yield ("text", c["text"].strip(), None, None)
            elif c.get("type") == "tool_use":
                yield ("tool_use", _tool_summary(c.get("name", "?"), c.get("input") or {}), c.get("name"), None)
    elif t == "user":
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                text = _content_text(c.get("content"))
                yield ("tool_result", text[:TOOL_RESULT_MAX], None, bool(c.get("is_error")))
    elif t == "result":
        yield ("result", str(ev.get("result") or ev.get("subtype") or ""), None, bool(ev.get("is_error")))


def parse_text(line: str) -> Iterable[Raw]:
    yield ("log", line, None, None)


PARSERS: dict[str, Callable[[str], Iterable[Raw]]] = {"claude-stream-json": parse_claude, "text": parse_text}


class EventReader:
    """Incremental reader: each poll() returns the events of lines completed since the last poll."""

    def __init__(self, path: Path, fmt: str):
        self.path, self.parse = path, PARSERS[fmt]
        self.pos, self.seq, self.partial = 0, 0, b""

    def poll(self, final: bool = False) -> list[JobEvent]:
        out: list[JobEvent] = []
        if self.path.exists():
            with self.path.open("rb") as f:
                f.seek(self.pos)
                data = f.read()
            self.pos += len(data)
            self.partial += data
        lines = self.partial.split(b"\n")
        self.partial = b"" if final else lines.pop()
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            if not line.strip():
                continue
            for kind, text, tool, is_error in self.parse(line):
                self.seq += 1
                out.append(JobEvent(seq=self.seq, kind=kind, text=text, tool=tool, is_error=is_error))
        return out


def read_events(path: Path, fmt: str, after: int = 0, final: bool = True) -> list[JobEvent]:
    return [e for e in EventReader(path, fmt).poll(final=final) if e.seq > after]
