"""Request and response bodies of the HTTP API (stored-file schemas are in reprocrowd.schemas)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, create_model

from ..schemas.common import Identifier, Model
from ..schemas.curation import Action, ActionFields, Target, TargetFields
from ..schemas.jobs import Job, TodoStatus
from ..schemas.manifest import Resource, Role


class Req(Model):
    """Base of request bodies: unknown fields are rejected."""


# ---------- service ----------


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class LlmProfileView(BaseModel):
    name: str
    default: bool
    argv: list[str] = Field(description="read-only: configured in the service, never set by callers")
    model: str
    effort: str
    timeout_s: int


class PromptPreview(BaseModel):
    """What a call would send. Nothing was run or stored."""

    prompt: str = Field(description="the full prompt, as it would go to the command's stdin")
    argv: list[str] = Field(description="the exact command line")
    profile: str = Field(description="the profile used: the requested one, or the default")
    model: str
    effort: str
    output_schema: str | None = Field(default=None, description="JSON Schema the CLI is told to constrain its output to, if the profile supports that")


class RunningJob(BaseModel):
    """A job running now, and the package it runs in."""

    package: str = Field(description="package id")
    job: Job


class TemplateView(BaseModel):
    id: str
    label: str
    language: str
    command: str
    master: str


# ---------- packages ----------


class PackageCreate(Req):
    path: str = Field(description="folder on the service's machine; created if missing, must be empty")
    identifier: str | None = Field(default=None, description="DOI, OpenAlex id (W…), pmid:…, or pmcid:…")
    fetch_metadata: bool = Field(default=False, description="query OpenAlex with the identifier right away")
    adopt: bool = Field(default=False, description="register a folder that already contains a package")


class PackageSummary(BaseModel):
    id: str
    path: str
    title: str | None
    identifier: Identifier | None
    identifiers: dict[str, str]
    metadata_fetched_at: datetime | None
    resources: dict[str, int] = Field(description="number of resources per kind")


class MetadataRequest(Req):
    identifier: str | None = Field(default=None, description="defaults to the package's identifier")


class MetadataSummary(BaseModel):
    title: str | None
    publication_year: int | None
    authors: list[str | None]
    venue: str | None
    identifiers: dict[str, str]
    open_access: bool | None


# ---------- full texts and extractions ----------


class FulltextFromPath(Req):
    path: str = Field(description="local file (pdf, TEI/JATS XML, Docling JSON) on the service's machine")
    name: str | None = Field(default=None, description="resource name; default ft-<file stem>-<format>")


class FulltextFromCandidate(Req):
    candidate: str = Field(description="id from GET …/fulltexts/candidates")
    name: str | None = None


class FulltextFromConversion(Req):
    source: str = Field(description="PDF full text to convert")
    converter: str = Field(description="converter name from GET /converters (grobid → TEI, docling → Docling JSON)")
    name: str | None = None


FulltextCreate = Union[FulltextFromPath, FulltextFromCandidate, FulltextFromConversion]


class ConverterView(BaseModel):
    name: str
    configured: bool
    output: str
    detail: str


class CandidateView(BaseModel):
    id: str
    label: str
    url: str
    format: str
    filename: str
    openalex_auth: bool


class CandidateList(BaseModel):
    candidates: list[CandidateView]
    notes: list[str]


class ExtractionParse(Req):
    method: Literal["parse"]
    fulltext: str = Field(description="full-text resource (tei, jats or docling)")
    name: str | None = Field(default=None, description="ignored by PUT (the name is in the path)")


class ExtractionReconcile(Req):
    method: Literal["reconcile"]
    sources: list[str] = Field(min_length=1, description="full-text resources the LLM reads")
    prompt: str | None = Field(default=None, description="custom prompt template; placeholders $files, $schema, $instructions. Omit for the default")
    instructions: str = Field(default="", description="extra instructions added to the prompt")
    profile: str | None = Field(default=None, description="LLM profile name; default from the service config")
    name: str | None = None


ExtractionCreate = Annotated[Union[ExtractionParse, ExtractionReconcile], Field(discriminator="method")]


# ---------- inputs ----------


class InputCreate(Req):
    path: str = Field(description="local file on the service's machine; zip archives are unpacked")
    role: Role | None = Field(default=None, description="default: guessed from the file extension")
    note: str = ""
    unpack: bool = Field(default=True, description="unpack zip archives into inputs/<stem>/")


class FulltextUploadForm(Req):
    """multipart/form-data fields of an upload (the file is the `file` part)."""

    name: str | None = None


class InputUploadForm(Req):
    """multipart/form-data fields of an upload (the file is the `file` part)."""

    role: Role | None = None
    note: str = ""
    unpack: bool = True


class InputPatch(Req):
    """JSON merge patch (RFC 7396)."""

    role: Role | None = None
    note: str | None = None
    used: bool | None = None


# ---------- targets and actions ----------


class Origin(BaseModel):
    source: Literal["manual", "llm"]
    run: str | None = None
    edited: bool | None = Field(default=None, description="LLM entries: the user changed fields")
    disabled: bool | None = None


class TargetView(BaseModel):
    item: Target
    origin: Origin


class ActionView(BaseModel):
    item: Action
    origin: Origin


class ScopeModel(Req):
    sections: list[str] = Field(default_factory=list, description="section ids (all their sentences, figures and tables)")
    items: list[str] = Field(default_factory=list, description="single sentence, figure or table ids")


class RunCreate(Req):
    extraction: str = Field(description="extraction whose ids the items will reference")
    scope: ScopeModel = Field(default_factory=ScopeModel, description="empty = the whole document")
    instructions: str = ""
    prompt: str | None = Field(default=None, description="custom prompt template; placeholders $document, $schema, $instructions (actions also $inputs, $targets)")
    profile: str | None = None


class RunPatch(Req):
    disabled: bool


def _patch_model(name: str, fields: type[BaseModel]) -> type[BaseModel]:
    """All fields of the item optional, plus ``disabled``: a JSON merge patch body."""
    defs: dict[str, Any] = {k: (Optional[f.annotation], None) for k, f in fields.model_fields.items()}
    defs["disabled"] = (Optional[bool], Field(default=None, description="LLM entries only: exclude from the effective list"))
    return create_model(name, __config__=ConfigDict(extra="forbid"), __doc__="JSON merge patch (RFC 7396) of the item's fields.", **defs)


TargetPatch = _patch_model("TargetPatch", TargetFields)
ActionPatch = _patch_model("ActionPatch", ActionFields)


# ---------- scripts ----------


class ScriptRequest(Req):
    template: str = Field(description="template id from GET /templates")
    on_modified: Literal["refuse", "keep", "overwrite"] = Field(
        default="refuse", description="generated files edited since (e.g. implemented by the agent): refuse (409), keep them and regenerate "
                                      "the rest (e.g. exports after targets changed), or overwrite them")


class ScriptResult(BaseModel):
    created: bool
    template: str
    command: str
    master: str
    files: list[str] = Field(description="files written")
    kept: list[str] = Field(description="edited files left as they are (on_modified=keep)")


class ScriptState(BaseModel):
    template: str | None = Field(description="null: no master script yet")
    command: str | None
    master: str | None
    agent_prompt: str | None = Field(description="path of the suggested prompt for a coding agent")
    todo: TodoStatus | None = Field(description="actionable TODO(LLM): markers left (the explanatory header line is not counted)")
    files: list[Resource]


# ---------- jobs and files ----------


class AgentProfileView(BaseModel):
    name: str
    default: bool
    argv: list[str] = Field(description="read-only: configured in the service; {tools} is filled from tools (+ exec_tools)")
    tools: list[str]
    exec_tools: list[str]
    model: str
    effort: str
    timeout_s: int
    events: str


class CodegenJobCreate(Req):
    type: Literal["codegen"]
    profile: str | None = Field(default=None, description="agent profile; default from the service config")
    allow_exec: bool = Field(default=True, description="let the agent run the analysis (the profile's exec_tools)")
    instructions: str = Field(default="", description="added to the agent prompt")


class RunJobCreate(Req):
    """Run the master script with the template's command."""

    type: Literal["run"]


