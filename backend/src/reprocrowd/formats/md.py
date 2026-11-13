"""Markdown: a plain-text document format. ATX headings become sections, blank-line-separated paragraphs become
paragraphs, and sentences are segmented (there are no coordinates)."""
from __future__ import annotations

import re
from pathlib import Path

from ..schemas.document import DocumentDraft, DraftSection
from .base import CheckReport, Format
from .builder import paragraph_from_text
from .segment import clean

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# A markdown file has at least one ATX heading, or markdown list/emphasis/link syntax.
_SNIFF = re.compile(rb"(?m)^#{1,6}\s|^\s*[-*+]\s|\*\*|\[[^\]]+\]\([^)]+\)")


def check(path: Path) -> CheckReport:
    r = CheckReport()
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        r.problems.append(f"not readable text: {e}")
        return r
    if not text.strip():
        r.problems.append("file is empty")
    r.notes.append(f"{len(text.splitlines())} line(s)")
    return r


def parse(path: Path) -> DocumentDraft:
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[DraftSection] = []
    current: DraftSection | None = None
    buf: list[str] = []
    title: str | None = None
    seen_body = False

    def flush() -> None:
        nonlocal buf, current
        text = clean(" ".join(buf))
        buf = []
        if not text:
            return
        p = paragraph_from_text(text)
        if p is None:
            return
        if current is None:
            sections.append(DraftSection(title="", level=1))
            current = sections[-1]
        current.paragraphs.append(p)

    for line in lines:
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            heading = clean(m.group(2))
            # The first top-level heading before any body text is the paper's title.
            if level == 1 and title is None and not seen_body:
                title = heading
                current = None
                continue
            sections.append(DraftSection(title=heading, level=level))
            current = sections[-1]
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line.strip())
        seen_body = True
    flush()
    return DocumentDraft(title=title, sections=sections)


FORMAT = Format(
    name="md",
    description="Markdown",
    extension=".md",
    frictionless_format="md",
    mediatype="text/markdown",
    resource_type="file",
    sniff=lambda head: _SNIFF.search(head) is not None,
    check=check,
    parse=parse,
)
