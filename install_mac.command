#!/bin/bash
# ho doc parser installer for macOS (double-click in Finder, or: bash install_mac.command)
cd "$(dirname "$0")" || exit 1

pause_exit() {
  [ -z "$HO_NONINTERACTIVE" ] && read -r -p "엔터를 누르면 창을 닫습니다. " _
  exit "$1"
}

echo "=================================================="
echo "  ho doc parser 설치 (macOS)"
echo "=================================================="
echo

# ---------------------------------------------------------------- 1. Python 3.10+
PY=""
for c in python3.12 python3.13 python3.11 python3.14 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$c")"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.10 이상을 찾지 못했습니다."
  if command -v brew >/dev/null 2>&1; then
    ans="y"
    [ -z "$HO_NONINTERACTIVE" ] && read -r -p "Homebrew로 Python 3.12를 설치할까요? [Y/n] " ans
    if [ "${ans:-y}" != "n" ] && [ "${ans:-y}" != "N" ]; then
      brew install python@3.12 || pause_exit 1
      PY="$(brew --prefix)/bin/python3.12"
    fi
  fi
fi
if [ -z "$PY" ]; then
  echo
  echo "Python 3.10 이상이 필요합니다. https://www.python.org/downloads/macos/ 에서 설치한 뒤"
  echo "이 파일을 다시 실행하세요."
  [ -z "$HO_NONINTERACTIVE" ] && open "https://www.python.org/downloads/macos/"
  pause_exit 1
fi
echo "사용할 Python: $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
echo

# ---------------------------------------------------------------- 2. what to install
REQ="requirements-ocr.txt"
if [ -z "$HO_NONINTERACTIVE" ]; then
  echo "설치 종류를 고르세요."
  echo "  [1] 전체 - 그림 속 글자 OCR, GUI의 RAM/CPU 측정 포함 (권장)"
  echo "  [2] 기본 - OCR 없이, 용량이 작음"
  read -r -t 30 -p "선택 [1/2] (30초 뒤 1): " choice
  echo
  [ "$choice" = "2" ] && REQ="requirements.txt"
fi

# ---------------------------------------------------------------- 3. virtual environment + packages
if [ -x ".venv/bin/python" ]; then
  echo "기존 가상환경 .venv 를 그대로 사용합니다."
else
  echo "가상환경 .venv 를 만드는 중..."
  "$PY" -m venv .venv || pause_exit 1
fi
VPY=".venv/bin/python"
echo "패키지를 설치하는 중... ($REQ, 처음에는 몇 분 걸릴 수 있습니다)"
"$VPY" -m pip install --upgrade pip --quiet --disable-pip-version-check || pause_exit 1
"$VPY" -m pip install -r "$REQ" --disable-pip-version-check || pause_exit 1

# ---------------------------------------------------------------- 4. check
echo
"$VPY" -m docparser --version || pause_exit 1
"$VPY" -c "import docx, pptx, pdfplumber, openpyxl, odf, olefile" || pause_exit 1
chmod +x run_mac.command 2>/dev/null
echo
echo "설치 완료. 실행: run_mac.command 더블클릭"
echo

if [ -z "$HO_NONINTERACTIVE" ]; then
  read -r -p "지금 실행할까요? [Y/n] " ans
  if [ "${ans:-y}" != "n" ] && [ "${ans:-y}" != "N" ]; then
    exec ./run_mac.command
  fi
fi
pause_exit 0
