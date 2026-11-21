"""Environments of the programs the backend starts (#91).

The backend inherits the app's whole environment, which may hold the user's API keys and cloud credentials
(``AWS_*``, ``GITHUB_TOKEN``, …) and ``REPROCROWD_CONFIG``, the path of the file with the backend's own token.
Analysis scripts run code from papers and repositories, and agents run whatever a prompt talks them into, so each
gets an allowlist instead:

- every program: what a process needs to work at all (paths, locale, temp folders, proxies and CA certificates,
  and on Windows the system variables);
- analysis scripts: plus the variables of R, Python and their package managers;
- LLM and agent CLIs: plus the variables of that CLI and the model providers it talks to (chosen by the name of
  the executable, like the isolation flags in config), plus the profile's ``env_pass``.

``REPROCROWD_*`` is never passed on. A name ending in ``*`` matches every variable starting with the rest.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Mapping

BASE = [
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "LC_*", "TERM", "COLORTERM", "TZ", "TMPDIR", "TMP", "TEMP", "XDG_*",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "__CF_USER_TEXT_ENCODING",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "ALLUSERSPROFILE",
    "PUBLIC", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "PSMODULEPATH",
]

SCRIPTS = [
    "R_*", "RENV_*", "VIRTUAL_ENV", "PYTHONPATH", "CONDA*", "MAMBA*", "PYENV*", "JAVA_HOME", "LD_LIBRARY_PATH", "PKG_CONFIG_PATH",
    "OMP_NUM_THREADS", "MKL_*", "OPENBLAS_*", "GDAL_*", "PROJ_*", "SNAKEMAKE_*",
]

# Model providers an LLM CLI may be set up for through environment variables.
_PROVIDERS = ["ANTHROPIC_*", "OPENAI_*", "AZURE_*", "AWS_*", "GOOGLE_*", "GEMINI_*", "VERTEX_*", "CLOUD_ML_*", "OPENROUTER_*",
              "GROQ_*", "MISTRAL_*", "XAI_*", "DEEPSEEK_*", "TOGETHER_*", "FIREWORKS_*", "CEREBRAS_*", "OLLAMA_*"]
CLIS = {
    # Claude Code: its own settings, and Bedrock/Vertex when it is set up for them.
    "claude": ["ANTHROPIC_*", "CLAUDE_*", "DISABLE_*", "AWS_*", "GOOGLE_*", "VERTEX_*", "CLOUD_ML_*"],
    "codex": ["OPENAI_*", "CODEX_*", "AZURE_*"],
    "opencode": ["OPENCODE_*", *_PROVIDERS],
    "gemini": ["GEMINI_*", "GOOGLE_*"],
}


def cli_name(argv: list[str]) -> str:
    """The executable's name, without its folder and Windows suffix: ``C:\\npm\\codex.cmd`` → ``codex``."""
    name = Path(argv[0].replace("\\", "/")).name.lower() if argv else ""
    for suffix in (".exe", ".cmd", ".ps1", ".bat"):
        name = name.removesuffix(suffix)
    return name


def allowed(names: Iterable[str], env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The variables of ``env`` (default: this process's) that ``names`` allow; never ``REPROCROWD_*``. Windows names are
    case-insensitive, so the comparison is too."""
    exact = {n.upper() for n in names if not n.endswith("*")}
    prefixes = tuple(n[:-1].upper() for n in names if n.endswith("*"))
    return {k: v for k, v in (os.environ if env is None else env).items()
            if not k.upper().startswith("REPROCROWD_") and (k.upper() in exact or k.upper().startswith(prefixes))}


def for_script(env: Mapping[str, str] | None = None) -> dict[str, str]:
    return allowed(BASE + SCRIPTS, env)


def for_cli(argv: list[str], env_pass: Iterable[str] = (), env: Mapping[str, str] | None = None) -> dict[str, str]:
    return allowed([*BASE, *CLIS.get(cli_name(argv), []), *env_pass], env)
