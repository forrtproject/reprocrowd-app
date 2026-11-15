"""Dev-only helpers for explicit live-provider runs (scenario.py --llm/--agent, provider_smoke.py).

Live runs use the CLI's own sign-in. They are never started by the test suite. A run is refused when an API-key
variable would switch the CLI from the subscription to metered API billing. Metered providers need a spend ledger: a
private JSON file outside the repository that is checked before and appended after each run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

from reprocrowd.config import AgentProfile, LlmProfile, _default_agents, _default_profiles, load_settings

# Variables that make the CLI bill an API account instead of the signed-in subscription.
BILLING_VARS = {"claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "codex": ("OPENAI_API_KEY", "CODEX_API_KEY")}
# CLIs whose cost goes to a metered account (OpenCode with an API-key provider) rather than a subscription.
METERED = {"opencode"}
# A job killed (timeout/cancel) before OpenCode reports a step may still be billed for its first request; counted at
# about 15k input tokens at DeepSeek V4 Pro's peak cache-miss price, rounded up.
KILLED_JOB_ALLOWANCE_USD = 0.02


def cli_of(argv: list[str]) -> str:
    return Path(argv[0]).name.removesuffix(".exe").removesuffix(".cmd")


def profiles(name: str, source: Path | None) -> tuple[LlmProfile, AgentProfile]:
    """The LLM and agent profiles called ``name``: from a backend.toml (e.g. one saved through Settings), else the
    shipped defaults. ``fixture`` is never resolved here."""
    if source:
        cfg = load_settings(source)
        llm, agent = cfg.llm_profiles.get(name), cfg.agent_profiles.get(name)
    else:
        llm, agent = _default_profiles().get(name), _default_agents().get(name)
    if llm is None or agent is None:
        raise SystemExit(f"no llm and agent profile {name!r} in {source or 'the shipped defaults'}")
    return llm, agent


def refuse_api_billing(argv: list[str]) -> None:
    found = [v for v in BILLING_VARS.get(cli_of(argv), ()) if os.environ.get(v)]
    if found:
        raise SystemExit(f"refusing a live {cli_of(argv)} run: {', '.join(found)} is set and would bill an API account "
                         "instead of the signed-in subscription. Unset it for this command.")


def version(argv: list[str]) -> str | None:
    exe = shutil.which(argv[0])
    if not exe:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else None


def peak(at: datetime) -> bool:
    """DeepSeek's peak windows (pricing page, 15 Nov 2026): Mon-Fri 01-04 and 06-10 UTC. Chinese public holidays are
    off-peak but treated as peak here, which can only overcount."""
    at = at.astimezone(timezone.utc)
    return at.weekday() < 5 and (1 <= at.hour < 4 or 6 <= at.hour < 10)


def reported_cost(text: str) -> float:
    """Sum of OpenCode's per-step ``cost`` in an event stream (USD at the provider's list price)."""
    total = 0.0
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "step_finish" and isinstance(ev.get("part"), dict):
            total += float(ev["part"].get("cost") or 0)
    return total


def tokens(text: str) -> dict:
    """Token use reported in an event stream (OpenCode step_finish, Codex turn.completed, Claude result)."""
    out = {"input": 0, "output": 0, "cache_read": 0}
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "step_finish" and isinstance(ev.get("part"), dict):
            t = ev["part"].get("tokens") or {}
            out["input"] += t.get("input", 0); out["output"] += t.get("output", 0) + t.get("reasoning", 0)
            out["cache_read"] += (t.get("cache") or {}).get("read", 0)
        elif ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            out["input"] += u.get("input_tokens", 0); out["output"] += u.get("output_tokens", 0)
            out["cache_read"] += u.get("cached_input_tokens", 0)
        elif ev.get("type") == "result" and isinstance(ev.get("usage"), dict):
            u = ev["usage"]
            out["input"] += u.get("input_tokens", 0); out["output"] += u.get("output_tokens", 0)
            out["cache_read"] += u.get("cache_read_input_tokens", 0)
    return out


class Ledger:
    """The shared private spend ledger. counted_usd = reported cost, doubled if the run touched a peak window."""

    def __init__(self, path: Path | None, run_cap: float):
        self.path, self.run_cap = path, run_cap

    def data(self) -> dict:
        return json.loads(self.path.read_text())

    def total(self) -> float:
        return round(sum(e.get("counted_usd") if e.get("counted_usd") is not None else e.get("estimate_usd") or 0
                         for e in self.data()["entries"]), 6)

    def check(self, what: str) -> None:
        if self.path is None:
            raise SystemExit("a metered (OpenCode) live run needs --spend-ledger PATH (a private file outside git) and --max-usd")
        d = self.data()
        if self.total() + self.run_cap > d["hard_cap_usd"]:
            raise SystemExit(f"refusing {what}: ledger total {self.total():.4f} + this run's cap {self.run_cap:.2f} "
                             f"exceeds the hard cap {d['hard_cap_usd']:.2f} USD")

    def record(self, who: str, what: str, started: datetime, reported: float, usage: dict, folder: str, unreported_jobs: int = 0) -> dict:
        ended = datetime.now(timezone.utc)
        touched = peak(started) or peak(ended)
        allowance = unreported_jobs * KILLED_JOB_ALLOWANCE_USD
        entry = {"at": ended.isoformat(timespec="seconds"), "who": who, "what": what, "reported_usd": round(reported, 6),
                 "counted_usd": round(reported * (2 if touched else 1) + allowance, 6), "peak": touched, "tokens": usage, "run_folder": folder,
                 **({"killed_jobs_without_reported_cost": unreported_jobs, "allowance_usd": allowance} if unreported_jobs else {})}
        d = self.data()
        d["entries"].append(entry)
        d["total_counted_usd"] = round(sum(e.get("counted_usd") if e.get("counted_usd") is not None else e.get("estimate_usd") or 0
                                           for e in d["entries"]), 6)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        return entry


def run_env(argv: list[str]) -> dict[str, str]:
    """Environment changes for a live run: OpenCode otherwise forwards ~/.claude/CLAUDE.md (private, unrelated
    instructions) to the model provider."""
    return {"OPENCODE_DISABLE_CLAUDE_CODE": "1"} if cli_of(argv) == "opencode" else {}


if __name__ == "__main__":
    # Price a package whose metered calls were made from the app UI, with the same counting rule:
    #   uv run python tests/live.py record-package PACKAGE_DIR --spend-ledger PATH --since ISO_TIME --what "..."
    #   uv run python tests/live.py total --spend-ledger PATH
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["record-package", "total"])
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument("--spend-ledger", type=Path, required=True)
    parser.add_argument("--since", help="UTC ISO time the UI run started (for the peak rule); default: now")
    parser.add_argument("--what", default="UI-driven run")
    parser.add_argument("--killed-jobs", type=int, default=0, help="metered jobs stopped before reporting a cost")
    a = parser.parse_args()
    ledger = Ledger(a.spend_ledger.expanduser(), 0)
    if a.action == "total":
        d = ledger.data()
        print(json.dumps({"counted_usd": ledger.total(), "hard_cap_usd": d["hard_cap_usd"], "remaining_usd": round(d["hard_cap_usd"] - ledger.total(), 6)}))
    else:
        started = datetime.fromisoformat(a.since) if a.since else datetime.now(timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        # Only calls written since the UI run started: earlier (already recorded) calls in the package are not recounted.
        files = [f for f in [*a.package.glob("llm-calls/*/stdout.txt"), *a.package.glob("jobs/*/stdout.log")] if f.stat().st_mtime >= started.timestamp()]
        text = "".join(f.read_text(errors="replace") + "\n" for f in files)
        print(json.dumps(ledger.record("live.py", a.what, started, reported_cost(text), tokens(text), str(a.package), a.killed_jobs)))
