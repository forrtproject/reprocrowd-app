"""Small process-transport contract shared by extraction, jobs and their previews.

Profiles select a command and a supported wire format. Research workflows never
depend on the model vendor. Availability is not a live conformance certification.
"""
import json
import shutil
from .config import AgentProfile, LlmProfile


def exec_restriction(profile: AgentProfile, tools: list[str] | None = None) -> str:
    supports = any("{tools}" in arg for arg in profile.argv) and bool(profile.exec_tools) and bool(profile.tools) and bool(profile.tools if tools is None else tools)
    return "tool_allowlist" if supports else "unsupported"


def available(argv: list[str]) -> bool:
    return bool(argv and shutil.which(argv[0]))


def preview(profile: LlmProfile | AgentProfile, argv: list[str]) -> dict:
    return {"available": available(argv), "output_protocol": profile.events if isinstance(profile, AgentProfile) else profile.output,
            "exec_restriction": exec_restriction(profile) if isinstance(profile, AgentProfile) else "not_applicable",
            "timeout_s": profile.timeout_s, "structured_output": bool(getattr(profile, "json_schema_argv", []))}


def error_in_line(line: str, protocol: str) -> str | None:
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if not isinstance(ev, dict):
        return None
    if protocol in ("claude-json", "claude-stream-json") and ev.get("type") == "result" and (ev.get("is_error") or ev.get("subtype", "success") != "success"):
        return str(ev.get("result") or ev.get("subtype"))[:1000]
    if protocol == "opencode-json" and ev.get("type") in ("error", "session_error"):
        part = ev.get("part") or ev.get("error") or {}
        return str(part.get("message") or part.get("text") or part.get("error") or ev.get("type"))[:1000] if isinstance(part, dict) else str(part)[:1000]
    return None
