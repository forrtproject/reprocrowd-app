"""Reading a job's stdout as a stream of events (what the agent says, which tools it uses, their results).

One parser per output format, selected by the agent profile's ``events`` setting:
- claude-stream-json: Claude Code's ``--output-format stream-json`` (one JSON object per line);
- opencode-json: OpenCode's ``run --format json``;
- codex-json: Codex's ``exec --json`` (thread/turn/item events);
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
    for key in ("file_path", "filePath", "command", "pattern", "path", "url", "description"):
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
    if not isinstance(ev, dict):
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


def parse_opencode(line: str) -> Iterable[Raw]:
    """OpenCode's ``run --format json``: one JSON object per line; a tool call carries its result in ``state``."""
    try:
        ev = json.loads(line)
    except ValueError:
        yield ("log", line, None, None)
        return
    if not isinstance(ev, dict):
        yield ("log", line, None, None)
        return
    t = ev.get("type")
    part = ev.get("part") or {}
    if t == "step_start":
        yield ("start", "step started", None, None)
    elif t == "text":
        txt = (part.get("text") or "").strip()
        if txt:
            yield ("text", txt, None, None)
    elif t == "tool_use":
        tool = part.get("tool", "?")
        state = part.get("state") or {}
        yield ("tool_use", _tool_summary(tool, state.get("input") or {}), tool, None)
        status = state.get("status")
        output = state.get("output")
        if status == "error":
            yield ("tool_result", str(output or "")[:TOOL_RESULT_MAX], None, True)
        elif output:
            yield ("tool_result", str(output)[:TOOL_RESULT_MAX], None, False)
    elif t == "reasoning":
        return
    elif t == "error":
        yield ("log", str(part.get("message") or part.get("text") or "error"), None, True)
    elif t == "session_error":
        yield ("log", str(part.get("error") or "error"), None, True)
    elif t == "step_finish":
        return


def parse_codex(line: str) -> Iterable[Raw]:
    """Codex's ``exec --json``. Items arrive as item.started/item.completed; only completed ones are shown, except a
    command, shown when it starts (so a long run is visible) and again with its output when it finishes."""
    try:
        ev = json.loads(line)
    except ValueError:
        yield ("log", line, None, None)
        return
    if not isinstance(ev, dict):
        yield ("log", line, None, None)
        return
    t = ev.get("type")
    item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
    kind = item.get("type")
    if t == "thread.started":
        yield ("start", "session started", None, None)
    elif t == "item.started" and kind == "command_execution":
        yield ("tool_use", f"shell {item.get('command', '')}"[:TOOL_RESULT_MAX], "shell", None)
    elif t == "item.completed":
        if kind == "agent_message" and isinstance(item.get("text"), str) and item["text"].strip():
            yield ("text", item["text"].strip(), None, None)
        elif kind == "command_execution":
            code = item.get("exit_code")
            yield ("tool_result", str(item.get("aggregated_output") or f"exit {code}")[:TOOL_RESULT_MAX], None, code not in (0, None))
        elif kind == "file_change":
            paths = ", ".join(f"{c.get('kind', 'edit')} {Path(str(c.get('path', '?'))).name}" for c in item.get("changes") or [] if isinstance(c, dict))
            yield ("tool_use", f"edit {paths}", "edit", None)
        elif kind in ("mcp_tool_call", "web_search"):
            yield ("tool_use", f"{kind} {item.get('tool') or item.get('query') or ''}".strip(), kind, None)
        elif kind == "error":  # a warning; the turn goes on
            yield ("log", str(item.get("message", "")), None, None)
    elif t == "turn.completed":
        u = ev.get("usage") or {}
        yield ("result", f"turn completed ({u.get('input_tokens', '?')} input, {u.get('output_tokens', '?')} output tokens)", None, False)
    elif t == "turn.failed":
        yield ("result", str((ev.get("error") or {}).get("message") or "turn failed"), None, True)
    elif t == "error":  # often recoverable ("Reconnecting... 2/5"); a terminal failure arrives as turn.failed
        yield ("log", str(ev.get("message") or "error"), None, None)


PARSERS: dict[str, Callable[[str], Iterable[Raw]]] = {"claude-stream-json": parse_claude, "opencode-json": parse_opencode, "codex-json": parse_codex,
                                                     "text": parse_text}


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
