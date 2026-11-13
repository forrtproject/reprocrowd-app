"""PDF: integrity check, and a plain-text extraction for local files. A PDF has no structure of its own, so the
direct parse is lossy (no sections, tables or coordinates); the structured routes are GROBID / docling conversion
and the reconcile LLM call."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..schemas.document import DocumentDraft, DraftSection
from .base import CheckReport, Format
from .builder import paragraph_from_text
from .segment import clean

logging.getLogger("pypdf").setLevel(logging.ERROR)


def check(path: Path) -> CheckReport:
    r = CheckReport()
    with path.open("rb") as f:
        if b"%PDF-" not in f.read(1024):
            r.problems.append("no %PDF- header")
            return r
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            r.problems.append("PDF is encrypted")
            return r
        n = len(reader.pages)
        if n == 0:
            r.problems.append("PDF has no pages")
            return r
        r.notes.append(f"{n} page(s)")
        if not (reader.pages[0].extract_text() or "").strip():
            r.notes.append("no text layer on page 1 (scanned?)")
    except (PdfReadError, ValueError, KeyError, OSError) as e:
        r.problems.append(f"PDF cannot be read: {e}")
    return r


def parse(path: Path) -> DocumentDraft:
    """Extract the text of each page (PyMuPDF) into paragraphs, sentence-segmented. No sections, tables or figures:
    PDFs carry no structure, so this is a plain-text first pass. Headers, footers and page numbers are kept."""
    import pymupdf

    sections: list[DraftSection] = []
    current: DraftSection | None = None
    with pymupdf.open(str(path)) as doc:
        for page in doc:
            text = page.get_text("text")
            for block in re.split(r"\n\s*\n", text):
                p = paragraph_from_text(clean(block))
                if p is None:
                    continue
                if current is None:
                    sections.append(DraftSection(title="", level=1))
                    current = sections[-1]
                current.paragraphs.append(p)
    return DocumentDraft(title=None, sections=sections)


FORMAT = Format(
    name="pdf",
    description="PDF",
    extension=".pdf",
    frictionless_format="pdf",
    mediatype="application/pdf",
    resource_type="file",
    sniff=lambda head: b"%PDF-" in head[:1024],
    check=check,
    parse=parse,
)
