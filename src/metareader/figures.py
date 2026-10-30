"""Crops of the original figures for figure targets (targets/figures/<target id>.png).

The figure's coordinates come from the extraction (GROBID ``coords``, Docling ``prov`` boxes). The PDF they
refer to is found through provenance: the extraction's full text, or the PDF it was converted from. A PDF
can also be named explicitly (e.g. for a TEI that was added from a local file). The crop is the union of
the figure's boxes on its page (graphic and caption), with a small margin, rendered with PDFium.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import pypdfium2 as pdfium

from .curation import Curation
from .errors import conflict, invalid, not_found
from .extractions import float_number, mentions
from .extractions import get as get_extraction
from .package import Package
from .schemas.common import Box, now
from .schemas.document import Figure
from .schemas.manifest import OtherMeta

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


def _pdf_for(pkg: Package, extraction: str) -> str:
    m = pkg.load()
    for ft in m.get(extraction).metareader.fulltexts:
        r = m.get(ft)
        if r is None:
            continue
        if r.metareader.flavor == "pdf":
            return r.name
        if r.metareader.origin.type == "conversion":
            return r.metareader.origin.source
    raise conflict("pdf_unknown", f"can't tell which PDF the coordinates of {extraction} refer to; name it (pdf)")


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


def crop(pkg: Package, target_id: str, pdf: str | None = None, scale: float = 2.0) -> dict[str, Any]:
    target = next((t for t, _ in Curation(pkg, "targets").effective(include_disabled=True) if t.id == target_id), None)
    if target is None:
        raise not_found("target", target_id)
    if target.type != "figure":
        raise invalid("not_a_figure_target", f"{target_id} is a {target.type} target")
    extraction, fig, how = _figure_of(pkg, target)
    if not fig.boxes:
        raise invalid("no_coordinates", f"{fig.id} of {extraction} has no coordinates (e.g. JATS); crops need GROBID or Docling output")
    pdf_name = pdf or _pdf_for(pkg, extraction)
    m = pkg.load()
    src = m.get(pdf_name)
    if src is None or src.metareader.kind != "fulltext" or src.metareader.flavor != "pdf":
        raise invalid("not_convertible", f"{pdf_name} is not a PDF full text")
    page_no = Counter(b.page for b in fig.boxes).most_common(1)[0][0]
    boxes = [b for b in fig.boxes if b.page == page_no]
    doc = pdfium.PdfDocument(str(pkg.path(src.path)))
    try:
        if page_no > len(doc):
            raise invalid("no_coordinates", f"{fig.id} is on page {page_no}, but {pdf_name} has {len(doc)} pages")
        page = doc[page_no - 1]
        w, h = page.get_size()
        image = page.render(scale=scale, crop=_crop_margins(boxes, w, h)).to_pil()
    finally:
        doc.close()
    rel = f"targets/figures/{target_id}.png"
    p = pkg.path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    image.save(tmp, format="PNG")
    tmp.replace(p)
    with pkg.edit() as m:
        res = pkg.put(m, pkg.describe(f"figure-{target_id.lower()}", rel, type="file", format="png", mediatype="image/png",
                                      meta=OtherMeta(kind="figure", added_at=now())))
    return {"resource": res, "target": target_id, "figure": f"{extraction} {fig.id} ({how})", "pdf": pdf_name, "page": page_no,
            "size": list(image.size)}


def crop_all(pkg: Package, pdf: str | None = None, scale: float = 2.0) -> list[dict[str, Any]]:
    out = []
    for t, _ in Curation(pkg, "targets").effective():
        if t.type != "figure":
            continue
        try:
            r = crop(pkg, t.id, pdf, scale)
            out.append({"target": t.id, "ok": True, "path": r["resource"].path, "page": r["page"]})
        except Exception as e:  # report per target; ApiError carries a readable message
            out.append({"target": t.id, "ok": False, "error": getattr(e, "message", str(e)), "code": getattr(e, "code", "internal_error")})
    return out
