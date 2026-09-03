"""Host profile + the low-power defaults it drives."""

from __future__ import annotations

import tailcam.hostinfo as hostinfo
from tailcam.config import AppConfig


def _reset(monkeypatch, low: bool):
    monkeypatch.setenv("TAILCAM_LOW_POWER", "1" if low else "0")
    hostinfo.profile.cache_clear()


def test_profile_override_and_presets(monkeypatch):
    _reset(monkeypatch, True)
    assert hostinfo.is_low_power() and hostinfo.x264_preset() == "ultrafast"
    assert hostinfo.encode_threads() == 2
    _reset(monkeypatch, False)
    assert not hostinfo.is_low_power() and hostinfo.x264_preset() == "veryfast"
    hostinfo.profile.cache_clear()


def test_low_power_profile_only_touches_defaults(monkeypatch):
    cfg = AppConfig()
    cfg.stream.default_fps = 25  # the user tuned this
    changed = cfg.apply_low_power_profile(only_defaults=True)
    assert cfg.stream.default_fps == 25
    assert cfg.stream.max_width == 960 and cfg.detection.enabled is False
    assert "StreamConfig.max_width" in changed and "DetectionConfig.enabled" in changed


def test_v3_migration_applies_profile_on_low_power_host(monkeypatch):
    _reset(monkeypatch, True)
    cfg = AppConfig.from_dict({"config_version": 2, "stream": {"default_fps": 15}})
    assert cfg.detection.enabled is False and cfg.stream.default_fps == 10
    # A v3 file is never re-migrated (the user may have turned detection on).
    cfg = AppConfig.from_dict({"config_version": 3, "detection": {"enabled": True}})
    assert cfg.detection.enabled is True
    _reset(monkeypatch, False)
    cfg = AppConfig.from_dict({"config_version": 2})
    assert cfg.detection.enabled is True and cfg.stream.default_fps == 15
    hostinfo.profile.cache_clear()


def test_fresh_config_on_low_power_host(isolated_env, monkeypatch):
    _reset(monkeypatch, True)
    cfg = AppConfig.load()
    assert cfg.detection.enabled is False and cfg.stream.default_fps == 10
    _reset(monkeypatch, False)
    hostinfo.profile.cache_clear()


def test_homekit_stream_cmd_uses_host_preset(monkeypatch):
    from tailcam.integrations.homekit import build_stream_cmd

    _reset(monkeypatch, True)
    cmd = build_stream_cmd("http://127.0.0.1:8088/stream/x.mjpg")
    assert "-preset ultrafast" in cmd and "-threads 2" in cmd and "zerolatency" in cmd
    hostinfo.profile.cache_clear()


def test_mjpeg_encode_cache_shares_one_encode(monkeypatch):
    import numpy as np

    from tailcam.camera.frame import FrameBuffer
    from tailcam.camera.transforms import StreamTransform
    from tailcam.streaming import mjpeg

    calls = []

    def fake_encode(image, quality):
        calls.append(quality)
        return b"jpeg"

    monkeypatch.setattr(mjpeg, "encode_jpeg", fake_encode)
    backend = mjpeg.MJPEGBackend()
    buf = FrameBuffer()
    frame = buf.publish(np.zeros((4, 4, 3), np.uint8))
    t = StreamTransform()
    assert backend._encode(buf, frame.seq, frame.image, t, 80) == b"jpeg"
    assert backend._encode(buf, frame.seq, frame.image, t, 80) == b"jpeg"  # cached
    assert len(calls) == 1
    backend._encode(buf, frame.seq, frame.image, t, 55)  # different quality → new encode
    assert len(calls) == 2
    nxt = buf.publish(np.zeros((4, 4, 3), np.uint8))
    backend._encode(buf, nxt.seq, nxt.image, t, 80)  # new frame → new encode
    assert len(calls) == 3
