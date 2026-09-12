"""Regression guards for install.ps1 (v1.1.2: Windows-on-ARM + visibility).

We can't execute PowerShell in CI/Linux, so these pin the load-bearing
*textual* invariants of the installer — the two failure classes here
(host-killing `exit`, architecture-blind Python selection) are one careless
edit away from coming back.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "install.ps1").read_text(encoding="utf-8")


def test_fail_never_exits_the_host():
    # Under `irm | iex` the script runs in session scope: `exit` closes the
    # entire PowerShell window before the error can be read. Fail must throw.
    m = re.search(r"function Fail\(\$m\)\s*{([^}]*)}", SCRIPT)
    assert m, "Fail() helper missing"
    body = m.group(1)
    assert "throw" in body
    assert not re.search(r"\bexit\b", body), "Fail() must not call exit"
    # No bare `exit` anywhere in the script (Stop-Transcript et al. excluded).
    assert not re.search(r"(?m)^\s*exit\b", SCRIPT)


def test_transcript_and_pause_on_error():
    assert "Start-Transcript" in SCRIPT
    assert "Stop-Transcript" in SCRIPT
    assert "install-" in SCRIPT and "$LogPath" in SCRIPT
    # Interactive failure keeps the window open; the detached self-updater
    # must skip the pause or it would hang forever with no console.
    assert "Read-Host" in SCRIPT
    assert "TAILCAM_INSTALL_NONINTERACTIVE" in SCRIPT


def test_arm64_selects_x64_python():
    assert "PROCESSOR_ARCHITECTURE" in SCRIPT
    assert "PROCESSOR_ARCHITEW6432" in SCRIPT  # emulated-shell case
    # winget must be forced off the native arm64 build.
    assert re.search(r'--architecture["\s,]+.{0,4}x64', SCRIPT), (
        "winget install must pass --architecture x64 on ARM64"
    )
    # Pre-existing native ARM64 interpreters must be rejected, not reused.
    assert "platform.machine()" in SCRIPT
    # Direct python.org x64 fallback when winget is missing.
    assert "amd64.exe" in SCRIPT


def test_venv_is_built_at_its_final_path():
    # pip's Windows launcher .exes embed the ABSOLUTE interpreter path at
    # install time, so a venv built at one path and renamed to another leaves
    # every console script broken ("Fatal error in launcher"). The new venv
    # must be created at $VenvDir directly and NEVER moved into place.
    assert "-m venv $VenvDir" in SCRIPT
    # The only Move-Item calls touch the BACKUP (old venv aside / restore) —
    # never a freshly built venv.
    moves = re.findall(r"Move-Item\s+(\S+)\s+(\S+)", SCRIPT)
    assert ("$VenvDir", "$BackupDir") in moves  # old install set aside
    assert ("$BackupDir", "$VenvDir") in moves  # rollback restore
    for src, _dest in moves:
        assert src in ("$VenvDir", "$BackupDir"), f"unexpected venv move from {src}"


def test_failed_install_restores_previous():
    # A failed pip run must put the old venv back (rename back = launchers
    # valid again, since the path is restored), leaving the service stopped.
    assert "function Restore-Previous" in SCRIPT
    body = SCRIPT[
        SCRIPT.index("function Restore-Previous"):SCRIPT.index('  Info "Creating virtualenv')
    ]
    assert "Move-Item $BackupDir $VenvDir" in body
    assert "Stop-TailCamProcesses" in body
    assert 'Disable-ScheduledTask -TaskName "TailCam"' in body
    assert "Start-ScheduledTask" not in body
    # Every install failure path routes through the restore helper.
    assert SCRIPT.count("Restore-Previous ") >= 3  # venv fail, pip fail(s), exe missing
    # The backup is only discarded after tailcam.exe is verified present.
    discard = SCRIPT.index("if ($HadPrevious) { Remove-Item -Recurse -Force $BackupDir")
    assert SCRIPT.index("Test-Path $TailcamBin") < discard
    assert SCRIPT.index("-m tailcam.service.readiness --timeout 30") < discard


def test_setup_runs_via_python_m_not_launcher_exes():
    # Setup steps invoke `python -m tailcam ...` so they can never hit a stale
    # launcher stub; tailcam.exe is only checked for existence and displayed.
    assert "-m tailcam setup @SetupArgs" in SCRIPT
    assert "-m tailcam install-service" in SCRIPT
    assert "-m tailcam tailscale serve" in SCRIPT
    assert "-m tailcam status" in SCRIPT
    assert not re.search(r"& \$TailcamBin ", SCRIPT), "no direct launcher-exe invocations"


def test_exit_codes_checked():
    # venv creation and pip both gate on $LASTEXITCODE; tailcam.exe presence
    # is verified before use.
    assert re.search(r"-m venv \$VenvDir\s*\n\s*if \(\$LASTEXITCODE -ne 0\)", SCRIPT)
    assert "Test-Path $TailcamBin" in SCRIPT


def test_self_updater_sets_noninteractive():
    from tailcam.update import PS_INSTALL_CMD

    assert "TAILCAM_INSTALL_NONINTERACTIVE" in PS_INSTALL_CMD
    assert "install.ps1 | iex" in PS_INSTALL_CMD


def test_pyproject_arm64_markers():
    # Native win-arm64 Python defense in depth: no httptools (never shipped a
    # win_arm64 wheel), imageio-ffmpeg skipped (no wheel; degrades gracefully).
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "uvicorn[standard]~=0.34; platform_machine != 'ARM64'" in pyproject
    assert "uvicorn~=0.34; platform_machine == 'ARM64'" in pyproject
    assert "websockets>=13; platform_machine == 'ARM64'" in pyproject
    assert "imageio-ffmpeg~=0.5; platform_machine != 'ARM64'" in pyproject


def test_native_probes_survive_ps51_stop_preference():
    # Windows PowerShell 5.1 + $ErrorActionPreference="Stop" turns redirected
    # native stderr (`2>$null`) into a terminating error. Every such probe
    # must run through the helper that relaxes the preference.
    assert "function Invoke-Native" in SCRIPT
    for probe in ("sys.version_info[:2] >= (3, 10)", "platform.machine()",
                  "status --json 2>$null", "ollama list 2>$null"):
        line = next(ln for ln in SCRIPT.splitlines() if probe in ln)
        assert "Invoke-Native" in line, f"probe not wrapped: {line.strip()}"


def test_tailscale_login_loop_gated_on_needslogin():
    # Poll only when `tailscale up` failed AND the daemon reports NeedsLogin;
    # an empty/Stopped state must bail out with advice, not wait 10 minutes.
    start = SCRIPT.index("function Wait-TailscaleLogin")
    body = SCRIPT[start:SCRIPT.index("if (-not $NoTailscale)")]
    assert "$upRc = $LASTEXITCODE" in body
    assert '$upRc -eq 0 -or $state -ne "NeedsLogin"' in body
    assert body.index('if (-not $state)') < body.index("while ($waited -lt $timeout)")


def test_tray_relaunched_after_install():
    # Stop-TailCamProcesses kills a running tray; when autostart is configured
    # (HKCU Run key) the installer brings it back instead of leaving it dead.
    tail = SCRIPT[SCRIPT.index("Setup-DesktopApp $VenvPy"):]
    assert "CurrentVersion\\Run" in tail
    assert "Start-Process" in tail
    assert "'-m','tailcam','app','--no-window'" in tail


def test_windows_setup_executes_before_services(isolated_env):
    import os
    import subprocess
    import sys

    import pytest

    if sys.platform != "win32":
        pytest.skip("Windows PowerShell runtime check")
    from tailcam import paths

    start = SCRIPT.index("  # Configure before registration")
    end = SCRIPT.index("  # New install verified", start)
    block = SCRIPT[start:end]
    harness = isolated_env / "setup.ps1"
    harness.write_text(
        '$ErrorActionPreference = "Stop"\n'
        '$VenvPy = $env:TEST_PYTHON\n'
        '$Preset = "hub"; $Port = 9123; $NodeName = "Windows hub"\n'
        '$HadPrevious = $false\n'
        'function Fail($why) { throw $why }\n'
        'function Info($text) {}\n' + block,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=dict(os.environ, TEST_PYTHON=sys.executable), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    from tailcam.config import AppConfig
    assert AppConfig.load().node.roles == []
    assert AppConfig.load().server.port == 9123
    original = paths.config_file().read_bytes()
    # Also parse the complete installer with the actual Windows parser.
    command = (
        '$tokens=$null; $errors=$null; '
        '[System.Management.Automation.Language.Parser]::ParseFile('
        '$env:TEST_INSTALLER, [ref]$tokens, [ref]$errors) | Out-Null; '
        'if ($errors.Count) { throw ($errors | Out-String) }'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        env=dict(os.environ, TEST_INSTALLER=str(ROOT / "install.ps1")), check=True,
    )
    assert paths.config_file().read_bytes() == original
