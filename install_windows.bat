@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ho doc parser 설치

echo ==================================================
echo   ho doc parser 설치 (Windows)
echo ==================================================
echo.

rem ---------------------------------------------------------------- 1. Python 3.10+
set "PY="
for %%V in (3.12 3.13 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY goto :install_python
goto :have_python

:install_python
echo Python 3.10 이상을 찾지 못했습니다.
where winget >nul 2>&1 || goto :no_python
if defined HO_NONINTERACTIVE goto :no_python
choice /c YN /m "winget으로 Python 3.12를 설치할까요"
if errorlevel 2 goto :no_python
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
rem PATH is not refreshed in this window, so call the launcher by its install path
for %%L in ("%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" "%WINDIR%\py.exe") do (
  if not defined PY if exist %%L (
    %%L -3.12 -c "import sys" >nul 2>&1 && set "PY=%%~L -3.12"
  )
)
if not defined PY (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY goto :no_python

:have_python
for /f "delims=" %%I in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%I"
echo 사용할 Python: %PY% (%PYVER%)
echo.

rem ---------------------------------------------------------------- 2. what to install
set "REQ=requirements-ocr.txt"
if defined HO_NONINTERACTIVE goto :venv
echo 설치 종류를 고르세요.
echo   [1] 전체 - 그림 속 글자 OCR, GUI의 RAM/CPU 측정 포함 (권장)
echo   [2] 기본 - OCR 없이, 용량이 작음
choice /c 12 /t 30 /d 1 /m "선택 (30초 뒤 1)"
if errorlevel 2 set "REQ=requirements.txt"

:venv
rem ---------------------------------------------------------------- 3. virtual environment + packages
if exist ".venv\Scripts\python.exe" (
  echo 기존 가상환경 .venv 를 그대로 사용합니다.
) else (
  echo 가상환경 .venv 를 만드는 중...
  %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"
echo 패키지를 설치하는 중... (%REQ%, 처음에는 몇 분 걸릴 수 있습니다)
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check || goto :fail
"%VPY%" -m pip install -r "%REQ%" --disable-pip-version-check || goto :fail

rem ---------------------------------------------------------------- 4. check
echo.
"%VPY%" -m docparser --version || goto :fail
"%VPY%" -c "import docx, pptx, pdfplumber, openpyxl, odf, olefile" || goto :fail
echo.
echo 설치 완료. 실행: run_windows.bat 더블클릭
echo.

if defined HO_NONINTERACTIVE goto :done
choice /c YN /m "바탕화면에 'ho 문서 파서' 바로가기를 만들까요"
if errorlevel 2 goto :ask_run
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\ho 문서 파서.lnk');" ^
  "$s.TargetPath = '%~dp0run_windows.bat'; $s.WorkingDirectory = '%~dp0'; $s.Save()" && echo 바탕화면에 바로가기를 만들었습니다.

:ask_run
choice /c YN /m "지금 실행할까요"
if errorlevel 2 goto :done
call "%~dp0run_windows.bat"
goto :eof

:no_python
echo.
echo Python 3.10 이상이 필요합니다. https://www.python.org/downloads/windows/ 에서 설치할 때
echo "Add python.exe to PATH" 를 체크한 뒤, 이 파일을 다시 실행하세요.
if not defined HO_NONINTERACTIVE start "" "https://www.python.org/downloads/windows/"
goto :fail_pause

:fail
echo.
echo 설치 중 오류가 났습니다. 위 메시지를 확인하세요.
:fail_pause
if not defined HO_NONINTERACTIVE pause
exit /b 1

:done
if not defined HO_NONINTERACTIVE pause
exit /b 0
