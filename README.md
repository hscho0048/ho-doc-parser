# ho doc parser (docparser)

PDF · DOCX · PPTX · PPT · ODT · HWPX · XLSX 문서에서 **본문 텍스트, 구조(제목/문단/목록/표), 메타데이터**를 추출하는
로컬 전용 파서입니다. 네트워크 호출 없음, 런타임 의존성은 모두 MIT/BSD/Apache 계열(카피레프트 없음 — [NOTICE](NOTICE)).

| 포맷 | 엔진 | 표(병합/중첩) | 다단 | 기타 |
|---|---|---|---|---|
| PDF | pdfplumber + 자체 레이아웃 분석 | 선 있는 표: rowspan/colspan ✅, 중첩 → 셀 텍스트로 평탄화 | ✅ 좌표 기반 읽기 순서 복원(2~3단, 단 혼합 페이지) | 머리글/바닥글 제거, 폰트 크기 기반 제목, 암호 PDF, 스캔 페이지 표시 |
| DOCX | python-docx(XML 직접 순회) | ✅ gridSpan/vMerge, 중첩 표 | ✅ (논리 순서) | 제목 스타일/개요수준, 목록, 텍스트상자, 각주, 변경추적 삭제분 제외 |
| PPTX | python-pptx | ✅ span | – | 좌표순 도형, 그룹, 차트 데이터→표, SmartArt, 노트 |
| PPT | olefile + [MS-PPT] 자체 구현 | ✗ (도형 텍스트로 추출) | – | Persist directory 기반 슬라이드 순서, 암호 감지 |
| ODT | odfpy | ✅ spanned/covered 셀, 중첩 | ✅ (논리 순서) | 제목 outline-level, 목록, 프레임, 각주 |
| HWPX | zipfile + defusedxml | ✅ cellAddr/cellSpan, 중첩, 캡션 | ✅ (논리 순서) | 각주, 글상자, 수식, 암호 감지 |
| XLSX | openpyxl | ✅ 병합 셀, 시트 안 여러 표 분리 | – | 시트별 제목, 표시 형식(천 단위·%·날짜), 수식 결과값, 셀 메모, 이미지, 숨김 시트 제외(옵션) |

**바로 써 보기 (설치 없음)**: https://hscho0048.github.io/ho-doc-parser/ — 파일을 끌어다 놓으면 브라우저 안에서 파싱합니다
(서버로 업로드하지 않음). 브라우저 버전은 OCR과 PDF의 JPEG 외 그림 추출이 빠지므로, 전체 기능은 아래처럼 로컬에 설치하세요.

## 로컬 설치

Python 3.9 이상이 필요합니다(3.12 권장). 저장소를 받은 뒤 가상환경을 만들어 설치합니다.

### macOS

```bash
git clone https://github.com/hscho0048/ho-doc-parser.git
```

```bash
cd ho-doc-parser
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

- Python이 없으면 `brew install python@3.12` (Homebrew) 또는 python.org 설치 파일을 쓰세요.
- 그림 속 글자 OCR과 GUI의 RAM·CPU 측정까지 쓰려면 위 대신 `pip install -r requirements-ocr.txt` (Apple Silicon 지원).
- 구형 PPT의 표까지 복원하려면 LibreOffice를 설치하세요(`brew install --cask libreoffice`, 선택).
- WMF/EMF 그림은 macOS에서 PNG로 변환되지 않고 원본 형식 그대로 저장됩니다.

### Windows (PowerShell)

```powershell
git clone https://github.com/hscho0048/ho-doc-parser.git
```

```powershell
cd ho-doc-parser
```

```powershell
py -3.12 -m venv .venv
```

```powershell
.venv\Scripts\Activate.ps1
```

```powershell
pip install -r requirements.txt
```

- 스크립트 실행이 막혀 있으면 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 후 다시 활성화하세요.
- OCR·GUI 측정까지: `pip install -r requirements-ocr.txt`.

### 실행 확인

```bash
python -m docparser --version
```

```bash
python -m docparser tests/fixtures/generated --out output -j 4
```

### 로컬 GUI (레퍼런스 실행기)

```bash
python tools/ho_gui/app.py
```

브라우저에서 http://127.0.0.1:8770 이 열립니다(이 PC에서만 접속). 파일을 끌어다 놓으면 파일마다 별도 프로세스로 실행해
**처리 시간 · 최대 RAM · 평균 코어 · CPU 시간**을 재고, 미리보기(표·이미지) / Markdown / JSON / 이미지(OCR 결과 포함) 탭으로 보여 줍니다.
옵션: OCR·스레드 수, 이미지 추출, DOCX 변경 추적·메모, XLSX 숨김 시트, PDF 암호. OCR은 `requirements-ocr.txt`로 설치했을 때 켜집니다.
업로드한 파일과 결과는 임시 폴더에만 두고 서버를 끄면 지웁니다.

## 사용법

```python
from docparser import DocumentParserRegistry

