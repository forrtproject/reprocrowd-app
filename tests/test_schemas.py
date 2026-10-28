from pathlib import Path

from metareader.schemas.export import outputs

ROOT = Path(__file__).parent.parent


def test_generated_files_up_to_date():
    """schemas/*.schema.json, docs/schemas.md and docs/openapi.json are generated: run `mr-schemas export`."""
    stale = [str(p.relative_to(ROOT)) for p, text in outputs(ROOT).items() if not p.exists() or p.read_text() != text]
    assert not stale, f"regenerate with `uv run mr-schemas export`: {stale}"
