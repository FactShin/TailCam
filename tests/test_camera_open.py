"""V4L2 open ladder: MJPEG + size are negotiated before the first STREAMON, a
refused size steps down instead of leaving the camera offline, and failures
carry an actionable diagnosis (the Pi two-webcam USB-bandwidth case)."""

from __future__ import annotations

import errno
import os

import numpy as np
import pytest

from tailcam.camera import source as src
from tailcam.camera.properties import CameraProperties
from tailcam.camera.source import CameraDescriptor, OpenCVCameraSource, linux_open_diagnosis

_FOURCC = {"MJPG": 0x47504A4D}


class _FakeCap:
    """Mimics cv2.VideoCapture(path, api, params): refuses sizes listed in
    ``refuse`` (as a bandwidth-starved uvcvideo would) and remembers what it
    was asked for."""

    refuse: set[tuple[int, int]] = set()
    calls: list[list[int]] = []

    def __init__(self, path, api, params=None):
        import cv2

        _FakeCap.calls.append(list(params or []))
        p = dict(zip((params or [])[::2], (params or [])[1::2], strict=False))
        self.size = (p.get(cv2.CAP_PROP_FRAME_WIDTH, 640), p.get(cv2.CAP_PROP_FRAME_HEIGHT, 480))
        self.fourcc = p.get(cv2.CAP_PROP_FOURCC, 0)
        self._ok = self.size not in _FakeCap.refuse

    def isOpened(self):
        return self._ok

    def read(self):
        if not self._ok:
            return False, None
        return True, np.zeros((self.size[1], self.size[0], 3), np.uint8)

    def get(self, prop):
        import cv2

        return {
            cv2.CAP_PROP_FOURCC: self.fourcc,
            cv2.CAP_PROP_FRAME_WIDTH: self.size[0],
            cv2.CAP_PROP_FRAME_HEIGHT: self.size[1],
        }.get(prop, 0)

    def set(self, *_a):
        return True

    def release(self):
        pass


@pytest.fixture
def fake_cv2(monkeypatch):
    import cv2

    _FakeCap.calls = []
    _FakeCap.refuse = set()
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCap)
    monkeypatch.setattr(cv2, "VideoWriter_fourcc", lambda *c: _FOURCC["".join(c)])
    monkeypatch.setattr(src.sys, "platform", "linux")
    monkeypatch.delenv("TAILCAM_RAW_V4L2", raising=False)
    return cv2


def _source(w=1280, h=720):
    return OpenCVCameraSource(
        CameraDescriptor(id="/dev/video0", name="cam", backend="v4l2"),
        CameraProperties(width=w, height=h, fps=15),
    )


def test_mjpeg_and_size_are_requested_before_streaming(fake_cv2):
    cam = _source()
    assert cam.open()
    params = _FakeCap.calls[0]
    assert params[params.index(fake_cv2.CAP_PROP_FOURCC) + 1] == _FOURCC["MJPG"]
    assert params[params.index(fake_cv2.CAP_PROP_FRAME_WIDTH) + 1] == 1280
    assert params[params.index(fake_cv2.CAP_PROP_BUFFERSIZE) + 1] == 1
    assert cam.negotiated == ("MJPG", 1280, 720)
    assert cam.last_error == ""


def test_refused_720p_steps_down_to_480p_instead_of_offline(fake_cv2):
    _FakeCap.refuse = {(1280, 720)}
    cam = _source()
    assert cam.open()
    assert cam.negotiated == ("MJPG", 640, 480)
    assert "refused" in cam.last_error and "USB bandwidth" in cam.last_error


def test_raw_opt_out_skips_mjpeg(fake_cv2, monkeypatch):
    monkeypatch.setenv("TAILCAM_RAW_V4L2", "1")
    cam = _source()
    assert cam.open()
    assert fake_cv2.CAP_PROP_FOURCC not in _FakeCap.calls[0]


def test_total_failure_reports_bandwidth_diagnosis(fake_cv2, monkeypatch):
    _FakeCap.refuse = {(1280, 720), (640, 480)}
    monkeypatch.setattr(src.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(src.os, "close", lambda fd: None)
    cam = _source()
    assert not cam.open()
    assert "USB bandwidth" in cam.last_error
    assert len(_FakeCap.calls) == 4  # MJPG 720p, MJPG 480p, raw 720p, raw default


def test_linux_diagnosis_distinguishes_os_errors(monkeypatch):
    def raise_(exc):
        def _o(*a, **k):
            raise exc
        return _o

    monkeypatch.setattr(src.os, "open", raise_(FileNotFoundError()))
    assert "unplugged" in linux_open_diagnosis("/dev/video9")
    monkeypatch.setattr(src.os, "open", raise_(PermissionError()))
    assert "video group" in linux_open_diagnosis("/dev/video9")
    monkeypatch.setattr(src.os, "open", raise_(OSError(errno.EBUSY, "busy")))
    assert "in use" in linux_open_diagnosis("/dev/video9")
    monkeypatch.setattr(src.os, "open", lambda *a, **k: 7)
    monkeypatch.setattr(src.os, "close", lambda fd: None)
    assert "USB bandwidth" in linux_open_diagnosis("/dev/video9")


def test_uvc_quirk_detection(tmp_path, monkeypatch):
    conf_dir = tmp_path / "etc" / "modprobe.d"
    conf_dir.mkdir(parents=True)
    real_path = src.Path
    monkeypatch.setattr(
        src, "Path", lambda p: tmp_path / p.lstrip("/") if p.startswith("/") else real_path(p)
    )
    assert src.uvc_bandwidth_quirk_active() is False
    (conf_dir / "tailcam-uvcvideo.conf").write_text("options uvcvideo quirks=0x80\n")
    assert src.uvc_bandwidth_quirk_active() is True


def test_worker_surfaces_source_diagnosis(fake_cv2, monkeypatch):
    from tailcam.camera.worker import CameraWorker

    _FakeCap.refuse = {(1280, 720), (640, 480)}
    monkeypatch.setattr(src.os, "open", lambda *a, **k: 99)
    monkeypatch.setattr(src.os, "close", lambda fd: None)
    worker = CameraWorker(
        CameraDescriptor(id="/dev/video0", name="cam", backend="v4l2"),
        properties=CameraProperties(width=1280, height=720, fps=15),
        source_factory=OpenCVCameraSource,  # the suite defaults to the synthetic source
    )
    worker.start()
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not worker.state.last_error:
        time.sleep(0.05)
    worker.stop()
    assert "USB bandwidth" in (worker.state.last_error or "")
    assert os is not None
