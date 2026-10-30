"""Running LLM calls through configured profiles, and reading JSON out of their output.

Every call leaves an audit trail in llm-calls/<id>/: prompt.txt, stdout.txt, stderr.txt, call.json.
Errors are classed so callers can tell them apart:
    unknown_profile     400  the requested profile is not configured
    llm_call_failed     502  the command could not start or exited non-zero
    llm_timeout         504  it ran longer than the profile's timeout_s
    llm_output_invalid  422  (raised by callers) the output is not what the schema requires
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import time
from importlib import resources
from string import Template
from typing import Any

from .config import LlmProfile, expand_argv, settings
from .errors import ApiError
from .package import Package, atomic_write, dump_json
from .schemas import json_schema
from .schemas.common import LlmCall, now


def profile(name: str | None) -> tuple[str, LlmProfile]:
    cfg = settings()
    name = name or cfg.llm_default_profile
    p = cfg.llm_profiles.get(name)
    if p is None:
        raise ApiError(400, "unknown_profile", f"no LLM profile {name!r}; configured: {', '.join(cfg.llm_profiles)}")
    return name, p


def build_argv(p: LlmProfile, read_files: bool) -> list[str]:
    """Substitute {model}/{effort}; an empty value drops the placeholder argument and the flag before it."""
    return expand_argv(p.argv, {"model": p.model, "effort": p.effort}) + (p.read_files_argv if read_files else [])


def load_prompt(name: str) -> str:
    return resources.files("metareader.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def render(template: str, values: dict[str, str], required: tuple[str, ...] = ()) -> str:
    """Fill $placeholders. Required placeholders missing from a custom template are appended as sections,
    so the model always gets the material it must work on."""
    text = Template(template).safe_substitute(values)
    for key in required:
        if f"${key}" not in template and "${" + key + "}" not in template:
            text += f"\n\n## {key}\n\n{values[key]}"
    return text


def run(pkg: Package, task: str, prompt: str, *, profile_name: str | None, template: str, instructions: str, read_files: bool = False,
        schema: str | None = None) -> LlmCall:
    """``schema``: name of the output's JSON Schema; passed to the CLI if the profile supports structured output."""
    name, p = profile(profile_name)
    argv = build_argv(p, read_files)
    structured = schema if schema and p.json_schema_argv else None
    if structured:
        argv += expand_argv(p.json_schema_argv, {"schema": cli_schema(structured)})
    call_id = f"{task}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
    folder = pkg.path(f"llm-calls/{call_id}")
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    started, t0 = now(), time.monotonic()

    def record(out: str, err: str, code: int | None) -> LlmCall:
        (folder / "stdout.txt").write_text(out, encoding="utf-8")
        (folder / "stderr.txt").write_text(err, encoding="utf-8")
        call = LlmCall(id=call_id, task=task, profile=name, argv=argv, model=p.model, effort=p.effort, template=template,
                       instructions=instructions, prompt=prompt, output=out, output_format=p.output, output_schema=structured, started_at=started,
                       duration_ms=int((time.monotonic() - t0) * 1000), exit_code=code)
        atomic_write(folder / "call.json", dump_json(call))
        return call

    try:
        proc = subprocess.Popen(argv, cwd=pkg.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
    except OSError as e:
        record("", str(e), None)
        raise ApiError(502, "llm_call_failed", f"could not start the LLM command: {e}", {"call": call_id, "argv": argv})
    try:
        out, err = proc.communicate(prompt, timeout=p.timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # the whole process group: the CLI may have spawned children
        out, err = proc.communicate()
        record(out, err, None)
        raise ApiError(504, "llm_timeout", f"the LLM call exceeded {p.timeout_s} s", {"call": call_id})
    call = record(out, err, proc.returncode)
    if proc.returncode != 0:
        raise ApiError(502, "llm_call_failed", f"the LLM command exited with {proc.returncode}",
                       {"call": call_id, "stderr": err[-2000:], "stdout": out[-2000:]})
    if p.output == "claude-json":
        env = _envelope(out)
        if env is None or env.get("is_error") or env.get("subtype") not in (None, "success"):
            raise ApiError(502, "llm_call_failed", "the LLM CLI reported an error",
                           {"call": call_id, "result": str((env or {}).get("result") or out[-2000:])[:2000], "subtype": (env or {}).get("subtype")})
    return call


def cli_schema(name: str) -> str:
    """The output schema for a CLI's structured-output flag: without the "$schema"/"$id" metadata, which some
    validators reject (Claude Code: no schema with key "https://json-schema.org/draft/2020-12/schema")."""
    schema = {k: v for k, v in json_schema(name).items() if k not in ("$schema", "$id")}
    return json.dumps(schema, separators=(",", ":"))


def _envelope(out: str) -> dict | None:
    """Claude's --output-format json: one JSON object (the last line, if there are several)."""
    for line in reversed([x for x in out.strip().splitlines() if x.strip()] or [""]):
        try:
            v = json.loads(line)
            return v if isinstance(v, dict) else None
        except ValueError:
            continue
    try:
        v = json.loads(out)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def output_json(call: LlmCall) -> Any:
    """The JSON object the call produced: the structured output if the CLI gave one, else parsed from the text."""
    if call.output_format == "claude-json":
        env = _envelope(call.output) or {}
        if isinstance(env.get("structured_output"), (dict, list)):
            return env["structured_output"]
        return extract_json(str(env.get("result") or ""))
    return extract_json(call.output)


def extract_json(text: str) -> Any:
    """The first JSON object in the output (tolerates prose and ``` fences around it)."""
    t = text.replace("```json", "").replace("```", "")
    start = t.find("{")
    if start < 0:
        raise ValueError("the output contains no JSON object")
    end = t.rfind("}")
    while end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            end = t.rfind("}", start, end)
    raise ValueError("the output contains no parseable JSON object")


def output_invalid(call: LlmCall, stage: str, errors: list[str]) -> ApiError:
    """The call ran fine but its output is unusable. ``stage``: json | schema | references."""
    return ApiError(422, "llm_output_invalid", f"the LLM output failed at the {stage} stage",
                    {"stage": stage, "errors": errors[:50], "call": call.id, "raw_output": f"llm-calls/{call.id}/stdout.txt"})
