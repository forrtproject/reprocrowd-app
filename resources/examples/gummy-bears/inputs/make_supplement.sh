#!/usr/bin/env bash
# Typeset figures/supplement.md as a PDF and as a Word document.
#
# Session G, 2026-08-29. make_figures.py writes supplement.md; this turns that
# same file into two readable documents without touching its content. Run it
# after make_figures.py, from the project root.
#
#   bash analysis/make_supplement.sh
#
# Writes figures/supplement.{pdf,docx}, and copies both into supplement/ when
# that folder exists (it does in the upload archive, not in the project).
#
# Requires pandoc; the PDF additionally needs xelatex. Two things are not
# incidental. The PDF font: the LaTeX default has no pi and no prime glyph, and
# Table S14 is built entirely on pi, so the default would silently drop the
# character the table rests on. And the page orientation: the widest table runs
# to nine columns, so both outputs are landscape.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp "$ROOT/figures/supplement.md" "$WORK/"
cp "$ROOT"/figures/figS*.png "$WORK/"

# Replace the "path.pdf, path.png" pointer lines with the image itself, so the
# three supplementary figures appear in the document rather than as file names.
sed -i 's|`figures/figS\([0-9]\)_[a-z-]*\.pdf`, `figures/\(figS[0-9]_[a-z-]*\.png\)`|\n![](\2)\n|' \
    "$WORK/supplement.md"

cd "$WORK"

# ---- PDF -------------------------------------------------------------------
pandoc supplement.md -o supplement.pdf \
    --pdf-engine=xelatex \
    -V geometry:margin=1.6cm \
    -V geometry:landscape \
    -V fontsize=8pt \
    -V mainfont="DejaVu Serif" \
    -V monofont="DejaVu Sans Mono"

# ---- DOCX ------------------------------------------------------------------
# pandoc has no landscape switch for docx, so patch the section properties of
# its own default reference document and hand that back to it.
pandoc --print-default-data-file reference.docx > ref.docx
python3 - <<'PY'
import zipfile
from lxml import etree
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
q = lambda t: f"{{{W}}}{t}"
with zipfile.ZipFile("ref.docx") as z:
    names = z.namelist()
    data = {n: z.read(n) for n in names}
doc = etree.fromstring(data["word/document.xml"])
sect = doc.find(f".//{q('sectPr')}")
pg = sect.find(q("pgSz"))
if pg is None:
    pg = etree.Element(q("pgSz")); sect.insert(0, pg)
pg.set(q("w"), "16838"); pg.set(q("h"), "11906")   # A4 landscape, twips
pg.set(q("orient"), "landscape")
mar = sect.find(q("pgMar"))
if mar is None:
    mar = etree.Element(q("pgMar")); sect.insert(1, mar)
for k, v in (("top", "907"), ("bottom", "907"), ("left", "907"),
             ("right", "907"), ("header", "454"), ("footer", "454"),
             ("gutter", "0")):
    mar.set(q(k), v)                                # 1.6 cm margins
data["word/document.xml"] = etree.tostring(
    doc, xml_declaration=True, encoding="UTF-8", standalone=True)
with zipfile.ZipFile("ref_landscape.docx", "w", zipfile.ZIP_DEFLATED) as z:
    for n in names:
        z.writestr(n, data[n])
PY
pandoc supplement.md -o supplement.docx --reference-doc=ref_landscape.docx

# ---- install ---------------------------------------------------------------
for ext in pdf docx; do
    cat "supplement.$ext" > "$ROOT/figures/supplement.$ext"
    if [ -d "$ROOT/supplement" ]; then
        cat "supplement.$ext" > "$ROOT/supplement/supplement.$ext"
    fi
done
if [ -d "$ROOT/supplement" ]; then
    echo "wrote supplement.pdf and supplement.docx into figures/ and supplement/"
else
    echo "wrote figures/supplement.pdf and figures/supplement.docx"
fi

# ---- verify both outputs ---------------------------------------------------
python3 - "$ROOT/figures/supplement.pdf" "$ROOT/figures/supplement.docx" <<'PY'
import re, sys, zipfile
from pypdf import PdfReader
import docx
from docx.oxml.ns import qn


def check(label, squashed, extra=""):
    missing = [f"S{i}" for i in range(1, 15) if f"TableS{i}." not in squashed]
    assert not missing, f"{label}: missing tables {missing}"
    assert all(f"FigureS{i}." in squashed for i in (1, 2, 3)), \
        f"{label}: missing a supplementary figure"
    assert "π" in squashed, f"{label}: pi glyph did not render"
    assert "p<.0001" not in squashed, f"{label}: stale p threshold"
    print(f"  {label}: Tables S1 to S14, Figures S1 to S3, pi renders, "
          f"no stale p threshold{extra}")


r = PdfReader(sys.argv[1])
box = r.pages[0].mediabox
assert box.width > box.height, "PDF is not landscape"
check("supplement.pdf ",
      re.sub(r"\s+", "", " ".join((p.extract_text() or "") for p in r.pages)),
      f", {len(r.pages)} pages, landscape")

d = docx.Document(sys.argv[2])
allp = d.element.body.findall(".//" + qn("w:p"))
text = " ".join("".join(t.text or "" for t in p.findall(".//" + qn("w:t")))
                for p in allp)
sect = d.element.body.find(".//" + qn("w:sectPr"))
pg = sect.find(qn("w:pgSz"))
assert int(pg.get(qn("w:w"))) > int(pg.get(qn("w:h"))), "DOCX is not landscape"
imgs = [n for n in zipfile.ZipFile(sys.argv[2]).namelist()
        if n.startswith("word/media/")]
assert len(imgs) == 3, f"DOCX should embed 3 figures, found {len(imgs)}"
check("supplement.docx",
      re.sub(r"\s+", "", text),
      f", {len(d.tables)} Word tables, 3 embedded figures, landscape")
PY
