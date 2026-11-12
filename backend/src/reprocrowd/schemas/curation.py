"""Targets and actions, and the generic structure that keeps LLM extractions apart from user changes.

A curated file (targets.json, actions.json) holds
- ``manual``: items added by hand (edited in place, deletable), and
- ``llm_runs``: one entry per LLM call with the call's metadata and its extractions. Each extraction keeps
  the LLM's version (``base``); user changes go into ``edit`` and ``disabled``, so they can be restored.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import ConfigDict, Field, model_validator

from .claims import ClaimResult
from .common import LlmCall, Model
from .refs import Ref, SkippedSentence  # noqa: F401  (re-exported)

# ---------- references into extractions ----------


class Referenced(Model):
    """Something that must say where in the paper it comes from, or explicitly that it doesn't."""

    refs: list[Ref] = Field(default_factory=list)
    unreferenced: bool = Field(default=False, description="set to allow an empty refs list; then note is required")
    note: str | None = None

    @model_validator(mode="after")
    def _refs_or_note(self):
        if self.unreferenced:
            if self.refs:
                raise ValueError("unreferenced items must not have refs")
            if not (self.note or "").strip():
                raise ValueError("unreferenced items need a note explaining where they come from")
        elif not self.refs:
            raise ValueError("refs is empty; reference the text, or set unreferenced with a note")
        return self


# ---------- targets ----------

TargetType = Literal["number", "categorical", "table", "figure"]
Comparator = Literal["=", "<", "<=", ">", ">=", "~"]


class TargetFields(Referenced):
    """A reported result the reproduction must recompute (target without id)."""

    type: TargetType
    description: str = Field(min_length=1, description="self-contained: variable, model/test, subgroup")
    quote: str = Field(default="", description="verbatim text from the paper that states the result")
    expected: float | int | str | list[list[str]] | None = Field(default=None, description="number for number, string for categorical, rows of cell strings for table, null for figure")
    comparator: Comparator | None = Field(default=None, description='for numbers: how the paper states it ("p < .001" -> "<"); null means "="')
    decimals: int | None = Field(default=None, ge=0, description="decimals as printed")
    unit: str | None = None

    @model_validator(mode="after")
    def _expected_matches_type(self):
        e, t = self.expected, self.type
        if t == "number" and (not isinstance(e, (int, float)) or isinstance(e, bool)):
            raise ValueError("a number target needs a numeric expected value")
        if t == "categorical" and not isinstance(e, str):
            raise ValueError("a categorical target needs a string expected value")
        if t == "table" and not (isinstance(e, list) and all(isinstance(r, list) for r in e)):
            raise ValueError("a table target needs expected as a list of rows")
        if t == "figure" and e is not None:
            raise ValueError("a figure target has no expected value")
        if t != "number" and (self.comparator or self.decimals is not None):
            raise ValueError("comparator and decimals only apply to number targets")
        return self


class Target(TargetFields):
    id: str = Field(pattern=r"^T\d{3,}$")


# ---------- actions (the former analysis-plan steps) ----------


class ActionFields(Referenced):
    """One step of the analysis described in the paper (action without id)."""

    kind: Literal["transform", "analysis"]
    summary: str = Field(min_length=1, description="imperative description, specific enough to code")
    quote: str = Field(default="", description="verbatim text from the paper this action implements")
    inputs: list[str] = Field(default_factory=list, description="input resource names (in-…) used directly")
    depends_on: list[str] = Field(default_factory=list, description="ids of actions that must run first")
    produces: list[str] = Field(default_factory=list, description="ids of targets this action computes")


class Action(ActionFields):
    id: str = Field(pattern=r"^A\d{2,}$")


# ---------- claims (broad claim → sub-claims → evidence + method) ----------

ClaimMode = Literal["quote", "paraphrase"]


class SubClaim(Referenced):
    """One narrower, testable piece of a broad claim."""

    id: str = Field(pattern=r"^C\d+\.\d+$")
    text: str = Field(min_length=1)
    method: str | None = Field(default=None, description="id of the action that implements the test")
    evidence: list[str] = Field(default_factory=list, description="ids of targets that report the result")


class ClaimFields(Referenced):
    """A claim of the paper (claim without id)."""

    text: str = Field(min_length=1)
    mode: ClaimMode = "quote"
    original_quote: str = Field(default="", description="verbatim text of the paper; required when mode is paraphrase")
    testable: bool = True
    method: str | None = Field(default=None, description="id of the action that implements the test (atomic claims)")
    evidence: list[str] = Field(default_factory=list, description="ids of targets that report the result (atomic claims)")
    sub_claims: list[SubClaim] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list, description="ids of claims this one builds on")
    traced_under: list[str] = Field(default_factory=list, description="ids of claims this one was traced under")
    checked: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _rules(self):
        if self.mode == "paraphrase" and not self.original_quote.strip():
            raise ValueError("a paraphrased claim needs original_quote")
        if self.sub_claims and (self.method is not None or self.evidence):
            raise ValueError("a claim with sub-claims has no method or evidence of its own; they belong to the sub-claims")
        if not self.testable and (self.method is not None or self.evidence or self.sub_claims):
            raise ValueError("an untestable claim has no method, evidence or sub-claims")
        return self


