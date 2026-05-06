# RTT Windows Installer
# Uses WASAPI loopback (built into Windows) — no drivers, no virtual cables.
#
# Usage (PowerShell):
#   .\install\windows.ps1
#
# Or one-liner:
#   irm https://raw.githubusercontent.com/idanshimon/realtime-transcriber/master/install/windows.ps1 | iex

$ErrorActionPreference = 'Stop'

function Write-Step($msg)  { Write-Host "`n▸ $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "✅ $msg" -ForegroundColor Green }
function Write-Info($msg)  { Write-Host "→  $msg" -ForegroundColor Blue }
function Write-Warnx($msg) { Write-Host "⚠  $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "✖  $msg" -ForegroundColor Red }

function Ask-YN($q, $def='Y') {
    $prompt = if ($def -eq 'Y') { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Host "? $q $prompt"
    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $def }
    return ($reply -match '^[Yy]')
}

@'

  ╭──────────────────────────────────────────╮
  │   RTT — Real-Time Transcriber Installer  │
  │   (Windows / WASAPI loopback edition)    │
  │   ⚠  Beta — not yet tested on Windows    │
  ╰──────────────────────────────────────────╯

'@ | Write-Host

$RepoDir = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $RepoDir

# ── Step 1: Python ─────────────────────────────────────────────────────────
Write-Step '1/4  Checking Python'
$pythonCmd = $null
foreach ($c in @('python', 'py', 'python3')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        $ver = & $c --version 2>&1
        if ($ver -match 'Python 3\.(1[0-9]|[2-9][0-9])') { $pythonCmd = $c; break }
    }
}

if (-not $pythonCmd) {
    Write-Warnx 'Python 3.10+ not found.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask-YN 'Install Python 3.12 via winget?') {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            $pythonCmd = 'python'
        } else { exit 1 }
    } else {
        Write-Err 'Install Python from https://www.python.org/downloads/ and re-run this script.'
        exit 1
    }
}
Write-Ok "Python: $(& $pythonCmd --version)"

# ── Step 2: venv + deps ────────────────────────────────────────────────────
Write-Step '2/4  Python virtual environment'
$venv = Join-Path $RepoDir '.venv'
if (-not (Test-Path $venv)) {
    Write-Info 'Creating .venv (one-time)…'
    & $pythonCmd -m venv $venv
}
$activate = Join-Path $venv 'Scripts\Activate.ps1'
. $activate

$marker = Join-Path $venv '.deps-installed'
if (-not (Test-Path $marker) -or ((Get-Item 'requirements.txt').LastWriteTime -gt (Get-Item $marker).LastWriteTime)) {
    Write-Info 'Installing Python dependencies…'
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    New-Item -ItemType File -Force -Path $marker | Out-Null
    Write-Ok 'Dependencies installed'
} else {
    Write-Ok 'Dependencies already installed'
}

# ── Step 3: WASAPI loopback detection ──────────────────────────────────────
Write-Step '3/4  Detecting WASAPI loopback device'

$detect = @"
import sounddevice as sd
import sys
try:
    apis = sd.query_hostapis()
    wasapi_idx = next(i for i, a in enumerate(apis) if 'WASAPI' in a['name'])
    devices = sd.query_devices()
    # Find default output device for WASAPI
    default_out_name = None
    for i, d in enumerate(devices):
        if d['hostapi'] == wasapi_idx and d['max_output_channels'] > 0:
            default_out_name = d['name']
            print(f'WASAPI_OUT::{i}::{default_out_name}')
            break
    if not default_out_name:
        sys.exit(2)
except StopIteration:
    print('NO_WASAPI', file=sys.stderr)
    sys.exit(3)
"@

$tmp = New-TemporaryFile
$detect | Set-Content -Path $tmp.FullName
$out = & python $tmp.FullName 2>&1
Remove-Item $tmp.FullName -Force

if ($LASTEXITCODE -eq 0 -and $out -match 'WASAPI_OUT::(\d+)::(.+)') {
    $loopDev = $matches[2].Trim()
    Write-Ok "WASAPI default output: $loopDev (loopback-capable)"
} else {
    Write-Warnx 'WASAPI not detected — falling back to mic capture only.'
    $loopDev = $null
}

# ── Step 4: .env ───────────────────────────────────────────────────────────
Write-Step '4/4  Configuration'
$envFile = Join-Path $RepoDir '.env'
if (-not (Test-Path $envFile)) { New-Item -ItemType File -Path $envFile | Out-Null }

function Set-EnvLine($key, $val) {
    $content = if (Test-Path $envFile) { Get-Content $envFile } else { @() }
    $newLine = "$key=$val"
    if ($content -match "^$key=") {
        ($content -replace "^$key=.*", $newLine) | Set-Content $envFile
    } else {
        Add-Content -Path $envFile -Value $newLine
    }
}

if ($loopDev) {
    Set-EnvLine 'RTT_INPUT_DEVICE' $loopDev
    Set-EnvLine 'RTT_USE_LOOPBACK' '1'
    Write-Ok ".env: RTT_INPUT_DEVICE=$loopDev (WASAPI loopback)"
}

if (-not (Get-Content $envFile | Select-String 'AZURE_SPEECH_REGION')) {
    Add-Content -Path $envFile -Value @"

# Azure Speech (optional — only needed for --backend azure)
# AZURE_SPEECH_REGION=eastus
# AZURE_SPEECH_KEY=
# AZURE_SPEECH_RESOURCE_ID=
"@
}

# ── Done ───────────────────────────────────────────────────────────────────
Write-Host ''
Write-Ok 'RTT installed!'
Write-Host ''
Write-Host 'Try it out:'
Write-Host '  python transcribe.py'
Write-Host '  python transcribe.py --list-devices'
Write-Host '  python transcribe.py --input-file recording.mp3'
Write-Host ''
