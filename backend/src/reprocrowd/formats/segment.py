"""Sentence segmentation for formats that don't mark sentences themselves (JATS, Docling, TEI without <s>).

Rule-based: split after . ! ? followed by whitespace and an uppercase letter, digit or opening bracket, then
re-join splits after common scholarly abbreviations. Replace ``split_sentences`` to use a different
segmenter.
"""
from __future__ import annotations

import re

_SPLIT = re.compile(r"(?<=[.!?])[\"”’)\]]?\s+(?=[A-Z0-9(\[\"“])")
_ABBREV = re.compile(
    r"\b(?:e\.g|i\.e|et al|Fig|Figs|Eq|Eqs|Ref|Refs|vs|cf|approx|ca|Dr|Mr|Ms|Prof|No|Vol|pp|Tab|Sect|resp|St|Jr|Inc|Ltd|al|[A-Z])\.$"
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for piece in _SPLIT.split(clean(text)):
        piece = piece.strip()
        if not piece:
            continue
        if parts and _ABBREV.search(parts[-1]):
            parts[-1] += " " + piece
        else:
            parts.append(piece)
    return parts
