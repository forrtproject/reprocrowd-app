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


def page_texts(path: Path) -> list[str]:
    """The text layer of each page (PDFium, through pypdfium2), with plain newlines."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for page in pdf:
            tp = page.get_textpage()
            out.append(tp.get_text_bounded().replace("\r\n", "\n"))
            tp.close()
            page.close()
        return out
    finally:
        pdf.close()


def parse(path: Path) -> DocumentDraft:
    """Extract the text of each page into paragraphs, sentence-segmented. No sections, tables or figures: PDFs
    carry no structure, so this is a plain-text first pass. Headers, footers and page numbers are kept."""
    paragraphs = [p for text in page_texts(path) for block in re.split(r"\n\s*\n", text) if (p := paragraph_from_text(clean(block)))]
    return DocumentDraft(title=None, sections=[DraftSection(title="", level=1, paragraphs=paragraphs)] if paragraphs else [])


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
