@echo off
setlocal
title YOUSEF AI - voice setup
cd /d "%~dp0"

echo.
echo  YOUSEF AI - installing local speech recognition (faster-whisper)
echo  This needs internet once. The speech model itself downloads the
echo  first time you use voice (about 500 MB for the "small" model).
echo.

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  Creating a private Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo  Could not create .venv
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-voice.txt
if errorlevel 1 (
  echo.
  echo  Installation failed. Check your internet connection and run this file again.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import faster_whisper; print('  faster-whisper', faster_whisper.__version__, 'installed')"
echo.
echo  Done. Start YOUSEF AI with start.bat - it will use the voice environment automatically.
echo.
pause
endlocal
