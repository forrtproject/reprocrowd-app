from scenario import run_scenario


def test_offline_paper_scenario_is_repeatable(tmp_path):
    first = run_scenario(tmp_path)
    second = run_scenario(tmp_path)
    assert first["projection"] == second["projection"]
    assert second["same_as_previous"] is True
    from pathlib import Path
    assert Path(first["report"]).is_file() and Path(second["report"]).is_file()
