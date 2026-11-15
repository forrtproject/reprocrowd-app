"""Comparison of reproduced results (outputs/results.json) with the targets (results_schema.json)."""
from __future__ import annotations

from typing import Literal
from datetime import datetime
from .curation import Ref

from pydantic import Field

from .common import Model

Status = Literal["match", "within", "deviation", "missing", "figure", "unparsable"]
# /3 (current): decimal reading of reproduced values; a tie matches either neighbour.
# /2 (recorded by earlier reports): exact binary value, half-even rounding.
Policy = Literal["reprocrowd-compare/3", "reprocrowd-compare/2"]
RoundingMode = Literal["either_direction_at_ties", "half_even_exact_binary"]
STATUS_LABEL: dict[str, str] = {
    "match": "✓ match",
    "within": "≈ within tolerance",
    "deviation": "✗ deviation",
    "missing": "∅ missing",
    "figure": "🖼 compare visually",
    "unparsable": "? not comparable",
}


class CompareSettings(Model):
    rounding: Literal["reported"] | int = Field(default="reported", description='"reported": round to the decimals printed in the paper; or a number of decimals (0–10)')
    threshold: float = Field(default=5, ge=0, description="deviation in % up to which a numeric result counts as within tolerance")


class Cell(Model):
    printed: str | None = None
    comparator: str | None = None
    quantum: int | None = None
    expected: str
    reproduced: str
    status: Status
    deviation: float | None = Field(default=None, description="% deviation of a numeric cell")


class CompareRow(Model):
    refs: list[Ref] = Field(default_factory=list)
    precision: Literal["printed", "explicit", "inferred"] | None = Field(default=None, description='where the rounding place came from: "printed" token, an "explicit" legacy decimals field, or "inferred" from an older numeric value')
    quantum: int | None = Field(default=None, description="decimal exponent of the last printed digit (-2 for 0.17, 2 for 1.2e3)")
    target: str
    type: Literal["number", "categorical", "table", "figure"]
    description: str
    status: Status
    expected_text: str
    reproduced_text: str
    deviation: float | None = Field(default=None, description="% deviation (numbers)")
    cells: list[list[Cell]] | None = Field(default=None, description="table targets: cell by cell")
    matched: int | None = None
    total: int | None = None
    note: str | None = Field(default=None, description="the reproduction's note on this result")


class ComparisonBasis(Model):
    results_sha256: str
    results_schema_sha256: str
    created_at: datetime
    policy: Policy = "reprocrowd-compare/3"


class Comparison(Model):
    """GET …/comparison, and comparison.json written with the report."""

    policy: Policy | None = Field(default=None, description="null: an older comparison.json that recorded no policy")
    rounding_mode: RoundingMode | None = None
    basis: ComparisonBasis | None = None
    settings: CompareSettings
    summary: dict[str, int] = Field(description="number of targets per status")
    rows: list[CompareRow]
    output_errors: list[str] = Field(description="outputs/results.json does not have the required format")
    output_warnings: list[str] = Field(description="targets missing from the output, unknown ids")
