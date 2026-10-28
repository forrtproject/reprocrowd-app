"""Package metadata from OpenAlex (metadata/openalex.json)."""
from __future__ import annotations

from typing import Any

from . import openalex
from .errors import ApiError, conflict, not_found
from .identifiers import parse_identifier
from .package import Package
from .schemas.common import now
from .schemas.manifest import OtherMeta

RESOURCE = "metadata-openalex"
PATH = "metadata/openalex.json"


def fetch(pkg: Package, identifier: str | None, overwrite: bool) -> dict[str, Any]:
    """Fetch the OpenAlex record. Refuses to replace existing metadata (or change the identifier) unless overwrite."""
    with pkg.locked():
        m = pkg.load()
        current = m.metareader.identifier
        ident = parse_identifier(identifier) if identifier else current
        if ident is None:
            raise ApiError(400, "no_identifier", "the package has no identifier; pass one")
        if not overwrite:
            if m.get(RESOURCE):
                raise conflict("metadata_exists", "metadata was already retrieved; replace it explicitly (PUT, or --overwrite)")
            if current and ident != current:
                raise conflict("identifier_conflict", f"the package was created for {current.scheme}:{current.value}; replace it explicitly")
        work = openalex.fetch_work(ident)
        with pkg.edit() as m:
            m.metareader.identifier = ident
            m.metareader.identifiers = openalex.identifiers_of(work)
            m.metareader.metadata_fetched_at = now()
            m.title = work.get("title") or m.title
            pkg.write_json(m, RESOURCE, PATH, work, schema=None, meta=OtherMeta(kind="metadata", added_at=now()))
        return summary(work)


def summary(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": work.get("title"),
        "publication_year": work.get("publication_year"),
        "authors": [a.get("author", {}).get("display_name") for a in work.get("authorships") or []],
        "venue": ((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "identifiers": openalex.identifiers_of(work),
        "open_access": (work.get("open_access") or {}).get("is_oa"),
    }


def load(pkg: Package) -> dict[str, Any]:
    if not pkg.load().get(RESOURCE):
        raise not_found("metadata of package", pkg.id)
    return pkg.read_json(PATH)
