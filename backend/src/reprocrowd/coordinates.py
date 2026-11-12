"""Which full texts locate the paper's figures and tables on the pages of which PDF.

Read from the full-text files (formats' ``locate``), not from extractions: those don't say whether a box marks a real
figure, and a reconciled extraction has no boxes at all. Coordinates only mean something on the PDF they were measured
on, so each source is tied to one: by the hash GROBID and Docling record, else by the conversion that made it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from . import formats
from .formats.base import Located
from .package import Package
from .schemas.manifest import Manifest, Resource

PdfMatch = Literal["verified", "provenance", "mismatch", "unknown"]
PROBES = (None, 120, 80)  # how much of a caption to look for: all of it, then its start
MIN_PROBE = 15  # shorter captions match too much
_LABEL = re.compile(r"^\s*(fig(ure)?|table)\.?\s*s?\d+[a-z]?\s*[.:]?\s*", re.I)


@dataclass
class Source:
    """A full text with a format that can carry coordinates."""

    fulltext: Resource
    located: list[Located]
    pdf: str | None
    pdf_match: PdfMatch


def pdfs(m: Manifest) -> list[Resource]:
    return [r for r in m.by_kind("fulltext") if r.reprocrowd.flavor == "pdf"]


def _pdf_of(pkg: Package, m: Manifest, ft: Resource) -> tuple[str | None, PdfMatch]:
    fmt = formats.BY_NAME[ft.reprocrowd.flavor]
    answers = {r.name: fmt.made_from(pkg.path(ft.path), pkg.path(r.path)) for r in pdfs(m)} if fmt.made_from else {}
    verified = next((name for name, same in answers.items() if same), None)
    if verified:
        return verified, "verified"
    origin = ft.reprocrowd.origin
    if origin.type == "conversion" and m.get(origin.source):
        return origin.source, "provenance"  # our own record; a hash that disagrees would be a change of hash scheme
    return None, "mismatch" if answers and all(same is False for same in answers.values()) else "unknown"


def sources(pkg: Package) -> list[Source]:
    """Most reliable format first: Docling found all the figures and nearly all the tables of the papers tried, GROBID
    merged neighbouring figures and missed most tables."""
    m = pkg.load()
    out = []
    for ft in sorted(m.by_kind("fulltext"), key=lambda r: r.reprocrowd.flavor != "docling"):
        locate = formats.BY_NAME[ft.reprocrowd.flavor].locate
        if locate:
            out.append(Source(ft, locate(pkg.path(ft.path)), *_pdf_of(pkg, m, ft)))
    return out


def report(pkg: Package) -> list[dict]:
    """For each full text: can it carry coordinates, what do they locate, and on which PDF. Absence is only conclusive
    for formats without coordinates; a format that has them can still miss an item."""
    found = {s.fulltext.name: s for s in sources(pkg)}
    out = []
    for ft in pkg.load().by_kind("fulltext"):
        if ft.reprocrowd.flavor == "pdf":
            continue
        s = found.get(ft.name)
        count = lambda kind: sum(i.kind == kind for i in s.located) if s else 0
        out.append({"fulltext": ft.name, "format": ft.reprocrowd.flavor, "has_coordinates": s is not None,
                    "figures": count("figure"), "tables": count("table"), "pdf": s.pdf if s else None, "pdf_match": s.pdf_match if s else None})
    return out


def normalize(text: str) -> str:
    """Letters and digits only: line breaks, hyphenation marks and typography differ between PDF text and extractions."""
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", text).lower())


def find_caption(caption: str, places: list[str]) -> int | None:
    """Index of the one normalized text containing the caption. Near-identical captions can share a long start, so look
    for all of it first, and give up rather than guess when a probe matches several."""
    text = normalize(_LABEL.sub("", caption))  # labels are written differently ("Fig 1", "Figure 1")
    for size in PROBES:
        probe = text[:size]
        if len(probe) < MIN_PROBE:
            return None
        hits = [i for i, place in enumerate(places) if probe in place]
        if hits:
            return hits[0] if len(hits) == 1 else None
    return None
