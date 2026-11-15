"""Small process-transport contract shared by extraction, jobs and their previews.

Profiles select a command and a supported wire format. Research workflows never depend on the model vendor: they
call ``answer`` (the model's final text, or a structured object) and ``terminal_error`` (a vendor-declared failure,
even at exit 0) for the profile's protocol. Adding a CLI with a new wire format means one entry in each of these two
functions plus an event parser in events.py and a fake in tests/conformance. Availability is not a live conformance
certification.
"""
from __future__ import annotations

import json
import shutil
from typing import Any

from .config import AgentProfile, LlmProfile

PROTOCOLS = {"llm": ("text", "claude-json", "opencode-json", "codex-json"),
             "agent": ("claude-stream-json", "opencode-json", "codex-json", "text")}


def exec_restriction(profile: AgentProfile, tools: list[str] | None = None) -> str:
    supports = any("{tools}" in arg for arg in profile.argv) and bool(profile.exec_tools) and bool(profile.tools) and bool(profile.tools if tools is None else tools)
    return "tool_allowlist" if supports else "unsupported"


def available(argv: list[str]) -> bool:
    return bool(argv and shutil.which(argv[0]))


def preview(profile: LlmProfile | AgentProfile, argv: list[str]) -> dict:
    return {"available": available(argv), "output_protocol": profile.events if isinstance(profile, AgentProfile) else profile.output,
            "exec_restriction": exec_restriction(profile) if isinstance(profile, AgentProfile) else "not_applicable",
            "timeout_s": profile.timeout_s, "structured_output": bool(getattr(profile, "json_schema_argv", []))}


def _events(out: str) -> list[dict]:
    """The JSON objects among stdout's lines; other lines (logs, banners) are skipped."""
    found = []
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            found.append(ev)
    return found


def envelope(out: str) -> dict | None:
    """Claude's --output-format json: one JSON object (the last line, if there are several)."""
    for line in reversed([x for x in out.strip().splitlines() if x.strip()] or [""]):
        try:
            v = json.loads(line)
            return v if isinstance(v, dict) else None
        except ValueError:
            continue
    try:
        v = json.loads(out)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def answer(out: str, protocol: str) -> str | dict | list:
    """The model's final answer: text to search for JSON in, or an already structured object."""
    if protocol == "claude-json":
        env = envelope(out) or {}
        if isinstance(env.get("structured_output"), (dict, list)):
            return env["structured_output"]
        return str(env.get("result") or "")
    if protocol == "opencode-json":
        # The answer is the text of the ``text`` events, in order (reasoning and tool calls are ignored).
        return "\n".join(p["text"] for ev in _events(out) if ev.get("type") == "text"
                         and isinstance(p := ev.get("part"), dict) and isinstance(p.get("text"), str))
    if protocol == "codex-json":
        # The last agent message: earlier ones are progress notes ("I'll read the file first").
        messages = [it["text"] for ev in _events(out) if ev.get("type") == "item.completed"
                    and isinstance(it := ev.get("item"), dict) and it.get("type") == "agent_message" and isinstance(it.get("text"), str)]
        return messages[-1] if messages else ""
    return out


def _message(part: Any, fallback: str) -> str:
    if isinstance(part, dict):
        return str(part.get("message") or part.get("text") or part.get("error") or fallback)[:1000]
    return str(part or fallback)[:1000]


def terminal_error(out: str, protocol: str) -> str | None:
    """A failure the CLI declared in its output, which can happen at exit 0. Recoverable events don't count: failed
    tool results (the agent retries), Codex's ``error`` items (warnings) and its ``error`` events followed by a
    completed turn ("Reconnecting... 2/5")."""
    events = _events(out)
    if protocol == "claude-json":
        env = envelope(out)
        if env is None:
            return "no result envelope in the output"
        if env.get("is_error") or env.get("subtype") not in (None, "success"):
            return str(env.get("result") or env.get("subtype"))[:1000]
        return None
    if protocol == "claude-stream-json":
        for ev in events:
            if ev.get("type") == "result" and (ev.get("is_error") or ev.get("subtype", "success") != "success"):
                return str(ev.get("result") or ev.get("subtype"))[:1000]
    if protocol == "opencode-json":
        for ev in events:
            if ev.get("type") in ("error", "session_error"):
                return _message(ev.get("part") or ev.get("error"), ev["type"])
    if protocol == "codex-json":
        failed = [ev for ev in events if ev.get("type") == "turn.failed"]
        if failed:
            return _message(failed[-1].get("error"), "turn.failed")
        last_completed = max((i for i, ev in enumerate(events) if ev.get("type") == "turn.completed"), default=-1)
        late = [ev for ev in events[last_completed + 1:] if ev.get("type") == "error"]
        if late:
            return _message(late[-1], "error")
    return None
