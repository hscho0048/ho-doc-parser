"""End-to-end tests per format. Office fixtures come from real Word/PowerPoint output."""
from docparser import Heading, ListItem
from docparser.model import Table
from docparser.parsers import PdfParser

from conftest import markers, office_fixture

EXPECTED_ORDER = [f"A{i:02d}" for i in range(1, 13)] + ["Z01"]


def _find_table(doc, predicate=lambda t: True):
    tables = [b for b in doc.blocks if isinstance(b, Table) and predicate(b)]
    assert tables, "no table found"
    return tables[0]


def _cell(table, row, col):
    return next(c for c in table.cells if c.row == row and c.col == col)


def _assert_budget_table(doc, nested=True):
    t = _find_table(doc, lambda t: "구분" in t.cells[0].text)
    assert (_cell(t, 0, 0).rowspan, _cell(t, 0, 0).text) == (2, "구분")
    assert (_cell(t, 0, 1).colspan, _cell(t, 0, 1).text.replace(" ", "")) == (2, "2025년")
    assert _cell(t, 0, 3).rowspan == 2
    assert _cell(t, 2, 1).text == "1,200"
    host = _cell(t, 3, 3)
    assert "세부내역" in host.text
    if nested:
        inner = [b for b in host.blocks if isinstance(b, Table)]
        assert inner and _cell(inner[0], 1, 1).text == "640"
    else:
        assert "640" in host.text
    assert 'rowspan="2"' in doc.text and 'colspan="2"' in doc.text


# ---------------------------------------------------------------------- DOCX / ODT


def test_docx(registry):
    doc = registry.parse(office_fixture("complex.docx"))
    assert doc.success, doc.error_message
    assert doc.file_type == "docx"
    assert doc.metadata["title"] == "문서 파서 테스트 보고서"
    assert doc.metadata["column_layout"] == 2
    assert any(isinstance(b, Heading) and b.text == "1. 예산 현황" and b.level == 2 for b in doc.blocks)
    assert [b.text for b in doc.blocks if isinstance(b, ListItem)] == ["첫째 항목", "둘째 항목"]
    _assert_budget_table(doc)
    assert markers(doc.text) == EXPECTED_ORDER
    assert "[^1]" in doc.text and "각주 본문: 2025년 기준 자료" in doc.text


def test_odt(registry):
    doc = registry.parse(office_fixture("complex.odt"))
    assert doc.success, doc.error_message
    assert doc.file_type == "odt"
    _assert_budget_table(doc)
    assert markers(doc.text) == EXPECTED_ORDER
    assert any(isinstance(b, Heading) and "결론" in b.text for b in doc.blocks)
    assert "각주 본문" in doc.text


# ---------------------------------------------------------------------- PDF


def test_pdf_word_two_columns_and_merged_table(registry):
    doc = registry.parse(office_fixture("complex.pdf"))
    assert doc.success, doc.error_message
    assert doc.metadata["page_count"] == 2
    assert doc.metadata["multi_column_pages"] >= 1
    assert markers(doc.text) == EXPECTED_ORDER          # reading order across the 2 columns
    _assert_budget_table(doc, nested=False)              # nested table flattens into the host cell in PDF
    assert "표, 병합 셀" in doc.text                      # no stray space before CJK punctuation
    assert "흔히 쓰이는 배치 방식으로" in doc.text          # lines of one paragraph are joined
    conclusion = doc.text.index("결론")
    assert doc.text.index("<<A12>>") < conclusion < doc.text.index("<<Z01>>")


def test_pdf_three_columns(registry, synth_dir):
    doc = registry.parse(synth_dir / "three_col.pdf")
    assert doc.success
    found = markers(doc.text)
    assert found == sorted(found) and len(found) >= 10, found
    assert doc.text.index("Three Column Layout Report") < doc.text.index("<<B01>>")


