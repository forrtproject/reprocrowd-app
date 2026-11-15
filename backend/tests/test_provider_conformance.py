from conformance.runner import run_conformance


def test_fake_transports_conform():
    result = run_conformance()
    assert result["passed"], [row for row in result["cases"] if not row["passed"]]
