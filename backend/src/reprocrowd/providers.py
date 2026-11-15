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
from typing import Any, Iterable, Iterator

from .config import AgentProfile, LlmProfile

PROTOCOLS = {"llm": ("text", "claude-json", "opencode-json", "codex-json"),
             "agent": ("claude-stream-json", "opencode-json", "codex-json", "text")}


def exec_restriction(profile: AgentProfile, tools: list[str] | None = None) -> str:
    """How a profile honours allow_exec=false. tool_allowlist: derived from argv ({tools} with editing tools, execution
    tools added only when allowed). cli_settings: the profile declares flags/environment that remove the CLI's
    command tools; declared, so the live smoke check tests it (an environment nonce must stay unreadable).
    Neither: unsupported, and no-analysis requests are refused before launch. None of these is an OS sandbox."""
    supports = any("{tools}" in arg for arg in profile.argv) and bool(profile.exec_tools) and bool(profile.tools) and bool(profile.tools if tools is None else tools)
    if supports:
        return "tool_allowlist"
    return "cli_settings" if profile.no_exec_argv or profile.no_exec_env else "unsupported"


def available(argv: list[str]) -> bool:
    return bool(argv and shutil.which(argv[0]))


def preview(profile: LlmProfile | AgentProfile, argv: list[str]) -> dict:
    return {"available": available(argv), "output_protocol": profile.events if isinstance(profile, AgentProfile) else profile.output,
            "exec_restriction": exec_restriction(profile) if isinstance(profile, AgentProfile) else "not_applicable",
            "timeout_s": profile.timeout_s, "structured_output": bool(getattr(profile, "json_schema_argv", []))}


def _events(out: "str | Iterable[str]") -> Iterator[dict]:
    """The JSON objects among stdout's lines; other lines (logs, banners) are skipped. Accepts the text or an open file."""
    for line in (out.splitlines() if isinstance(out, str) else out):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


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


# Event types kept in records that leave the machine (targets.json/actions.json go into archives and deposits):
# answers and turn status, never tool calls or their outputs, which can hold any file the agent read.
_KEPT = {"opencode-json": lambda ev: ev.get("type") in ("text", "step_finish", "error", "session_error"),
         "codex-json": lambda ev: ev.get("type") in ("turn.completed", "turn.failed")
         or (ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict) and ev["item"].get("type") == "agent_message")}


def recorded_output(out: str, protocol: str) -> str:
    """The stdout kept in a package's run records: for event streams only the lines ``answer`` and
    ``terminal_error`` read, so the record parses the same. The full stdout stays in llm-calls/<id>/stdout.txt."""
    keep = _KEPT.get(protocol)
    if keep is None:
        return out
    return "\n".join(json.dumps(ev, separators=(",", ":")) for ev in _events(out) if keep(ev))


def _message(part: Any, fallback: str) -> str:
    if isinstance(part, dict):
        return str(part.get("message") or part.get("text") or part.get("error") or fallback)[:1000]
    return str(part or fallback)[:1000]


def terminal_error(out: "str | Iterable[str]", protocol: str) -> str | None:
    """A failure the CLI declared in its output, which can happen at exit 0. Recoverable events don't count: failed
    tool results (the agent retries), Codex's ``error`` items (warnings) and its ``error`` events followed by a
    completed turn ("Reconnecting... 2/5"). ``out`` may be an open log file: it is read line by line."""
    if protocol == "claude-json":
        env = envelope(out if isinstance(out, str) else "".join(out))
        if env is None:
            return "no result envelope in the output"
        if env.get("is_error") or env.get("subtype") not in (None, "success"):
            return str(env.get("result") or env.get("subtype"))[:1000]
        return None
    events = _events(out)
    if protocol == "claude-stream-json":
        for ev in events:
            if ev.get("type") == "result" and (ev.get("is_error") or ev.get("subtype", "success") != "success"):
                return str(ev.get("result") or ev.get("subtype"))[:1000]
    if protocol == "opencode-json":
        for ev in events:
            if ev.get("type") in ("error", "session_error"):
                return _message(ev.get("part") or ev.get("error"), ev["type"])
    if protocol == "codex-json":
        failed = late = None  # the last turn.failed; the last error event not followed by a completed turn
        for ev in events:
            t = ev.get("type")
            if t == "turn.failed":
                failed = ev
            elif t == "turn.completed":
                late = None
            elif t == "error":
                late = ev
        if failed:
            return _message(failed.get("error"), "turn.failed")
        if late:
            return _message(late, "error")
    return None
