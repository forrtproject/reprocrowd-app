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
import re
import secrets
import shutil
import signal
import subprocess
import time
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from . import providers
from . import childenv
from .config import ISOLATION_ENV, LlmProfile, expand_argv, isolate_argv, settings
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


def build_argv(p: LlmProfile, read_files: bool, model: str | None = None) -> list[str]:
    """Substitute {model}/{effort}; an empty value drops the placeholder argument and the flag before it.
    ``model`` overrides the profile's model (e.g. a model chosen from the CLI's available ones)."""
    return expand_argv(p.argv, {"model": model if model is not None else p.model, "effort": p.effort}) + (p.read_files_argv if read_files else [])


def resolve_argv(argv: list[str]) -> list[str]:
    """Make a command line runnable where the CLI is a shell shim. On Windows, npm installs agent CLIs as
    ``.cmd``/``.ps1`` wrappers, which ``subprocess`` can't start on its own; run those through the shell they need."""
    if not argv:
        return argv
    exe = shutil.which(argv[0])
    if exe is None:
        return argv  # let Popen raise its usual error
    if os.name == "nt":
        low = exe.lower()
        if low.endswith((".cmd", ".bat")):
            return ["cmd", "/c", exe, *argv[1:]]
        if low.endswith(".ps1"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, *argv[1:]]
    return [exe, *argv[1:]]


