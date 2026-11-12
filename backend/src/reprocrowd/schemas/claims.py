"""Per-claim results and statuses (GET …/claims:status). Must not import from curation.py (which embeds ClaimResult)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Model
from .comparison import Status

Verdict = Literal["reproduced", "partly", "not_reproduced"]
ClaimState = Literal["review", "ready", "running", "reproduced", "partly", "not_reproduced", "not_testable"]


class EvidenceResult(Model):
    target: str
    status: Status
    expected_text: str
    reproduced_text: str


class SubClaimResult(Model):
    id: str
    verdict: Verdict
    matched: int = Field(description="evidence values that match or are within tolerance")
    tested: int = Field(description="evidence values that were computed (match, within or deviation)")
    evidence: list[EvidenceResult]


class ClaimResult(Model):
    """A claim's result from the comparison: every evidence value of the claim and its sub-claims pooled."""

    claim: str
    verdict: Verdict
    matched: int
    tested: int
    by_sub_claim: list[SubClaimResult] = Field(description="results of the sub-claims, beside the pooled one")
    evidence: list[EvidenceResult] = Field(description="the claim's own evidence (an atomic claim's)")


class ClaimStatus(Model):
    claim: str
    state: ClaimState
    stale: bool = Field(description="the claim changed since it last ran; `result` is then the old one")
    ran_at: datetime | None
    result: ClaimResult | None
