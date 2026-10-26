@echo off
rem SlimeBridge installer: Python 3.12 + WebView2 check, .venv, dependencies, self-check, desktop shortcut.
rem Options: -NoShortcut  -Yes (unattended)  -Dev (test tools)
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if /i not "%~1"=="-Yes" if /i not "%~2"=="-Yes" if /i not "%~3"=="-Yes" pause
exit /b %CODE%
