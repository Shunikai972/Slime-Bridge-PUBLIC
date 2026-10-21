# SlimeBridge installer (Windows 10/11, PowerShell 5.1+). Run through Install-SlimeBridge.cmd.
#
# What it does, and nothing else:
#   1. finds Python 3.12 (64-bit); if missing, offers to install it for the current user with winget
#   2. checks the Microsoft Edge WebView2 runtime the launcher window needs (offers winget if missing)
#   3. creates .venv next to this folder and installs requirements.txt from PyPI
#   4. checks that the launcher and the extractor import correctly
#   5. creates a "SlimeBridge" shortcut on the desktop (skip with -NoShortcut)
# It never touches your game folders or saves. Plugins are installed later from the launcher (Games page).

param(
    [switch]$NoShortcut,
    [switch]$Yes,          # answer "yes" to every question (unattended install)
    [switch]$Dev           # also install the test tools (requirements-dev.txt)
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Step($text) { Write-Host ''; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host ''
    Write-Host "ERREUR / ERROR: $text" -ForegroundColor Red
    exit 1
}
function Ask($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question [O/n] (Y/n)"
    return ($answer -eq '' -or $answer -match '^(o|oui|y|yes)$')
}

function Test-Python312($exe, [string[]]$prefix = @()) {
    try {
        $out = & $exe @prefix -c "import sys, struct; print('%d.%d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq '3.12 64')
    } catch { return $false }
}

function Find-Python312 {
    if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python312 'py' @('-3.12'))) {
        return @{ Exe = 'py'; Args = @('-3.12') }
    }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "C:\Python312\python.exe"
    )
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate) -and (Test-Python312 $candidate)) {
            return @{ Exe = $candidate; Args = @() }
        }
    }
    return $null
}

function Test-WebView2 {
    $id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $keys = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$id",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$id",
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$id"
    )
    foreach ($key in $keys) {
        try {
            $pv = (Get-ItemProperty -Path $key -Name pv -ErrorAction Stop).pv
            if ($pv -and $pv -ne '0.0.0.0') { return $true }
        } catch { }
    }
    return $false
}

Say 'SlimeBridge - installation' 'Green'
Say "Dossier / folder: $Root"

# ---------------------------------------------------------------------------------------------------------------------
Step 'Python 3.12 (64-bit)'
$python = Find-Python312
if (-not $python) {
    Say 'Python 3.12 64-bit est introuvable / was not found.' 'Yellow'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget -and (Ask 'Installer Python 3.12 avec winget (pour cet utilisateur) ? / Install Python 3.12 with winget?')) {
        & winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $python = Find-Python312
    }
    if (-not $python) {
        Start-Process 'https://www.python.org/downloads/release/python-31210/'
        Fail 'Installe Python 3.12 (64-bit, coche "Add python.exe to PATH") puis relance Install-SlimeBridge.cmd. / Install Python 3.12 (64-bit) then run Install-SlimeBridge.cmd again.'
    }
}
Say "OK: $($python.Exe) $($python.Args -join ' ')" 'Green'

# ---------------------------------------------------------------------------------------------------------------------
Step 'Microsoft Edge WebView2 runtime'
if (Test-WebView2) {
    Say 'OK' 'Green'
} else {
    Say 'WebView2 est introuvable (fenêtre du launcher) / WebView2 runtime not found.' 'Yellow'
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget -and (Ask 'Installer WebView2 avec winget ? / Install WebView2 with winget?')) {
        & winget install --exact --id Microsoft.EdgeWebView2Runtime --accept-package-agreements --accept-source-agreements
    } else {
        Start-Process 'https://developer.microsoft.com/microsoft-edge/webview2/'
        Say 'Installe le "Evergreen Bootstrapper" puis relance le launcher. / Install the Evergreen Bootstrapper.' 'Yellow'
    }
}

# ---------------------------------------------------------------------------------------------------------------------
Step 'Environnement Python (.venv)'
if ((Test-Path $VenvPython) -and -not (Test-Python312 $VenvPython)) {
    Say '.venv existant avec une autre version de Python: recréation / rebuilding .venv' 'Yellow'
    Remove-Item -Recurse -Force $Venv
}
if (-not (Test-Path $VenvPython)) {
    & $python.Exe @($python.Args) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail 'python -m venv a échoué / failed.' }
}
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail 'pip upgrade a échoué (connexion internet ?) / failed (internet connection?).' }
$req = if ($Dev) { 'requirements-dev.txt' } else { 'requirements.txt' }
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $Root $req)
if ($LASTEXITCODE -ne 0) { Fail "pip install -r $req a échoué / failed." }

# ---------------------------------------------------------------------------------------------------------------------
Step 'Vérification / self-check'
& $VenvPython -m apps.launcher.selfcheck
if ($LASTEXITCODE -ne 0) { Fail 'Le self-check a échoué (voir ci-dessus) / self-check failed (see above).' }

# ---------------------------------------------------------------------------------------------------------------------
if (-not $NoShortcut) {
    Step 'Raccourci bureau / desktop shortcut'
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut((Join-Path $desktop 'SlimeBridge.lnk'))
        $link.TargetPath = Join-Path $Venv 'Scripts\pythonw.exe'
        $link.Arguments = '-m apps.launcher.desktop'
        $link.WorkingDirectory = $Root
        $link.Description = 'SlimeBridge launcher'
        $icon = Join-Path $Root 'apps\launcher\ui\slimebridge.ico'
        if (Test-Path $icon) { $link.IconLocation = $icon }
        $link.Save()
        Say 'OK' 'Green'
    } catch {
        Say "Raccourci non créé / shortcut not created: $($_.Exception.Message)" 'Yellow'
    }
}

Write-Host ''
Say 'Installation terminée / done. Lance SlimeBridge.cmd (ou le raccourci du bureau).' 'Green'
Say 'Ensuite / next: page Jeux -> choisis tes dossiers SR1/SR2 -> installe les plugins -> Extraire.' 'Green'
exit 0
