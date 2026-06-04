<#  AnyCam uninstaller for Windows. #>
[CmdletBinding()]
param([switch]$KeepData)

$ErrorActionPreference = "Continue"
$VenvDir = Join-Path $env:LOCALAPPDATA "AnyCam\venv"
$DataDir = Join-Path $env:LOCALAPPDATA "AnyCam"
$AnycamBin = Join-Path $VenvDir "Scripts\anycam.exe"

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }

Info "Removing AnyCam"
if (Test-Path $AnycamBin) { & $AnycamBin uninstall-service }

if (Get-Command tailscale -ErrorAction SilentlyContinue) {
  Info "Resetting tailscale serve"
  tailscale serve --https 8443 off 2>$null
}

if (Test-Path $VenvDir) {
  Remove-Item -Recurse -Force $VenvDir
  Info "Removed virtualenv"
}

if (-not $KeepData -and (Test-Path $DataDir)) {
  $ans = Read-Host "Delete stored media and database at $DataDir? [y/N]"
  if ($ans -match '^[yY]') { Remove-Item -Recurse -Force $DataDir; Info "Deleted $DataDir" }
  else { Info "Kept $DataDir" }
}

Info "AnyCam uninstalled."
