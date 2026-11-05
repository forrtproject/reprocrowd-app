"""Originals of figure targets, for comparing with the reproduced ones (targets/figures/<target id>.png).

The exact crop comes from a full text that locates the figure (coordinates.py). A target usually refers to an extraction
without usable boxes (JATS, reconciled), so the figure is looked up there by its caption. Otherwise the whole page is
rendered, found by the caption's text in the PDF. Which of the two a file is, is recorded with its resource.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import pypdfium2 as pdfium

from .coordinates import Source, find_caption, normalize, pdfs, sources
from .curation import Curation
from .errors import conflict, invalid, not_found
from .extractions import float_number, mentions
from .extractions import get as get_extraction
from .formats.base import Located
from .package import Package
from .schemas.common import Box, now
from .schemas.document import Figure
from .schemas.manifest import OtherMeta, Resource

MARGIN_PT = 4


def _figure_of(pkg: Package, target) -> tuple[str, Figure, str]:
    """The figure a target is about: a referenced figure (or its caption), else the figure that its description,
    quote or referenced sentences mention ("Fig 3"). Returns (extraction, figure, how it was found)."""
    for ref in target.refs:
        head = ref.id.split(".")[0]  # fig2 or fig2.s1 (a caption sentence) → fig2
        if head.startswith("fig"):
            res, doc = get_extraction(pkg, ref.extraction)
            fig = next((f for f in doc.figures if f.id == head), None)
            if fig:
                return res.name, fig, "referenced"
    for ref in target.refs:
        _, doc = get_extraction(pkg, ref.extraction)
        found = doc.index().get(ref.id)
        text = " ".join([target.description, target.quote, getattr(found[1], "text", "") if found else ""])
        numbers = mentions(text, "figure")
        fig = next((f for n in numbers for f in doc.figures if float_number(f.label) == n), None)
        if fig:
            return ref.extraction, fig, f"mentioned as Fig {float_number(fig.label)}"
    raise invalid("no_figure_reference", f"{target.id} neither references nor mentions a figure of an extraction")


def _crop_margins(boxes: list[Box], width: float, height: float) -> tuple[float, float, float, float]:
    """PDFium crop = (left, bottom, right, top) distances from the page edges, in points."""
    x0 = min(b.x0 for b in boxes) - MARGIN_PT
    x1 = max(b.x1 for b in boxes) + MARGIN_PT
    if boxes[0].origin == "top":
        top, bottom = min(b.y0 for b in boxes) - MARGIN_PT, max(b.y1 for b in boxes) + MARGIN_PT
        t, b = top, height - bottom
    else:
        low, high = min(b.y0 for b in boxes) - MARGIN_PT, max(b.y1 for b in boxes) + MARGIN_PT
        t, b = height - high, low
    return max(0, x0), max(0, b), max(0, width - x1), max(0, t)


def _on_pdf(pkg: Package, name: str) -> Resource:
    src = pkg.load().get(name)
    if src is None or src.reprocrowd.kind != "fulltext" or src.reprocrowd.flavor != "pdf":
        raise invalid("not_convertible", f"{name} is not a PDF full text")
    return src


def _render(pkg: Package, target_id: str, pdf: str, page_no: int, boxes: list[Box] | None, scale: float) -> tuple[str, Any]:
    src = _on_pdf(pkg, pdf)
    doc = pdfium.PdfDocument(str(pkg.path(src.path)))
    try:
        if page_no > len(doc):
            raise invalid("no_coordinates", f"page {page_no} doesn't exist: {pdf} has {len(doc)} pages")
        page = doc[page_no - 1]
        w, h = page.get_size()
        image = page.render(scale=scale, crop=_crop_margins(boxes, w, h) if boxes else (0, 0, 0, 0)).to_pil()
    finally:
        doc.close()
    rel = f"targets/figures/{target_id}.png"
    p = pkg.path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    image.save(tmp, format="PNG")
    tmp.replace(p)
    return rel, image


def _keys(boxes: list[Box] | None) -> set[tuple]:
    return {(b.page, b.x0, b.y0, b.x1, b.y1) for b in boxes or []}


def _matches(fig: Figure, source: Source) -> Located | None:
    """The located figure this one is: the same boxes (it comes from this full text), or the same caption."""
    figures = [i for i in source.located if i.kind == "figure"]
    same = next((i for i in figures if _keys(i.boxes) & _keys(fig.boxes)), None)
    if same:
        return same
    i = find_caption(" ".join(s.text for s in fig.caption), [normalize(f.caption) for f in figures])
    return None if i is None else figures[i]


def _page_of_caption(pkg: Package, fig: Figure, pdf: str | None) -> tuple[str, int] | None:
    """The one page, of the given PDF or of all the package's, with the figure's caption."""
    pages = []
    for name in [pdf] if pdf else [r.name for r in pdfs(pkg.load())]:
        doc = pdfium.PdfDocument(str(pkg.path(pkg.load().get(name).path)))
        try:
            for n in range(len(doc)):
                text = doc[n].get_textpage()
                pages.append((name, n + 1, normalize(text.get_text_range())))
                text.close()
        finally:
            doc.close()
    i = find_caption(" ".join(s.text for s in fig.caption), [t for _, _, t in pages])
    return None if i is None else pages[i][:2]


def crop(pkg: Package, target_id: str, pdf: str | None = None, scale: float = 2.0, known: list[Source] | None = None) -> dict[str, Any]:
    target = next((t for t, _ in Curation(pkg, "targets").effective(include_disabled=True) if t.id == target_id), None)
    if target is None:
        raise not_found("target", target_id)
    if target.type != "figure":
        raise invalid("not_a_figure_target", f"{target_id} is a {target.type} target")
    extraction, fig, how = _figure_of(pkg, target)
    if pdf:
        _on_pdf(pkg, pdf)
    known = sources(pkg) if known is None else known
    hits = [(s, item) for s in known if (item := _matches(fig, s))]
    usable = next(((s, item) for s, item in hits if pdf or s.pdf), None)
    if usable:
        source, item = usable
        pdf_name = pdf or source.pdf
        page_no = Counter(b.page for b in item.boxes).most_common(1)[0][0]
        boxes, method, origin = [b for b in item.boxes if b.page == page_no], "crop", source.fulltext.name
    else:
        found = _page_of_caption(pkg, fig, pdf)
        origin_pdf = next((s.pdf for s in known if fig.boxes and s.pdf and s.fulltext.name in pkg.load().get(extraction).reprocrowd.fulltexts), None)
        if found is None and fig.boxes and (pdf or origin_pdf):  # no caption to go by, but the boxes tell the page
            found = pdf or origin_pdf, Counter(b.page for b in fig.boxes).most_common(1)[0][0]
        if found is None:
            if hits:
                raise conflict("pdf_unknown", f"can't tell which PDF {hits[0][0].fulltext.name} was made from; name it (pdf)")
            raise invalid("no_coordinates", f"no full text locates {fig.id} of {extraction}, and its caption isn't on one page of "
                                            f"{pdf or 'the PDF'}; crops need a GROBID or Docling conversion of the PDF")
        pdf_name, page_no = found
        boxes, method, origin = None, "page", None
    rel, image = _render(pkg, target_id, pdf_name, page_no, boxes, scale)
    with pkg.edit() as m:
        res = pkg.put(m, pkg.describe(f"figure-{target_id.lower()}", rel, type="file", format="png", mediatype="image/png",
                                      meta=OtherMeta(kind="figure", added_at=now(), method=method, page=page_no)))
    return {"resource": res, "target": target_id, "figure": f"{extraction} {fig.id} ({how})", "pdf": pdf_name, "page": page_no,
            "size": list(image.size), "method": method, "source": origin}


def listing(pkg: Package) -> list[dict[str, Any]]:
    """The originals made so far. Crops from before full pages existed say nothing: they are crops."""
    return [{"target": r.path.rsplit("/", 1)[-1].removesuffix(".png"), "path": r.path, "method": r.reprocrowd.method or "crop", "page": r.reprocrowd.page}
            for r in pkg.load().resources if r.reprocrowd.kind == "figure"]


def crop_all(pkg: Package, pdf: str | None = None, scale: float = 2.0) -> list[dict[str, Any]]:
    out = []
    known = sources(pkg)
    for t, _ in Curation(pkg, "targets").effective():
        if t.type != "figure":
            continue
        try:
            r = crop(pkg, t.id, pdf, scale, known)
            out.append({"target": t.id, "ok": True, "path": r["resource"].path, "page": r["page"], "method": r["method"]})
        except Exception as e:  # report per target; ApiError carries a readable message
            out.append({"target": t.id, "ok": False, "error": getattr(e, "message", str(e)), "code": getattr(e, "code", "internal_error")})
    return out
