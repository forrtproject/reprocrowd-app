"""Targets and actions, and the generic structure that keeps LLM extractions apart from user changes.

A curated file (targets.json, actions.json) holds
- ``manual``: items added by hand (edited in place, deletable), and
- ``llm_runs``: one entry per LLM call with the call's metadata and its extractions. Each extraction keeps
  the LLM's version (``base``); user changes go into ``edit`` and ``disabled``, so they can be restored.
- ``last_item`` / ``last_run``: the highest item and run numbers ever issued, so the ids of deleted items and runs
  are never handed out again (stopgap until ids are content-based, see issue #65).
"""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import ConfigDict, Field, field_validator, model_validator

from .common import LlmCall, Model
from .document import ref_kind

# ---------- references into extractions ----------


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


# ---------- generic curated collection ----------

T = TypeVar("T", Target, Action)


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
    last_item: int = Field(0, ge=0, description="highest item number ever issued; ids of deleted items are not reused")
    last_run: int = Field(0, ge=0, description="highest run number ever issued; numbers of deleted runs are not reused")


class ActionsFile(Model):
    """actions.json"""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["actions"] = "actions"
    manual: list[Action] = Field(default_factory=list)
    llm_runs: list[LlmRun[Action]] = Field(default_factory=list)
    last_item: int = Field(0, ge=0, description="highest item number ever issued; ids of deleted items are not reused")
    last_run: int = Field(0, ge=0, description="highest run number ever issued; numbers of deleted runs are not reused")


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


class ActionLlmOutput(_LlmItem):
    """Output format of the action-extraction prompt."""

    actions: list[ActionLlmItem]