JobCreate = Annotated[Union[CodegenJobCreate, RunJobCreate], Field(discriminator="type")]


class FileEntry(BaseModel):
    path: str
    size: int
    modified: float = Field(description="Unix time")
    resource: str | None = Field(description="resource name; null = not in the manifest yet (see :sync)")
    kind: str | None


class SyncReport(BaseModel):
    added: list[str]
    updated: list[str]
    removed: list[str]


# ---------- report and archive ----------


class ReportRequest(Req):
    rounding: Literal["reported"] | int = Field(default="reported", description='"reported" or a number of decimals')
    threshold: float = Field(default=5, ge=0, description="% deviation counted as within tolerance")


class ReportResult(BaseModel):
    path: str
    comparison: str
    summary: dict[str, int]
    reproduced: str = Field(description="non-figure targets that match or are within tolerance, e.g. 12/20")


# ---------- package database ----------


class DatabaseStatus(BaseModel):
    url: str
    configured: bool
    reachable: bool
    error: str | None = None


class SubmissionCreate(Req):
    note: str = Field(default="", max_length=4000, description="shown with the package in the database")
    database_token: SecretStr | None = Field(
        default=None, description="the user's token for the package database (from its /auth/client sign-in). With it the package is "
                                  "published at once under that user; without it the user confirms at finish_url. Passed on, never stored")


