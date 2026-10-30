"""The package manifest: a Frictionless Data Package (datapackage.json) with MetaReader properties.

Standard Data Package properties (name, path, format, mediatype, bytes, hash) stay standard, so
``frictionless validate datapackage.json`` checks file integrity. MetaReader-specific properties live under
``metareader`` on the package and on each resource.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import ConfigDict, Field, model_validator

from .common import Identifier, Model

RESOURCE_NAME = r"^[a-z0-9][a-z0-9._-]*$"

Flavor = Literal["pdf", "tei", "jats", "docling"]
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


class CheckResult(Model):
    passed: bool
    checked_at: datetime
    notes: list[str] = Field(default_factory=list, description="non-fatal observations (e.g. page count)")


class FulltextMeta(Model):
    kind: Literal["fulltext"] = "fulltext"
    added_at: datetime
    flavor: Flavor
    origin: Annotated[Union[LocalOrigin, UrlOrigin, ConvertedOrigin], Field(discriminator="type")]
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
    origin: LocalOrigin


class ScriptMeta(Model):
    """A file written from a master-script template."""

    kind: Literal["script"] = "script"
    added_at: datetime
    template: str = Field(description="template id")
    generated_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$", description="hash as generated; a different current hash means the file was edited (e.g. by the agent)")


class OtherMeta(Model):
    """metadata: OpenAlex record · curation: targets/actions · export: files derived for the agent · schema: JSON Schema
    copies · code: files created by the agent or the user · output: files under outputs/ (written by runs) ·
    report: report.html and comparison.json."""

    kind: Literal["metadata", "curation", "export", "schema", "code", "output", "report"]
    added_at: datetime


ResourceMeta = Annotated[Union[FulltextMeta, ExtractionMeta, InputMeta, ScriptMeta, OtherMeta], Field(discriminator="kind")]


class Resource(Model):
    """One file of the package (Frictionless Data Resource)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=RESOURCE_NAME, description="unique id, e.g. ft-grobid, ex-grobid, in-data-csv")
    path: str = Field(description="package-relative, /-separated")
    type: Literal["file", "json"] = Field(description='"json" for JSON files (frictionless parses them), "file" otherwise')
    format: str
    mediatype: str
    bytes: int = Field(ge=0)
    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    jsonSchema: str | None = Field(default=None, description="package-relative path of the JSON Schema the file must satisfy")
    metareader: ResourceMeta


class ArchiveInfo(Model):
    """The last ZIP of the package (PUT …/archive)."""

    path: str = Field(description="package-relative, under archive/ (not itself a resource)")
    bytes: int
    sha256: str
    created_at: datetime
    manifest_sha256: str = Field(description="hash of the datapackage.json inside the archive")
    files: int


class PackageMeta(Model):
    version: Literal["1.0"] = "1.0"
    created_at: datetime
    identifier: Identifier | None = Field(default=None, description="identifier the package was created for")
    identifiers: dict[str, str] = Field(default_factory=dict, description="all identifiers known from OpenAlex (doi, openalex, pmid, pmcid)")
    metadata_fetched_at: datetime | None = None
    archive: ArchiveInfo | None = None


class Manifest(Model):
    """datapackage.json"""

    name: str = Field(pattern=RESOURCE_NAME)
    title: str | None = None
    resources: list[Resource] = Field(default_factory=list)
    metareader: PackageMeta

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
        return [r for r in self.resources if r.metareader.kind == kind]
