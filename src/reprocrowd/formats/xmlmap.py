"""Declarative XML → document mapping, shared by TEI and JATS.

A format describes *which elements mean what* in an ``XmlMapping``; ``parse_with_mapping`` walks the tree and
builds a DocumentDraft. Namespaces are stripped first, so mappings use plain local names.

Walk: for each region (e.g. abstract, body, back), read its children in order:
    skip(el)             → ignored with its whole subtree
    table / figure rule  → a table / figure in the current section (document level if there is none yet)
    section element      → a new section one level deeper; its title is the ``heading`` child
    paragraph element    → a paragraph; floats nested in it are taken out and added to the section;
                           sentences are the ``sentence`` children if present, otherwise segmented
    transparent element  → its children are read as if they were in the parent (lists, groups)
    anything else        → ignored
Text directly in a region (outside sections) goes into a section named after the region.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from lxml import etree

from ..schemas.common import Box
from ..schemas.document import DocumentDraft, DraftFigure, DraftParagraph, DraftSection, DraftSentence, DraftTable
from .base import CheckReport
from .builder import sentences_from_text
from .segment import clean

Element = etree._Element


@dataclass(frozen=True)
class FloatRule:
    match: Callable[[Element], bool]
    label: tuple[str, ...]  # child elements tried in order; the first with text is the label
    caption: tuple[str, ...]  # child elements whose text forms the caption
    rows: str | None = None  # XPath (relative to the float) of table rows
    cells: tuple[str, ...] = ()  # cell element names within a row
    default_label: str = "Figure"


@dataclass(frozen=True)
class Region:
    xpath: str  # relative to the root element
    title: str  # title for text outside sections, and for untitled sections, in this region


@dataclass(frozen=True)
class XmlMapping:
    roots: tuple[str, ...]  # accepted root element names
    title: str  # XPath of the article title
    regions: tuple[Region, ...]
    section: frozenset[str]
    heading: str
    paragraph: frozenset[str]
    figure: FloatRule
    table: FloatRule
    sentence: str | None = None  # element wrapping one sentence inside paragraphs/captions, if pre-segmented
    transparent: frozenset[str] = field(default_factory=frozenset)
    skip: Callable[[Element], bool] = lambda el: False
    boxes: Callable[[Element], list[Box] | None] = lambda el: None
    required: tuple[str, ...] = ()  # XPaths of which at least one must match (integrity check)


# ---------- loading ----------

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True, remove_pis=True)


def load(path: Path) -> Element:
    root = etree.parse(str(path), _PARSER).getroot()
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = etree.QName(el).localname
            for k in list(el.attrib):  # xml:id → id, etc.
                if k.startswith("{"):
                    el.attrib[etree.QName(k).localname] = el.attrib.pop(k)
    return root


def root_name(head: bytes) -> str | None:
    """Local name of the root element, from the start of a file (no full parse)."""
    text = head.decode("utf-8", errors="replace")
    text = re.sub(r"<\?.*?\?>|<!--.*?-->|<![A-Z][^>]*>", "", text, flags=re.S)
    m = re.search(r"<(?:[\w-]+:)?([\w-]+)", text)
    return m.group(1) if m else None


def check_with_mapping(path: Path, m: XmlMapping) -> CheckReport:
    r = CheckReport()
    try:
        root = load(path)
    except etree.XMLSyntaxError as e:
        r.problems.append(f"not well-formed XML: {e}")
        return r
    if root.tag not in m.roots:
        r.problems.append(f"root element is <{root.tag}>, expected one of {', '.join(m.roots)}")
        return r
    if m.required and not any(root.xpath(x) for x in m.required):
        r.problems.append(f"none of {', '.join(m.required)} found: no text content")
        return r
    for region in m.regions:
        n = len(root.xpath(region.xpath))
        if n:
            r.notes.append(f"{region.xpath}: {n} element(s)")
    return r


# ---------- walking ----------


def text_of(el: Element | None) -> str:
    return clean("".join(el.itertext())) if el is not None else ""


def _remove_keep_tail(el: Element):
    parent = el.getparent()
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def parse_with_mapping(path: Path, m: XmlMapping) -> DocumentDraft:
    root = load(path)
    title_el = root.xpath(m.title)
    sections: list[DraftSection] = []
    loose_figures: list[DraftFigure] = []
    loose_tables: list[DraftTable] = []

    def sentences(el: Element) -> list[DraftSentence]:
        marked = [c for c in el if c.tag == m.sentence] if m.sentence else []
        if marked:
            return [DraftSentence(text=t, boxes=m.boxes(c)) for c in marked if (t := text_of(c))]
        return sentences_from_text(text_of(el), m.boxes(el))

    def float_of(el: Element) -> DraftFigure:
        rule = m.table if m.table.match(el) else m.figure
        label = next((t for name in rule.label for c in el if c.tag == name and (t := text_of(c))), rule.default_label)
        caption = [s for name in rule.caption for c in el if c.tag == name for s in sentences(c)]
        if rule is m.table:
            rows = [[text_of(c) for c in row if c.tag in rule.cells] for row in el.xpath(rule.rows or "")]
            return DraftTable(label=label, caption=caption, rows=[r for r in rows if r], boxes=m.boxes(el))
        return DraftFigure(label=label, caption=caption, boxes=m.boxes(el))

    def is_float(el: Element) -> bool:
        return m.table.match(el) or m.figure.match(el)

    def add_float(sec: DraftSection | None, f: DraftFigure):
        tables, figures = (sec.tables, sec.figures) if sec else (loose_tables, loose_figures)
        (tables if isinstance(f, DraftTable) else figures).append(f)

    def paragraph(el: Element, sec: DraftSection):
        floats = [d for d in el.iterdescendants() if isinstance(d.tag, str) and is_float(d)]
        if floats:
            el = deepcopy(el)
            nested = [d for d in el.iterdescendants() if isinstance(d.tag, str) and is_float(d)]
            for d in nested:
                if d.getparent() is not None and not any(a in nested for a in d.iterancestors()):
                    add_float(sec, float_of(d))
                    _remove_keep_tail(d)
        sents = sentences(el)
        if sents:
            sec.paragraphs.append(DraftParagraph(sentences=sents, boxes=m.boxes(el)))

    def read(container: Element, sec: DraftSection | None, level: int, fallback: str, region_sec: Callable[[bool], DraftSection | None]):
        """``sec`` is None directly inside a region; region_sec(create) then gives the region's own section."""
        for c in container:
            if not isinstance(c.tag, str) or m.skip(c):
                continue
            if is_float(c):
                add_float(sec or region_sec(False), float_of(c))
            elif c.tag in m.section:
                walk_section(c, level + 1, fallback)
            elif c.tag in m.paragraph:
                paragraph(c, sec or region_sec(True))
            elif c.tag in m.transparent:
                read(c, sec, level, fallback, region_sec)

    def walk_section(el: Element, level: int, fallback: str):
        head = next((c for c in el if c.tag == m.heading), None)
        sec = DraftSection(title=text_of(head) or fallback, level=level)
        sections.append(sec)
        read(el, sec, level, fallback, lambda create: sec)

    for region in m.regions:
        for el in root.xpath(region.xpath):
            holder: list[DraftSection] = []

            def region_sec(create: bool, region=region, holder=holder) -> DraftSection | None:
                if not holder and create:
                    holder.append(DraftSection(title=region.title, level=1))
                    sections.append(holder[0])
                return holder[0] if holder else None

            read(el, None, 0, region.title, region_sec)

    return DocumentDraft(
        title=text_of(title_el[0]) if title_el else None,
        sections=sections,
        figures=loose_figures,
        tables=loose_tables,
    )
