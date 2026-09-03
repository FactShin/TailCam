"""Service start/stop/restart dispatch — verified per-platform via monkeypatching."""

import subprocess
import sys
from types import SimpleNamespace

from tailcam.service import installer


def _capture(monkeypatch):
    calls: list[list[str]] = []

    def runner(args, **kw):
        calls.append(list(args))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", runner)
    return calls


def test_not_installed_message(monkeypatch):
    monkeypatch.setattr(installer, "_installed", lambda: False)
    monkeypatch.setattr(sys, "platform", "linux")
    for fn in (installer.start, installer.stop, installer.restart):
        assert "install-service" in fn()


def test_linux_systemctl_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(installer, "_installed", lambda: True)
    calls = _capture(monkeypatch)

    assert "Started" in installer.start()
    assert ["systemctl", "--user", "start", "tailcam.service"] in calls
    calls.clear()
    assert "Stopped" in installer.stop()
    assert ["systemctl", "--user", "stop", "tailcam.service"] in calls
    calls.clear()
    assert "Restarted" in installer.restart()
    assert ["systemctl", "--user", "restart", "tailcam.service"] in calls


def test_macos_launchctl_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_installed", lambda: True)
    calls = _capture(monkeypatch)

    assert "Started" in installer.start()
    assert any(c[:2] == ["launchctl", "load"] for c in calls)
    calls.clear()
    # KeepAlive=true means a true stop must unload, not `launchctl stop`.
    assert "Stopped" in installer.stop()
    assert any(c[:2] == ["launchctl", "unload"] for c in calls)
    calls.clear()
    assert "Restarted" in installer.restart()
    assert any(c[:2] == ["launchctl", "unload"] for c in calls)
    assert any(c[:2] == ["launchctl", "load"] for c in calls)


def test_windows_schtask_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = _capture(monkeypatch)

    def flat() -> str:
        return " ".join(a for c in calls for a in c)

    assert "Started" in installer.start()
    assert "Start-ScheduledTask" in flat()
    calls.clear()
    assert "Stopped" in installer.stop()
    assert "Stop-ScheduledTask" in flat()
    calls.clear()
    assert "Restarted" in installer.restart()
    assert "Stop-ScheduledTask" in flat() and "Start-ScheduledTask" in flat()


# --- install(): unit rendering + failure reporting ---------------------------


def _fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.Path, "home", staticmethod(lambda: tmp_path))


def test_systemd_unit_rendering(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/home/my user/venv/bin/python")
    _fake_home(monkeypatch, tmp_path)
    calls = _capture(monkeypatch)

    msg = installer.install()
    assert not msg.startswith("FAILED")
    unit = (tmp_path / ".config" / "systemd" / "user" / "tailcam.service").read_text()
    # User managers have no network-online.target; the dependency was noise.
    assert "network-online" not in unit
    # A home path with a space must survive systemd's ExecStart word-splitting.
    assert 'ExecStart="/home/my user/venv/bin/python" -m tailcam run' in unit
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "tailcam.service"] in calls
    assert ["systemctl", "--user", "restart", "tailcam.service"] in calls


def test_systemd_install_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_home(monkeypatch, tmp_path)

    def runner(args, **kw):
        if args[:3] == ["systemctl", "--user", "restart"]:
            return SimpleNamespace(returncode=1, stderr="Failed to connect to bus: No medium found")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", runner)
    msg = installer.install()
    assert msg.startswith("FAILED")
    assert "systemctl --user restart tailcam.service" in msg
    assert "No medium found" in msg


def test_cli_install_service_exits_nonzero_on_failure(monkeypatch):
    from typer.testing import CliRunner

    from tailcam.cli import app

    monkeypatch.setattr(installer, "install", lambda: "FAILED: `systemctl --user enable` — boom")
    result = CliRunner().invoke(app, ["install-service"])
    assert result.exit_code == 1
    assert "FAILED" in result.stdout

    monkeypatch.setattr(installer, "install", lambda: "Installed systemd user service")
    assert CliRunner().invoke(app, ["install-service"]).exit_code == 0


def test_launchd_plist_rendering_and_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    _fake_home(monkeypatch, tmp_path)
    calls = _capture(monkeypatch)

    msg = installer.install()
    assert not msg.startswith("FAILED")
    plist = (tmp_path / "Library" / "LaunchAgents" / "com.tailcam.plist").read_text()
    # launchd agents get a minimal PATH; Homebrew/ffmpeg/tailscale live elsewhere.
    assert "<key>EnvironmentVariables</key>" in plist
    assert "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" in plist
    assert any(c[:2] == ["launchctl", "load"] for c in calls)

    def runner(args, **kw):
        if args[:2] == ["launchctl", "load"]:
            return SimpleNamespace(returncode=1, stderr="Load failed: 5: Input/output error")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", runner)
    msg = installer.install()
    assert msg.startswith("FAILED") and "Input/output error" in msg
