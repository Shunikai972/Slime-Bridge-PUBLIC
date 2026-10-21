@echo off
rem SlimeBridge installer: Python 3.12 + WebView2 check, .venv, dependencies, self-check, desktop shortcut.
rem Options: -NoShortcut  -Yes (unattended)  -Dev (test tools)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
