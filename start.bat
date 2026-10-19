@echo off
setlocal
title YOUSEF AI
cd /d "%~dp0"

rem Use the voice environment if install-voice.bat created it.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

rem Otherwise find Python 3.10+ (the "py" launcher first, then python on PATH).
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  [YOUSEF AI] Python 3.10 or newer was not found.
  echo  Install it from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" during setup, then run start.bat again.
  echo.
  pause
  exit /b 1
)

%PY% -m yousef %*
if errorlevel 1 pause
endlocal
