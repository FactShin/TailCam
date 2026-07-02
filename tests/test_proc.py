"""The no-console-window subprocess helper (Windows cmd-popup fix)."""

from __future__ import annotations

import subprocess

from tailcam import proc
from tailcam.tailscale.client import TailscaleClient


def test_run_adds_no_window_flag_on_windows(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    monkeypatch.setattr(proc, "NO_WINDOW", 0x08000000)  # as on win32
    proc.run(["tailscale", "status"], capture_output=True)
    assert seen["creationflags"] & 0x08000000
    assert seen["capture_output"] is True


def test_run_preserves_existing_creationflags(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    monkeypatch.setattr(proc, "NO_WINDOW", 0x08000000)
    proc.run(["x"], creationflags=0x00000008)  # DETACHED_PROCESS
    assert seen["creationflags"] == (0x00000008 | 0x08000000)


def test_run_is_passthrough_on_posix(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    monkeypatch.setattr(proc, "NO_WINDOW", 0)
    proc.run(["x"], capture_output=True)
    assert "creationflags" not in seen  # untouched off-Windows


def test_tailscale_status_is_cached(monkeypatch):
    """Multiple pollers within the TTL must share one `tailscale status` spawn."""
    client = TailscaleClient.__new__(TailscaleClient)
    client._binary = "/usr/bin/true"
    # Pre-seed the one-time capability probe so status() spawns exactly one CLI
    # call and the count below isolates the status cache itself.
    client._app_capabilities_supported = False
    import threading

    client._status_lock = threading.Lock()
    client._status_cache = None
    client._status_ts = 0.0

    calls = {"n": 0}

    def fake_run(*args):
        calls["n"] += 1
        return subprocess.CompletedProcess(
            args, 0, '{"BackendState":"Running","Self":{"TailscaleIPs":["100.1.2.3"]}}', ""
        )

    monkeypatch.setattr(client, "_run", fake_run)
    first = client.status()
    second = client.status()
    assert calls["n"] == 1  # second call served from cache
    assert first.running and second.ipv4 == "100.1.2.3"
