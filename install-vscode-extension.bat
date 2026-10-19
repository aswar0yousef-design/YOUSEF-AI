@echo off
setlocal
title YOUSEF AI - VS Code extension
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)

%PY% tools\build_vsix.py
if errorlevel 1 (
  echo  Could not build the extension package.
  pause
  exit /b 1
)

where code >nul 2>nul
if errorlevel 1 (
  echo.
  echo  The "code" command was not found.
  echo  Install the file by hand: in VS Code open Extensions, click the "..." menu,
  echo  choose "Install from VSIX..." and pick dist\yousef-ai.vsix
  echo.
  pause
  exit /b 1
)

call code --install-extension "dist\yousef-ai.vsix" --force
echo.
echo  Done. Reload VS Code, then click the YOUSEF AI icon in the activity bar.
echo  Keep YOUSEF AI running with start.bat while you use the extension.
echo.
pause
endlocal
