"""The OpenCode ``run --format json`` event parser."""
from reprocrowd.events import parse_opencode


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
