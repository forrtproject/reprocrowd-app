"""DOCX (Word): headings become sections, body paragraphs become paragraphs. Sentences are segmented; tables and
figures are not extracted (a follow-up)."""
from __future__ import annotations

import re
from pathlib import Path

from ..schemas.document import DocumentDraft, DraftSection
from .base import CheckReport, Format
from .builder import paragraph_from_text
from .segment import clean

_LEVEL = re.compile(r"(\d+)")


def _open(path: Path):
    import docx

    return docx.Document(str(path))


def check(path: Path) -> CheckReport:
    r = CheckReport()
    try:
        d = _open(path)
    except Exception as e:
        r.problems.append(f"not a readable .docx: {e}")
        return r
    if not any(p.text.strip() for p in d.paragraphs):
        r.problems.append("document has no text")
    return r


def parse(path: Path) -> DocumentDraft:
    d = _open(path)
    sections: list[DraftSection] = []
    current: DraftSection | None = None
    title: str | None = None
    for para in d.paragraphs:
        text = clean(para.text)
        if not text:
            continue
        style = (para.style.name if para.style else "").lower()
        if style.startswith("heading") or style.startswith("title"):
            level = 1
            m = _LEVEL.search(style)
            if m:
                level = int(m.group(1))
            # Only Word's Title style names the paper: a first "Heading 1" is a section (e.g. Introduction).
            if style.startswith("title"):
                title = title or text
                continue
            sections.append(DraftSection(title=text, level=level))
            current = sections[-1]
            continue
        p = paragraph_from_text(text)
        if p is None:
            continue
        if current is None:
            sections.append(DraftSection(title="", level=1))
            current = sections[-1]
        current.paragraphs.append(p)
    return DocumentDraft(title=title, sections=sections)


FORMAT = Format(
    name="docx",
    description="DOCX (Word)",
    extension=".docx",
    frictionless_format="docx",
    mediatype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    resource_type="file",
    sniff=lambda head: head[:4] == b"PK\x03\x04" and b"word/document.xml" in head,
    check=check,
    parse=parse,
)
