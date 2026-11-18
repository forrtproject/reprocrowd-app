"""The package manifest: a Frictionless Data Package (datapackage.json) with ReproCrowd properties.

Standard Data Package properties (name, path, format, mediatype, bytes, hash) stay standard, so
``frictionless validate datapackage.json`` checks file integrity. ReproCrowd-specific properties live under
``reprocrowd`` on the package and on each resource.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated, Literal, Union

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from .common import Identifier, Model

RESOURCE_NAME = r"^[a-z0-9][a-z0-9._-]*$"
#: Line breaks and other control characters, which no package path may contain: paths end up in generated code (a
#: line break in a comment would start a line of code) and in commands.
CONTROL_CHARS = r"\x00-\x1f\x7f\x85\u2028\u2029"
RESOURCE_PATH = rf"^[^{CONTROL_CHARS}]+$"
_CONTROL = re.compile(f"[{CONTROL_CHARS}]")


def unsafe_parts(parts: list[str]) -> bool:
    """Path components that leave the package or misbehave: .., backslashes, control characters, Windows drive prefixes."""
    return any(p == ".." or "\\" in p or _CONTROL.search(p) or PureWindowsPath(p).drive for p in parts)

Flavor = Literal["pdf", "tei", "jats", "docling", "docx", "md"]
Role = Literal["data", "code", "other"]


class LocalOrigin(Model):
    type: Literal["local"] = "local"
    path: str = Field(description="absolute path the file was copied from")


class UrlOrigin(Model):
    type: Literal["url"] = "url"
    url: str = Field(description="download URL (secrets such as API keys removed)")
    candidate: str = Field(description="candidate id it was chosen as")
    label: str = Field(description='where the candidate came from, e.g. "OpenAlex best OA location"')


class ConvertedOrigin(Model):
    type: Literal["conversion"] = "conversion"
    source: str = Field(description="resource name of the PDF it was converted from")
    converter: str = Field(description="converter name (grobid, docling)")
    detail: str = Field(default="", description="e.g. the GROBID URL or the docling command")


class UploadOrigin(Model):
    type: Literal["upload"] = "upload"
    filename: str = Field(description="file name the client sent (multipart upload)")


class FetchedOrigin(Model):
    type: Literal["fetched"] = "fetched"
    url: str = Field(description="download URL (secrets such as API keys removed)")
    repo: str = Field(description="repository type the file came from (zenodo, github, gitlab, osf, figshare, ...)")
    id: str = Field(description="repository-specific identifier (DOI, owner/repo, record id, ...)")
    label: str = Field(description="where the link was found, e.g. 'data availability statement'")


class CheckResult(Model):
    passed: bool
    checked_at: datetime
    notes: list[str] = Field(default_factory=list, description="non-fatal observations (e.g. page count)")


class FulltextMeta(Model):
    kind: Literal["fulltext"] = "fulltext"
    added_at: datetime
    flavor: Flavor
    origin: Annotated[Union[LocalOrigin, UrlOrigin, ConvertedOrigin, UploadOrigin], Field(discriminator="type")]
    check: CheckResult


class ExtractionMeta(Model):
    kind: Literal["extraction"] = "extraction"
    added_at: datetime
    method: Literal["parse", "reconcile"]
    fulltexts: list[str] = Field(description="full-text resources it was made from")
    llm_call: str | None = None


class InputMeta(Model):
    kind: Literal["input"] = "input"
    added_at: datetime
    role: Role
    note: str = ""
    used: bool = Field(default=True, description="false: kept on disk but not part of the reproduction")
    archive: str | None = Field(default=None, description="resource name of the zip archive this file was unpacked from")
    origin: Annotated[Union[LocalOrigin, UploadOrigin, FetchedOrigin], Field(discriminator="type")]


class ScriptMeta(Model):
    """A file written from a master-script template."""

    kind: Literal["script"] = "script"
    added_at: datetime
    template: str = Field(description="template id")
    generated_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", description="hash as generated; a different current hash means the file was edited (e.g. by the agent)")


class OtherMeta(Model):
    """metadata: OpenAlex record · curation: targets/actions · export: files derived for the agent · schema: JSON Schema
    copies · code: files created by the agent or the user · output: files under outputs/ (written by runs) ·
    report: report.html and comparison.json · figure: originals of figure targets (targets/figures/)."""

    kind: Literal["metadata", "curation", "export", "schema", "code", "output", "report", "figure"]
    added_at: datetime
    method: Literal["published", "crop", "page"] | None = Field(default=None, description="figure: the image as published, an exact crop, or the whole page the figure is on")
    page: int | None = Field(default=None, ge=1, description="figure: page of the PDF")
    source: str | None = Field(default=None, description="figure: where a published image was downloaded from")


ResourceMeta = Annotated[Union[FulltextMeta, ExtractionMeta, InputMeta, ScriptMeta, OtherMeta], Field(discriminator="kind")]


class Resource(Model):
    """One file of the package (Frictionless Data Resource)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=RESOURCE_NAME, description="unique id, e.g. ft-grobid, ex-grobid, in-data-csv")
    path: str = Field(pattern=RESOURCE_PATH, description="package-relative, /-separated")
    type: Literal["file", "json"] = Field(description='"json" for JSON files (frictionless parses them), "file" otherwise')
    format: str
    mediatype: str
    bytes: int = Field(ge=0)
    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    jsonSchema: str | None = Field(default=None, description="package-relative path of the JSON Schema the file must satisfy")
    # Packages made before the rename call it "metareader"; they load, and are saved under the new name.
    reprocrowd: ResourceMeta = Field(validation_alias=AliasChoices("reprocrowd", "metareader"))

    @field_validator("path", "jsonSchema")
    @classmethod
    def _package_relative(cls, v: str | None) -> str | None:
        # The plan and the master script take r.path as it is: the same rules as Package.path, and no leading /.
        if v is not None and (v.startswith("/") or unsafe_parts([p for p in v.split("/") if p not in ("", ".")])):
            raise ValueError(f"not a safe package-relative path: {v!r}")
        return v


