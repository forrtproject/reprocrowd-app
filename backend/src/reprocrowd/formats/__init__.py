"""Registry of full-text formats. To add a format: write a module with a ``FORMAT`` (see base.py) and list it
in FORMATS. Order matters: the first format whose sniff matches is used."""
from __future__ import annotations

from pathlib import Path

from . import docling, docx, jats, md, pdf, tei
from .base import CheckReport, Format

FORMATS: list[Format] = [pdf.FORMAT, tei.FORMAT, jats.FORMAT, docling.FORMAT, docx.FORMAT, md.FORMAT]
BY_NAME: dict[str, Format] = {f.name: f for f in FORMATS}


def detect(path: Path) -> Format | None:
    with path.open("rb") as f:
        head = f.read(65536)
    return next((fmt for fmt in FORMATS if fmt.sniff(head)), None)


def by_extension(name: str) -> Format | None:
    """The format a file's name declares, by its extension. Used before content sniffing, because text formats
    (Markdown, DOCX) don't sniff reliably from their content. Longest extension first (e.g. .jats.xml before .xml)."""
    lower = name.lower()
    for fmt in sorted(FORMATS, key=lambda f: len(f.extension), reverse=True):
        if lower.endswith(fmt.extension):
            return fmt
    return None


def check(path: Path, filename: str | None = None) -> tuple[Format | None, CheckReport]:
    """Detect the format (by the file's name when it has one, then by content) and run its integrity check."""
    fmt = by_extension(filename) if filename else None
    if fmt is None:
        fmt = detect(path)
    if fmt is None:
        names = ", ".join(f.description for f in FORMATS)
        return None, CheckReport(problems=[f"unrecognized format; accepted: {names}"])
    return fmt, fmt.check(path)


__all__ = ["FORMATS", "BY_NAME", "Format", "CheckReport", "detect", "by_extension", "check"]
