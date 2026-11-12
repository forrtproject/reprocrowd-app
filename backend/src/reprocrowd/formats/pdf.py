"""PDF: integrity check only. PDFs are not parsed directly; they are inputs to reconcile (and, later, to
GROBID / docling conversion)."""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .base import CheckReport, Format

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


FORMAT = Format(
    name="pdf",
    description="PDF",
    extension=".pdf",
    frictionless_format="pdf",
    mediatype="application/pdf",
    resource_type="file",
    sniff=lambda head: b"%PDF-" in head[:1024],
    check=check,
)