# ---------- figure crops ----------


class CropRequest(Req):
    pdf: str | None = Field(default=None, description="PDF full text to render from, skipping the published image; default: the one the coordinates were measured on, by hash or provenance")
    scale: float = Field(default=2.0, gt=0, le=8, description="pixels per point")


class CropResult(BaseModel):
    resource: Resource
    target: str
    figure: str = Field(description="extraction and figure id")
    pdf: str | None = Field(default=None, description="PDF the image was rendered from; none for a published image")
    page: int | None = None
    size: list[int] = Field(description="width, height in pixels")
    method: Literal["published", "crop", "page"] = Field(description="published: the image file of the article · crop: the figure's area on the PDF · "
                                                                      "page: the whole page, as no full text locates the figure")
    source: str | None = Field(default=None, description="crop: the full text with the coordinates · published: the URL of the image")
    note: str | None = Field(default=None, description="why a better kind of original wasn't available, if something went wrong")


class FigureOriginal(BaseModel):
    target: str
    path: str
    method: Literal["published", "crop", "page"]
    page: int | None = None
    source: str | None = Field(default=None, description="published: the URL of the image")


class CoordinateReport(BaseModel):
    """What one full text says about where things are on the page. Figures and tables count only when the coordinates
    really mark one: formats also box running text and logos. `has_coordinates: false` is conclusive; few located
    items in a format that has coordinates only mean that it missed them."""

    fulltext: str
    format: str
    has_coordinates: bool
    figures: int
    tables: int
    pdf: str | None = Field(default=None, description="PDF the coordinates refer to")
    pdf_match: Literal["verified", "provenance", "mismatch", "unknown"] | None = Field(
        default=None, description="verified: the hash recorded in the file matches · provenance: converted from it, hash not confirmed · "
                                  "mismatch: no PDF of the package has the recorded hash · unknown: no hash, no conversion record")


class CropOutcome(BaseModel):
    target: str
    ok: bool
    path: str | None = None
    page: int | None = None
    method: Literal["published", "crop", "page"] | None = None
    note: str | None = None
    error: str | None = None
    code: str | None = None
