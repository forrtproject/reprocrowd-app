"""TEI (as produced by GROBID): mapping onto the document schema."""
from __future__ import annotations

from ..schemas.common import Box
from .base import Format
from .xmlmap import FloatRule, Region, XmlMapping, check_with_mapping, parse_with_mapping, root_name


def grobid_coords(el) -> list[Box] | None:
    """GROBID coords attribute: "page,x,y,w,h;page,x,y,w,h", origin top-left."""
    boxes = []
    for part in (el.get("coords") or "").split(";"):
        try:
            p, x, y, w, h = (float(v) for v in part.split(","))
        except ValueError:
            continue
        boxes.append(Box(page=int(p), x0=x, y0=y, x1=x + w, y1=y + h, origin="top"))
    return boxes or None


def _is_table(el) -> bool:
    return el.tag == "figure" and el.get("type") == "table"


MAPPING = XmlMapping(
    roots=("TEI", "teiCorpus"),
    title=".//teiHeader/fileDesc/titleStmt/title",
    regions=(
        Region(".//teiHeader/profileDesc/abstract", "Abstract"),
        Region(".//text/body", ""),
        Region(".//text/back", ""),
    ),
    section=frozenset({"div"}),  # <div> with a <head> title; nested divs are subsections
    heading="head",
    paragraph=frozenset({"p", "formula"}),
    sentence="s",  # GROBID with segmentSentences=1 wraps sentences in <s>
    figure=FloatRule(match=lambda el: el.tag == "figure" and not _is_table(el), label=("head", "label"), caption=("figDesc",)),
    table=FloatRule(match=_is_table, label=("head", "label"), caption=("figDesc",), rows="table/row", cells=("cell",), default_label="Table"),
    skip=lambda el: el.tag in {"listBibl", "note"} or el.get("type") == "references",
    boxes=grobid_coords,
    required=(".//text/body", ".//abstract"),
)

FORMAT = Format(
    name="tei",
    description="TEI XML (e.g. GROBID full-text output)",
    extension=".tei.xml",
    frictionless_format="xml",
    mediatype="application/tei+xml",
    resource_type="file",
    sniff=lambda head: root_name(head) in MAPPING.roots,
    check=lambda path: check_with_mapping(path, MAPPING),
    parse=lambda path: parse_with_mapping(path, MAPPING),
)

