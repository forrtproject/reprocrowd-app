"""Per-claim results and statuses (GET …/claims:status) and the claims trace (GET …/claims:trace, claims_trace.json).
Must not import from curation.py (which embeds ClaimResult); shared types come from refs.py."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Model
from .comparison import Status
from .refs import SkippedSentence

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


# ---------- the claims trace (one object per broad claim) ----------


class TraceEvidence(Model):
    """One reported value. There is no per-value tolerance: values are compared with the package-wide rule."""

    id: str = Field(description="the target's id")
    label: str = Field(description="the target's description")
    value: float | int | str | list[list[str]] | None = Field(default=None, description="the value as the paper reports it")
    comparator: str = Field(description='how the paper states it: "=", "<", …')
    unit: str | None = None
    decimals: int | None = Field(default=None, description="decimals as printed")
    source: str | None = Field(default=None, description="readable location of the target's first reference (section, table, figure)")


class TraceSubClaim(Model):
    id: str
    claim: str
    method: str | None = Field(default=None, description="id of the method in the claim's `methods`")
    quantitative_evidence: list[TraceEvidence]


class TraceMethod(Model):
    id: str
    summary: str
    quote: str


class ClaimTrace(Model):
    """A broad claim in the user's trace format, with ids, mode, links, methods, flagged assumptions and its result."""

    id: str | None = Field(default=None, description="the paper's DOI")
    title: str | None = Field(default=None, description="the paper's title")
    claim_id: str
    main_claim: str
    mode: Literal["quote", "paraphrase"]
    original_quote: str
    testable: bool
    depends_on: list[str]
    traced_under: list[str]
    sub_claims: list[TraceSubClaim]
    quantitative_evidence: list[TraceEvidence] = Field(description="an atomic claim's own evidence")
    methods: list[TraceMethod]
    assumptions: list[str] = Field(description="the coding agent's `# ASSUMPTION:` comments in the claim's file and the prepare file")
    status: ClaimState
    result: ClaimResult | None = Field(default=None, description="the last run's result; absent when the claim has not run")


class TracePaper(Model):
    doi: str | None = None
    title: str | None = None


class ClaimsTrace(Model):
    """claims_trace.json: every effective claim's trace, and the sentences the enabled claims run passed over."""

    paper: TracePaper
    claims: list[ClaimTrace]
    skipped: list[SkippedSentence]
