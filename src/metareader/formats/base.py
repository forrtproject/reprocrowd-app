"""What a full-text format module provides. One module per format, registered in formats/__init__.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from ..schemas.document import DocumentDraft


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)  # any problem = the file is rejected
    notes: list[str] = field(default_factory=list)  # informational, stored with the resource

    @property
    def passed(self) -> bool:
        return not self.problems


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
