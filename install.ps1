<#
  TailCam installer for Windows.

  Run:
    irm https://raw.githubusercontent.com/factshin/tailcam/main/install.ps1 | iex

  Installs TailCam into a per-user virtualenv, registers a logon Scheduled Task,
  and (when Tailscale is running) exposes the dashboard over your tailnet.

  Windows on ARM (Surface / Snapdragon X): TailCam's camera stack (OpenCV)
  publishes no native ARM64 wheels yet, so this installer uses x64 Python,
  which Windows 11 runs transparently under emulation. Requires Windows 11 —
  Windows 10 on ARM cannot emulate x64.

  A full transcript of every run is written to
  %LOCALAPPDATA%\TailCam\install-<timestamp>.log — if anything goes wrong,
  that file has the whole story.
#>
[CmdletBinding()]
param(
  [int]$Port = 8088,
  [string]$Ref = "main",
  [switch]$NoService,
  [switch]$NoTailscale,
  [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$Repo = "factshin/tailcam"
$AppDir = Join-Path $env:LOCALAPPDATA "TailCam"
$VenvDir = Join-Path $AppDir "venv"
$StageDir = "$VenvDir.new"
$LegacyVenvDir = Join-Path $env:LOCALAPPDATA "AnyCam\venv"
$LogPath = Join-Path $AppDir ("install-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

# ARM64 detection. PROCESSOR_ARCHITEW6432 catches the case where this script
# itself runs inside an emulated x64 PowerShell on an ARM64 machine.
$IsArm64 = ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") -or ($env:PROCESSOR_ARCHITEW6432 -eq "ARM64")

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
# NEVER `exit` here: under `irm | iex` the script runs in the session scope, so
# `exit` kills the whole PowerShell window before the error can be read. Throw
# instead — the try/catch at the bottom shows the message, points at the log,
# and pauses so the window stays open.
function Fail($m) { throw $m }

# --- locate / install Python 3.10+ (x64 on ARM64 machines) -------------------
function Test-PyVersion($exe, $argList) {
  try {
    & $exe @argList -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch { return $false }
}

function Get-PyMachine($exe, $argList) {
  try {
    $m = & $exe @argList -c "import platform; print(platform.machine())" 2>$null
    if ($LASTEXITCODE -eq 0) { return ("$m").Trim() }
  } catch { }
  return ""
}

function Find-Python {
  $rejectedArm = $false
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@{ Exe = "py"; Args = @("-3") } }
  foreach ($name in @("python", "python3")) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $candidates += ,@{ Exe = $name; Args = @() } }
  }
  foreach ($cand in $candidates) {
    if (-not (Test-PyVersion $cand.Exe $cand.Args)) { continue }
    if ($IsArm64) {
      # A native ARM64 interpreter cannot install TailCam: OpenCV (and several
      # other dependencies) ship no win_arm64 wheels. Only accept x64 builds,
      # which Windows 11 runs under emulation.
      $machine = Get-PyMachine $cand.Exe $cand.Args
      if ($machine -eq "ARM64" -or $machine -eq "aarch64") {
        $script:RejectedArmPython = $true
        Warn ("Skipping " + $cand.Exe + " — it's native ARM64 Python, but TailCam needs x64 " +
              "Python on this PC (OpenCV has no ARM64 wheels yet; x64 runs via Windows emulation).")
        continue
      }
    }
    return $cand
  }
  return $null
}

function Install-X64Python {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    $wingetArgs = @("install", "-e", "--id", "Python.Python.3.12", "--silent",
                    "--accept-package-agreements", "--accept-source-agreements")
    if ($IsArm64) {
      # winget prefers the native (arm64) build by default; force x64.
      Info "ARM64 PC detected — installing x64 Python 3.12 (runs via Windows 11 emulation),"
      Info "because OpenCV publishes no native ARM64 wheels yet."
      $wingetArgs += @("--architecture", "x64")
    } else {
      Info "Installing Python 3.12 via winget..."
    }
    & winget @wingetArgs
    if ($LASTEXITCODE -ne 0) { Warn "winget returned exit code $LASTEXITCODE (may be fine if already installed)." }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    return
  }
  # No winget: fetch the official x64 installer from python.org.
  $pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
  $pyExe = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
  Info "winget not found — downloading x64 Python from python.org..."
  Invoke-WebRequest -Uri $pyUrl -OutFile $pyExe
  Start-Process -FilePath $pyExe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1" -Wait
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Stop-TailCamProcesses {
  Stop-ScheduledTask -TaskName "TailCam" -ErrorAction SilentlyContinue
  Stop-ScheduledTask -TaskName "AnyCam" -ErrorAction SilentlyContinue
  Get-Process pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "$VenvDir*" -or $_.Path -like "$LegacyVenvDir*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

function Install-TailCam {
  $script:RejectedArmPython = $false
  $py = Find-Python
  if (-not $py) {
    if ($script:RejectedArmPython) {
      Warn "Only native ARM64 Python was found; installing x64 Python alongside it."
    } else {
      Warn "Python 3.10+ not found."
    }
    Install-X64Python
    $py = Find-Python
    if (-not $py) {
      Fail ("Python 3.10+ (x64) is required. Install it from https://www.python.org/downloads/ " +
            "(pick the 64-bit/AMD64 installer and check 'Add to PATH'), then re-run this installer.")
    }
  }
  Info ("Using Python: " + $py.Exe + " " + ($py.Args -join " "))

  # --- build into a STAGING venv first, so a failed install (or upgrade) never
  # destroys a working TailCam. The old venv is only replaced after pip succeeds.
  if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
  Info "Creating virtualenv (staging) at $StageDir"
  New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
  $PyArgs = $py.Args
  & $py.Exe @PyArgs -m venv $StageDir
  if ($LASTEXITCODE -ne 0) { Fail "Failed to create the virtualenv (python -m venv). See the log for details." }
  $StagePy = Join-Path $StageDir "Scripts\python.exe"

  & $StagePy -m pip install --upgrade pip
  $Zip = "https://github.com/$Repo/archive/refs/heads/$Ref.zip"
  Info "Installing TailCam from $Zip"
  & $StagePy -m pip install $Zip
  if ($LASTEXITCODE -ne 0) {
    Remove-Item -Recurse -Force $StageDir -ErrorAction SilentlyContinue
    if ($IsArm64) {
      Fail ("pip install failed. This is an ARM64 PC: make sure the Python used above is x64 " +
            "(this installer selects it automatically) and that you're on Windows 11, which can " +
            "run x64 programs under emulation. Scroll up for pip's error, or read the log.")
    }
    Fail "pip install failed. Scroll up for pip's error, or read the log."
  }

  # --- swap staging into place (stop the old service first: running pythonw
  # locks the old venv's files) ------------------------------------------------
  foreach ($dir in @($VenvDir, $LegacyVenvDir)) {
    if (Test-Path $dir) {
      Info "Stopping TailCam service"
      Stop-TailCamProcesses
      Info "Removing old virtualenv at $dir"
      try {
        Remove-Item -Recurse -Force $dir
      } catch {
        Remove-Item -Recurse -Force $StageDir -ErrorAction SilentlyContinue
        Fail ("Couldn't remove the old install at $dir — a TailCam process or terminal is " +
              "holding files open. Close them (or reboot) and re-run the installer.")
      }
    }
  }
  Move-Item $StageDir $VenvDir
  $VenvPy = Join-Path $VenvDir "Scripts\python.exe"
  $TailcamBin = Join-Path $VenvDir "Scripts\tailcam.exe"
  $Scripts = Join-Path $VenvDir "Scripts"
  if (-not (Test-Path $TailcamBin)) {
    Fail "tailcam.exe was not created by pip — the install is incomplete. See the log."
  }

  # Remove the pre-rename AnyCam logon task if present. Runs even with
  # -NoService so a stale task can't keep launching the old build.
  if (Get-ScheduledTask -TaskName "AnyCam" -ErrorAction SilentlyContinue) {
    Info "Removing old AnyCam logon task"
    Stop-ScheduledTask -TaskName "AnyCam" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "AnyCam" -Confirm:$false -ErrorAction SilentlyContinue
  }

  # Put `tailcam` on PATH for this user (takes effect in new terminals). Drop
  # the pre-rename AnyCam Scripts dir from PATH if it's there.
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

  # AI motion labeling (optional, local Ollama)
  Write-Host ""
  Info "AI motion labeling (optional)"
  $ollama = Get-Command ollama -ErrorAction SilentlyContinue
  if ($ollama) {
    $models = (& ollama list 2>$null | Out-String)
    if ($models -match 'moondream|llava|minicpm-v|llama3.2-vision|bakllava') {
      Write-Host "    Ollama is installed and a vision model is downloaded."
    } else {
      Warn "Ollama is installed, but no vision model is downloaded yet. Get one:"
      Write-Host "        ollama pull moondream"
    }
  } else {
    Write-Host "    To label what your cameras see (person / animal / vehicle...), install Ollama"
    Write-Host "    from https://ollama.com/download/windows, then download a model:"
    Write-Host "        ollama pull moondream"
  }
  Write-Host "    You can also do all of this from the TailCam UI -> AI."

  Write-Host ""
  Info "TailCam installed."
  & $TailcamBin status
  Write-Host ""
  Info "Open the web UI at one of the URLs above."
  Info "Manage it with: $TailcamBin <command>   (status, tailscale serve, uninstall-service, ...)"
  Info "Install log: $LogPath"
}

# --- top-level driver: transcript + readable failures ------------------------
# Everything runs inside try/catch so ANY failure (explicit Fail or an uncaught
# terminating error) prints its message, points at the transcript, and pauses —
# the window never vanishes with the error unread.
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$TranscriptStarted = $false
try { Start-Transcript -Path $LogPath -Force | Out-Null; $TranscriptStarted = $true } catch { }
try {
  Install-TailCam
} catch {
  Write-Host ""
  Write-Host ("xx  " + $_.Exception.Message) -ForegroundColor Red
  Write-Host ("    Full install log: " + $LogPath) -ForegroundColor Yellow
  # Keep the window open for interactive runs so the error is readable. Skipped
  # for the detached self-updater (TAILCAM_INSTALL_NONINTERACTIVE) and -NonInteractive.
  if (-not $NonInteractive -and -not $env:TAILCAM_INSTALL_NONINTERACTIVE -and [Environment]::UserInteractive) {
    try { Read-Host "Press Enter to close" | Out-Null } catch { }
  }
} finally {
  if ($TranscriptStarted) { try { Stop-Transcript | Out-Null } catch { } }
}
