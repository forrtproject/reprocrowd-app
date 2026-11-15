"""Files the script step writes for the code-generation agent, and the format the agent must produce."""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

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


class LicenseEntry(Model):
    # The JSON Schema states the same rule as _rights_match_license, for schema-only consumers.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"allOf": [
        {"if": {"properties": {"rights": {"const": "third-party"}}}, "then": {"properties": {"license": {"type": "null"}}}},
        {"if": {"properties": {"rights": {"const": "ours"}}}, "then": {"properties": {"license": {"type": "string"}}, "required": ["license"]}},
    ]})

    path: str = Field(description="archive-relative path")
    kind: str = Field(description="resource kind (fulltext, input, script, output, report, …)")
    rights: Literal["ours", "third-party"] = Field(description="ours: authored for this reproduction; third-party: the article's or the source's")
    license: str | None = Field(default=None, description="SPDX id of the licence we grant; null for third-party material (see source_license)")
    source_license: str | None = Field(default=None, description="third-party: the licence reported by the source (e.g. OpenAlex for the article); null = unknown")
    source: str | None = Field(default=None, description="third-party: where it came from (DOI, URL or repository id)")
    note: str = ""

    @model_validator(mode="after")
    def _rights_match_license(self):
        if (self.rights == "third-party") != (self.license is None):
            raise ValueError(f"{self.path}: third-party files have no licence of ours (license null); our own files need one")
        return self


class LicenseMap(Model):
    """licenses.json (written with the archive): which licence applies to which file. Our licence covers only our own
    contribution; third-party articles, data and code keep their original terms."""

    policy: Literal["reprocrowd-licensing/1"] = Field(description="version of the licence policy")
    rules: dict[str, str] = Field(description="the policy in words, by category")
    paper_license: str | None = Field(default=None, description="the article's licence as reported by OpenAlex; null = unknown")
    files: list[LicenseEntry]