class ArchiveInfo(Model):
    """The last ZIP of the package (PUT …/archive)."""

    path: str = Field(description="package-relative, under archive/ (not itself a resource)")
    bytes: int
    sha256: str
    created_at: datetime
    manifest_sha256: str = Field(description="hash of the datapackage.json inside the archive")
    files: int


class Submission(Model):
    """An upload of the archive to the package database."""

    id: str = Field(pattern=r"^sub-[0-9a-f]+$")
    database: str = Field(description="database URL at the time")
    upload_id: str
    finish_url: str = Field(description="where the user signs in and confirms the submission")
    status_url: str
    expires: str = Field(description="unconfirmed uploads expire")
    status: Literal["pending", "submitted", "deleted", "gone"] = Field(description="gone: expired or discarded without confirmation")
    submitted_at: datetime
    checked_at: datetime | None = None
    archive_sha256: str
    note: str = ""
    package_url: str | None = Field(default=None, description="the package's page once confirmed")


class ZenodoCreator(Model):
    name: str = Field(min_length=1, description='"Family, Given"')
    orcid: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
    affiliation: str | None = None


class ZenodoDeposit(Model):
    """A deposit of the package on Zenodo: a draft until it is published (then it has its DOI for good)."""

    id: str = Field(pattern=r"^zen-[0-9a-f]+$")
    server: str = Field(description="Zenodo base URL at the time")
    sandbox: bool = Field(description="deposited to Zenodo's test instance")
    deposition_id: int
    status: Literal["draft", "published", "discarded", "gone"] = Field(description="gone: deleted on Zenodo (only drafts can be)")
    title: str
    doi: str | None = Field(default=None, description="reserved while a draft, registered once published")
    url: str = Field(description="the draft's edit page, or the published record")
    file: str = Field(description="name of the uploaded ZIP")
    sha256: str = Field(description="hash of the uploaded ZIP")
    archive_sha256: str = Field(description="hash of the package archive it was made from")
    exclude: list[Literal["paper", "inputs"]] = Field(default_factory=list, description="groups of files left out of the upload")
    excluded: list[str] = Field(default_factory=list, description="the resources that were left out")
    community: str | None = None
    creators: list[ZenodoCreator]
    note: str = ""
    created_at: datetime
    published_at: datetime | None = None
    checked_at: datetime | None = None


