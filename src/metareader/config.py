"""Service configuration (TOML). Everything executable (LLM commands) is configured here, never passed by
API callers. Location: $MR_CONFIG, else ~/.config/metareader/config.toml; missing file = defaults."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LlmProfile(BaseModel):
    argv: list[str] = Field(description="command line; {model} and {effort} are substituted, the prompt goes to stdin")
    read_files_argv: list[str] = Field(default_factory=list, description="appended when the LLM must read package files (cwd = package root)")
    json_schema_argv: list[str] = Field(default_factory=list, description="appended with {schema} = the task's output JSON Schema, for CLIs "
                                                                          "that constrain output to a schema (claude: --json-schema)")
    output: Literal["text", "claude-json"] = Field(default="text", description="how to read stdout: plain text, or Claude's --output-format json "
                                                                               "envelope (structured_output / result)")
    model: str = ""
    effort: str = ""
    timeout_s: int = 1800


class AgentProfile(BaseModel):
    """A headless coding agent for code generation. It runs in the package folder as a job."""

    argv: list[str] = Field(description="command line; {model}, {effort} and {tools} (comma-separated allowed tools) are substituted, the prompt goes to stdin")
    tools: list[str] = Field(default_factory=lambda: ["Read", "Write", "Edit", "Glob", "Grep"], description="always allowed")
    exec_tools: list[str] = Field(
        default_factory=lambda: ["Bash(Rscript:*)", "Bash(R:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(snakemake:*)", "Bash(pip:*)", "Bash(ls:*)"],
        description="added when the caller allows the agent to run the analysis (allow_exec)")
    model: str = ""
    effort: str = ""
    timeout_s: int = 7200
    events: Literal["claude-stream-json", "text"] = Field(default="claude-stream-json", description="how to read the agent's stdout as events")


def expand_argv(argv: list[str], values: dict[str, str]) -> list[str]:
    """Substitute {name} placeholders. An argument that becomes empty is dropped, with the flag before it."""
    out: list[str] = []
    for arg in argv:
        value = arg
        for k, v in values.items():
            value = value.replace("{" + k + "}", v)
        if value == "" and arg != "":
            if out and out[-1].startswith("-"):
                out.pop()
            continue
        out.append(value)
    return out


def _default_agents() -> dict[str, AgentProfile]:
    return {
        "claude": AgentProfile(
            argv=["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
                  "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"],
            model="sonnet",
            effort="high",
        )
    }


def _default_profiles() -> dict[str, LlmProfile]:
    return {
        "claude": LlmProfile(
            argv=["claude", "-p", "--output-format", "json", "--model", "{model}", "--effort", "{effort}"],
            read_files_argv=["--allowedTools", "Read"],
            json_schema_argv=["--json-schema", "{schema}"],
            output="claude-json",
            model="sonnet",
            effort="medium",
        )
    }


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8770
    token: str = Field(default="", description="if set, requests need 'Authorization: Bearer <token>'")
    registry: Path = Path("~/.local/share/metareader/registry.json")
    allowed_roots: list[Path] = Field(default_factory=list, description="local paths the service may read/create under; empty = any")
    openalex_api_key: str = ""
    mailto: str = ""
    max_download_mb: int = 200
    max_upload_mb: int = 500
    llm_default_profile: str = "claude"
    llm_profiles: dict[str, LlmProfile] = Field(default_factory=_default_profiles)
    grobid_url: str = Field(default="", description="GROBID service, e.g. http://localhost:8070; empty = not configured")
    grobid_timeout_s: int = 600
    docling_argv: list[str] = Field(default_factory=lambda: ["docling", "--to", "json", "--output", "{outdir}", "{input}"],
                                    description="{input}: the PDF, {outdir}: folder for the JSON; empty = not configured")
    docling_timeout_s: int = 1800
    database_url: str = Field(default="", description="package database, e.g. http://127.0.0.1:8765; empty = none")
    run_timeout_s: int = Field(default=7200, description="limit for a run of the master script")
    agent_default_profile: str = "claude"
    agent_profiles: dict[str, AgentProfile] = Field(default_factory=_default_agents)

    def path_allowed(self, p: Path) -> bool:
        if not self.allowed_roots:
            return True
        p = p.expanduser().resolve()
        return any(p.is_relative_to(r.expanduser().resolve()) for r in self.allowed_roots)


def config_path() -> Path:
    return Path(os.environ.get("MR_CONFIG", "~/.config/metareader/config.toml")).expanduser()


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    if not path.exists():
        return Settings()
    raw = tomllib.loads(path.read_text())
    server, openalex, llm = raw.get("server", {}), raw.get("openalex", {}), raw.get("llm", {})
    data: dict = {**server}
    if "api_key" in openalex:
        data["openalex_api_key"] = openalex["api_key"]
    if "mailto" in openalex:
        data["mailto"] = openalex["mailto"]
    for k in ("max_download_mb", "max_upload_mb"):
        if k in raw.get("downloads", {}):
            data[k] = raw["downloads"][k]
    if "default_profile" in llm:
        data["llm_default_profile"] = llm["default_profile"]
    if "profiles" in llm:
        data["llm_profiles"] = llm["profiles"]
    conv = raw.get("converters", {})
    for name in ("grobid", "docling"):
        for k, v in conv.get(name, {}).items():
            data[f"{name}_{k}"] = v
    if "url" in raw.get("database", {}):
        data["database_url"] = raw["database"]["url"]
    if "timeout_s" in raw.get("run", {}):
        data["run_timeout_s"] = raw["run"]["timeout_s"]
    agent = raw.get("agent", {})
    if "default_profile" in agent:
        data["agent_default_profile"] = agent["default_profile"]
    if "profiles" in agent:
        data["agent_profiles"] = agent["profiles"]
    return Settings.model_validate(data)


@lru_cache
def settings() -> Settings:
    return load_settings()
