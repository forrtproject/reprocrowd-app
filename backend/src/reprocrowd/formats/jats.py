"""JATS (e.g. PubMed Central / Europe PMC full-text XML): mapping onto the document schema."""
from __future__ import annotations

from pathlib import Path

from .base import Format, Graphic
from .xmlmap import FloatRule, Region, XmlMapping, check_with_mapping, load, parse_with_mapping, root_name, text_of

MAPPING = XmlMapping(
    roots=("article",),
    title=".//front/article-meta/title-group/article-title",
    regions=(
        Region(".//front/article-meta/abstract", "Abstract"),
        Region("body", ""),
        Region("back", ""),
        Region("floats-group", ""),  # floats collected outside the text (document level)
    ),
    section=frozenset({"sec", "app", "ack", "notes"}),
    heading="title",
    paragraph=frozenset({"p", "disp-formula", "disp-quote", "list-item", "def-item"}),
    transparent=frozenset({"list", "def-list", "fig-group", "table-wrap-group", "app-group", "boxed-text"}),
    sentence=None,  # JATS doesn't mark sentences: segmented
    figure=FloatRule(match=lambda el: el.tag == "fig", label=("label",), caption=("caption",)),
    table=FloatRule(match=lambda el: el.tag == "table-wrap", label=("label",), caption=("caption",), rows=".//tr", cells=("th", "td"), default_label="Table"),
    skip=lambda el: el.tag in {"ref-list", "fn-group", "sec-meta", "glossary"},
    required=("body", ".//front/article-meta/abstract"),
)


def graphics(path: Path) -> list[Graphic]:
    """<fig> elements with a label and one image. A <fig> with several images (panels) has no single file to show, but
    <alternatives> can list one image in several formats, so what counts is the file name without its extension. Images
    of a <fig> nested in this one (eLife's figure supplements) are that one's."""
    out = []
    for el in load(path).iter("fig"):
        files = [g.get("href") or "" for g in el.iter("graphic") if next(g.iterancestors("fig")) is el]
        stems = {f.rsplit("/", 1)[-1].rsplit(".", 1)[0] for f in files if f}
        label = text_of(next((c for c in el if c.tag == "label"), None))
        if label and len(stems) == 1:
            out.append(Graphic(label, text_of(next((c for c in el if c.tag == "caption"), None)), next(f for f in files if f)))
    return out


def pmcid(path: Path) -> str | None:
    ids = load(path).xpath(".//front/article-meta/article-id[@pub-id-type='pmc' or @pub-id-type='pmcid']/text()")
    digits = next((i.strip().upper().removeprefix("PMC") for i in ids if i.strip().upper().removeprefix("PMC").isdigit()), None)
    return f"PMC{digits}" if digits else None


FORMAT = Format(
    name="jats",
    description="JATS XML (PubMed Central / Europe PMC full text)",
    extension=".jats.xml",
    frictionless_format="xml",
    mediatype="application/jats+xml",
    resource_type="file",
    sniff=lambda head: root_name(head) in MAPPING.roots,
    check=lambda path: check_with_mapping(path, MAPPING),
    parse=lambda path: parse_with_mapping(path, MAPPING),
    graphics=graphics,
    pmcid=pmcid,
)