def test_pdf_borderless_form_keeps_row_order(registry, synth_dir):
    doc = registry.parse(synth_dir / "form.pdf")
    assert "Name: John Smith" in doc.text
    assert doc.text.index("Department: Research") < doc.text.index("Phone: 555-0100")


def test_pdf_password(synth_dir):
    locked = PdfParser().parse(synth_dir / "secret.pdf")
    assert not locked.success and "encrypted" in locked.error_message
    opened = PdfParser(password="s3cret").parse(synth_dir / "secret.pdf")
    assert opened.success and "Top secret content" in opened.text


# ---------------------------------------------------------------------- PPTX / PPT


def test_pptx(registry):
    doc = registry.parse(office_fixture("complex.pptx"))
    assert doc.success
    assert doc.metadata["slide_count"] == 3
    assert "Slide 1: 분기 실적 요약" in doc.text
    assert any(isinstance(b, ListItem) and b.level == 1 and b.text == "신규 고객 340곳" for b in doc.blocks)
    t = _find_table(doc, lambda t: "지역 합계" in t.cells[0].text)
    assert _cell(t, 0, 0).colspan == 2
    assert "Grouped text A" in doc.text and "Grouped text B" in doc.text
    assert "| Q1 | 10.5 |" in doc.text                     # chart data as a table
    assert "발표자 노트" in doc.text


def test_ppt_binary(registry):
    doc = registry.parse(office_fixture("complex.ppt"))
    assert doc.success, doc.error_message
    assert doc.file_type == "ppt"
    assert doc.metadata["slide_count"] == 3
    for s in ("Slide 1: 분기 실적 요약", "매출 12% 증가", "신규 고객 340곳", "Slide 2: 지역별 표",
              "서울", "Grouped text B"):
        assert s in doc.text
    assert doc.text.index("Slide 1") < doc.text.index("Slide 2") < doc.text.index("Slide 3")


# ---------------------------------------------------------------------- HWPX


def test_hwpx(registry, synth_dir):
    doc = registry.parse(synth_dir / "sample.hwpx")
    assert doc.success, doc.error_message
    assert doc.file_type == "hwpx"
    assert doc.metadata["title"] == "HWPX 테스트" and doc.metadata["author"] == "홍길동"
    assert doc.blocks[0] == Heading("HWPX 보고서", 1)
    assert "첫 문단 탭 뒤\n둘째 줄[^1]" in doc.text and "[^1]: 각주 내용" in doc.text
    t = _find_table(doc)
    assert t.caption == "표 1. 병합 표"
    assert _cell(t, 0, 0).colspan == 2 and _cell(t, 0, 2).rowspan == 3
    inner = [b for b in _cell(t, 2, 1).blocks if isinstance(b, Table)]
    assert inner and _cell(inner[0], 0, 1).text == "내부2"
    assert "글상자 텍스트" in doc.text and "[수식: a over b]" in doc.text
    assert "마지막 문단 & 특수문자" in doc.text
    enc = registry.parse(synth_dir / "encrypted.hwpx")
    assert not enc.success and "encrypted" in enc.error_message


# ---------------------------------------------------------------------- images


def test_images_all_formats(registry, synth_dir, tmp_path):
    for name in ("complex.docx", "complex.odt", "complex.pptx", "complex.ppt", "complex.pdf"):
        doc = registry.parse(office_fixture(name))
        assert doc.images, name
        assert all(i.data and i.name.startswith("img_") for i in doc.images), name
        assert f"complex_images/{doc.images[0].name}" in doc.text, name
    docx = registry.parse(office_fixture("complex.docx"))
    assert "![예산 차트](complex_images/img_001.png)" in docx.text
    assert (docx.images[0].width, docx.images[0].height) == (400, 240)
    # the picture sits between the budget table and the bullet list
    assert docx.text.index("</table>") < docx.text.index("![예산") < docx.text.index("- 첫째 항목")

    hwpx = registry.parse(synth_dir / "sample.hwpx")
    assert "![로고 그림](sample_images/img_001.png)" in hwpx.text

    md = hwpx.save(tmp_path, "sample")
    assert md.read_text(encoding="utf-8") == hwpx.text
    assert (tmp_path / "sample_images" / "img_001.png").read_bytes() == hwpx.images[0].data

    no_img = registry.__class__.default(extract_images=False).parse(synth_dir / "sample.hwpx")
    assert no_img.success and not no_img.images and "img_" not in no_img.text



