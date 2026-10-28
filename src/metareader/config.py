"""Service configuration (TOML). Everything executable (LLM commands) is configured here, never passed by
API callers. Location: $MR_CONFIG, else ~/.config/metareader/config.toml; missing file = defaults."""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class LlmProfile(BaseModel):
    argv: list[str] = Field(description="command line; {model} and {effort} are substituted, the prompt goes to stdin")
    read_files_argv: list[str] = Field(default_factory=list, description="appended when the LLM must read package files (cwd = package root)")
    model: str = ""
    effort: str = ""
    timeout_s: int = 1800


def _default_profiles() -> dict[str, LlmProfile]:
    return {
        "claude": LlmProfile(
            argv=["claude", "-p", "--output-format", "text", "--model", "{model}", "--effort", "{effort}"],
            read_files_argv=["--allowedTools", "Read"],
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
    llm_default_profile: str = "claude"
    llm_profiles: dict[str, LlmProfile] = Field(default_factory=_default_profiles)

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
    if "max_download_mb" in raw.get("downloads", {}):
        data["max_download_mb"] = raw["downloads"]["max_download_mb"]
    if "default_profile" in llm:
        data["llm_default_profile"] = llm["default_profile"]
    if "profiles" in llm:
        data["llm_profiles"] = llm["profiles"]
    return Settings.model_validate(data)


@lru_cache
def settings() -> Settings:
    return load_settings()
