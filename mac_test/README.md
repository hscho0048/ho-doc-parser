# 맥 형식별 테스트 (받는 사람용)

문서 파서(docparser)와 로컬 OCR이 맥(Apple Silicon)에서 형식별로 잘 되는지, 얼마나 빠르고 메모리를 얼마나 쓰는지 재요.
**명령 하나로 돌리고, 끝나면 결과표가 화면에 나와요.** 40~90분 걸려요.

## 1. 준비 (처음 한 번)

1. **uv** 설치 — 터미널에서
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   설치가 끝나면 터미널을 **새로 연다**.
2. **Docker Desktop**(또는 OrbStack)을 설치하고 **실행해 둔다**.
3. 받은 zip 을 풀고 그 폴더로 이동한다.
   ```bash
   cd ~/Downloads/docs-parser
   ```

## 2. 실행

```bash
bash mac_test/run_all.sh
```

- 처음엔 라이브러리·OCR 모델(수백 MB~1GB 남짓)을 내려받아요. 인터넷이 필요해요.
- 도중에 맥이 잠들지 않게 전원을 연결해 두세요.
- 끝나면 **결과표가 화면에 출력**되고, `mac_results/<날짜-시각>/results.md` 로도 저장돼요.

| 단계 | 하는 일 |
|---|---|
| A) 맥에서 직접 | `samples/` 의 모든 형식(PDF·DOCX·PPTX·PPT·ODT·XLSX·HWP·HWPX)을 OCR 없이 / OCR 포함으로 파싱 |
| B) Docker | 같은 파일을 Docker(Linux arm64) 안에서 똑같이 |
| C) PaddleOCR | 한국어 OCR 엔진을 Docker 와 맥 직접으로 비교 (페이지 1장 + 그림 1장) |

일부만 돌리기: `bash mac_test/run_all.sh native` · `docker` · `paddle` · 표만 다시 만들기 `python3 mac_test/summarize.py mac_results/<폴더>`

### samples/ 구성

| 폴더 | 파일 | 확인하려는 것 |
|---|---|---|
| pdf | complex · three_col · form · **scanned**(스캔본) · **secret**(암호) | 표·다단·양식 / 스캔 페이지 OCR / 암호 파일 실패 안내 |
| docx | complex · tracked(변경 추적) | 병합·중첩 표, 변경 추적 처리 |
| pptx | complex · flat_picture(그림 위주) | 표·차트, 그림 속 글자 OCR |
| ppt · odt · xlsx | complex | 구형·기타 형식 |
| hwp · hwpx | 실제 한글 문서 1개씩 | 표·병합 셀·그림 OCR |

## 3. 보내 줄 것

`mac_results/<날짜-시각>` **폴더 하나를 압축해서** 보내 주세요.
- 문서에서 뽑은 본문이 들어 있으니 **사내 메신저로만** 보내 주세요.
- 중간에 실패해도 폴더 그대로 보내 주세요. 원인이 `*.time`·`*.log`·`*build.log` 에 남아요.

## 문제가 생기면

| 증상 | 해결 |
|---|---|
| `uv: command not found` | 터미널을 새로 열기 |
| `docker` 연결 안 됨 | Docker Desktop 실행 후 `bash mac_test/run_all.sh docker` |
| 결과표에 "실패" 가 있음 | 정상일 수 있어요 — `secret.pdf` 는 암호 파일이라 일부러 실패해요 |
