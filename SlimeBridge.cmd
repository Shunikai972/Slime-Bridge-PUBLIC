@echo off
rem Starts the SlimeBridge launcher (run Install-SlimeBridge.cmd once first).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo SlimeBridge n'est pas encore installe : lance Install-SlimeBridge.cmd d'abord.
  echo SlimeBridge is not installed yet: run Install-SlimeBridge.cmd first.
  pause
  exit /b 1
)
if /i "%~1"=="--console" (
  ".venv\Scripts\python.exe" -m apps.launcher.desktop %2 %3 %4
) else (
  start "" ".venv\Scripts\pythonw.exe" -m apps.launcher.desktop %*
)
