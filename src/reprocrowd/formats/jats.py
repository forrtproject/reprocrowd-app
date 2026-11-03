"""JATS (e.g. PubMed Central / Europe PMC full-text XML): mapping onto the document schema."""
from __future__ import annotations

from .base import Format
from .xmlmap import FloatRule, Region, XmlMapping, check_with_mapping, parse_with_mapping, root_name

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
)
