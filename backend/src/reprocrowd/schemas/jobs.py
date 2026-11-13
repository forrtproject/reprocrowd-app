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


class RunResult(Model):
    type: Literal["run"] = "run"
    command: str
    log: str = Field(description="package-relative path of the run log")
    results_file: bool = Field(description="outputs/results.json exists after the run")
    output_errors: list[str] = Field(default_factory=list, description="outputs/results.json problems (format, unknown ids)")
    output_warnings: list[str] = Field(default_factory=list, description="coverage problems (targets missing from the output)")
    commit_after: str | None = None


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
    events_format: Literal["claude-stream-json", "opencode-json", "text"]
    result: JobResult | None = None
    error: str | None = None
