"""The live-run guards, without any live call: billing variables, the spend ledger's cap and peak counting."""
from datetime import datetime, timezone
import json

import pytest

import live


def test_refuses_api_billing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        live.refuse_api_billing(["codex", "exec"])
    live.refuse_api_billing(["claude", "-p"])  # another CLI's key is irrelevant
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture")
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        live.refuse_api_billing(["/usr/local/bin/claude"])


def test_peak_windows():
    assert live.peak(datetime(2026, 11, 16, 7, tzinfo=timezone.utc))      # Monday 07 UTC
    assert not live.peak(datetime(2026, 11, 16, 5, tzinfo=timezone.utc))  # Monday 05 UTC
    assert not live.peak(datetime(2026, 11, 15, 7, tzinfo=timezone.utc))  # Sunday


def test_ledger_cap_and_counting(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"hard_cap_usd": 0.99, "entries": [{"estimate_usd": 0.5, "counted_usd": 0.6}]}))
    with pytest.raises(SystemExit, match="exceeds the hard cap"):
        live.Ledger(path, 0.4).check("run")
    live.Ledger(path, 0.3).check("run")
    with pytest.raises(SystemExit, match="spend-ledger"):
        live.Ledger(None, 0.1).check("run")
    out = '{"type":"step_finish","part":{"cost":0.01,"tokens":{"input":10,"output":2,"reasoning":1,"cache":{"read":5}}}}\nnoise\n'
    assert live.reported_cost(out) == 0.01 and live.tokens(out) == {"input": 10, "output": 3, "cache_read": 5}
    entry = live.Ledger(path, 0.3).record("test", "fixture", datetime(2026, 11, 16, 7, tzinfo=timezone.utc), 0.01, {}, "x")
    assert entry["peak"] and entry["counted_usd"] == 0.02
    assert json.loads(path.read_text())["total_counted_usd"] == 0.62
