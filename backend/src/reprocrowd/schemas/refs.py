"""References into extractions, and the sentences a claims run passed over. Shared by curation.py and claims.py
(which must not import each other's way: curation.py embeds ClaimResult)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .common import Model
from .document import ref_kind


class Ref(Model):
    """Points at one sentence, figure or table of an extraction."""

    extraction: str = Field(description="resource name of the extraction, e.g. ex-grobid")
    id: str = Field(description="sentence (sec3.p2.s1, fig1.s1), figure (fig1) or table (tab1) id in that extraction")

    @field_validator("id")
    @classmethod
    def _kind(cls, v: str) -> str:
        if ref_kind(v) is None:
            raise ValueError(f"{v!r} is not a sentence, figure or table id")
        return v


class SkippedSentence(Model):
    """A sentence the LLM considered and did not turn into a claim."""

    ref: Ref
    reason: Literal["background", "study_design", "other"]
