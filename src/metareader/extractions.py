"""Extractions: full texts converted into the common document schema, by a format parser or by the
reconcile LLM call. Both paths end in build_document() + validation before anything is written."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import formats, llm
from .errors import ApiError, conflict, invalid, not_found, pydantic_errors
from .formats.builder import build_document
from .fulltexts import get as get_fulltext
from .package import Package, slug
from .schemas import json_schema
from .schemas.document import Document, DocumentDraft, DocumentSource, ref_kind
from .schemas.manifest import ExtractionMeta, Resource
from .schemas.common import now


def _store(pkg: Package, name: str, doc: Document, meta: ExtractionMeta, replace: bool) -> Resource:
    with pkg.edit() as m:
        existing = m.get(name)
        if existing and not replace:
            raise conflict("extraction_exists", f"extraction {name} exists; replace it explicitly (PUT)")
        if existing and existing.metareader.kind != "extraction":
            raise conflict("name_taken", f"{name} is not an extraction")
        rel = existing.path if existing else pkg.free_path(f"extractions/{name}.json")
        return pkg.write_json(m, name, rel, doc, schema="document", meta=meta)


def parse(pkg: Package, fulltext: str, name: str | None = None, replace: bool = False) -> Resource:
    ft = get_fulltext(pkg, fulltext)
    fmt = formats.BY_NAME[ft.metareader.flavor]
    if fmt.parse is None:
        raise invalid("not_parseable", f"{fmt.description} files can't be parsed into the document schema; use reconcile")
    try:
        draft = fmt.parse(pkg.path(ft.path))
        doc = build_document(draft, DocumentSource(method="parse", format=fmt.name, fulltexts=[ft.name]))
    except ValidationError as e:
        raise invalid("parse_failed", f"{ft.name} did not convert into a valid document", {"errors": pydantic_errors(e)})
    except Exception as e:  # parser bug or unexpected structure: report, don't 500
        raise invalid("parse_failed", f"{ft.name} could not be parsed: {e}")
    name = name or "ex-" + slug(ft.name.removeprefix("ft-"))
    meta = ExtractionMeta(added_at=now(), method="parse", fulltexts=[ft.name])
    return _store(pkg, name, doc, meta, replace)


def reconcile(pkg: Package, sources: list[str], *, prompt: str | None, instructions: str, profile: str | None,
              name: str | None = None, replace: bool = False) -> Resource:
    if not sources:
        raise ApiError(400, "no_sources", "list at least one full text")
    fts = [get_fulltext(pkg, s) for s in sources]
    files = "\n".join(f"- {r.path} ({formats.BY_NAME[r.metareader.flavor].description})" for r in fts)
    values = {
        "files": files,
        "schema": json.dumps(json_schema("document-draft"), indent=1),
        "instructions": f"\nAdditional instructions:\n{instructions.strip()}\n" if instructions.strip() else "",
    }
    text = llm.render(prompt if prompt is not None else llm.load_prompt("reconcile"), values, required=("files", "schema"))
    call = llm.run(pkg, "reconcile", text, profile_name=profile, template="custom" if prompt is not None else "default",
                   instructions=instructions, read_files=True)
    # From here on the call succeeded; anything wrong is the output's fault (llm_output_invalid).
    try:
        raw = llm.extract_json(call.output)
    except ValueError as e:
        raise llm.output_invalid(call, "json", [str(e)])
    try:
        draft = DocumentDraft.model_validate(raw)
        doc = build_document(draft, DocumentSource(method="reconcile", fulltexts=[r.name for r in fts], llm_call=call.id))
    except ValidationError as e:
        raise llm.output_invalid(call, "schema", pydantic_errors(e))
    meta = ExtractionMeta(added_at=now(), method="reconcile", fulltexts=[r.name for r in fts], llm_call=call.id)
    return _store(pkg, name or Package.free_name(pkg.load(), "ex-reconciled"), doc, meta, replace)


def get(pkg: Package, name: str) -> tuple[Resource, Document]:
    r = pkg.load().get(name)
    if r is None or r.metareader.kind != "extraction":
        raise not_found("extraction", name)
    return r, Document.model_validate(pkg.read_json(r.path))


def delete(pkg: Package, name: str) -> None:
    from .curation import CURATIONS, Curation  # avoid import cycle

    get(pkg, name)
    users = [i.id for kind in CURATIONS for i, _ in Curation(pkg, kind).effective(include_disabled=True)
             if any(ref.extraction == name for ref in i.refs)]
    if users:
        raise conflict("in_use", f"{name} is referenced by {', '.join(users)}", {"used_by": users})
    with pkg.edit() as m:
        pkg.remove(m, name)


# ---------- listings (outline view and prompt material) ----------


@dataclass
class Scope:
    """Which part of a document to show: whole sections and/or single items (sentence, figure, table ids)."""

    sections: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.sections and not self.items


def check_scope(doc: Document, scope: Scope) -> None:
    secs = {s.id for s in doc.sections}
    index = doc.index()
    bad = [s for s in scope.sections if s not in secs] + [i for i in scope.items if i not in index]
    if bad:
        raise invalid("unknown_ids", f"not in the extraction: {', '.join(bad[:20])}", {"ids": bad})


_MENTION = {"figure": r"\bFig(?:ure|s?\.)?\s*(\d+)", "table": r"\bTab(?:le|\.)?\s*(\d+)"}


def float_number(label: str) -> str | None:
    """"Fig 3" / "Figure 3." / "Table 2" → "3" / "2"."""
    m = re.search(r"(\d+)", label)
    return m.group(1) if m else None


def mentions(text: str, kind: str | None) -> list[str]:
    """Numbers of the figures (or tables) a text mentions."""
    return re.findall(_MENTION.get(kind or "", r"(?!x)x"), text, re.I)


def listing(doc: Document, scope: Scope | None = None, with_tables: bool = True) -> str:
    """Readable text with every referenceable id in brackets."""
    scope = scope or Scope()
    want_sec = set(scope.sections)
    want = set(scope.items)
    whole = scope.empty()

    def in_scope(item_id: str) -> bool:
        return whole or item_id in want or doc.section_of(item_id) in want_sec

    # Figures and tables the in-scope text mentions ("Fig 3", "Table 2") are shown too, wherever they are.
    mentioned: set[str] = set()
    if not whole:
        text = " ".join(s.text for sec in doc.sections for p in sec.paragraphs for s in p.sentences if in_scope(s.id))
        for f in [*doc.figures, *doc.tables]:
            if (n := float_number(f.label)) and any(n == m for m in mentions(text, ref_kind(f.id))):
                mentioned.add(f.id)

    def shown(item_id: str) -> bool:
        return in_scope(item_id) or item_id.split(".")[0] in mentioned

    lines: list[str] = [f"# {doc.title}"] if doc.title else []
    for sec in doc.sections:
        sents = [s for p in sec.paragraphs for s in p.sentences if shown(s.id)]
        if not sents and not (whole or sec.id in want_sec):
            continue
        lines.append(f"\n{'#' * min(sec.level + 1, 6)} [{sec.id}] {sec.title}")
        for p in sec.paragraphs:
            ps = [s for s in p.sentences if shown(s.id)]
            if ps:
                lines.append("\n" + "\n".join(f"[{s.id}] {s.text}" for s in ps))
    floats = [f for f in [*doc.figures, *doc.tables] if shown(f.id) or any(shown(c.id) for c in f.caption)]
    if floats:
        lines.append("\n## Figures and tables")
    for f in floats:
        caption = " ".join(f"[{c.id}] {c.text}" for c in f.caption)
        lines.append(f"\n[{f.id}] {f.label}{f' (in {f.section})' if f.section else ''}: {caption}")
        if with_tables and ref_kind(f.id) == "table":
            lines.extend("\t".join(row) for row in f.rows)
    return "\n".join(lines).strip() + "\n"
