"""Jobs: long-running processes (code-generation agent, pipeline runs) recorded in jobs/<id>.json."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import Field

from .common import Model

JobType = Literal["codegen", "run"]
JobStatus = Literal["running", "succeeded", "failed", "cancelled", "timeout", "interrupted"]


class JobEvent(Model):
    """One step of a job's progress, read from its stdout."""

    seq: int = Field(ge=1, description="1-based position; use as Last-Event-ID / ?after=")
    kind: Literal["start", "text", "tool_use", "tool_result", "result", "log"] = Field(
        description="start: agent session started · text: agent message · tool_use: the agent calls a tool · "
                    "tool_result: the tool's answer · result: final message · log: any other output line")
    text: str
    tool: str | None = Field(default=None, description="tool name (tool_use)")
    is_error: bool | None = None


class TodoLine(Model):
    line: int = Field(ge=1)
    text: str


class TodoFile(Model):
    path: str
    remaining: int
    lines: list[TodoLine]


class TodoStatus(Model):
    """Actionable TODO(LLM): markers left in the template's files. The header line that explains the marker
    ("Every TODO(LLM) block must be implemented…") is not counted: only the marker followed by a colon is."""

    remaining: int
    files: list[TodoFile]


class CodegenResult(Model):
    type: Literal["codegen"] = "codegen"
    commit_before: str | None = Field(default=None, description="package git commit before the agent ran")
    commit_after: str | None = Field(default=None, description="commit of the agent's changes (null: nothing changed)")
    changed_files: list[str] = Field(default_factory=list)
    todo: TodoStatus
    git_note: str | None = Field(default=None, description="why git was skipped, if it was")


DeterminismStatus = Literal["identical", "numerically_close", "different", "failed"]


class DeterminismDiff(Model):
    """One difference observed between two consecutive runs of the master script."""

    target: str | None = Field(default=None, description="target id in outputs/results.json, if applicable")
    path: str | None = Field(default=None, description="package-relative output file path, if applicable")
    kind: Literal["missing_in_run", "value_changed", "table_changed", "file_changed", "parse_error"]
    detail: str
    max_rel_diff: float | None = Field(default=None, description="largest relative numeric change between runs, if numeric")
    looks_like_clock: bool = Field(default=False, description="heuristic: the difference looks like a clock/timestamp reading")


class DeterminismCheck(Model):
    """Outcome of running the master script twice and comparing outputs/results.json and produced output files."""

    runs: int = Field(ge=2)
    status: DeterminismStatus
    run1_results_sha256: str | None = None
    run2_results_sha256: str | None = None
    differences: list[DeterminismDiff] = Field(default_factory=list)
    summary: str


class RunIsolation(Model):
    """Clean-room settings applied to the script run (honest record, not an OS container)."""

    network_denied_by: Literal["namespace", "sandbox", "proxy"] = Field(
        description="how network access was denied: Linux network namespace, macOS sandbox-exec, or dead-proxy env vars only")
    clean_home: bool = Field(default=True, description="private empty HOME/TMPDIR and scratch R library (user libraries stay readable)")
    tz: str = Field(default="UTC")
    locale: str = Field(default="C.UTF-8")
    runtime_caches: list[str] = Field(default_factory=list, description="runtime cache files left behind in the package by the run")


FailureHintKind = Literal["missing_package", "path", "network", "encoding", "session_state", "permission", "syntax", "memory"]


class FailureHint(Model):
    """Actionable hint parsed from a failed run's log."""

    kind: FailureHintKind
    detail: str
    suggestion: str


class RunResult(Model):
    commit_before: str | None = Field(default=None, description="git commit of the package just before the run")
    snapshot: dict[str, str] | None = Field(default=None, description='package path -> "sha256:<hex>" of the targets, plan, scripts/code and used inputs at launch; null for older runs')
    results_sha256: str | None = Field(default=None, description='"sha256:<hex>" of outputs/results.json at finish; null if none was written')
    log_sha256: str | None = Field(default=None, description='"sha256:<hex>" of the complete run log at finish')
    type: Literal["run"] = "run"
    command: str
    log: str = Field(description="package-relative path of the run log")
    results_file: bool = Field(description="outputs/results.json exists after the run")
    output_errors: list[str] = Field(default_factory=list, description="outputs/results.json problems (format, unknown ids)")
    output_warnings: list[str] = Field(default_factory=list, description="coverage problems (targets missing from the output)")
    commit_after: str | None = None
    runs: int = Field(default=1, ge=1, description="number of script runs requested (2 = determinism check)")
    determinism: DeterminismCheck | None = Field(default=None, description="two-run determinism check result when runs=2")
    isolation: RunIsolation | None = Field(default=None, description="clean-room environment and network isolation applied to the run")
    failure_hints: list[FailureHint] = Field(default_factory=list, description="structured failure hints parsed from the run log when a run fails")


JobResult = Annotated[Union[CodegenResult, RunResult], Field(discriminator="type")]


class Job(Model):
    """jobs/<id>.json"""

    id: str = Field(pattern=r"^job-[0-9a-z-]+$")
    type: JobType
    status: JobStatus
    profile: str | None = Field(default=None, description="agent profile (codegen)")
    argv: list[str] = Field(description="the command that runs (configured, never caller-supplied)")
    request: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    stdout: str = Field(description="package-relative path of the raw stdout")
    stderr: str
    events_format: Literal["claude-stream-json", "opencode-json", "codex-json", "text"]
    result: JobResult | None = None
    error: str | None = None
