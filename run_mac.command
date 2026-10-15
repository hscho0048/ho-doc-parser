#!/bin/bash
# Starts the ho GUI (double-click in Finder, or: bash run_mac.command)
cd "$(dirname "$0")" || exit 1

VPY=""
[ -x ".venv/bin/python" ] && VPY=".venv/bin/python"
[ -z "$VPY" ] && [ -x ".venv-ocr/bin/python" ] && VPY=".venv-ocr/bin/python"
if [ -z "$VPY" ]; then
  echo "아직 설치되지 않았습니다. install_mac.command 를 먼저 더블클릭하세요."
  read -r -p "엔터를 누르면 창을 닫습니다. " _
  exit 1
fi

echo "ho 문서 파서를 시작합니다. 브라우저가 열리면 파일을 끌어다 놓으세요."
echo "이 창을 닫거나 Ctrl+C 를 누르면 종료됩니다."
echo
"$VPY" tools/ho_gui/app.py "$@" || read -r -p "오류가 났습니다. 엔터를 누르면 창을 닫습니다. " _
