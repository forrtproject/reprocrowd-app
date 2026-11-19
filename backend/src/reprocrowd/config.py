"""Service configuration (TOML). Everything executable (LLM commands) is configured here, never passed by
API callers. Location: $REPROCROWD_CONFIG, else ~/.config/reprocrowd/config.toml; missing file = defaults."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LlmProfile(BaseModel):
    argv: list[str] = Field(min_length=1, description="command line; {model} and {effort} are substituted, the prompt goes to stdin")
    read_files_argv: list[str] = Field(default_factory=list, description="appended when the LLM must read package files (cwd = package root)")
    json_schema_argv: list[str] = Field(default_factory=list, description="appended with {schema} = the task's output JSON Schema, for CLIs "
                                                                          "that constrain output to a schema (claude: --json-schema)")
    output: Literal["text", "claude-json", "opencode-json", "codex-json"] = Field(
        default="text", description="how to read stdout: plain text, Claude's --output-format json envelope (structured_output / result), "
                                    "OpenCode's run --format json event stream (the answer is the text of the text events), or Codex's "
                                    "exec --json event stream (the answer is the last agent_message)")
    model: str = ""
    effort: str = ""
    timeout_s: int = Field(default=1800, gt=0)


class AgentProfile(BaseModel):
    """A headless coding agent for code generation. It runs in the package folder as a job."""

    argv: list[str] = Field(min_length=1, description="command line; {model}, {effort} and {tools} (comma-separated allowed tools) are substituted, the prompt goes to stdin")
    tools: list[str] = Field(default_factory=lambda: ["Read", "Write", "Edit", "Glob", "Grep"], description="always allowed")
    exec_tools: list[str] = Field(
        default_factory=lambda: ["Bash(Rscript:*)", "Bash(R:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(snakemake:*)", "Bash(pip:*)", "Bash(ls:*)"],
        description="added when the caller allows the agent to run the analysis (allow_exec)")
    model: str = ""
    effort: str = ""
    timeout_s: int = Field(default=7200, gt=0)
    events: Literal["claude-stream-json", "opencode-json", "codex-json", "text"] = Field(default="claude-stream-json", description="how to read the agent's stdout as events")
    no_exec_argv: list[str] = Field(default_factory=list, description="appended when the caller forbids running the analysis (allow_exec=false): "
                                                                    "CLI flags that remove its command-running tools while keeping its read and edit tools")
    no_exec_env: dict[str, str] = Field(default_factory=dict, description="environment added when allow_exec=false, e.g. OpenCode's "
                                                                         "OPENCODE_PERMISSION denying every tool but reading and editing")


def expand_argv(argv: list[str], values: dict[str, str]) -> list[str]:
    """Substitute {name} placeholders. An argument with a placeholder whose value is empty is dropped, with the flag
    before it: ``["--model", "{model}"]`` and ``["-c", "model_reasoning_effort={effort}"]`` both vanish when unset."""
    out: list[str] = []
    for arg in argv:
        value = arg
        for k, v in values.items():
            value = value.replace("{" + k + "}", v)
        if (value == "" and arg != "") or any(not v and "{" + k + "}" in arg for k, v in values.items()):
            # A dropped value takes its flag with it (`-c effort={effort}`); a dropped `--model={model}` is its own flag.
            if out and out[-1].startswith("-") and not arg.startswith("-"):
                out.pop()
            continue
        out.append(value)
    return out


# --disable apps/plugins: no ChatGPT connectors (e.g. GitHub or site-deploy actions) for an agent the app runs.
# The prompt is read from stdin (no prompt argument), so mode flags can be appended.
CODEX_ARGV = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--disable", "apps", "--disable", "plugins",
              "-m", "{model}", "-c", "model_reasoning_effort={effort}"]
# Editing-only Codex: no shell, no sub-agents; reading through reprocrowd.mcp_files (read-only, confined to the package,
# its tools approved for this server only); edits with apply_patch. {python} is the backend's own interpreter.
# Checked with codex-cli 0.167.0: it read and edited, could not run ls or read an environment nonce.
CODEX_NO_EXEC = ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "multi_agent",
                 "-c", "mcp_servers.rc_files.command='{python}'", "-c", 'mcp_servers.rc_files.args=["-I", "-m", "reprocrowd.mcp_files"]',
                 "-c", 'mcp_servers.rc_files.default_tools_approval_mode="approve"']
# Editing-only OpenCode: every tool denied except reading and editing. --auto approves only what is not denied.
OPENCODE_NO_EXEC = {"OPENCODE_PERMISSION": '{"*":"deny","read":"allow","edit":"allow","glob":"allow","grep":"allow","list":"allow","todowrite":"allow"}'}


def _default_agents() -> dict[str, AgentProfile]:
    return {
        "claude": AgentProfile(
            argv=["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
                  "--model", "{model}", "--effort", "{effort}", "--allowedTools", "{tools}"],
            model="sonnet",
            effort="high",
            # --allowedTools only pre-approves: Claude Code still runs commands it classes as read-only (ls, cat).
            # Denying Bash removes command execution when the analysis must not run (checked with Claude Code 2.1.322).
            no_exec_argv=["--disallowedTools", "Bash"],
        ),
        # OpenCode (https://opencode.ai) as a headless coding agent: --auto approves the tool calls it makes
        # (Read/Write/Edit/Glob/Grep/Bash), so it can work without a terminal. --format json emits one event per
        # line, read by events.py's opencode-json parser. model = "" uses the CLI's default; the app lets the user pick.
        "opencode": AgentProfile(
            argv=["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}", "--auto"],
            events="opencode-json",
            model="",
            effort="",
            tools=[],
            exec_tools=[],
            no_exec_env=OPENCODE_NO_EXEC,
        ),
        # Codex CLI (https://developers.openai.com/codex): exec --json emits JSONL events (events.py's codex-json parser).
        # workspace-write: Codex's sandbox confines writes to the package. --ignore-user-config skips ~/.codex/config.toml
        # (MCP servers, default model) but keeps the login. No-analysis mode (CODEX_NO_EXEC) removes its shell and
        # sub-agent tools; Codex reads files only through its shell, so it gets the read-only file server instead.
        "codex": AgentProfile(argv=CODEX_ARGV + ["-s", "workspace-write"], events="codex-json", tools=[], exec_tools=[], no_exec_argv=CODEX_NO_EXEC),
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
        ),
        # OpenCode (https://opencode.ai): the prompt goes to stdin; --format json emits one event per line and the
        # model's answer is read from the text events. model = "" uses the CLI's default; the app lets the user pick.
        "opencode": LlmProfile(
            argv=["opencode", "run", "--format", "json", "-m", "{model}", "--variant", "{effort}"],
            output="opencode-json",
            model="",
            effort="",
        ),
        # Codex CLI, read-only sandbox: it can read the package files it is pointed to but not change them.
        "codex": LlmProfile(argv=CODEX_ARGV + ["-s", "read-only"], output="codex-json"),
    }


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8770
    token: str = Field(default="", description="if set, requests need 'Authorization: Bearer <token>'")
    registry: Path = Path("~/.local/share/reprocrowd/registry.json")
    allowed_roots: list[Path] = Field(default_factory=list, description="local paths the service may read/create under; empty = any")
    openalex_api_key: str = ""
    mailto: str = ""
    max_download_mb: int = 200
    max_upload_mb: int = 500
    max_unpack_mb: int = Field(default=20000, gt=0, description="limit for the unpacked size of one zip archive (zip bombs)")
    max_unpack_files: int = Field(default=20000, gt=0, description="limit for the number of files in one zip archive")
    llm_default_profile: str = "claude"
    llm_profiles: dict[str, LlmProfile] = Field(default_factory=_default_profiles)
    grobid_url: str = Field(default="", description="GROBID service, e.g. http://localhost:8070; empty = not configured")
    grobid_timeout_s: int = 600
    docling_argv: list[str] = Field(default_factory=lambda: ["docling", "--to", "json", "--output", "{outdir}", "{input}"],
                                    description="{input}: the PDF, {outdir}: folder for the JSON; empty = not configured")
    docling_timeout_s: int = 1800
    database_url: str = Field(default="", description="package database, e.g. http://127.0.0.1:8765; empty = none")
    zenodo_url: str = Field(default="https://zenodo.org", description="Zenodo for deposits")
    zenodo_sandbox_url: str = Field(default="https://sandbox.zenodo.org", description="Zenodo's test instance (separate accounts and tokens)")
    zenodo_sandbox: bool = Field(default=False, description="use the sandbox unless a request says otherwise")
    zenodo_token: str = Field(default="", description="personal access token (scopes deposit:write, deposit:actions) used when a request "
                                                      "brings none; clients usually send the user's own")
    zenodo_community: str = Field(default="", description="Zenodo community the deposits are submitted to; empty = none")
    zenodo_creators: list[dict[str, str]] = Field(default_factory=list, description="creators when a request names none: [{name, orcid?, affiliation?}]")
    zenodo_license: str = Field(default="cc-by-4.0", description="license id of the deposits")
    license_code: Literal["MIT", "CC-BY-4.0"] = Field(default="MIT", description="SPDX licence of newly authored analysis code "
                                                      "(scripts, code) in archives; metadata is CC0-1.0, reports and outputs CC-BY-4.0")
    github_api_url: str = Field(default="https://api.github.com", description="GitHub REST API for pilot submissions")
    github_pilots_repo: str = Field(default="forrtproject/reprocrowd-pilots", pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
                                    description="owner/name of the repository that receives pilot submissions")
    github_token: str = Field(default="", description="fallback token for local CLI use; clients usually send the user's own")
    run_timeout_s: int = Field(default=7200, description="limit for a run of the master script")
    agent_default_profile: str = "claude"
    agent_profiles: dict[str, AgentProfile] = Field(default_factory=_default_agents)

    def path_allowed(self, p: Path) -> bool:
        if not self.allowed_roots:
            return True
        p = p.expanduser().resolve()
        return any(p.is_relative_to(r.expanduser().resolve()) for r in self.allowed_roots)


def config_path() -> Path:
    return Path(os.environ.get("REPROCROWD_CONFIG", "~/.config/reprocrowd/config.toml")).expanduser()


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
    for k in ("max_download_mb", "max_upload_mb", "max_unpack_mb", "max_unpack_files"):
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
    for k, v in raw.get("zenodo", {}).items():
        data[f"zenodo_{k}"] = v
    if "code" in raw.get("licensing", {}):
        data["license_code"] = raw["licensing"]["code"]
    for k, v in raw.get("github", {}).items():  # api_url, pilots_repo, token
        data[f"github_{k}"] = v
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