class PilotAsset(Model):
    name: str
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str | None = Field(default=None, description="download URL once the release is published")


class GithubPilot(Model):
    """One run of this reproduction submitted as a GitHub pilot: a release with the files in the submitter's fork, a
    comment on the reproduction's issue, and the copy the pilots repository's Action makes. Keyed by run_key (archive
    hash + left-out groups); a retry resumes the same record."""

    id: str = Field(pattern=r"^pil-[0-9a-f]+$")
    repo: str = Field(description="owner/name")
    reproduction_id: str = Field(pattern=r"^repro-[0-9a-f]{10}$")
    run_key: str = Field(pattern=r"^[0-9a-f]{12}$")
    version: int | None = Field(default=None, ge=1, description="run number within the reproduction, assigned by the pilots repository when it copies the run")
    status: Literal["partial", "submitted", "published"] = Field(
        description="partial: a step failed, submitting again resumes; submitted: files and comment are up, the pilots "
                    "repository hasn't copied them yet; published: the copy exists")
    tag: str
    issue_number: int | None = None
    issue_url: str | None = None
    files_repo: str = Field(default="", description="owner/name of the repository holding the submitted files (the submitter's fork)")
    files_url: str | None = Field(default=None, description="the submitted release")
    release_url: str | None = Field(default=None, description="the pilots repository's copy, once made")
    comment_url: str | None = None
    archive_sha256: str = Field(description="hash of the package archive it was made from")
    exclude: list[Literal["paper", "inputs"]] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    assets: list[PilotAsset] = Field(default_factory=list)
    synthetic: bool = False
    note: str = ""
    error: str | None = Field(default=None, description="why the last attempt stopped, or why the pilots repository refused the run")
    created_at: datetime
    published_at: datetime | None = None
    checked_at: datetime | None = Field(default=None, description="when the pilots repository was last asked about this run")


class PackageMeta(Model):
    track: Literal["recreation"] | None = Field(default=None, description="None: older package with unspecified workflow; recreation is not certified blinding")
    version: Literal["1.0"] = "1.0"
    created_at: datetime
    identifier: Identifier | None = Field(default=None, description="identifier the package was created for")
    identifiers: dict[str, str] = Field(default_factory=dict, description="all identifiers known from OpenAlex (doi, openalex, pmid, pmcid)")
    metadata_fetched_at: datetime | None = None
    archive: ArchiveInfo | None = None
    submissions: list[Submission] = Field(default_factory=list)
    deposits: list[ZenodoDeposit] = Field(default_factory=list, description="Zenodo deposits")
    reproduction_id: str | None = Field(default=None, pattern=r"^repro-[0-9a-f]{10}$",
                                        description="identity of this reproduction on pilot destinations (one GitHub issue); set on the first pilot submission")
    pilots: list[GithubPilot] = Field(default_factory=list, description="GitHub pilot submissions, one per run key")


class Manifest(Model):
    """datapackage.json"""

    name: str = Field(pattern=RESOURCE_NAME)
    title: str | None = None
    resources: list[Resource] = Field(default_factory=list)
    reprocrowd: PackageMeta = Field(validation_alias=AliasChoices("reprocrowd", "metareader"))

    @model_validator(mode="after")
    def _unique(self):
        for attr in ("name", "path"):
            vals = [getattr(r, attr) for r in self.resources]
            dup = {v for v in vals if vals.count(v) > 1}
            if dup:
                raise ValueError(f"duplicate resource {attr}: {', '.join(sorted(dup))}")
        return self

    def get(self, name: str) -> Resource | None:
        return next((r for r in self.resources if r.name == name), None)

    def by_kind(self, kind: str) -> list[Resource]:
        return [r for r in self.resources if r.reprocrowd.kind == kind]
