"""Stand-in for a coding agent emitting Claude Code stream-json. Usage: fake_agent.py MODE (ok | slow | fail).

ok: implements every TODO(LLM): marker of analysis.R except the last one, adds R/helpers.R and install.R. In claims
mode (the prompt lists claims/<id> files to implement), it implements the files the prompt names instead: each claim
file records its evidence values (the expected values from results_schema.json, so the claim reproduces).
"""
import json
import re
import sys
import time
from pathlib import Path

mode = sys.argv[1]
prompt = sys.stdin.read()


def emit(ev):
    print(json.dumps(ev), flush=True)


emit({"type": "system", "subtype": "init", "model": "fake-agent-1", "tools": ["Read", "Edit"]})
if mode == "fail":
    print("agent crashed", file=sys.stderr)
    sys.exit(2)
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "I will implement the steps."},
                                                   {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "analysis.R"}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "# Reproduction of: …"}]}})
if mode == "slow":
    time.sleep(60)
TODO = re.compile(r"^(\s*)# TODO\(LLM\):.*$", re.M)
named = re.findall(r"^\s+- (\S+?)(?: \(claim \w+\))?: implement", prompt, re.M)
if any(f.startswith("claims/") for f in named):
    expected = {t["id"]: t.get("expected") for t in json.loads(Path("results_schema.json").read_text())["results"]}
    for f in named:
        path = Path(f)
        text = TODO.sub(r"\1# implemented", path.read_text())
        if f.startswith("claims/"):
            calls = re.findall(r'^#\s+record\("([^"]+)", \.\.\., "([^"]+)"\)', text, re.M)
            none = "None" if f.endswith(".py") else "NULL"
            lit = lambda v: none if v is None else json.dumps(v)
            text += "# ASSUMPTION: the fake agent takes the reported values\n" + "".join(
                f"record({json.dumps(t)}, {lit(expected.get(t))}, {json.dumps(st)})\n" for t, st in calls)
        path.write_text(text)
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": named[-1]}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok", "is_error": False}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": f"Implemented {', '.join(named)}."})
    sys.exit(0)
script = Path("analysis.R")
text = script.read_text()
markers = re.findall(r"^# TODO\(LLM\): implement \S+.*$", text, re.M)
for m in markers[:-1]:
    text = text.replace(m, "x <- read.csv('inputs/data.csv')  # implemented", 1)
script.write_text(text)
Path("R").mkdir(exist_ok=True)
Path("R/helpers.R").write_text("helper <- function() 1\n")
Path("install.R").write_text("install.packages('dplyr')\n")
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "analysis.R"}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "ok"}], "is_error": False}]}})
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "Rscript analysis.R"}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t3", "content": "Error: R not installed", "is_error": True}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": f"Implemented {len(markers) - 1} of {len(markers)} steps."})