# ---------------------------------------------------------------------- Upstage-style output / OCR helpers


def test_elements_output(registry):
    doc = registry.parse(office_fixture("complex.docx"))
    out = doc.to_elements()
    assert set(out) >= {"elements", "content", "usage", "metadata"}
    cats = [e["category"] for e in out["elements"]]
    for c in ("heading1", "paragraph", "table", "figure", "list", "footnote"):
        assert c in cats, c
    assert [e["id"] for e in out["elements"]] == list(range(len(cats)))
    assert all(set(e["content"]) == {"text", "html", "markdown"} for e in out["elements"])
    assert all(e["page"] is None for e in out["elements"])          # DOCX has no fixed pages
    heading = next(e for e in out["elements"] if e["category"] == "heading1")
    assert heading["content"]["html"].startswith("<h") and "level" in heading
    lst = next(e for e in out["elements"] if e["category"] == "list")
    assert lst["content"]["text"] == "첫째 항목\n둘째 항목"           # consecutive items -> one element
    fig = next(e for e in out["elements"] if e["category"] == "figure")
    assert fig["image"] == "complex_images/img_001.png" and fig["alt"] == "예산 차트"
    assert out["content"]["markdown"] == doc.text

    pdf = registry.parse(office_fixture("complex.pdf")).to_elements()
    assert {e["page"] for e in pdf["elements"]} == {1, 2} and pdf["usage"]["pages"] == 2


def test_ocr_text_flows_into_outputs(registry, synth_dir):
    from docparser.ocr import lines_to_text, ocr_document
    boxes = [([[60, 10], [90, 10], [90, 20], [60, 20]], "world", 0.9),
             ([[0, 12], [50, 12], [50, 22], [0, 22]], "hello", 0.9),
             ([[0, 40], [40, 40], [40, 50], [0, 50]], "next", 0.9)]
    assert lines_to_text(boxes) == "hello world\nnext"

    doc = registry.parse(office_fixture("complex.docx"))
    ocr_document(doc, lambda data: [([[0, 0], [10, 0], [10, 10], [0, 10]], "그림 속 글자", 0.99)], min_px=0)
    assert "그림 속 글자" in doc.text
    assert doc.metadata["ocr_images"] == len(doc.images)


# ---------------------------------------------------------------------- DOCX tracked changes / comments


def test_docx_tracked_changes_and_comments(registry):
    from docparser.parsers import DocxParser
    path = office_fixture("tracked.docx")          # real Word file: track changes on + 2 comments

    doc = registry.parse(path)                     # default: as if every change were accepted
    assert doc.success
    assert "[추적 삽입] 검토 의견을 반영한 문장입니다." in doc.text   # inserted sentence kept
    assert "중첩 표" not in doc.text                                  # deleted words dropped
    t = next(b for b in doc.tables if "구분" in b.cells[0].text)
    assert next(c for c in t.cells if c.row == 2 and c.col == 1).text == "1,250"   # replaced in a cell
    assert "둘째 항목" not in doc.text                                # deleted paragraph dropped
    assert doc.metadata["revision_count"] == 5 and doc.metadata["comment_count"] == 2
    assert doc.warnings and "accepted" in doc.warnings[0]

    assert "## 1. 예산 현황[^c2]" in doc.text and "760[^c5]" in doc.text  # comment anchors
    assert '[^c2]: 메모(검토자, ' in doc.text and '— 대상: "예산 현황"' in doc.text
    assert '하반기 운영비 근거 자료 첨부 필요 — 대상: "760"' in doc.text
    comments = [e for e in doc.to_elements()["elements"] if e["category"] == "comment"]
    assert len(comments) == 2

    marked = DocxParser(revisions="markup").parse(path)
    assert "{--중첩 표, --}" in marked.text
    assert "{++ [추적 삽입] 검토 의견을 반영한 문장입니다.++}" in marked.text
    assert "{++1,250++}{--1,200--}" in marked.text
    assert "{--둘째 항목--}" in marked.text

    bare = DocxParser(include_comments=False).parse(path)
    assert "메모(" not in bare.text and "[^c" not in bare.text and bare.metadata["comment_count"] == 0


