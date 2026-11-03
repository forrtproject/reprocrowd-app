"""Registry of full-text formats. To add a format: write a module with a ``FORMAT`` (see base.py) and list it
in FORMATS. Order matters: the first format whose sniff matches is used."""
from __future__ import annotations

from pathlib import Path

from . import docling, jats, pdf, tei
from .base import CheckReport, Format

FORMATS: list[Format] = [pdf.FORMAT, tei.FORMAT, jats.FORMAT, docling.FORMAT]
BY_NAME: dict[str, Format] = {f.name: f for f in FORMATS}


def detect(path: Path) -> Format | None:
    with path.open("rb") as f:
        head = f.read(65536)
    return next((fmt for fmt in FORMATS if fmt.sniff(head)), None)


def check(path: Path) -> tuple[Format | None, CheckReport]:
    """Detect the format and run its integrity check."""
    fmt = detect(path)
    if fmt is None:
        names = ", ".join(f.description for f in FORMATS)
        return None, CheckReport(problems=[f"unrecognized format; accepted: {names}"])
    return fmt, fmt.check(path)


__all__ = ["FORMATS", "BY_NAME", "Format", "CheckReport", "detect", "check"]
