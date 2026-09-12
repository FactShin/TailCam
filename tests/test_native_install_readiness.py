"""Execute native handoff/rollback paths with all service boundaries mocked."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer")
@pytest.mark.parametrize("platform", ["linux", "macos"])
@pytest.mark.parametrize(
    "stage", ["pip", "config", "registration", "readiness", "success", "no-service"]
)
def test_native_install_keeps_previous_until_startup(platform, stage, tmp_path):
    script = (ROOT / f"install-{platform}.sh").read_text()
    functions = script[script.index("rollback_install() {"):script.index("\n# Remove a pre-rename")]
    service = script[script.index("setup_service() {"):]
    service = service[:service.index("\n}\n") + 3]
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "previous").write_text("keep")
    plist = tmp_path / "Library/LaunchAgents/com.tailcam.plist"
    if platform == "macos":
        plist.parent.mkdir(parents=True)
        plist.write_text("previous service")
    fake_python = tmp_path / "python"
    executable(fake_python, f'''#!{sys.executable}
import os, pathlib, shutil, sys
args = sys.argv[1:]
venv = pathlib.Path(os.environ["VENV_DIR"])
stage = os.environ["STAGE"]
name = pathlib.Path(sys.argv[0]).name
if args[:2] == ["-m", "venv"]:
    (venv / "bin").mkdir(parents=True)
    for name in ("pip", "python", "tailcam"):
        shutil.copy2(__file__, venv / "bin" / name)
elif name == "pip":
    sys.exit(1 if stage == "pip" else 0)
else:
    action = "registration" if name == "tailcam" else "readiness"
    assert pathlib.Path(str(venv) + ".bak", "previous").read_text() == "keep"
    with open(os.environ["CALLS"], "a") as out:
        out.write(action + " " + " ".join(args) + "\\n")
    sys.exit(1 if stage == action else 0)
''')
    body = "set -eu\n" + functions + service + '''
log() { printf '%s\n' "$*"; }; warn() { printf '%s\n' "$*"; }; err() { printf '%s\n' "$*"; }
have() { return 1; }; can_sudo() { return 1; }; node_has_role() { return 1; }
configure_node() { [ "$STAGE" != config ]; }
systemctl() { printf 'systemctl %s\n' "$*" >> "$CALLS"; }
launchctl() { printf 'launchctl %s\n' "$*" >> "$CALLS"; }
install_tailcam
setup_service
'''
    result = subprocess.run(
        ["bash", "-c", body],
        env=dict(os.environ, VENV_DIR=str(venv), REF="", VERSION="1.9.1", DO_DESKTOP="0",
                 DO_SERVICE="0" if stage == "no-service" else "1", PYTHON=str(fake_python),
                 STAGE=stage, CALLS=str(tmp_path / "calls"), HOME=str(tmp_path)),
        text=True, capture_output=True,
    )
    calls = (tmp_path / "calls").read_text()
    backup = Path(str(venv) + ".bak")
    if stage in {"success", "no-service"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (venv / "previous").exists()
        if stage == "success":
            assert not backup.exists()
            assert calls.index("registration install-service") < calls.index("readiness -m")
            assert "readiness -m tailcam.service.readiness --timeout 30" in calls
        else:
            assert (backup / "previous").read_text() == "keep"
            assert "registration" not in calls and "readiness" not in calls
            assert "Service not started" in result.stdout
    else:
        assert result.returncode != 0
        assert (venv / "previous").read_text() == "keep"
        assert not backup.exists()
        assert "left stopped" in result.stdout
        assert "tailcam install-service" in result.stdout
        if stage == "registration":
            assert "readiness -m" not in calls
        if platform == "linux":
            assert "systemctl --user disable --now tailcam.service" in calls
        else:
            assert not plist.exists()
            assert plist.with_suffix(".plist.failed").read_text() == "previous service"
    assert "--user start" not in calls and "launchctl load " not in calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX installer")
def test_macos_noninteractive_sudo_never_opens_terminal(tmp_path):
    script = (ROOT / "install-macos.sh").read_text()
    block = script[script.index("install_tailscale() {"):script.index("\nts_explain_state()")]
    body = "set -eu\n" + block + '''
log() { :; }; warn() { printf '%s\n' "$*"; }; have() { return 0; }
brew() { return 0; }
sudo() { printf '%s\n' "$*" > "$CALLS"; [ "$1" = -n ]; return 1; }
install_tailscale
'''
    result = subprocess.run(
        ["bash", "-c", body], text=True, capture_output=True,
        env=dict(os.environ, DO_TAILSCALE_INSTALL="1", NONINTERACTIVE="1",
                 CALLS=str(tmp_path / "sudo")),
    )
    assert result.returncode == 0
    assert (tmp_path / "sudo").read_text().strip() == "-n tailscaled install-system-daemon"
    assert "Passwordless sudo unavailable" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell installer")
@pytest.mark.parametrize("stage", ["registration", "readiness", "success", "no-service"])
def test_windows_handoff_preserves_backup_and_failure_exit(stage, tmp_path):
    script = (ROOT / "install.ps1").read_text()
    restore = script[
        script.index("  function Restore-Previous"):script.index('  Info "Creating virtualenv')
    ]
    block = script[script.index("  # New install verified"):script.index("  # Legacy binaries")]
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "candidate").write_text("candidate")
    backup = tmp_path / "venv.old"
    backup.mkdir()
    (backup / "previous").write_text("keep")
    stub = tmp_path / "python.ps1"
    stub.write_text('''
$action = if ($args[1] -eq "tailcam") { "registration" } else { "readiness" }
if (-not (Test-Path "$env:TEST_BACKUP/previous")) { throw "Backup discarded prematurely" }
Add-Content $env:TEST_CALLS ($action + " " + ($args -join " "))
$global:LASTEXITCODE = if ($env:TEST_STAGE -eq $action) { 1 } else { 0 }
''')
    harness = tmp_path / "handoff.ps1"
    harness.write_text('''
$ErrorActionPreference = "Stop"
$VenvDir = $env:TEST_VENV; $BackupDir = $env:TEST_BACKUP; $VenvPy = $env:TEST_PYTHON
$HadPrevious = $true; $NoService = $env:TEST_STAGE -eq "no-service"
function Info($text) { Write-Output $text }; function Warn($text) { Write-Output $text }
function Fail($text) { throw $text }
function Stop-TailCamProcesses { Add-Content $env:TEST_CALLS "stop" }
function Disable-ScheduledTask { Add-Content $env:TEST_CALLS "disable" }
''' + restore + block)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=dict(os.environ, TEST_VENV=str(venv), TEST_BACKUP=str(backup),
                 TEST_PYTHON=str(stub), TEST_STAGE=stage, TEST_CALLS=str(tmp_path / "calls")),
        text=True, capture_output=True,
    )
    calls_path = tmp_path / "calls"
    calls = calls_path.read_text() if calls_path.exists() else ""
    if stage in {"registration", "readiness"}:
        assert result.returncode != 0
        assert (venv / "previous").read_text() == "keep"
        assert "stop" in calls and "disable" in calls
    elif stage == "success":
        assert result.returncode == 0, result.stdout + result.stderr
        assert not backup.exists()
        assert calls.index("registration") < calls.index("readiness")
        assert "--timeout 30" in calls
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (backup / "previous").exists()
        assert not calls
        assert "Service not started" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell installer")
def test_windows_top_level_failure_returns_nonzero_without_prompt(tmp_path):
    script = (ROOT / "install.ps1").read_text()
    driver = script[script.index("$TranscriptStarted = $false"):]
    harness = tmp_path / "driver.ps1"
    harness.write_text('''
$NonInteractive = $true
$LogPath = "mock-install.log"
function Start-Transcript { }; function Stop-Transcript { }
function Read-Host { throw "Unexpected interactive prompt" }
function Install-TailCam { throw "Mock service registration failed" }
''' + driver)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-File", str(harness)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "Mock service registration failed" in result.stdout + result.stderr
    assert "Full install log" in result.stdout
    assert "Unexpected interactive prompt" not in result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell installer")
@pytest.mark.parametrize("layout", ["current", "legacy", "both", "fresh"])
def test_windows_stops_legacy_only_install_before_migration(layout, tmp_path):
    script = (ROOT / "install.ps1").read_text()
    block = script[
        script.index("  $HadPrevious = $false"):script.index("  function Restore-Previous")
    ]
    venv = tmp_path / "venv"
    legacy = tmp_path / "anycam-venv"
    backup = tmp_path / "venv.old"
    if layout in {"current", "both"}:
        venv.mkdir()
        (venv / "current").write_text("keep")
    if layout in {"legacy", "both"}:
        legacy.mkdir()
        (legacy / "legacy").write_text("keep")
    harness = tmp_path / "stop-before-migration.ps1"
    harness.write_text('''
$ErrorActionPreference = "Stop"
$VenvDir = $env:TEST_VENV; $LegacyVenvDir = $env:TEST_LEGACY; $BackupDir = $env:TEST_BACKUP
function Info($text) { }; function Fail($text) { throw $text }
function Stop-TailCamProcesses {
  if (Test-Path $BackupDir) { throw "Stop happened after the current venv moved" }
  Add-Content $env:TEST_CALLS "stop"
}
''' + block + 'Add-Content $env:TEST_CALLS "configure-next"\n')
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=dict(os.environ, TEST_VENV=str(venv), TEST_LEGACY=str(legacy),
                 TEST_BACKUP=str(backup), TEST_CALLS=str(tmp_path / "calls")),
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == (["configure-next"] if layout == "fresh" else ["stop", "configure-next"])
    if layout in {"legacy", "both"}:
        assert (legacy / "legacy").read_text() == "keep"
    if layout in {"current", "both"}:
        assert (backup / "current").read_text() == "keep"
