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


def test_extract_json_skips_braces_in_prose():
    text = 'I used the {variable} placeholder and a set {1, 2}. Here it is:\n{"targets": [{"id": "T1"}]}\nDone {ok}.'
    assert llm.extract_json(text) == {"targets": [{"id": "T1"}]}


def test_extract_json_skips_placeholder_objects_in_prose():
    assert llm.extract_json('Use {} as a placeholder; result: {"targets": [{"id": "T1"}]}') == {"targets": [{"id": "T1"}]}
    assert llm.extract_json('{"targets": [{"id": "T1"}]}\nNote: {} means empty.') == {"targets": [{"id": "T1"}]}
    assert llm.extract_json('```json\n{}\n```\n```json\n{"targets": [{"id": "T1"}]}\n```') == {"targets": [{"id": "T1"}]}
    assert llm.extract_json("{}") == {}


def test_extract_json_reads_fenced_blocks_first():
    text = 'Use {} as the default.\n```json\n{"a": 1, "b": {"c": [1, 2]}}\n```\nThe set {x} is unused.'
    assert llm.extract_json(text) == {"a": 1, "b": {"c": [1, 2]}}
    assert llm.extract_json('```\n{"plain": true}\n```') == {"plain": True}


def test_extract_json_returns_the_outer_object():
    assert llm.extract_json('{"outer": {"inner": {"deep": 1}}, "n": 2} trailing } brace') == {"outer": {"inner": {"deep": 1}}, "n": 2}


def test_extract_json_without_an_object_is_an_error():
    import pytest

    for text in ("no json here", "[1, 2, 3]", "{not json} and {also: not}"):
        with pytest.raises(ValueError):
            llm.extract_json(text)
