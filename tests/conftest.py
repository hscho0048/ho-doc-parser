import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from docparser import DocumentParserRegistry  # noqa: E402
import synth  # noqa: E402

GENERATED = ROOT / "tests" / "fixtures" / "generated"


@pytest.fixture(scope="session")
def registry():
    return DocumentParserRegistry.default()


def office_fixture(name: str) -> Path:
    """Fixtures produced by Word/PowerPoint (tests/fixtures/build_fixtures.py)."""
    p = GENERATED / name
    if not p.exists():
        pytest.skip(f"{name} not generated - run tests/fixtures/build_fixtures.py on Windows with Office")
    return p


def markers(text: str):
    return re.findall(r"<<([A-Z]\d\d)>>", text)


# ---------------------------------------------------------------------- synthetic images


def png_bytes() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 16), "#3b6fd8").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def synth_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    synth.write_cfb(d / "encrypted.docx", {"EncryptionInfo": b"\x04\x00\x04\x00", "EncryptedPackage": b"x"})

    sec = synth.hwpx_section([
        synth.hwpx_p(synth.hwpx_t("HWPX 보고서"), style=1),
        synth.hwpx_p('<hp:run charPrIDRef="0"><hp:t>첫 문단<hp:tab/>탭 뒤<hp:lineBreak/>둘째 줄</hp:t>'
                     '<hp:ctrl><hp:footNote><hp:subList>' + synth.hwpx_p(synth.hwpx_t("각주 내용")) +
                     '</hp:subList></hp:footNote></hp:ctrl></hp:run>'),
        synth.hwpx_p('<hp:run><hp:ctrl><hp:colPr type="NEWSPAPER" colCount="2"/></hp:ctrl></hp:run>',
                     synth.hwpx_tbl(3, 3, [
                         [synth.hwpx_tc(0, 0, synth.hwpx_p(synth.hwpx_t("병합 제목")), cs=2),
                          synth.hwpx_tc(0, 2, synth.hwpx_p(synth.hwpx_t("비고")), rs=3)],
                         [synth.hwpx_tc(1, 0, synth.hwpx_p(synth.hwpx_t("A"))),
                          synth.hwpx_tc(1, 1, synth.hwpx_p(synth.hwpx_t("B")))],
                         [synth.hwpx_tc(2, 0, synth.hwpx_p(synth.hwpx_t("C"))),
                          synth.hwpx_tc(2, 1, synth.hwpx_p(synth.hwpx_tbl(1, 2, [[
                              synth.hwpx_tc(0, 0, synth.hwpx_p(synth.hwpx_t("내부1"))),
                              synth.hwpx_tc(0, 1, synth.hwpx_p(synth.hwpx_t("내부2")))]])))],
                     ], caption="표 1. 병합 표")),
        synth.hwpx_p('<hp:run><hp:rect><hp:drawText><hp:subList>' + synth.hwpx_p(synth.hwpx_t("글상자 텍스트")) +
                     '</hp:subList></hp:drawText></hp:rect></hp:run>'),
        synth.hwpx_p('<hp:run><hp:equation><hp:script>a over b</hp:script></hp:equation></hp:run>'),
        synth.hwpx_p(synth.hwpx_t("마지막 문단 &amp; 특수문자")),
        synth.hwpx_p('<hp:run><hp:pic><hp:shapeComment>로고 그림</hp:shapeComment>'
                     '<hc:img binaryItemIDRef="image1"/></hp:pic></hp:run>'),
    ])
    synth.build_hwpx(d / "sample.hwpx", sec, images={"image1": png_bytes()})
    synth.build_hwpx(d / "encrypted.hwpx", sec, encrypted=True)
    _build_pdfs(d)
    return d


# ---------------------------------------------------------------------- reportlab PDFs


def _build_pdfs(d: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfgen import canvas
    from reportlab.platypus import BaseDocTemplate, Frame, FrameBreak, PageTemplate, Paragraph, Spacer

    styles = getSampleStyleSheet()
    W, H = A4
    m = 50
    title_frame = Frame(m, H - m - 60, W - 2 * m, 60, id="title")
    gap = 18
    cw = (W - 2 * m - 2 * gap) / 3
    cols = [Frame(m + i * (cw + gap), m + 40, cw, H - 2 * m - 100, id=f"c{i}") for i in range(3)]
    doc = BaseDocTemplate(str(d / "three_col.pdf"), pagesize=A4)
    doc.addPageTemplates([PageTemplate(id="p", frames=[title_frame] + cols)])
    filler = ("The quick brown fox jumps over the lazy dog while the analyst keeps reading the column "
              "text in order. ")
    story = [Paragraph("Three Column Layout Report", styles["Title"]), FrameBreak()]
    for i in range(1, 19):
        story.append(Paragraph(f"&lt;&lt;B{i:02d}&gt;&gt; " + filler * 2, styles["BodyText"]))
        story.append(Spacer(1, 4))
    doc.build(story)

    c = canvas.Canvas(str(d / "form.pdf"), pagesize=A4)
    c.setFont("Helvetica", 11)
    rows = [("Name:", "John Smith"), ("Department:", "Research"), ("Phone:", "555-0100"),
            ("Email:", "john@example.com"), ("Start date:", "2025-01-02")]
    for k, (a, b) in enumerate(rows):
        y = H - 100 - k * 18
        c.drawString(72, y, a)
        c.drawString(300, y, b)
    c.save()

    c = canvas.Canvas(str(d / "secret.pdf"), pagesize=A4, encrypt="s3cret")
    c.setFont("Helvetica", 12)
    c.drawString(72, H - 100, "Top secret content")
    c.save()
