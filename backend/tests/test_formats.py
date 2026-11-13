from pathlib import Path

from reprocrowd import formats
from reprocrowd.formats.builder import build_document
from reprocrowd.formats.segment import split_sentences
from reprocrowd.schemas.document import DocumentSource

from conftest import FIXTURES


def parse(name: str):
    fmt, report = formats.check(FIXTURES / name)
    assert report.passed, report.problems
    return build_document(fmt.parse(FIXTURES / name), DocumentSource(method="parse", format=fmt.name, fulltexts=["ft"]))


def test_tei():
    d = parse("paper.tei.xml")
    assert d.title == "A Test Paper"
    assert [(s.id, s.title, s.level) for s in d.sections] == [("sec1", "Abstract", 1), ("sec2", "Methods", 1), ("sec3", "Results", 1)]
    s = d.sections[1].paragraphs[0].sentences[0]
    assert (s.id, s.text) == ("sec2.p1.s1", "We ran a t-test on 40 participants.")
    assert s.boxes[0].page == 1 and s.boxes[0].x1 == 110
    assert [(f.id, f.label, f.section) for f in d.figures] == [("fig1", "Figure 1", "sec3")]
    t = d.tables[0]
    assert (t.id, t.label, t.section, t.rows) == ("tab1", "Table 1", "sec3", [["Group", "M"], ["A", "512.4"]])
    assert t.caption[0].id == "tab1.s1"


def test_jats_floats_inside_paragraphs_and_nesting():
    d = parse("paper.jats.xml")
    assert [(s.title, s.level) for s in d.sections] == [("Abstract", 1), ("Methods", 1), ("Sub", 2)]
    methods = [s.text for s in d.sections[1].paragraphs[0].sentences]
    assert methods == ["We ran a t-test on 40 participants (see Fig. 1).", "Data were cleaned first.", "Then we analysed them."]
    assert d.figures[0].section == "sec2" and d.figures[0].caption[0].text == "Reaction times."
    assert d.tables[0].section == "sec2" and d.tables[0].rows == [["Group", "M"], ["A", "512.4"]]
    assert [s.text for s in d.sections[2].paragraphs[0].sentences] == ["Nested text, e.g. here.", "Second sentence."]


def test_docling():
    d = parse("paper.docling.json")
    assert d.title == "A Test Paper"
    assert [s.title for s in d.sections] == ["Methods"]
    assert [[s.text for s in p.sentences] for p in d.sections[0].paragraphs] == [["We ran a test.", "It worked."], ["First item."]]
    assert d.sections[0].paragraphs[0].boxes[0].origin == "bottom"
    assert d.tables[0].label == "Table 1" and d.tables[0].rows[1] == ["A", "512.4"]


def test_pdf_check():
    fmt, report = formats.check(FIXTURES / "paper.pdf")
    assert fmt.name == "pdf" and report.passed and fmt.parse is not None
    assert "1 page(s)" in report.notes


def test_markdown(tmp_path: Path):
    p = tmp_path / "paper.md"
    p.write_text("# A Test Paper\n\n## Methods\n\nWe ran a t-test on 40 participants. Data were cleaned first.\n\n## Results\n\nIt was significant.\n", encoding="utf-8")
    fmt, report = formats.check(p, p.name)
    assert report.passed, report.problems
    d = build_document(fmt.parse(p), DocumentSource(method="parse", format=fmt.name, fulltexts=["ft"]))
    assert d.title == "A Test Paper"
    assert [s.title for s in d.sections] == ["Methods", "Results"]
    assert [s.text for s in d.sections[0].paragraphs[0].sentences] == ["We ran a t-test on 40 participants.", "Data were cleaned first."]


def test_docx(tmp_path: Path):
    import docx

    d = docx.Document()
    d.add_heading("A Test Paper", 0)
    d.add_heading("Methods", level=1)
    d.add_paragraph("We ran a t-test on 40 participants. Data were cleaned first.")
    d.add_heading("Results", level=1)
    d.add_paragraph("It was significant.")
    p = tmp_path / "paper.docx"
    d.save(str(p))
    fmt, report = formats.check(p, p.name)
    assert report.passed, report.problems
    doc = build_document(fmt.parse(p), DocumentSource(method="parse", format=fmt.name, fulltexts=["ft"]))
    assert doc.title == "A Test Paper"
    assert [s.title for s in doc.sections] == ["Methods", "Results"]
    # without a Title style, the first Heading 1 stays a section
    d2 = docx.Document()
    d2.add_heading("Introduction", level=1)
    d2.add_paragraph("Some text.")
    d2.save(str(p))
    assert [s.title for s in fmt.parse(p).sections] == ["Introduction"]
    assert [s.text for s in doc.sections[0].paragraphs[0].sentences] == ["We ran a t-test on 40 participants.", "Data were cleaned first."]


def _text_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one line of Helvetica text per page."""
    n = len(pages)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n)) + b"] /Count %d >>" % n,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode() + b") Tj ET"
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i))
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_pdf_parse(tmp_path: Path):
    p = tmp_path / "paper.pdf"
    p.write_bytes(_text_pdf(["We ran a t-test on 40 participants.", "The effect was significant."]))
    fmt, report = formats.check(p, p.name)
    assert report.passed, report.problems
    out = build_document(fmt.parse(p), DocumentSource(method="parse", format=fmt.name, fulltexts=["ft"]))
    sents = [s.text for sec in out.sections for para in sec.paragraphs for s in para.sentences]
    assert "We ran a t-test on 40 participants." in sents
    assert "The effect was significant." in sents


def test_rejects(tmp_path: Path):
    cases = {
        "broken.xml": b"<TEI><text><body><p>unclosed</body></TEI>",
        "other.xml": b"<html><body>hi</body></html>",
        "fake.pdf": b"%PDF-1.4\nthis is not a pdf",
        "notdocling.json": b'{"schema_name": "DoclingDocument", "texts": []}',
        "empty.xml": b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/></TEI>',
    }
    for name, data in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        _, report = formats.check(p)
        assert not report.passed, name


def test_xml_entities_not_resolved(tmp_path: Path):
    p = tmp_path / "xxe.xml"
    p.write_text('<?xml version="1.0"?><!DOCTYPE TEI [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                 '<TEI><text><body><div><head>H</head><p>&x; text.</p></div></body></text></TEI>')
    fmt, report = formats.check(p)
    if report.passed:
        draft = fmt.parse(p)
        assert "root:" not in str(draft.model_dump())


def test_segmenter():
    assert split_sentences("See Fig. 2 for details. The mean was 3.5 (SD = 1.2). Next one!") == [
        "See Fig. 2 for details.", "The mean was 3.5 (SD = 1.2).", "Next one!"]
    assert split_sentences("Smith et al. found it. Then we did.") == ["Smith et al. found it.", "Then we did."]
