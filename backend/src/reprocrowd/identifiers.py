"""Parse the identifiers a package can be created for (anything OpenAlex resolves)."""
from __future__ import annotations

import re

from .errors import ApiError
from .schemas.common import Identifier

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")


def parse_identifier(raw: str) -> Identifier:
    """Accepts: DOI (bare, doi:, or resolver URL), OpenAlex (W123, openalex:W123, URL), pmid:123, pmcid:PMC123."""
    s = raw.strip()
    low = s.lower()
    for p in _DOI_PREFIXES:
        if low.startswith(p):
            s, low = s[len(p):].strip(), low[len(p):].strip()
            break
    if re.match(r"^10\.\d{4,9}/\S+$", low):
        return Identifier(scheme="doi", value=low)
    m = re.match(r"^(?:openalex:|https?://openalex\.org/)?(w\d+)$", low)
    if m:
        return Identifier(scheme="openalex", value=m.group(1).upper())
    m = re.match(r"^pmid:\s*(\d+)$", low)
    if m:
        return Identifier(scheme="pmid", value=m.group(1))
    m = re.match(r"^pmcid:\s*(?:pmc)?(\d+)$", low) or re.match(r"^pmc(\d+)$", low)
    if m:
        return Identifier(scheme="pmcid", value=f"PMC{m.group(1)}")
    raise ApiError(400, "invalid_identifier", f"unrecognized identifier {raw!r}; use a DOI, OpenAlex id (W…), pmid:… or pmcid:…")


def openalex_key(ident: Identifier) -> str:
    """The id form used in https://api.openalex.org/works/<key>."""
    return ident.value if ident.scheme == "openalex" else f"{ident.scheme}:{ident.value}"
