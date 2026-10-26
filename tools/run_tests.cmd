@echo off
rem Full Python test suite with the environment the tests expect (Python 3.12, a .NET 10 SDK, TMPDIR).
rem One-time setup: Install-SlimeBridge.cmd -Dev (or: py -3.12 -m pip install -r requirements-dev.txt)
rem                 dotnet SDK 10 (e.g. https://dot.net/v1/dotnet-install.ps1 -Channel 10.0) and the 6.0 / 8.0 runtimes.
cd /d "%~dp0\.."
if not defined DOTNET_ROOT if exist "%USERPROFILE%\.dotnet\dotnet.exe" set "DOTNET_ROOT=%USERPROFILE%\.dotnet"
if defined DOTNET_ROOT set "PATH=%DOTNET_ROOT%;%PATH%"
if not defined TMPDIR set "TMPDIR=%TEMP%"
set DOTNET_NOLOGO=1
set DOTNET_CLI_TELEMETRY_OPTOUT=1
set PYTHONDONTWRITEBYTECODE=1
set "PY=py -3.12"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% -m unittest discover -s tests %*
