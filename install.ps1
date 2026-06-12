<#
  TailCam installer for Windows.

  Run:
    irm https://raw.githubusercontent.com/factshin/anycam/main/install.ps1 | iex

  Installs TailCam into a per-user virtualenv, registers a logon Scheduled Task,
  and (when Tailscale is running) exposes the dashboard over your tailnet.

  (The GitHub repo is still named "anycam" — rename pending; the URL above will
  redirect once it changes.)
#>
[CmdletBinding()]
param(
  [int]$Port = 8088,
  [string]$Ref = "main",
  [switch]$NoService,
  [switch]$NoTailscale
)

$ErrorActionPreference = "Stop"
$Repo = "factshin/anycam"
$VenvDir = Join-Path $env:LOCALAPPDATA "TailCam\venv"
$LegacyVenvDir = Join-Path $env:LOCALAPPDATA "AnyCam\venv"

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "xx  $m" -ForegroundColor Red; exit 1 }

# --- locate / install Python 3.10+ ------------------------------------------
function Test-Py($exe, $argList) {
  try {
    & $exe @argList -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    if (Test-Py "py" @("-3")) { return @{ Exe = "py"; Args = @("-3") } }
  }
  foreach ($name in @("python", "python3")) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
      if (Test-Py $name @()) { return @{ Exe = $name; Args = @() } }
    }
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Warn "Python 3.10+ not found."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Info "Installing Python 3.12 via winget..."
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $py = Find-Python
  }
  if (-not $py) {
    Fail "Python 3.10+ is required. Install it from https://www.python.org/downloads/ (check 'Add to PATH'), then re-run."
  }
}
Info ("Using Python: " + $py.Exe + " " + ($py.Args -join " "))

# --- create venv + install TailCam from the GitHub zip (no Git needed) ------
# Wipe any existing venv so upgrades always take effect, even when pip would
# consider the installed version "already satisfied". Stop the running service
# first — Windows locks the files of a running pythonw.exe, which would make
# the wipe fail and leave the old build in place.
foreach ($dir in @($VenvDir, $LegacyVenvDir)) {
  if (Test-Path $dir) {
    Info "Stopping TailCam service"
    Stop-ScheduledTask -TaskName "TailCam" -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName "AnyCam" -ErrorAction SilentlyContinue
    Get-Process pythonw, python -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -like "$dir*" } |
      Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Info "Removing old virtualenv at $dir"
    Remove-Item -Recurse -Force $dir
  }
}
Info "Creating virtualenv at $VenvDir"
New-Item -ItemType Directory -Force -Path (Split-Path $VenvDir) | Out-Null
$PyArgs = $py.Args
& $py.Exe @PyArgs -m venv $VenvDir
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$TailcamBin = Join-Path $VenvDir "Scripts\tailcam.exe"
$Scripts = Join-Path $VenvDir "Scripts"

& $VenvPy -m pip install --upgrade pip | Out-Null
$Zip = "https://github.com/$Repo/archive/refs/heads/$Ref.zip"
Info "Installing TailCam from $Zip"
& $VenvPy -m pip install $Zip
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

# Put `tailcam` on PATH for this user (takes effect in new terminals). Drop the
# pre-rename AnyCam Scripts dir from PATH if it's there.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$LegacyScripts = Join-Path $LegacyVenvDir "Scripts"
$parts = @(($userPath -split ';') | Where-Object { $_ -and $_ -ne $LegacyScripts })
if ($parts -notcontains $Scripts) { $parts += $Scripts }
$newPath = $parts -join ';'
if ($newPath -ne $userPath) {
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Info "Added TailCam to your PATH — open a NEW terminal to use the 'tailcam' command."
}

# --- background service (logon Scheduled Task) ------------------------------
# Persist the chosen port so the service and `tailscale serve` both use it.
& $TailcamBin config --port $Port | Out-Null
if (-not $NoService) {
  Info "Registering logon task"
  & $TailcamBin install-service
} else { Warn "Skipping service registration (-NoService)." }

# --- Tailscale serve --------------------------------------------------------
function Find-Tailscale {
  if (Get-Command tailscale -ErrorAction SilentlyContinue) { return $true }
  foreach ($p in @("$env:ProgramFiles\Tailscale\tailscale.exe",
                   "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe")) {
    if (Test-Path $p) { return $true }
  }
  return $false
}

if (-not $NoTailscale) {
  if (Find-Tailscale) {
    Info "Exposing TailCam over Tailscale"
    & $TailcamBin tailscale serve
  } else {
    Warn "Tailscale not found. Install it from https://tailscale.com/download/windows, run 'tailscale up', then: tailcam tailscale serve"
  }
}

Write-Host ""
Info "TailCam installed."
& $TailcamBin status
Write-Host ""
Info "Open the web UI at one of the URLs above."
Info "Manage it with: $TailcamBin <command>   (status, tailscale serve, uninstall-service, ...)"
