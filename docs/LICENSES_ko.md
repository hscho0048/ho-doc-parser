# ho 최종 라이선스 정리

확인일 2026-10-08 · 대상: ho(docparser) 본체, 선택 OCR 기능, 개발·비교용 도구

## 한눈에 보기

- 사용한 오픈소스는 모두 허용형(MIT·BSD·Apache·PSF)입니다. GPL·AGPL은 없어서 **상업 사용, 사내 배포, 웹 서비스 모두 가능**합니다.
- 꼭 지켜야 할 것은 세 가지입니다.
  1. **HWP 고지 문구**(한컴 조건, HWPX 파서): 화면·매뉴얼·도움말·소스 코드에 정해진 문장 표시
  2. **오픈소스 고지**: 의존성을 함께 배포할 때 각 라이선스 전문 동봉
  3. **FFmpeg(LGPL-2.1)**: OCR 기능을 의존성째 배포할 때만 해당 (opencv-python 안에 포함)
- 바이너리 HWP(.hwp) 파서는 제거했고, HWPX 파서만 남아 있습니다.

## 1. 파일 형식 규격

### HWPX (한컴 공개 문서)

ho의 HWPX 파서는 한컴이 공개한 파일 형식 문서를 보고 직접 작성한 코드입니다. HWPX는 국가표준 KS X 6101(OWPML)이지만,
형식 지식이 한컴 공개 문서([다운로드 페이지](https://store.hancom.com/etc/hwpDownload.do))에서 왔으므로 한컴 조건을 따릅니다.

| 조건 | 내용 | ho에 해당하는 점 |
|---|---|---|
| 원본 그대로 배포 | 공개 문서 자체는 수정하지 않은 원본으로만 배포 | 공개 문서 PDF는 저장소에 넣지 않음 |
| 고지 문구 표시 | 아래 문장을 **사용자 화면, 매뉴얼, 도움말, 소스 코드**에 표시 | 아래 표처럼 적용 |
| 보증 없음 | 한컴은 이를 바탕으로 만든 결과물의 정확성을 보증하지 않음 | NOTICE에 명시 |

> 본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.

| 표시 위치 | 적용한 곳 |
|---|---|
| 소스 코드 | `docparser/parsers/hwpx.py` 상단, `docparser.HWP_NOTICE` 상수 |
| 사용자 화면 | 로컬 GUI·웹 버전 왼쪽 아래, 명령줄 `--help`·`--licenses` |
| 매뉴얼 | README "라이선스 · 고지" 절 |

암호가 걸린 HWPX는 해제하지 않고 실패로 반환합니다.

### 그 밖의 형식

- 구형 PPT(.ppt)는 Microsoft 공개 규격([MS-PPT])으로 구현했습니다. 표시 의무는 없고 NOTICE에 출처만 적었습니다.

## 2. 오픈소스 라이선스 목록

### ho 본체 (항상 설치)

| 패키지 | 라이선스 | 비고 |
|---|---|---|
| pdfplumber, pdfminer.six | MIT | PDF |
| pypdfium2 (+ PDFium) | BSD-3-Clause / Apache-2.0 | PDFium 안의 FreeType·libjpeg-turbo·OpenJPEG 등도 허용형, 패키지에 전문 포함 |
| pillow | MIT-CMU (HPND) | 이미지 |
| charset-normalizer | MIT | |
| cryptography (cffi, pycparser) | Apache-2.0 또는 BSD-3 (MIT-0, BSD-3) | 암호 PDF |
| python-docx | MIT | DOCX |
| python-pptx (XlsxWriter) | MIT (BSD-2) | PPTX |
| odfpy | **Apache-2.0로 사용** | Apache-2.0 / GPL / LGPL 중 선택 가능한 3중 라이선스 → 고지에 "Apache-2.0로 사용" 명시 |
| olefile | BSD-2 | PPT |
| openpyxl, et-xmlfile | MIT | XLSX |
| lxml | BSD-3 | |
| defusedxml, typing_extensions | PSF-2.0 | HWPX XML 안전 파싱 |

### OCR 기능 (`--ocr`, 선택 설치)

| 패키지 | 라이선스 | 비고 |
|---|---|---|
| rapidocr | Apache-2.0 | |
| PP-OCRv5 mobile 모델 (검출 + 한국어 인식) | Apache-2.0 | 모델 파일을 함께 배포하면 Apache-2.0 전문 동봉 |
| onnxruntime | MIT | ThirdPartyNotices 포함 |
| opencv-python | Apache-2.0 | **안에 FFmpeg(LGPL-2.1) 포함** — 아래 3절 |
| numpy, shapely, omegaconf, antlr4 runtime, idna, protobuf | BSD 계열 | |
| pyclipper, PyYAML, colorlog, six, urllib3 | MIT | |
| requests, flatbuffers, packaging | Apache-2.0 계열 | |
| tqdm, certifi | MPL-2.0 (tqdm은 + MIT) | 해당 패키지 파일을 직접 고칠 때만 수정분 공개. 그대로 쓰면 의무 없음 |

### 쓰지 않은 것 (카피레프트)

PyMuPDF·MuPDF (AGPL-3.0), pyhwp (AGPL-3.0), marker (GPL-3.0), MinerU (AGPL-3.0)

### 개발·비교용 (배포물에 포함 안 함)

pytest (MIT), reportlab (BSD), pywin32 (PSF) — 테스트 문서 생성에만 사용

## 3. 사용 방식별로 해야 할 것

| 사용 방식 | HWP 고지 문구 | 오픈소스 고지 | OCR의 FFmpeg (LGPL-2.1) |
|---|---|---|---|
| 사내에서만 실행 (밖으로 배포 안 함) | 소스·매뉴얼·도움말에 유지 | 법적 의무 없음. NOTICE 유지 권장 | 의무 없음 |
| 웹 서비스·API로 제공 (서버에서 실행) | 서비스 화면에 표시 | 의무 없음 (AGPL 없음). 고지 페이지 권장 | 의무 없음 |
| 소스 코드 공개 (GitHub 등) · GitHub Pages 웹 버전 | 소스·README·웹 화면에 유지 (적용 완료) | 저장소에 LICENSE와 NOTICE 포함 | 의존성을 넣지 않으면 해당 없음 (웹 버전은 OCR 없음) |
| 설치 파일·도커 이미지 배포 (의존성 포함) | 화면·매뉴얼·도움말에 표시 | 포함한 모든 패키지의 라이선스 전문을 `THIRD_PARTY_LICENSES`로 동봉, Apache 패키지의 NOTICE 유지 | LGPL-2.1 전문 동봉, "FFmpeg는 LGPL-2.1" 고지, 해당 버전 FFmpeg 소스 위치 안내, DLL을 교체 가능한 상태로 유지(수정·정적 결합 금지) |

라이선스 전문 묶음은 다음 명령으로 만들 수 있습니다 (pip-licenses, MIT):

```bash
pip install pip-licenses
```

```bash
pip-licenses --with-license-file --with-notice-file --no-license-path --format=plain-vertical --output-file THIRD_PARTY_LICENSES.txt
```

FFmpeg 의무를 아예 피하려면 OCR 기능을 배포물에서 빼고 서버에서만 돌리면 됩니다.

## 4. 복사해서 쓰는 고지 문구

**제품 정보 화면 / 매뉴얼**

```
본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
본 제품은 오픈소스 소프트웨어를 사용합니다. 오픈소스 라이선스 고지: [고지 페이지 링크 또는 메뉴]
```

**오픈소스 고지 페이지 첫머리**

```
본 제품은 아래 오픈소스 소프트웨어를 사용하며, 각 소프트웨어의 라이선스 조건에 따라 고지합니다.
- pdfplumber, pdfminer.six, python-docx, python-pptx — MIT License
- pypdfium2 / PDFium — BSD-3-Clause, Apache-2.0
- odfpy — Apache License 2.0 (Apache-2.0 / GPL / LGPL 중 Apache-2.0을 선택하여 사용)
- olefile, lxml — BSD License
- (OCR 사용 시) RapidOCR, PP-OCRv5 모델 — Apache License 2.0 / onnxruntime — MIT License
- (OCR 사용 시) opencv-python — Apache License 2.0. 포함된 FFmpeg는 LGPL-2.1이며 소스는 https://ffmpeg.org 에서 받을 수 있습니다.
각 라이선스 전문은 THIRD_PARTY_LICENSES 파일에 있습니다.
```

## 5. ho 자체 라이선스 (결정 필요)

- `pyproject.toml`에는 임시로 `MIT`라고 적혀 있지만 LICENSE 파일은 아직 없습니다.
- 공개할 계획이면 MIT 또는 Apache-2.0, 사내 전용이면 비공개(독점) 라이선스로 정하면 됩니다. 사용한 오픈소스는 어느 쪽이든 허용합니다.

## 6. 참고

- 이 정리는 패키지 메타데이터와 각 공식 페이지를 기준으로 한 것이며, 외부 배포 전에는 법무 검토를 한 번 거치는 것을 권합니다.
