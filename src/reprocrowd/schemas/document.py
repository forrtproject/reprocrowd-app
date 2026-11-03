"""The common document schema every full-text format is converted into.

Two shapes:
- ``DocumentDraft``: what parsers and the reconcile LLM produce. No ids; figures and tables sit inside the
  section where they appear.
- ``Document``: the stored form. ``build_document`` (formats/builder.py) turns a draft into a document and
  assigns the hierarchical ids (``sec3``, ``sec3.p2``, ``sec3.p2.s1``, ``fig1``, ``fig1.s1``, ``tab1``).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import Box, Model

SECTION_ID = r"^sec\d+$"
PARAGRAPH_ID = r"^sec\d+\.p\d+$"
SENTENCE_ID = r"^(sec\d+\.p\d+|fig\d+|tab\d+)\.s\d+$"
FIGURE_ID = r"^fig\d+$"
TABLE_ID = r"^tab\d+$"

RefKind = Literal["sentence", "figure", "table"]


def ref_kind(item_id: str) -> RefKind | None:
    """What a reference id points to, judged from its shape."""
    if re.match(SENTENCE_ID, item_id):
        return "sentence"
    if re.match(FIGURE_ID, item_id):
        return "figure"
    if re.match(TABLE_ID, item_id):
        return "table"
    return None


# ---------- draft (no ids) ----------


class DraftSentence(Model):
    text: str = Field(min_length=1)
    boxes: list[Box] | None = None


def _sentences(v):
    """Allow plain strings as shorthand for sentences without coordinates."""
    return [{"text": s} if isinstance(s, str) else s for s in v]


class DraftParagraph(Model):
    sentences: list[DraftSentence] = Field(min_length=1, description='sentences in order; a plain string is shorthand for {"text": ...}')
    boxes: list[Box] | None = None

    _norm = field_validator("sentences", mode="before")(_sentences)


class DraftFigure(Model):
    label: str = Field(description='e.g. "Figure 2"')
    caption: list[DraftSentence] = Field(default_factory=list)
    boxes: list[Box] | None = None

    _norm = field_validator("caption", mode="before")(_sentences)


class DraftTable(DraftFigure):
    label: str = Field(description='e.g. "Table 1"')
    rows: list[list[str]] = Field(default_factory=list, description="cell texts, header row(s) included")


class DraftSection(Model):
    title: str = Field(description='section heading; "" if the source has none')
    level: int = Field(default=1, ge=1, description="1 = top level, 2 = subsection, …")
    paragraphs: list[DraftParagraph] = Field(default_factory=list)
    figures: list[DraftFigure] = Field(default_factory=list, description="figures that appear in this section")
    tables: list[DraftTable] = Field(default_factory=list, description="tables that appear in this section")


class DocumentDraft(Model):
    """Document content without ids, in reading order (output of parsers and of the reconcile LLM)."""

    title: str | None = None
    sections: list[DraftSection]
    figures: list[DraftFigure] = Field(default_factory=list, description="figures not tied to a section")
    tables: list[DraftTable] = Field(default_factory=list, description="tables not tied to a section")


# ---------- stored document ----------


class Sentence(Model):
    id: str = Field(pattern=SENTENCE_ID)
    text: str = Field(min_length=1)
    boxes: list[Box] | None = None


class Paragraph(Model):
    id: str = Field(pattern=PARAGRAPH_ID)
    sentences: list[Sentence] = Field(min_length=1)
    boxes: list[Box] | None = None


class Section(Model):
    id: str = Field(pattern=SECTION_ID)
    title: str
    level: int = Field(ge=1)
    paragraphs: list[Paragraph]


class Figure(Model):
    id: str = Field(pattern=FIGURE_ID)
    label: str
    section: str | None = Field(default=None, description="id of the section the figure appears in")
    caption: list[Sentence]
    boxes: list[Box] | None = None


class Table(Model):
    id: str = Field(pattern=TABLE_ID)
    label: str
    section: str | None = Field(default=None, description="id of the section the table appears in")
    caption: list[Sentence]
    rows: list[list[str]]
    boxes: list[Box] | None = None


class DocumentSource(Model):
    method: Literal["parse", "reconcile"]
    format: str | None = Field(default=None, description="source format for parse (tei, jats, docling); null for reconcile")
    fulltexts: list[str] = Field(description="resource names of the full texts this was made from")
    llm_call: str | None = Field(default=None, description="id of the LLM call (reconcile only)")


class Document(Model):
    """A paper's text as sections > paragraphs > sentences, plus figures and tables."""

    schema_version: Literal["1.0"] = "1.0"
    title: str | None = None
    source: DocumentSource
    sections: list[Section]
    figures: list[Figure] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent_ids(self):
        seen: set[str] = set()
        problems: list[str] = []

        def add(i: str, parent: str | None = None):
            if i in seen:
                problems.append(f"duplicate id {i}")
            seen.add(i)
            if parent and not i.startswith(parent + "."):
                problems.append(f"{i} is not inside {parent}")

        for sec in self.sections:
            add(sec.id)
            for p in sec.paragraphs:
                add(p.id, sec.id)
                for s in p.sentences:
                    add(s.id, p.id)
        section_ids = {s.id for s in self.sections}
        for f in [*self.figures, *self.tables]:
            add(f.id)
            if f.section is not None and f.section not in section_ids:
                problems.append(f"{f.id}: unknown section {f.section}")
            for s in f.caption:
                add(s.id, f.id)
        if problems:
            raise ValueError("; ".join(problems[:20]))
        return self

    def index(self) -> dict[str, tuple[str, object]]:
        """All referenceable items by id: sentences (body and captions), figures, tables."""
        out: dict[str, tuple[str, object]] = {}
        for sec in self.sections:
            for p in sec.paragraphs:
                for s in p.sentences:
                    out[s.id] = ("sentence", s)
        for f in self.figures:
            out[f.id] = ("figure", f)
            for s in f.caption:
                out[s.id] = ("sentence", s)
        for t in self.tables:
            out[t.id] = ("table", t)
            for s in t.caption:
                out[s.id] = ("sentence", s)
        return out

    def section_of(self, item_id: str) -> str | None:
        head = item_id.split(".")[0]
        if head.startswith("sec"):
            return head
        for f in [*self.figures, *self.tables]:
            if f.id == head:
                return f.section
        return None
