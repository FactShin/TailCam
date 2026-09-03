<#  TailCam uninstaller for Windows. Also cleans up pre-rename AnyCam installs. #>
[CmdletBinding()]
param([switch]$KeepData)

$ErrorActionPreference = "Continue"
$VenvDir = Join-Path $env:LOCALAPPDATA "TailCam\venv"
$LegacyVenvDir = Join-Path $env:LOCALAPPDATA "AnyCam\venv"
$DataDirs = @((Join-Path $env:LOCALAPPDATA "TailCam"), (Join-Path $env:LOCALAPPDATA "AnyCam"))
$TailcamBin = Join-Path $VenvDir "Scripts\tailcam.exe"
$LegacyBin = Join-Path $LegacyVenvDir "Scripts\anycam.exe"

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }

Info "Removing TailCam"
if (Test-Path $TailcamBin) { & $TailcamBin app uninstall 2>$null; & $TailcamBin uninstall-service }
elseif (Test-Path $LegacyBin) { & $LegacyBin uninstall-service }
# Cover the desktop shortcut even if the venv is already gone.
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\TailCam.lnk" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "TailCam" -ErrorAction SilentlyContinue
# uninstall-service removes both task names, but cover an install too old to do so.
Stop-ScheduledTask -TaskName "AnyCam" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "AnyCam" -Confirm:$false -ErrorAction SilentlyContinue

function Get-ServePort {
  # The tailnet HTTPS port lives in config.toml ([tailscale] serve_port);
  # default 8443. Read it with a plain regex so this works without the venv.
  $port = 8443
  foreach ($base in @($env:TAILCAM_CONFIG_DIR, (Join-Path $env:APPDATA "TailCam"))) {
    if (-not $base) { continue }
    $cfg = Join-Path $base "config.toml"
    if (-not (Test-Path $cfg)) { continue }
    try {
      $text = Get-Content $cfg -Raw -ErrorAction Stop
      $m = [regex]::Match($text, '(?ms)^\[tailscale\]\s*$(.*?)(?=^\[|\z)')
      if ($m.Success) {
        $pm = [regex]::Match($m.Groups[1].Value, '(?m)^\s*serve_port\s*=\s*(\d+)')
        if ($pm.Success) { $port = [int]$pm.Groups[1].Value }
      }
    } catch { }
    break
  }
  return $port
}

if (Get-Command tailscale -ErrorAction SilentlyContinue) {
  $servePort = Get-ServePort
  Info "Resetting tailscale serve (port $servePort)"
  tailscale serve --https $servePort off 2>$null
}

foreach ($v in @($VenvDir, $LegacyVenvDir)) {
  if (Test-Path $v) {
    Remove-Item -Recurse -Force $v
    Info "Removed virtualenv $v"
  }
}

if (-not $KeepData) {
  foreach ($d in $DataDirs) {
    if (Test-Path $d) {
      $ans = Read-Host "Delete stored media and database at $d? [y/N]"
      if ($ans -match '^[yY]') { Remove-Item -Recurse -Force $d; Info "Deleted $d" }
      else { Info "Kept $d" }
    }
  }
}

Info "TailCam uninstalled."
