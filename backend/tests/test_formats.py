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
    assert fmt.name == "pdf" and report.passed and fmt.parse is None
    assert "1 page(s)" in report.notes


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
