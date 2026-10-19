@echo off
setlocal
cd /d "%~dp0"
set "TARGET=%~dp0start.bat"
set "ICONDIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'YOUSEF AI.lnk')); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=$env:ICONDIR; $s.Description='YOUSEF AI'; $s.Save()"
if errorlevel 1 (
  echo Could not create the shortcut.
) else (
  echo Shortcut "YOUSEF AI" created on your Desktop.
)
pause
endlocal