class Claim(ClaimFields):
    id: str = Field(pattern=r"^C\d+$")

    @model_validator(mode="after")
    def _sub_ids(self):
        for s in self.sub_claims:
            if not s.id.startswith(f"{self.id}."):
                raise ValueError(f"sub-claim {s.id} must start with {self.id}.")
        return self


class ClaimRunRecord(Model):
    """When a claim's breakdown (evidence and method) was last generated, and for which text."""

    job: str
    at: datetime
    content_hash: str
    result: ClaimResult | None = Field(default=None, description="the claim's result in that run, kept when the claim is later unticked or edited")


# ---------- generic curated collection ----------

T = TypeVar("T", Target, Action, Claim)


class LlmEntry(Model, Generic[T]):
    base: T = Field(description="the item as extracted from the LLM output (never changed)")
    edit: dict[str, Any] | None = Field(default=None, description="user changes: fields of the item that override base")
    disabled: bool = False


class Rejected(Model):
    item: Any = Field(description="the LLM's item as returned")
    errors: list[str]


class LlmRun(Model, Generic[T]):
    id: str = Field(pattern=r"^run\d+$")
    disabled: bool = Field(default=False, description="true: none of the run's entries are effective")
    request: dict[str, Any] = Field(description="what was asked: extraction, scope, …")
    call: LlmCall
    entries: list[LlmEntry[T]]
    rejected: list[Rejected] = Field(default_factory=list, description="items of the output that failed validation")


class TargetsFile(Model):
    """targets.json"""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["targets"] = "targets"
    manual: list[Target] = Field(default_factory=list)
    llm_runs: list[LlmRun[Target]] = Field(default_factory=list)


class ActionsFile(Model):
    """actions.json"""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["actions"] = "actions"
    manual: list[Action] = Field(default_factory=list)
    llm_runs: list[LlmRun[Action]] = Field(default_factory=list)


class ClaimsFile(Model):
    """claims.json"""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["claims"] = "claims"
    manual: list[Claim] = Field(default_factory=list)
    llm_runs: list[LlmRun[Claim]] = Field(default_factory=list)
    skipped: dict[str, list[SkippedSentence]] = Field(default_factory=dict, description="sentences passed over, keyed by run id")
    ran: dict[str, ClaimRunRecord] = Field(default_factory=dict, description="breakdown runs, keyed by claim id")


# ---------- what the LLM is asked to return ----------


class _LlmItem(Model):
    model_config = ConfigDict(extra="ignore")  # tolerate extra keys from the model


class TargetLlmItem(_LlmItem):
    type: TargetType
    description: str
    quote: str = ""
    expected: float | int | str | list[list[str]] | None = None
    comparator: Comparator | None = None
    decimals: int | None = None
    unit: str | None = None
    refs: list[str] = Field(min_length=1, description="ids in brackets the result comes from")


class SubClaimLlmItem(_LlmItem):
    text: str
    refs: list[str] = Field(min_length=1)
    method: str | None = Field(default=None, description="key of a method")
    evidence: list[TargetLlmItem] = Field(default_factory=list)


class ClaimLlmItem(_LlmItem):
    text: str
    mode: ClaimMode = "quote"
    original_quote: str = ""
    refs: list[str] = Field(min_length=1)
    testable: bool = True
    method: str | None = Field(default=None, description="key of a method")
    evidence: list[TargetLlmItem] = Field(default_factory=list)
    sub_claims: list[SubClaimLlmItem] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list, description="1-based positions in the output's claims list")
    traced_under: list[int] = Field(default_factory=list, description="1-based positions in the output's claims list")


class TargetLlmOutput(_LlmItem):
    """Output format of the target-extraction prompt."""

    targets: list[TargetLlmItem]


class ActionLlmItem(_LlmItem):
    key: str = Field(description="A1, A2, … in order; used by depends_on")
    kind: Literal["transform", "analysis"]
    summary: str
    quote: str = ""
    refs: list[str] = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list, description="input names (in-…) used directly")
    depends_on: list[str] = Field(default_factory=list, description="keys of earlier actions")
    produces: list[str] = Field(default_factory=list, description="target ids computed by this action")


class MethodLlmItem(_LlmItem):
    key: str = Field(pattern=r"^M\d+$", description="M1, M2, … ; claims and sub-claims name it in method")
    summary: str
    quote: str = ""
    refs: list[str] = Field(min_length=1)


class PrepLlmItem(_LlmItem):
    key: str = Field(pattern=r"^P\d+$", description="P1, P2, …")
    summary: str
    quote: str = ""
    refs: list[str] = Field(min_length=1)


class SkippedLlmItem(_LlmItem):
    ref: str
    reason: Literal["background", "study_design", "other"]


class ClaimLlmOutput(_LlmItem):
    """Output format of the claims-extraction prompt."""

    claims: list[ClaimLlmItem]
    methods: list[MethodLlmItem] = Field(default_factory=list)
    preparation: list[PrepLlmItem] = Field(default_factory=list)
    skipped: list[SkippedLlmItem] = Field(default_factory=list)


class ActionLlmOutput(_LlmItem):
    """Output format of the action-extraction prompt."""

    actions: list[ActionLlmItem]
