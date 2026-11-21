"""Unit tests for the LLM runner's new pieces: OpenCode's event-stream output and running CLI shims.

These avoid the app's client fixture (which needs a TOML config) and test the pure functions directly.
"""
import json
import sys
from datetime import datetime, timezone

from reprocrowd import llm, providers
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


def test_calls_in_the_same_second_get_their_own_folders(tmp_path, monkeypatch):
    """The folder name has the second and a short random suffix; a suffix already used that second is drawn again."""
    from reprocrowd.package import Package, init_package

    init_package(tmp_path / "pkg", "pkg", None)
    pkg = Package("pkg", tmp_path / "pkg")
    monkeypatch.setattr(llm.time, "strftime", lambda fmt: "20260101T000000")
    suffixes = iter(["abcd", "abcd", "ef01"])
    monkeypatch.setattr(llm.secrets, "token_hex", lambda n: next(suffixes))
    first, second = llm._new_call_folder(pkg, "reconcile"), llm._new_call_folder(pkg, "reconcile")
    assert [c for c, _ in (first, second)] == ["reconcile-20260101T000000-abcd", "reconcile-20260101T000000-ef01"]
    assert first[1].is_dir() and second[1].is_dir()


def _envelope(usage: dict) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "{}", "modelUsage": usage})


def test_model_used_claude_json_names_the_version_behind_an_alias():
    # Claude Code's own housekeeping model (Haiku) shows up next to the one that answered.
    out = _envelope({"claude-haiku-5-5": {"outputTokens": 12}, "claude-sonnet-5-5": {"outputTokens": 900}})
    assert providers.model_used(out, "claude-json", "sonnet") == "claude-sonnet-5-5"
    assert providers.model_used(out, "claude-json", "claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert providers.model_used(out, "claude-json", "") == "claude-sonnet-5-5"  # the CLI's default: the one that wrote the most
    assert providers.model_used(out, "claude-json", "haiku") == "claude-haiku-5-5"
    assert providers.model_used(_envelope({"claude-haiku-5-5": {"outputTokens": 5}}), "claude-json", "haiku") == "claude-haiku-5-5"


def test_model_used_is_none_when_the_output_does_not_say():
    assert providers.model_used(_envelope({}), "claude-json", "sonnet") is None
    assert providers.model_used("not json", "claude-json", "sonnet") is None
    assert providers.model_used(json.dumps({"type": "text", "part": {"text": "{}"}}), "opencode-json", "x/y") is None
    assert providers.model_used("{}", "codex-json", "gpt-x") is None
    assert providers.model_used("hello", "text", "") is None


def test_model_used_claude_stream_json_reads_the_session_start():
    events = [{"type": "system", "subtype": "init", "model": "claude-opus-5-5"}, {"type": "result", "subtype": "success"}]
    lines = [json.dumps(e) + "\n" for e in events]
    assert providers.model_used(iter(lines), "claude-stream-json", "opus") == "claude-opus-5-5"
    assert providers.model_used("".join(lines[1:]), "claude-stream-json", "opus") is None
