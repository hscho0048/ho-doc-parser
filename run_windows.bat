@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ho 문서 파서

set "VPY="
if exist ".venv\Scripts\python.exe" set "VPY=.venv\Scripts\python.exe"
if not defined VPY if exist ".venv-ocr\Scripts\python.exe" set "VPY=.venv-ocr\Scripts\python.exe"
if not defined VPY (
  echo 아직 설치되지 않았습니다. install_windows.bat 을 먼저 더블클릭하세요.
  pause
  exit /b 1
)

echo ho 문서 파서를 시작합니다. 브라우저가 열리면 파일을 끌어다 놓으세요.
echo 이 창을 닫거나 Ctrl+C 를 누르면 종료됩니다.
echo.
"%VPY%" tools\ho_gui\app.py %*
if errorlevel 1 pause
