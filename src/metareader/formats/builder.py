"""Turn a DocumentDraft into a Document: the one place where ids are assigned.

Id scheme (readable, and says where an item is):
    sections   sec1, sec2, …              (reading order)
    paragraphs sec2.p1, sec2.p2, …
    sentences  sec2.p1.s1, …              captions: fig1.s1, tab3.s2
    figures    fig1, …   tables tab1, …   (numbered in reading order across the document)
"""
from __future__ import annotations

import re

from ..schemas.common import Box
from ..schemas.document import (
    Document,
    DocumentDraft,
    DocumentSource,
    DraftFigure,
    DraftParagraph,
    DraftSentence,
    DraftTable,
    Figure,
    Paragraph,
    Section,
    Sentence,
    Table,
)
from .segment import clean, split_sentences


# ---------- helpers for parsers ----------


def sentences_from_text(text: str, boxes: list[Box] | None = None) -> list[DraftSentence]:
    return [DraftSentence(text=s, boxes=boxes) for s in split_sentences(text)]


def paragraph_from_text(text: str, boxes: list[Box] | None = None) -> DraftParagraph | None:
    sents = sentences_from_text(text)
    return DraftParagraph(sentences=sents, boxes=boxes) if sents else None


# ---------- draft → document ----------


def _label(text: str) -> str:
    """"Fig 1 ." → "Fig 1" (GROBID leaves trailing punctuation)."""
    return re.sub(r"\s*[.:]\s*$", "", clean(text))


def _sentences(prefix: str, items: list[DraftSentence]) -> list[Sentence]:
    out = []
    for s in items:
        text = clean(s.text)
        if text:
            out.append(Sentence(id=f"{prefix}.s{len(out) + 1}", text=text, boxes=s.boxes))
    return out


def build_document(draft: DocumentDraft, source: DocumentSource) -> Document:
    sections: list[Section] = []
    figures: list[Figure] = []
    tables: list[Table] = []

    def add_float(f: DraftFigure, section: str | None):
        if isinstance(f, DraftTable):
            tid = f"tab{len(tables) + 1}"
            tables.append(Table(id=tid, label=_label(f.label), section=section, caption=_sentences(tid, f.caption),
                                rows=[[clean(c) for c in r] for r in f.rows], boxes=f.boxes))
        else:
            fid = f"fig{len(figures) + 1}"
            figures.append(Figure(id=fid, label=_label(f.label), section=section, caption=_sentences(fid, f.caption), boxes=f.boxes))

    for ds in draft.sections:
        has_content = ds.paragraphs or ds.figures or ds.tables
        if not ds.title.strip() and not has_content:
            continue
        sid = f"sec{len(sections) + 1}"
        paragraphs: list[Paragraph] = []
        for dp in ds.paragraphs:
            pid = f"{sid}.p{len(paragraphs) + 1}"
            sents = _sentences(pid, dp.sentences)
            if sents:
                paragraphs.append(Paragraph(id=pid, sentences=sents, boxes=dp.boxes))
        sections.append(Section(id=sid, title=clean(ds.title), level=ds.level, paragraphs=paragraphs))
        for f in [*ds.figures, *ds.tables]:
            add_float(f, sid)
    for f in [*draft.figures, *draft.tables]:
        add_float(f, None)
    return Document(title=clean(draft.title) if draft.title else None, source=source, sections=sections, figures=figures, tables=tables)