def load_prompt(name: str) -> str:
    return resources.files("reprocrowd.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def render(template: str, values: dict[str, str], required: tuple[str, ...] = ()) -> str:
    """Fill $placeholders. Required placeholders missing from a custom template are appended as sections,
    so the model always gets the material it must work on."""
    text = Template(template).safe_substitute(values)
    for key in required:
        if f"${key}" not in template and "${" + key + "}" not in template:
            text += f"\n\n## {key}\n\n{values[key]}"
    return text


def command(profile_name: str | None, *, read_files: bool = False, schema: str | None = None, model: str | None = None) -> tuple[str, LlmProfile, list[str], str | None]:
    """The profile's name, the profile, the exact command line, and the output schema if the CLI gets one.
    ``schema``: name of the output's JSON Schema; passed to the CLI if the profile supports structured output.
    ``model`` overrides the profile's model (a model chosen from the CLI's available ones)."""
    name, p = profile(profile_name)
    argv = build_argv(p, read_files, model)
    structured = schema if schema and p.json_schema_argv else None
    if structured:
        argv += expand_argv(p.json_schema_argv, {"schema": cli_schema(structured)})
    return name, p, isolate_argv(argv), structured


def _new_call_folder(pkg: Package, task: str) -> tuple[str, Path]:
    """A new folder for a call, named by its task and the second it started. Calls in the same second get different
    suffixes; creating the folder reserves the name, so calls running at the same time can't share one either."""
    while True:
        call_id = f"{task}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
        folder = pkg.path(f"llm-calls/{call_id}")
        try:
            folder.mkdir(parents=True)
            return call_id, folder
        except FileExistsError:
            continue


def run(pkg: Package, task: str, prompt: str, *, profile_name: str | None, template: str, instructions: str, read_files: bool = False,
        schema: str | None = None, model: str | None = None) -> LlmCall:
    name, p, argv, structured = command(profile_name, read_files=read_files, schema=schema, model=model)
    used_model = model if model is not None else p.model
    call_id, folder = _new_call_folder(pkg, task)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    started, t0 = now(), time.monotonic()

    def record(out: str, err: str, code: int | None) -> LlmCall:
        (folder / "stdout.txt").write_text(out, encoding="utf-8")
        (folder / "stderr.txt").write_text(err, encoding="utf-8")
        call = LlmCall(id=call_id, task=task, profile=name, argv=argv, model=used_model, effort=p.effort, template=template,
                       instructions=instructions, prompt=prompt, output=out, output_format=p.output, output_schema=structured, started_at=started,
                       duration_ms=int((time.monotonic() - t0) * 1000), exit_code=code)
        atomic_write(folder / "call.json", dump_json(call))
        return call

    try:
        # PWD follows cwd: some CLIs (OpenCode) resolve relative paths from $PWD, which would otherwise be the service's folder.
        proc = subprocess.Popen(resolve_argv(argv), cwd=pkg.root, env={**childenv.for_cli(argv, p.env_pass), **ISOLATION_ENV, "PWD": str(pkg.root)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as e:
        record("", str(e), None)
        raise ApiError(502, "llm_call_failed", f"could not start the LLM command: {e}", {"call": call_id, "argv": argv})
    try:
        out, err = proc.communicate(prompt, timeout=p.timeout_s)
    except subprocess.TimeoutExpired:
        # Kill the whole process group (the CLI may have spawned children); on Windows there is no process group,
        # so the process itself is killed.
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        out, err = proc.communicate()
        record(out, err, None)
        raise ApiError(504, "llm_timeout", f"the LLM call exceeded {p.timeout_s} s", {"call": call_id})
    call = record(out, err, proc.returncode)
    if proc.returncode != 0:
        raise ApiError(502, "llm_call_failed", f"the LLM command exited with {proc.returncode}",
                       {"call": call_id, "stderr": err[-2000:], "stdout": out[-2000:]})
    failure = providers.terminal_error(out, p.output)
    if failure:
        extra = {"subtype": (providers.envelope(out) or {}).get("subtype")} if p.output == "claude-json" else {}
        raise ApiError(502, "llm_call_failed", f"the LLM CLI reported an error: {failure}", {"call": call_id, "result": failure, "stdout": out[-2000:], **extra})
    return call


def cli_schema(name: str) -> str:
    """The output schema for a CLI's structured-output flag: without the "$schema"/"$id" metadata, which some
    validators reject (Claude Code: no schema with key "https://json-schema.org/draft/2020-12/schema")."""
    schema = {k: v for k, v in json_schema(name).items() if k not in ("$schema", "$id")}
    return json.dumps(schema, separators=(",", ":"))


def output_json(call: LlmCall) -> Any:
    """The JSON object the call produced: the structured output if the CLI gave one, else parsed from the text."""
    found = providers.answer(call.output, call.output_format)
    return found if isinstance(found, (dict, list)) else extract_json(found)


def _json_objects(chunk: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Every top-level JSON object in ``chunk``, as ``(span, start, obj)``."""
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, dict[str, Any]]] = []
    start = chunk.find("{")
    while start >= 0:
        try:
            obj, end = decoder.raw_decode(chunk, start)
        except json.JSONDecodeError:
            start = chunk.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            found.append((end - start, start, obj))
            start = chunk.find("{", end)
        else:
            start = chunk.find("{", start + 1)
    return found


def extract_json(text: str) -> Any:
    """The JSON object in the output. Tolerates prose and ``` fences around it, and braces or small placeholder
    objects (such as ``{}``) in the prose before or after it: fenced blocks are tried first, and within either
    pass the largest top-level JSON object wins."""
    fenced_objs = [(span, m.start(1) + start, obj) for m in _FENCE.finditer(text) for span, start, obj in _json_objects(m.group(1))]
    if fenced_objs:
        return max(fenced_objs, key=lambda c: (c[0], c[1]))[2]
    plain_objs = _json_objects(text)
    if plain_objs:
        return max(plain_objs, key=lambda c: (c[0], c[1]))[2]
    if "{" not in text:
        raise ValueError("the output contains no JSON object")
    raise ValueError("the output contains no parseable JSON object")


_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)


def output_invalid(call: LlmCall, stage: str, errors: list[str]) -> ApiError:
    """The call ran fine but its output is unusable. ``stage``: json | schema | references."""
    return ApiError(422, "llm_output_invalid", f"the LLM output failed at the {stage} stage",
                    {"stage": stage, "errors": errors[:50], "call": call.id, "raw_output": f"llm-calls/{call.id}/stdout.txt"})
