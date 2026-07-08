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
SCRIPT = (ROOT / "install.ps1").read_text()


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
    # valid again, since the path is restored) and restart the service.
    assert "function Restore-Previous" in SCRIPT
    body = SCRIPT[SCRIPT.index("function Restore-Previous"):]
    assert "Move-Item $BackupDir $VenvDir" in body
    assert 'Start-ScheduledTask -TaskName "TailCam"' in body
    # Every install failure path routes through the restore helper.
    assert SCRIPT.count("Restore-Previous ") >= 3  # venv fail, pip fail(s), exe missing
    # The backup is only discarded after tailcam.exe is verified present.
    discard = SCRIPT.index("if ($HadPrevious) { Remove-Item -Recurse -Force $BackupDir")
    assert SCRIPT.index("Test-Path $TailcamBin") < discard


def test_setup_runs_via_python_m_not_launcher_exes():
    # Setup steps invoke `python -m tailcam ...` so they can never hit a stale
    # launcher stub; tailcam.exe is only checked for existence and displayed.
    assert "-m tailcam config --port" in SCRIPT
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
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "uvicorn[standard]~=0.34; platform_machine != 'ARM64'" in pyproject
    assert "uvicorn~=0.34; platform_machine == 'ARM64'" in pyproject
    assert "websockets>=13; platform_machine == 'ARM64'" in pyproject
    assert "imageio-ffmpeg~=0.5; platform_machine != 'ARM64'" in pyproject
