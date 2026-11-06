"""What a full-text format module provides. One module per format, registered in formats/__init__.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from ..schemas.common import Box
from ..schemas.document import DocumentDraft


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)  # any problem = the file is rejected
    notes: list[str] = field(default_factory=list)  # informational, stored with the resource

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Located:
    """A figure or table of a full text whose coordinates really mark it. Formats also carry coordinates for things
    that aren't one (GROBID tags paragraphs that quote a caption as figures, Docling boxes logos as pictures)."""

    kind: Literal["figure", "table"]
    caption: str
    boxes: list[Box]


@dataclass(frozen=True)
class Graphic:
    """A figure of a full text that names its one published image file."""

    label: str
    caption: str
    file: str


@dataclass(frozen=True)
class Format:
    name: str  # also the "flavor" stored in the manifest
    description: str
    extension: str  # used when naming stored files, e.g. ".tei.xml"
    frictionless_format: str  # Data Package "format": pdf, xml, json
    mediatype: str
    resource_type: Literal["file", "json"]
    sniff: Callable[[bytes], bool]  # cheap test on the first 64 KiB
    check: Callable[[Path], CheckReport]  # integrity check of the whole file
    parse: Callable[[Path], DocumentDraft] | None = None  # None: not convertible to the document schema
    locate: Callable[[Path], list[Located]] | None = None  # None: the format has no coordinates
    made_from: Callable[[Path, Path], bool | None] | None = None  # (full text, PDF): its recorded hash matches? None: no hash
    graphics: Callable[[Path], list[Graphic]] | None = None  # None: the format doesn't name image files
    pmcid: Callable[[Path], str | None] | None = None  # None: the format doesn't record it