# ---------------------------------------------------------------------- XLSX


def test_xlsx(registry):
    from docparser.parsers import XlsxParser
    path = office_fixture("complex.xlsx")          # built with openpyxl, recalculated + saved by Excel
    doc = registry.parse(path)
    assert doc.success and doc.file_type == "xlsx"
    assert doc.metadata["sheets"] == ["예산", "원자료"] and doc.metadata["sheet_count"] == 3
    assert "## 예산" in doc.text and "## 원자료" in doc.text
    assert "2025년 예산 집행 현황" in doc.text and "※ 단위: 원" in doc.text   # single-cell rows -> paragraphs
    t = next(b for b in doc.tables if "구분" in b.cells[0].text)
    assert t.n_cols == 5 and t.n_rows == 5
    cell = {(c.row, c.col): c for c in t.cells}
    assert (cell[(0, 0)].rowspan, cell[(0, 1)].colspan, cell[(0, 3)].rowspan) == (2, 2, 2)
    assert cell[(2, 1)].text == "1,200,000"                     # thousands separator
    assert cell[(2, 3)].text == "12.5%" and cell[(3, 3)].text == "-5.0%"   # formula results, % format
    assert cell[(4, 1)].text == "2,000,000"                     # =SUM(...) result
    assert "| 작성일 | 2025-03-31 |" in doc.text                 # second table on the same sheet, date
    assert "| 1월 | 1,520.50 | 8.1% |" in doc.text
    assert "[^c:예산!C6]: 메모(검토자): 하반기 운영비 근거 자료 첨부 필요" in doc.text
    assert len(doc.images) == 1
    assert "숨겨진" not in doc.text and any("hidden" in w for w in doc.warnings)
    assert "숨겨진 시트 내용" in XlsxParser(include_hidden=True).parse(path).text


def test_xlsx_without_saved_formula_results(tmp_path):
    import build_fixtures
    from docparser.parsers import XlsxParser
    path = tmp_path / "never_opened_in_excel.xlsx"
    build_fixtures.build_xlsx(path)                 # openpyxl writes formulas without cached results
    doc = XlsxParser().parse(path)
    assert doc.success and "=SUM(B5:B6)" in doc.text
    assert any("no saved result" in w for w in doc.warnings)


# ---------------------------------------------------------------------- flattened PPTX (slides as pictures)


def test_flattened_pptx_detected(registry):
    for name in ("flat_picture.pptx", "flat_background.pptx"):
        doc = registry.parse(office_fixture(name))
        assert doc.success
        assert doc.metadata["image_only_slides"] == [1, 2, 3], name
        assert len(doc.images) == 3, name                      # background pictures extracted too
        assert any("--ocr" in w for w in doc.warnings), name
    normal = registry.parse(office_fixture("complex.pptx"))     # slides with real text: no warning
    assert normal.metadata.get("image_only_slides") in (None, []) and not normal.warnings


def test_flattened_pptx_ocr(registry):
    pytest = __import__("pytest")
    pytest.importorskip("rapidocr")
    from docparser.ocr import RapidOcrEngine, ocr_document
    doc = registry.parse(office_fixture("flat_background.pptx"))
    ocr_document(doc, RapidOcrEngine(threads=2))
    flat = doc.text.replace(" ", "")
    for word in ("분기실적요약", "지역합계", "서울120증가", "GroupedtextA"):
        assert word in flat
