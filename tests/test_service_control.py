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
