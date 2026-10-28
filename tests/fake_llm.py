"""Stand-in for an LLM CLI in tests. Usage: fake_llm.py MODE; the prompt arrives on stdin.

MODE: ok (valid output), garbage (no JSON), badschema (JSON that fails the schema), fail (exit 3), sleep.
The task is recognised from the prompt text.
"""
import json
import re
import sys
import time

mode = sys.argv[1]
prompt = sys.stdin.read()

if mode == "fail":
    print("model overloaded", file=sys.stderr)
    sys.exit(3)
if mode == "sleep":
    time.sleep(30)
if mode == "garbage":
    print("Sorry, I can't help with that.")
    sys.exit(0)

if "reconciling" in prompt:
    out = {"title": "A Test Paper", "sections": [
        {"title": "Results", "level": 1, "paragraphs": [{"sentences": ["The effect was significant, t(38) = 2.31, p = .026."]}],
         "tables": [{"label": "Table 1", "caption": ["Descriptives."], "rows": [["Group", "M"], ["A", "512.4"]]}]}]}
    if mode == "badschema":
        out = {"sections": [{"title": "x", "paragraphs": [{"sentences": []}]}]}
elif "RESULTS" in prompt:
    out = {"targets": [
        {"type": "number", "description": "t statistic", "quote": "t(38) = 2.31", "expected": "2.31", "refs": ["sec3.p1.s1"]},
        {"type": "number", "description": "p value", "expected": 0.026, "comparator": "=", "refs": ["sec3.p1.s1"]},
        {"type": "number", "description": "bogus", "expected": 1, "refs": ["sec9.p1.s1"]},
        {"type": "table", "description": "Descriptives", "expected": [["Group", "M"], ["A", "512.4"]], "refs": ["tab1"]},
    ]}
    if mode == "badschema":
        out = {"results": []}
elif "ACTIONS" in prompt:
    inputs = re.findall(r"^- (in-[^:]+):", prompt, re.M)
    targets = re.findall(r"^- (T\d+) ", prompt, re.M)
    out = {"actions": [
        {"key": "A1", "kind": "transform", "summary": "Clean the data", "refs": ["sec2.p1.s2"], "inputs": inputs[:1]},
        {"key": "A2", "kind": "analysis", "summary": "Run the t-test", "refs": ["sec2.p1.s1"], "depends_on": ["A1"], "produces": targets[:1]},
        {"key": "A3", "kind": "analysis", "summary": "Depends on the future", "refs": ["sec2.p1.s1"], "depends_on": ["A9"]},
    ]}
else:
    out = {"unexpected": True}
print("Here you go:\n```json\n" + json.dumps(out) + "\n```")
