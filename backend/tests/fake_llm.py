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
schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1]) if "--json-schema" in sys.argv else None

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
elif "CLAIMS" in prompt:
    R = "sec3.p1.s1"
    ev = lambda d, v, refs=(R,): {"type": "number", "description": d, "expected": v, "refs": list(refs)}
    out = {
        "claims": [
            {"text": "The effect was significant.", "original_quote": "", "refs": [R], "method": "M1",
             "evidence": [ev("t statistic", 2.31), ev("bogus", 1, ["sec9.p1.s1"])]},
            {"text": "Both outcomes differed.", "mode": "paraphrase", "original_quote": "Both outcomes differed between groups.",
             "refs": ["sec2.p1.s1"], "sub_claims": [
                 {"text": "Outcome one differed.", "refs": [R], "method": "M1", "evidence": [ev("p value", 0.026)]},
                 {"text": "Outcome two differed.", "refs": ["sec2.p1.s1"], "method": "M1", "evidence": [ev("df", 38)]}]},
            {"text": "Clinicians should adopt this.", "refs": ["sec2.p1.s2"], "testable": False, "depends_on": [1, 2]},
        ],
        "methods": [{"key": "M1", "summary": "Run the t-test", "refs": ["sec2.p1.s1"]}],
        "preparation": [{"key": "P1", "summary": "Exclude incomplete records", "refs": ["sec2.p1.s2"]}],
        "skipped": [{"ref": "sec2.p1.s2", "reason": "study_design"}],
    }
    if mode == "badschema":
        out = {"results": []}
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
if mode == "structured":  # like claude --output-format json --json-schema: an envelope with structured_output
    assert schema and "$defs" in schema or "properties" in schema, "no usable schema passed"
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "(see structured_output)",
                      "structured_output": out, "schema_title": schema.get("title")}))
elif mode == "structured-error":
    print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "ran out of turns"}))
else:
    print("Here you go:\n```json\n" + json.dumps(out) + "\n```")
