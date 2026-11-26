"""Backend-recorded process evidence, distinct from scientific validation."""
from typing import Any, Literal
from pydantic import Field
from .common import Model
from .curation import Ref


class TargetEvidence(Model):
    target: str
    refs: list[Ref]
    step: str | None = None
    step_evidence: Literal["declared_matches_plan", "declared_conflicts_plan", "declared_unchecked", "planned_only", "none"]
    level: Literal["process"] = "process"


class ExecutionEvidence(Model):
    schema_version: Literal["1"] = "1"
    track: Literal["recreation"] | None = None
    run: dict[str, Any] | None = None
    stale: list[str] | None = None
    stale_reason: str | None = None
    added_since_run: list[str] = Field(default_factory=list)
    stale_hashes: dict[str, str | None] = Field(default_factory=dict, description=(
        "current hash of each stale or added file (null if deleted), so a later edit to an already stale file is detected"))
    results_match_run: bool | None = None
    codegen: dict[str, Any] | None = None
    targets: list[TargetEvidence] = Field(default_factory=list)
    log_path: str | None = None
    log_truncated: bool = False
    log_sha256: str | None = None
