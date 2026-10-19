@echo off
setlocal
title YOUSEF AI - voice on the graphics card
cd /d "%~dp0"

echo.
echo  YOUSEF AI - speech recognition on an NVIDIA graphics card
echo  Downloads NVIDIA's cuBLAS and cuDNN libraries (about 1 GB) into .venv.
echo  You do not need to install CUDA itself. Run install-voice.bat first.
echo.

if not exist ".venv\Scripts\python.exe" (
  echo  Voice is not installed yet. Run install-voice.bat first, then this file.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements-voice-gpu.txt
if errorlevel 1 (
  echo.
  echo  Installation failed. Check your internet connection and run this file again.
  pause
  exit /b 1
)

echo.
echo  Done. Restart YOUSEF AI, then in Settings - Voice choose
echo  "graphics card (CUDA)" and save. If the card cannot be used, Yousef keeps
echo  working on the processor and tells you why.
echo.
pause
endlocal
