# Creates the local virtual environment and installs the pinned dependencies.
# Network use: package installation from PyPI only (tools, never game assets).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    python -m venv "$root\.venv"
}
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"
& "$root\.venv\Scripts\python.exe" -m unittest discover -s tests -t .
Write-Host "Ready. Try: .\sbconv.cmd engines"
