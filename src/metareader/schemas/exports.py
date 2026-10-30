"""Files the script step writes for the code-generation agent, and the format the agent must produce."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import Identifier, Model
from .curation import Ref, Target


class Paper(Model):
    doi: str | None = Field(default=None, description="the paper's DOI, if known (the package database requires it)")
    title: str | None = None
    identifier: Identifier | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)


class ResultsSchema(Model):
    """results_schema.json: the enabled targets at the time the master script was created. The list is called
    "results", as in the package database's format."""

    schema_version: Literal["1.0"] = "1.0"
    paper: Paper
    results: list[Target] = Field(description="the targets")


class PlanStep(Model):
    id: str = Field(description="L01… load steps, A… actions, OUT the results step")
    kind: Literal["load", "transform", "analysis", "results"]
    summary: str
    quote: str = ""
    refs: list[Ref] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list, description="package-relative paths of input files")
    depends_on: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list, description="target ids")


class AnalysisPlan(Model):
    """analysis_plan.json: load steps (one per used data input), the enabled actions, the results step."""

    schema_version: Literal["1.0"] = "1.0"
    steps: list[PlanStep]


class OutputResult(Model):
    id: str = Field(description="target id")
    value: float | int | str | None = Field(default=None, description="number/categorical targets; unrounded")
    table: list[list[str | float | int | None]] | None = Field(default=None, description="table targets; same layout as expected")
    file: str | None = Field(default=None, description="figure targets: package-relative image path")
    step: str | None = Field(default=None, description="plan step that produced it")
    note: str | None = None


class ReproducedOutput(Model):
    """outputs/results.json, written by the reproduction code."""

    results: list[OutputResult]


class InputEntry(Model):
    path: str
    role: str
    note: str = ""
    used: bool = True
    archive: str | None = Field(default=None, description="path of the zip archive it was unpacked from")


class InputsList(Model):
    """inputs.json: the input files (also in datapackage.json), in the package database's format."""

    inputs: list[InputEntry]
