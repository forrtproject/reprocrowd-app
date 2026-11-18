"""The OpenCode ``run --format json`` event parser."""
from reprocrowd.events import parse_codex, parse_opencode


def test_opencode_text_event():
    out = list(parse_opencode('{"type":"text","part":{"type":"text","text":"Hi!"}}'))
    assert out == [("text", "Hi!", None, None)]


def test_opencode_tool_use_with_result():
    line = ('{"type":"tool_use","part":{"type":"tool","tool":"read","state":'
            '{"status":"completed","input":{"filePath":"a.txt"},"output":"file contents"}}}')
    out = list(parse_opencode(line))
    assert out[0] == ("tool_use", "read a.txt", "read", None)
    assert out[1] == ("tool_result", "file contents", None, False)


def test_opencode_tool_error():
    line = ('{"type":"tool_use","part":{"type":"tool","tool":"bash","state":'
            '{"status":"error","input":{"command":"false"},"output":"boom"}}}')
    out = list(parse_opencode(line))
    assert out[0] == ("tool_use", "bash false", "bash", None)
    assert out[1] == ("tool_result", "boom", None, True)


def test_opencode_step_start_and_reasoning_ignored():
    out = list(parse_opencode('{"type":"step_start","part":{"type":"step-start"}}'))
    assert out == [("start", "step started", None, None)]
    assert list(parse_opencode('{"type":"reasoning","part":{"type":"reasoning","text":"thinking"}}')) == []
    assert list(parse_opencode('{"type":"step_finish","part":{"type":"step-finish"}}')) == []


def test_opencode_non_json_becomes_log():
    out = list(parse_opencode("some plain line"))
    assert out == [("log", "some plain line", None, None)]


def test_codex_events():
    started = '{"type":"item.started","item":{"type":"command_execution","command":"python3 analysis.py","exit_code":null}}'
    done = '{"type":"item.completed","item":{"type":"command_execution","command":"python3 analysis.py","aggregated_output":"boom","exit_code":1}}'
    change = '{"type":"item.completed","item":{"type":"file_change","changes":[{"path":"/x/analysis.py","kind":"update"}]}}'
    assert list(parse_codex(started)) == [("tool_use", "shell python3 analysis.py", "shell", None)]
    assert list(parse_codex(done)) == [("tool_result", "boom", None, True)]
    ok = '{"type":"item.completed","item":{"type":"command_execution","command":"ls","aggregated_output":"a.txt","exit_code":0}}'
    assert list(parse_codex(ok)) == [("tool_result", "a.txt", None, False)]
    assert list(parse_codex('{"type":"error","message":"Reconnecting... 1/5"}')) == [("log", "Reconnecting... 1/5", None, None)]
    assert list(parse_codex('{"type":"item.completed","item":{"type":"agent_message","text":null}}')) == []
    assert list(parse_codex(change)) == [("tool_use", "edit update analysis.py", "edit", None)]
    assert list(parse_codex('{"type":"item.completed","item":{"type":"reasoning","text":"hm"}}')) == []
    assert list(parse_codex('{"type":"turn.failed","error":{"message":"no access"}}')) == [("result", "no access", None, True)]
    assert list(parse_codex("plain log")) == [("log", "plain log", None, None)]
