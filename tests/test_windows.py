"""Windows platform-dispatch tests — run on any OS via monkeypatching.

These don't require a Windows machine; they verify that the OS branches resolve
to the Windows code paths and build the right commands/paths.
"""

import subprocess
import sys

import cv2

from anycam import paths
from anycam.camera import enumerate as cam_enumerate
from anycam.camera.properties import CameraProperties
from anycam.camera.source import CameraDescriptor, OpenCVCameraSource
from anycam.service import installer
from anycam.tailscale.client import _KNOWN_BINARIES


def _win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    for var in ("ANYCAM_CONFIG_DIR", "ANYCAM_DATA_DIR", "ANYCAM_CONFIG", "ANYCAM_SYNTHETIC"):
        monkeypatch.delenv(var, raising=False)


def test_paths_use_appdata(monkeypatch):
    _win(monkeypatch)
    # Use forward slashes so the assertion holds even when this test runs on a
    # POSIX host (PurePosixPath doesn't treat "\\" as a separator).
    monkeypatch.setenv("APPDATA", "C:/Users/me/AppData/Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/me/AppData/Local")
    assert str(paths.config_dir()).replace("\\", "/").endswith("AppData/Roaming/AnyCam")
    assert str(paths.data_dir()).replace("\\", "/").endswith("AppData/Local/AnyCam")
    assert paths.config_file().name == "config.toml"


def test_source_uses_directshow(monkeypatch):
    _win(monkeypatch)
    src = OpenCVCameraSource(
        CameraDescriptor(id="0", name="Cam 0", backend="dshow"), CameraProperties()
    )
    assert src._api_preference() == cv2.CAP_DSHOW
    assert src._device_arg() == 0  # dshow uses an integer index


def test_enumerate_windows(monkeypatch):
    _win(monkeypatch)

    class FakeCap:
        def __init__(self, opened: bool):
            self._opened = opened

        def isOpened(self):
            return self._opened

        def release(self):
            pass

    # Only index 0 opens.
    monkeypatch.setattr(cv2, "VideoCapture", lambda index, api: FakeCap(index == 0))

    found = cam_enumerate.discover()
    assert len(found) == 1
    assert found[0].backend == "dshow"
    assert found[0].id == "0"


def test_enumerate_windows_falls_back_to_synthetic(monkeypatch):
    _win(monkeypatch)

    class ClosedCap:
        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", lambda index, api: ClosedCap())
    found = cam_enumerate.discover()
    assert found == [cam_enumerate.SYNTHETIC_DESCRIPTOR]


def test_installer_windows_schtask(monkeypatch):
    _win(monkeypatch)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))

    msg = installer.install()
    assert "AnyCam" in msg
    # PowerShell Register-ScheduledTask (robust quoting), at logon.
    register = next(c for c in calls if any("Register-ScheduledTask" in str(a) for a in c))
    script = register[-1]
    assert "New-ScheduledTaskTrigger -AtLogOn" in script
    assert "-m anycam run" in script
    assert "AnyCam" in script

    calls.clear()
    installer.uninstall()
    assert any(any("Unregister-ScheduledTask" in str(a) for a in c) for c in calls)


def test_tailscale_known_windows_path():
    assert any(p.endswith("Tailscale\\tailscale.exe") for p in _KNOWN_BINARIES)
