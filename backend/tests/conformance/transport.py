"""Sanitized deterministic transports. No provider executable or credential is used."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main(protocol):
    case = sys.argv[1]
    sys.stdin.read()
    def emit(event): print(json.dumps(event), flush=True)
    if case in ("timeout", "cancel"):
        if case == "cancel":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            Path(sys.argv[2]).write_text(str(child.pid))
            def reap(signum, frame):
                child.wait(timeout=3)
                raise SystemExit(128 + signum)
            signal.signal(signal.SIGTERM, reap)
        time.sleep(60)
    if case == "nonzero":
        print("fixture command failed", file=sys.stderr)
        raise SystemExit(3)
    if case == "terminal-error":
        if protocol == "codex-json":  # sanitized from codex-cli 0.167.0 with an unsupported model; exit 0 here on purpose
            emit({"type": "thread.started", "thread_id": "fixture"})
            emit({"type": "turn.started"})
            emit({"type": "error", "message": "The 'fixture' model is not supported when using Codex with a ChatGPT account."})
            emit({"type": "turn.failed", "error": {"message": "The 'fixture' model is not supported when using Codex with a ChatGPT account."}})
        else:
            emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "fixture error"} if protocol.startswith("claude") else {"type": "error", "error": {"message": "fixture error"}})
        return
    answer = {"targets": [{"type": "number", "description": "fixture", "expected": "2.31", "refs": ["sec3.p1.s1"]}]}
    if case == "mixed":
        print("not a JSON line", flush=True)
        print("[]", flush=True)
    if protocol == "claude-json":
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "no answer" if case == "malformed" else json.dumps(answer)})
    elif protocol == "claude-stream-json":
        if case == "malformed":
            print("malformed opaque log", flush=True)
        else:
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixture completion."}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "recoverable fixture tool error"}]}})
            emit({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
    elif protocol == "opencode-json":
        if case == "malformed":
            print("malformed opaque log", flush=True)
        else:
            emit({"type": "text", "part": {"text": json.dumps(answer)}})
            emit({"type": "step_finish", "part": {"reason": "stop"}})
    elif protocol == "codex-json":
        if case == "malformed":
            print("malformed opaque log", flush=True)
        else:  # shapes from codex-cli 0.167.0 exec --json; a reconnect error followed by a completed turn is recoverable
            emit({"type": "thread.started", "thread_id": "fixture"})
            emit({"type": "turn.started"})
            if case == "mixed":
                emit({"type": "error", "message": "Reconnecting... 1/5 (fixture)"})
                emit({"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": "fixture warning"}})
            emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "I will read the paper first."}})
            emit({"type": "item.started", "item": {"id": "item_2", "type": "command_execution", "command": "cat paper.txt", "aggregated_output": "", "exit_code": None, "status": "in_progress"}})
            emit({"type": "item.completed", "item": {"id": "item_2", "type": "command_execution", "command": "cat paper.txt", "aggregated_output": "no such file", "exit_code": 1, "status": "failed"}})
            emit({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": json.dumps(answer)}})
            emit({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0}})
    else:
        print("no answer" if case == "malformed" else json.dumps(answer), flush=True)
