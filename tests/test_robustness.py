"""Edge cases: the parser must never raise, and must explain failures."""
import shutil

from docparser import ParsedDocument
from docparser.model import Paragraph, Table, TableCell, render_markdown
from docparser.normalize import normalize_text
from docparser.parsers import DocxParser, PdfParser

from conftest import office_fixture


def test_missing_and_empty(registry, tmp_path):
    missing = registry.parse(tmp_path / "nope.pdf")
    assert not missing.success and "not found" in missing.error_message
    empty = tmp_path / "empty.docx"
    empty.write_bytes(b"")
    r = registry.parse(empty)
    assert not r.success and "empty" in r.error_message
    assert not DocxParser().parse(empty).success


def test_garbage_files_fail_gracefully(registry, tmp_path):
    for name, payload in [("junk.pdf", b"%PDF-1.7\n garbage \x00\x01"), ("junk.docx", b"PK\x03\x04broken"),
                          ("junk.xyz", b"hello")]:
        f = tmp_path / name
        f.write_bytes(payload)
        r = registry.parse(f)
        assert isinstance(r, ParsedDocument) and not r.success and r.error_message, name


def test_truncated_office_files(registry, tmp_path):
    for name in ("complex.docx", "complex.pptx", "complex.pdf", "complex.ppt", "complex.odt"):
        src = office_fixture(name)
        f = tmp_path / ("trunc_" + name)
        f.write_bytes(src.read_bytes()[: src.stat().st_size // 3])
        r = registry.parse(f)
        assert isinstance(r, ParsedDocument)  # success or clean failure, never an exception
        if not r.success:
            assert r.error_message


def test_encrypted_ooxml(registry, synth_dir):
    r = registry.parse(synth_dir / "encrypted.docx")
    assert not r.success and "encrypted" in r.error_message


def test_content_beats_extension(registry, tmp_path):
    wrong = tmp_path / "actually_docx.pdf"
    shutil.copy(office_fixture("complex.docx"), wrong)
    r = registry.parse(wrong)
    assert r.success and r.file_type == "docx"


def test_pdf_without_text_layer(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from PIL import Image
    img = tmp_path / "scan.png"
    Image.new("RGB", (200, 100), "white").save(img)
    pdf = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.drawImage(str(img), 72, 500, 200, 100)
    c.save()
    r = PdfParser().parse(pdf)
    assert r.success and r.metadata.get("needs_ocr_pages") == [1] and r.warnings


def test_normalize_text():
    raw = "Ａ b​ c�\x07  d\r\n\n\n\n e　f  \t g"
    assert normalize_text(raw, fix_mojibake=False) == "Ａ b c d\n\n e f g"
    assert normalize_text("한가") == "한가"  # NFC composes conjoining jamo


def test_markdown_table_rendering():
    simple = Table(cells=[TableCell(0, 0, [Paragraph("a|b")]), TableCell(0, 1, [Paragraph("c")]),
                          TableCell(1, 0, [Paragraph("1")]), TableCell(1, 1, [Paragraph("2")])])
    md = render_markdown([simple])
    assert md.splitlines()[0] == "| a\\|b | c |"
    merged = Table(cells=[TableCell(0, 0, [Paragraph("h")], colspan=2), TableCell(1, 0, [Paragraph("<x>")]),
                          TableCell(1, 1, [])])
    html = render_markdown([merged])
    assert '<td colspan="2">h</td>' in html and "&lt;x&gt;" in html
