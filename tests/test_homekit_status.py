"""Settings polling must not resolve, execute or repair bundled FFmpeg."""

from __future__ import annotations

import builtins
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tailcam.config import AppConfig
from tailcam.integrations.homekit import HomeKitBridge
from tailcam.timelapse import ffmpeg


def forbidden(*args, **kwargs):
    raise AssertionError("Status invoked an active runtime operation")


def bridge(configured="ffmpeg"):
    config = AppConfig()
    config.homekit.ffmpeg = configured
    return HomeKitBridge(SimpleNamespace(config=config))


def test_integrations_poll_does_not_import_run_or_chmod_ffmpeg(client, monkeypatch, tmp_path):
    package = tmp_path / "imageio_ffmpeg"
    binaries = package / "binaries"
    binaries.mkdir(parents=True)
    executable = binaries / "ffmpeg-platform"
    executable.write_bytes(b"not an executable")
    original_mode = executable.stat().st_mode
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] == "imageio_ffmpeg":
            forbidden()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(ffmpeg, "_KNOWN_BINARIES", {})
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    monkeypatch.delenv("IMAGEIO_FFMPEG_EXE", raising=False)
    monkeypatch.setattr(
        ffmpeg.importlib.util, "find_spec",
        lambda name: SimpleNamespace(origin=str(package / "__init__.py")),
    )
    monkeypatch.setattr(ffmpeg.os, "access", lambda *args: False)
    monkeypatch.setattr(ffmpeg.os, "chmod", forbidden)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", forbidden)
    monkeypatch.setattr(ffmpeg, "run_hidden", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    for _ in range(2):
        response = client.get("/api/integrations")
        assert response.status_code == 200
        assert response.json()["homekit"]["ffmpeg_present"] is False
    assert executable.read_bytes() == b"not an executable"
    assert executable.stat().st_mode == original_mode


@pytest.mark.parametrize("configured", ["custom-ffmpeg", "/custom/tools/ffmpeg"])
def test_homekit_status_recognizes_configured_executable_without_fallback(monkeypatch, configured):
    monkeypatch.setattr(
        ffmpeg.shutil, "which", lambda name: configured if name == configured else None,
    )
    monkeypatch.setattr(ffmpeg, "passive_ffmpeg_present", forbidden)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", forbidden)
    assert bridge(configured).ffmpeg_present() is True


@pytest.mark.parametrize("present", [True, False])
def test_homekit_status_uses_passive_fallback_for_missing_configured_binary(monkeypatch, present):
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    passive = Mock(return_value=present)
    monkeypatch.setattr(ffmpeg, "passive_ffmpeg_present", passive)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", forbidden)
    assert bridge("missing-custom-ffmpeg").ffmpeg_present() is present
    passive.assert_called_once_with()


def test_homekit_startup_keeps_active_ffmpeg_resolution(monkeypatch):
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    resolve = Mock(return_value="bundled-ffmpeg")
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", resolve)
    monkeypatch.setattr(ffmpeg, "passive_ffmpeg_present", forbidden)
    assert bridge()._ffmpeg() == "bundled-ffmpeg"
    resolve.assert_called_once_with()
