import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"


def _profile(mode: str, timeout: int = 30) -> str:
    return f'[llm.profiles.{mode}]\nargv = ["{sys.executable}", "{HERE / "fake_llm.py"}", "{mode}"]\nmodel = "fake-1"\neffort = "low"\ntimeout_s = {timeout}\n'


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[server]\nregistry = "{tmp_path / "registry.json"}"\n\n[openalex]\napi_key = ""\n\n'
        f'[converters.grobid]\nurl = "http://grobid.test"\n\n'
        f'[converters.docling]\nargv = ["{sys.executable}", "{HERE / "fake_docling.py"}", "--to", "json", "--output", "{{outdir}}", "{{input}}"]\n\n'
        '[llm]\ndefault_profile = "ok"\n\n'
        + "".join(_profile(m) for m in ("ok", "garbage", "badschema", "fail"))
        + _profile("sleep", timeout=1)
        + '\n[agent]\ndefault_profile = "ok"\n'
        + "".join(f'[agent.profiles.{name}]\nargv = ["{sys.executable}", "{HERE / "fake_agent.py"}", "{mode}", "--tools", "{{tools}}"]\nmodel = "fake-agent-1"\ntimeout_s = {t}\n'
                  for name, mode, t in (("ok", "ok", 30), ("slow", "slow", 30), ("fail", "fail", 30), ("short", "slow", 1)))
    )
    monkeypatch.setenv("MR_CONFIG", str(cfg))
    from metareader.config import settings

    settings.cache_clear()
    from metareader.api.app import app

    with TestClient(app) as c:
        yield c
    settings.cache_clear()


@pytest.fixture
def pkg(client, tmp_path):
    r = client.post("/v1/packages", json={"path": str(tmp_path / "pkg"), "identifier": "10.1371/journal.pbio.1002106"})
    assert r.status_code == 201, r.text
    return r.json()["id"]
