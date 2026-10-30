"""Stand-in for a coding agent emitting Claude Code stream-json. Usage: fake_agent.py MODE (ok | slow | fail).

ok: implements every TODO(LLM): marker of analysis.R except the last one, adds R/helpers.R and install.R.
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
