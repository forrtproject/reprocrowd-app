"""Unit tests for the LLM runner's new pieces: OpenCode's event-stream output and running CLI shims.

These avoid the app's client fixture (which needs a TOML config) and test the pure functions directly.
"""
import json
import sys
from datetime import datetime, timezone

from reprocrowd import llm
from reprocrowd.schemas.common import LlmCall


def _call(output: str, fmt: str) -> LlmCall:
    return LlmCall(id="x", task="targets", profile="opencode", argv=[], model="m", effort="",
                   template="default", instructions="", prompt="p", output=output, output_format=fmt,
                   started_at=datetime.now(timezone.utc), duration_ms=1)


def test_opencode_output_json_reads_text_events():
    events = "\n".join([
        json.dumps({"type": "step_start"}),
        json.dumps({"type": "text", "part": {"text": "Here you go:\n"}}),
        json.dumps({"type": "text", "part": {"text": '{"ok": true}'}}),
        json.dumps({"type": "step_finish"}),
    ])
    assert llm.output_json(_call(events, "opencode-json")) == {"ok": True}


def test_opencode_output_ignores_other_events():
    events = (
        json.dumps({"type": "step_start"}) + "\n"
        + json.dumps({"type": "text", "part": {"text": '{"ok": true}'}}) + "\n"
        + json.dumps({"type": "reasoning", "part": {"text": "thinking"}}) + "\n"
    )
    assert llm.output_json(_call(events, "opencode-json")) == {"ok": True}


def test_opencode_output_without_json_is_an_error():
    events = json.dumps({"type": "text", "part": {"text": "I can't help with that."}}) + "\n"
    try:
        llm.output_json(_call(events, "opencode-json"))
    except ValueError:
        return
    raise AssertionError("expected ValueError when the answer has no JSON")


def test_resolve_argv_leaves_a_real_executable_alone():
    assert llm.resolve_argv([sys.executable, "--version"]) == [sys.executable, "--version"]


def test_resolve_argv_handles_unknown_command_gracefully():
    # An executable that isn't found is left as-is, so Popen raises its usual error.
    assert llm.resolve_argv(["definitely-not-a-command-xyz", "x"]) == ["definitely-not-a-command-xyz", "x"]