registry = DocumentParserRegistry.default()          # 매직 바이트 우선, 확장자 fallback
doc = registry.parse("보고서.docx")                   # 예외를 던지지 않음

if doc.success:
    print(doc.text)          # Markdown (병합·중첩 표는 HTML <table>)
    print(doc.metadata)      # title, author, page_count/slide_count, table_count, ...
    for table in doc.tables: # 구조화된 표 접근
        for c in table.cells:
            print(c.row, c.col, c.rowspan, c.colspan, c.text)
else:
    print(doc.error_message) # 예: "encrypted or protected document: PDF requires a password ..."
```

CLI / 예제:

```bash
python -m docparser 문서.pdf --json
```

Markdown + 이미지 폴더로 저장 (`<이름>.md`, `<이름>_images/img_001.png ...`, `<이름>.json`):

```bash
python -m docparser 보고서.docx 제안서.pdf --out output
```

### 여러 파일 한 번에 (병렬) + 결과 저장
파일과 폴더를 섞어서 넘길 수 있습니다. 폴더는 하위 폴더까지 지원 형식만 골라 처리합니다.
`-j/--jobs N`이면 N개 파일을 동시에(파일마다 별도 프로세스) 처리하고, `-j 0`은 CPU 코어 수만큼 씁니다.

```bash
python -m docparser 내문서폴더/ 추가파일.pdf --out output -j 4
```

- 파일마다 `output/<이름>.md`, `<이름>.json`(elements 형식), `<이름>_images/`가 저장됩니다.
- 이름이 같은 파일(`a.pdf`, `a.docx`)은 `a_pdf`, `a_docx`처럼 나눠 서로 덮어쓰지 않습니다.
- 여러 파일이면 `output/_summary.json`에 파일별 성공 여부·형식·오류·글자 수·이미지 수·처리 시간이 남습니다.
- 출력 순서는 입력 순서와 같고, 하나라도 실패하면 종료 코드 1입니다.
- `--ocr`와 함께 쓰면 프로세스마다 OCR 모델(약 0.5GB)을 따로 올리므로 `-j`는 작게 두세요.

### elements JSON 출력
`doc.to_elements()` / CLI `--out`의 `<이름>.json` / `--json`(기본 `--format elements`)은 Upstage Document Parse와 호환되는 구조입니다:
`{"elements": [{"id", "category", "page", "content": {"text","html","markdown"}}...], "content": {...}, "usage": {"pages"}, "metadata"}`.
category: `heading1`(level 포함), `paragraph`, `list`(연속 항목 묶음), `table`, `caption`, `figure`(image 경로·alt·OCR 텍스트), `footnote`.
`page`는 PDF/PPT/PPTX만 값이 있고, 쪽이 고정되지 않은 DOCX/ODT/HWPX/XLSX는 `null`.

### 그림 속 글자 OCR (선택, 로컬)
```bash
pip install rapidocr onnxruntime
```
```bash
python -m docparser 보고서.pdf --out output --ocr
```
RapidOCR(onnxruntime) + PaddleOCR PP-OCRv5 **mobile** 한국어 모델(검출 4.6MB + 인식 13MB). 같은 모델을 PaddleOCR로 돌리면
1.5GB / 이미지당 29초, PaddleOCR 기본 server/medium 모델은 8GB 이상 → 이 구성은 약 0.57GB / 3.6초(CPU 노트북 기준).
`--ocr-max-side 1280`으로 약 0.49GB까지 줄일 수 있으나 작은 글씨를 일부 놓칩니다. 결과는 그림 아래 인용문(`> ...`)과
figure 요소의 text, 표 셀 텍스트에 들어갑니다.

### 이미지
모든 포맷에서 본문 속 이미지를 **등장 위치 그대로** 추출합니다 (`doc.images`, Markdown `![설명](이름_images/img_001.png)`,
표 셀 안은 `<img>`). 같은 그림은 한 파일로 중복 제거, BMP/TIFF/WMF/EMF는 PNG로 변환(WMF/EMF 변환은 Windows의 Pillow만 지원),
대체 텍스트(alt)와 픽셀 크기 포함. `doc.save("out")`으로 md+이미지 저장, `extract_images=False`(CLI `--no-images`)로 끌 수 있음.
- HWPX: `hc:img` → manifest → `BinData/*`
- DOCX `a:blip`·VML, PPTX 그림 도형, PPT `Pictures` 스트림(BStore), ODT `draw:image`, XLSX 시트 그림
- PDF: 내장 JPEG은 원본 바이트 그대로, 그 외 형식은 해당 영역을 150dpi PNG로 렌더링(24pt 미만 아이콘·페이지 전체 배경 제외)

```bash
python examples/parse_file.py 문서.docx
```

`ParsedDocument` 필드: `text`, `file_type`, `metadata`, `success`, `error_message` (+ `blocks`: Heading/Paragraph/ListItem/Table/PageMarker, `warnings`).

옵션: `PdfParser(password=..., detect_tables=True, detect_columns=True, remove_headers_footers=True, max_pages=None)`,
`PptParser(use_libreoffice=True)` (설치돼 있으면 LibreOffice로 PPTX 변환 후 파싱 → 표까지 복원),
`HwpxParser(include_headers_footers=False)`, `XlsxParser(include_hidden=False, max_rows=5000)`, `DocxParser(include_footnotes=True, include_comments=True, revisions="accept")`.
DOCX 변경 추적·메모: 기본은 모든 변경을 수락한 상태로 추출하고 메타데이터 `revision_count`·경고로 알려 줍니다. `revisions="markup"`(CLI `--revisions markup`)이면 `{++추가++}` `{--삭제--}`, 메모는 `[^cN]: 메모(작성자, 날짜): 내용 — 대상: "…"`로 나옵니다(`--no-comments`로 제외).
`registry.register("pdf", lambda: PdfParser(password="..."), (".pdf",))` 로 교체/확장.

## 웹 버전 (GitHub Pages)

`site/`는 같은 레퍼런스 실행기를 [Pyodide](https://pyodide.org)(브라우저용 Python)로 돌리는 정적 페이지입니다.
`.github/workflows/pages.yml`이 main에 푸시될 때마다 `site/build_bundle.py`로 docparser를 묶어 배포합니다.

- 처음 열 때 Python 런타임과 의존성(약 20MB)을 받느라 수십 초 걸리고, 그 뒤에는 브라우저 캐시를 씁니다.
- 브라우저에서 빠지는 기능: 그림 속 글자 OCR(onnxruntime), PDF의 JPEG 외 그림(pypdfium2는 네이티브 라이브러리라 브라우저용 빌드가 없음), 처리 RAM·CPU 측정.
- 로컬에서 미리 보기:

```bash
python site/build_bundle.py
```

```bash
python -m http.server 8780 --directory site
```

## 성능 평가 (AI API 도입 판단용)

```bash
python bench/benchmark.py 내문서폴더/ --synthetic-pages 50 --out report.md
```

파일별 성공 여부, 처리 시간, pages/sec, 표 수(병합 표 수), 다단 페이지 수, OCR 필요 페이지, 읽기 순서 점수(문서에
`<<A01>>` 형태 표식이 있을 때)를 출력합니다. 현재 테스트 결과(Windows, Python 3.14):

| 문서 | 시간 | 표(병합) | 다단 | 읽기 순서 |
|---|---|---|---|---|
| Word 생성 DOCX/ODT (병합·중첩 표, 2단) | 0.02s | 2(1) | ✅ | 1.0 |
| Word 생성 PDF (병합 표, 2단, 각주) | 0.09s / 2p | 1(1) | 1p | 1.0 |
| 합성 50쪽 2단 PDF (표 38개) | 2.5s / 26p (≈10 p/s) | 38(36) | 26p | 1.0 |
| PowerPoint PPTX / PPT | <0.01s | ✅ / – | – | – |

**AI(또는 로컬 AI 모델)가 필요한 경우** — 아래에 해당하는 문서 비중이 크면 검토할 가치가 있습니다:
- 스캔 PDF(`metadata["needs_ocr_pages"]`) → 로컬 오픈소스 OCR: Tesseract(Apache-2.0), PaddleOCR(Apache-2.0)
- 선 없는(borderless) 표가 많은 PDF, 잡지형 비정형 레이아웃 → 로컬 AI 레이아웃: Docling(MIT, 모델 포함 로컬 실행)
- 차트/이미지 속 텍스트 의미 해석 → VLM API

Docling 같은 엔진은 `BaseParser`를 상속해 `registry.register("pdf", ...)`로 끼워 넣을 수 있습니다.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```

- `tests/fixtures/build_fixtures.py`: python-docx/pptx로 원본 생성 → **실제 Word/PowerPoint(COM)** 로 재저장해 DOCX·PDF·ODT·PPT 픽스처 생성
- `tests/fixtures/synth.py`: OLE(Compound File) 작성기 + HWPX 패키지 작성기 (암호 OOXML/HWPX 케이스 포함)
- 엣지 케이스: 빈 파일, 없는 파일, 잘린 파일, 쓰레기 바이트, 암호 PDF/DOCX/HWPX, 확장자 위장, 텍스트 레이어 없는 PDF

## 라이선스 · 고지

> 본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

- HWPX 파서는 한컴이 공개한 파일 형식 문서를 보고 직접 작성한 코드입니다. 한컴은 이를 바탕으로 만든 결과물의 정확성을 보증하지 않습니다.
- 사용한 오픈소스는 모두 MIT·BSD·Apache 계열입니다(odfpy는 Apache-2.0으로 사용). 전체 목록과 조건은 [NOTICE](NOTICE), 명령줄에서는 `python -m docparser --licenses`.
- OCR 옵션이 쓰는 opencv-python에는 FFmpeg(LGPL-2.1)가 들어 있습니다. 설치 파일·도커 이미지처럼 의존성을 함께 배포할 때의 조건은 [docs/LICENSES_ko.md](docs/LICENSES_ko.md)를 보세요.

## 알려진 한계
- HWPX: 스펙 기반 합성 파일(pytest) + 실제 한컴 생성 문서로 확인. 자동 문단 번호("1.", "가." 등)는 파일에 텍스트로 저장되지 않아 출력에 없음.
- 바이너리 HWP(.hwp)는 지원하지 않음(한/글에서 HWPX·DOCX·PDF로 저장).
- 구형 .doc, .xls 미지원(Office에서 .docx/.xlsx로 저장). PPT 바이너리의 표는 셀 구조 없이 텍스트로만 추출.
- XLSX: 차트는 추출하지 않음(그림은 추출). 숨김 시트는 기본 제외, 시트당 5000행까지(`max_rows`).
  수식은 엑셀이 마지막으로 저장한 결과값을 쓰며, 한 번도 계산되지 않은 파일은 수식 텍스트 + 경고.
- PDF: 선이 없는 표는 표로 인식하지 않음(텍스트 행으로 추출), 세로쓰기/회전 텍스트는 단순 처리, OCR 없음.
