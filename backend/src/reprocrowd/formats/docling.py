"""Docling JSON (DoclingDocument): mapping onto the document schema.

DoclingDocument stores items in flat collections (texts, tables, pictures, groups) and the reading order
as a tree of JSON references from ``body``. Each text item has a ``label``; LABELS says what it becomes.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from ..schemas.common import Box
from ..schemas.document import DocumentDraft, DraftFigure, DraftSection, DraftTable
from .base import CheckReport, Format, Located
from .builder import paragraph_from_text, sentences_from_text
from .segment import clean

Role = Literal["title", "heading", "paragraph", "skip"]

# Docling text label → role in the document.
LABELS: dict[str, Role] = {
    "title": "title",
    "section_header": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "list_item": "paragraph",
    "formula": "paragraph",
    "code": "paragraph",
    "footnote": "paragraph",
    "caption": "skip",  # read through the captions of tables and pictures
    "page_header": "skip",
    "page_footer": "skip",
    "reference": "skip",  # bibliography entries
}
UNKNOWN_LABEL: Role = "paragraph"


def _boxes(item: dict) -> list[Box] | None:
    out = []
    for p in item.get("prov") or []:
        b = p.get("bbox") or {}
        if (b.get("coord_origin") or "BOTTOMLEFT").upper() == "TOPLEFT":
            out.append(Box(page=p["page_no"], x0=b["l"], y0=b["t"], x1=b["r"], y1=b["b"], origin="top"))
        else:
            out.append(Box(page=p["page_no"], x0=b["l"], y0=b["b"], x1=b["r"], y1=b["t"], origin="bottom"))
    return out or None


def _table_rows(item: dict) -> list[list[str]]:
    data = item.get("data") or {}
    if data.get("grid"):
        return [[clean(c.get("text", "")) for c in row] for row in data["grid"]]
    n_rows, n_cols = data.get("num_rows") or 0, data.get("num_cols") or 0
    grid = [[""] * n_cols for _ in range(n_rows)]
    for c in data.get("table_cells") or []:
        r, k = c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0)
        if r < n_rows and k < n_cols:
            grid[r][k] = clean(c.get("text", ""))
    return grid


def parse(path: Path) -> DocumentDraft:
    doc = json.loads(path.read_text(encoding="utf-8"))

    def resolve(ref: dict) -> dict | None:
        ptr = ref.get("$ref") or ref.get("cref") or ""
        parts = ptr.lstrip("#/").split("/")
        node: Any = doc
        for p in parts:
            node = node[int(p)] if isinstance(node, list) else node.get(p)
            if node is None:
                return None
        return node

    title: str | None = None
    sections: list[DraftSection] = [DraftSection(title="", level=1)]  # text before the first heading
    loose: list[DraftFigure] = []

    def caption(item: dict):
        text = " ".join(c.get("text", "") for r in item.get("captions") or [] if (c := resolve(r)))
        return sentences_from_text(text)

    def label(cap_text: str, pattern: str, default: str) -> str:
        m = re.match(pattern, cap_text, re.I)
        return m.group(1) if m else default

    def visit(item: dict | None):
        nonlocal title
        if item is None:
            return
        collection = (item.get("self_ref") or "#/body").split("/")[1]
        sec = sections[-1]
        if collection == "texts":
            role = LABELS.get(item.get("label", ""), UNKNOWN_LABEL)
            text = clean(item.get("text", ""))
            if role == "skip" or not text:
                return
            if role == "title":
                title = title or text
            elif role == "heading":
                sections.append(DraftSection(title=text, level=max(1, int(item.get("level") or 1))))
            elif (p := paragraph_from_text(text, _boxes(item))) is not None:
                sec.paragraphs.append(p)
        elif collection == "tables":
            cap = caption(item)
            first = cap[0].text if cap else ""
            sec.tables.append(DraftTable(label=label(first, r"(Table\s*\S+?)[.:]?(\s|$)", "Table"), caption=cap, rows=_table_rows(item), boxes=_boxes(item)))
            return
        elif collection == "pictures":
            cap = caption(item)
            first = cap[0].text if cap else ""
            sec.figures.append(DraftFigure(label=label(first, r"(Fig(?:ure|\.)?\s*\S+?)[.:]?(\s|$)", "Figure"), caption=cap, boxes=_boxes(item)))
            return  # text inside pictures (axis labels etc.) is not body text
        for child in item.get("children") or []:
            visit(resolve(child))

    visit(doc.get("body"))
    return DocumentDraft(title=title, sections=sections, figures=loose)


def locate(path: Path) -> list[Located]:
    """Tables, and pictures with a caption: the ones without are logos and icons (or, rarely, a figure whose caption
    Docling didn't link). A table keeps its box when its caption wasn't linked."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    texts = {t.get("self_ref"): t.get("text", "") for t in doc.get("texts") or []}
    out = []
    for key, kind in (("pictures", "figure"), ("tables", "table")):
        for item in doc.get(key) or []:
            caption = clean(" ".join(texts.get(r.get("$ref") or r.get("cref"), "") for r in item.get("captions") or []))
            boxes = _boxes(item)
            if boxes and (caption or kind == "table"):
                out.append(Located(kind, caption, boxes))
    return out


def made_from(path: Path, pdf: Path) -> bool | None:
    """origin.binary_hash is the last 8 bytes of the PDF's SHA-256, as an unsigned big-endian integer."""
    recorded = (json.loads(path.read_text(encoding="utf-8")).get("origin") or {}).get("binary_hash")
    if recorded is None:
        return None
    return int(recorded) == int.from_bytes(hashlib.sha256(pdf.read_bytes()).digest()[-8:], "big")


def check(path: Path) -> CheckReport:
    r = CheckReport()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        r.problems.append(f"not valid JSON: {e}")
        return r
    if not isinstance(doc, dict) or doc.get("schema_name") != "DoclingDocument":
        r.problems.append('not a DoclingDocument (schema_name must be "DoclingDocument")')
        return r
    if not isinstance(doc.get("body"), dict) or not isinstance(doc.get("texts"), list):
        r.problems.append("DoclingDocument without body or texts")
        return r
    if not doc["texts"]:
        r.problems.append("DoclingDocument has no text items")
    r.notes.append(f"docling schema version {doc.get('version', '?')}: {len(doc['texts'])} texts, {len(doc.get('tables') or [])} tables, {len(doc.get('pictures') or [])} pictures")
    return r


FORMAT = Format(
    name="docling",
    description="Docling JSON (DoclingDocument)",
    extension=".docling.json",
    frictionless_format="json",
    mediatype="application/json",
    resource_type="json",
    sniff=lambda head: head.lstrip().startswith(b"{") and b"DoclingDocument" in head,
    check=check,
    parse=parse,
    locate=locate,
    made_from=made_from,
)
