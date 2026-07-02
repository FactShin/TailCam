"""v0.99.11: built-in plug-and-play detection, config migration, Windows camera
open ladder, and MCP fleet routing fixes."""

from __future__ import annotations

import numpy as np
import pytest

from tailcam.ai.analyzer import Detection
from tailcam.ai.detector import COCO_CLASSES, BuiltinDetector
from tailcam.config import AppConfig, DetectionConfig


# --------------------------------------------------------------- detector
def _ready_detector(cfg: DetectionConfig, boxes: list[Detection]) -> BuiltinDetector:
    det = BuiltinDetector(cfg)
    det._status = "ready"
    det._engine = "opencv"
    det._model_name = "yolov4-tiny"
    det._net_model = object()  # sentinel; _detect_opencv is stubbed below
    det._detect_opencv = lambda image: list(boxes)  # type: ignore[method-assign]
    return det


def test_detector_disabled_returns_empty_and_never_provisions():
    det = BuiltinDetector(DetectionConfig(enabled=False))
    assert det.detect(np.zeros((4, 4, 3), dtype=np.uint8)) == []
    assert det.status().status == "off"
    assert det._download_thread is None


def test_detector_class_filter():
    boxes = [
        Detection("person", 0.9, 0.5, 0.5, 0.2, 0.4),
        Detection("cup", 0.8, 0.2, 0.2, 0.1, 0.1),
    ]
    cfg = DetectionConfig(classes=["Person"])  # case-insensitive
    det = _ready_detector(cfg, boxes)
    out = det.detect(np.zeros((4, 4, 3), dtype=np.uint8))
    assert [d.label for d in out] == ["person"]
    cfg.classes = []
    assert len(det.detect(np.zeros((4, 4, 3), dtype=np.uint8))) == 2


def test_detector_engine_resolution_without_ultralytics(monkeypatch):
    det = BuiltinDetector(DetectionConfig(engine="auto"))
    monkeypatch.setattr(det, "_ultralytics_available", lambda: False)
    assert det._resolve_engine() == "opencv"
    monkeypatch.setattr(det, "_ultralytics_available", lambda: True)
    assert det._resolve_engine() == "ultralytics"
    det._config.engine = "opencv"  # explicit pin beats availability
    assert det._resolve_engine() == "opencv"


def test_coco_class_list_shape():
    assert len(COCO_CLASSES) == 80
    for expected in ("person", "cup", "bottle", "cat", "dog"):
        assert expected in COCO_CLASSES


# ------------------------------------------------------ inference routing
def test_router_uses_builtin_when_nothing_else_active(store):
    from tailcam.ai.analyzer import OllamaAnalyzer
    from tailcam.config import AIConfig, TrainingConfig
    from tailcam.training.inference import InferenceRouter

    boxes = [Detection("cup", 0.88, 0.5, 0.5, 0.2, 0.2)]
    builtin = _ready_detector(DetectionConfig(), boxes)
    router = InferenceRouter(store, TrainingConfig(), OllamaAnalyzer(AIConfig()), builtin=builtin)

    assert router.detection_active is True
    assert router.enabled is True  # motion workers will call analyze()
    out = router.detect(np.zeros((4, 4, 3), dtype=np.uint8))
    assert out is not None and out[0].label == "cup"
    analysis = router.analyze(np.zeros((4, 4, 3), dtype=np.uint8))
    assert analysis is not None and analysis.label == "cup"


def test_router_builtin_disabled_behaves_like_before(store):
    from tailcam.ai.analyzer import OllamaAnalyzer
    from tailcam.config import AIConfig, TrainingConfig
    from tailcam.training.inference import InferenceRouter

    builtin = BuiltinDetector(DetectionConfig(enabled=False))
    router = InferenceRouter(store, TrainingConfig(), OllamaAnalyzer(AIConfig()), builtin=builtin)
    assert router.detection_active is False
    assert router.detect(np.zeros((4, 4, 3), dtype=np.uint8)) is None
    assert router.enabled is False


def test_router_note_while_downloading(store):
    from tailcam.ai.analyzer import OllamaAnalyzer
    from tailcam.config import AIConfig, TrainingConfig
    from tailcam.training.inference import InferenceRouter

    builtin = BuiltinDetector(DetectionConfig())
    builtin._status = "downloading"
    builtin._percent = 42.0
    builtin._detail = "downloading yolov4-tiny.weights (23 MB)"
    router = InferenceRouter(store, TrainingConfig(), OllamaAnalyzer(AIConfig()), builtin=builtin)
    assert "42" in router.detection_note()


# ---------------------------------------------------------- config migration
def test_config_migration_flips_auto_record_from_v1(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[motion]\nenabled = true\nauto_record = false\n")
    config = AppConfig.load(cfg_file)
    assert config.motion.auto_record is True  # old default was a bug, not a choice
    assert config.motion.enabled is True  # other values untouched


def test_config_migration_respects_v2_choice(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("config_version = 2\n\n[motion]\nauto_record = false\n")
    config = AppConfig.load(cfg_file)
    assert config.motion.auto_record is False  # user's explicit post-v2 choice


def test_config_save_records_version_and_roundtrips(tmp_path):
    cfg_file = tmp_path / "config.toml"
    config = AppConfig()
    config.motion.auto_record = False
    config.detection.confidence = 0.6
    config.save(cfg_file)
    text = cfg_file.read_text()
    assert text.splitlines()[0].startswith("config_version = ")
    loaded = AppConfig.load(cfg_file)
    assert loaded.motion.auto_record is False
    assert loaded.detection.confidence == 0.6


def test_config_unknown_keys_do_not_reset_file(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "config_version = 99\n\n[server]\nport = 9999\nfrom_the_future = true\n"
    )
    config = AppConfig.load(cfg_file)
    assert config.server.port == 9999  # known key kept, unknown key dropped
    assert cfg_file.exists() and not (tmp_path / "config.toml.bad").exists()


def test_new_installs_default_to_clips_and_detection():
    config = AppConfig()
    assert config.motion.auto_record is True
    assert config.detection.enabled is True
    assert config.detection.overlay_default is True


# ------------------------------------------------------ Windows camera open
class _FakeCap:
    def __init__(self, opened: bool, frames: bool) -> None:
        self._opened = opened
        self._frames = frames
        self.released = False

    def isOpened(self):  # noqa: N802 - cv2 API shape
        return self._opened

    def read(self):
        if self._frames:
            return True, np.zeros((4, 4, 3), dtype=np.uint8)
        return False, None

    def release(self):
        self.released = True


def test_windows_open_falls_through_to_backend_that_delivers_frames(monkeypatch):
    import cv2

    from tailcam.camera import source as source_mod
    from tailcam.camera.properties import CameraProperties
    from tailcam.camera.source import CameraDescriptor, OpenCVCameraSource

    source_mod._WIN_BACKEND_CACHE.clear()
    # DSHOW "opens" but never produces a frame (the Dell symptom); MSMF works.
    caps = {cv2.CAP_DSHOW: (True, False), cv2.CAP_MSMF: (True, True)}
    calls: list[int] = []

    def fake_capture(arg, api):
        calls.append(api)
        opened, frames = caps.get(api, (False, False))
        return _FakeCap(opened, frames)

    monkeypatch.setattr(cv2, "VideoCapture", fake_capture)
    src = OpenCVCameraSource(
        CameraDescriptor(id="0", name="Integrated Camera", backend="dshow"),
        CameraProperties(),
    )
    monkeypatch.setattr(src, "_verify_frames", lambda cap, deadline_s=0.1: cap.read()[0])
    assert src._open_windows() is True
    assert calls == [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    # The working backend is remembered: a reconnect goes straight to MSMF.
    src2 = OpenCVCameraSource(
        CameraDescriptor(id="0", name="Integrated Camera", backend="dshow"),
        CameraProperties(),
    )
    calls.clear()
    monkeypatch.setattr(src2, "_verify_frames", lambda cap, deadline_s=0.1: cap.read()[0])
    assert src2._open_windows() is True
    assert calls == [cv2.CAP_MSMF]
    source_mod._WIN_BACKEND_CACHE.clear()


def test_windows_open_fails_when_no_backend_delivers(monkeypatch):
    import cv2

    from tailcam.camera import source as source_mod
    from tailcam.camera.properties import CameraProperties
    from tailcam.camera.source import CameraDescriptor, OpenCVCameraSource

    source_mod._WIN_BACKEND_CACHE.clear()
    monkeypatch.setattr(cv2, "VideoCapture", lambda arg, api: _FakeCap(True, False))
    src = OpenCVCameraSource(
        CameraDescriptor(id="1", name="IR Camera", backend="dshow"), CameraProperties()
    )
    monkeypatch.setattr(src, "_verify_frames", lambda cap, deadline_s=0.1: False)
    assert src._open_windows() is False
    assert "1" not in source_mod._WIN_BACKEND_CACHE


def test_verify_frames_accepts_first_good_frame():
    from tailcam.camera.properties import CameraProperties
    from tailcam.camera.source import CameraDescriptor, OpenCVCameraSource

    src = OpenCVCameraSource(
        CameraDescriptor(id="0", name="c", backend="dshow"), CameraProperties()
    )
    assert src._verify_frames(_FakeCap(True, True), deadline_s=0.5) is True
    assert src._verify_frames(_FakeCap(True, False), deadline_s=0.3) is False


def test_windows_enumeration_tolerates_gaps_with_known_names(monkeypatch):
    import cv2

    from tailcam.camera import enumerate as enum_mod

    # Two named devices; index 0 (IR cam) refuses to open, index 1 works. The
    # old stop-at-first-gap logic would have hidden the real webcam.
    monkeypatch.setattr(enum_mod, "_windows_names", lambda: ["IR Camera", "Integrated Camera"])
    monkeypatch.setattr(
        cv2, "VideoCapture", lambda idx, api: _FakeCap(opened=(idx == 1), frames=True)
    )
    found = enum_mod._enumerate_windows()
    assert [(d.id, d.name) for d in found] == [("1", "Integrated Camera")]


# ----------------------------------------------------------- MCP fleet fixes
class _FakeMcpClient:
    def __init__(self, cams: list[dict], hosts: list[dict] | None = None) -> None:
        self._cams = cams
        self.hosts_data = hosts or []
        self.calls: list[tuple] = []

    async def cameras(self, *, scope: str = "all"):
        return self._cams

    async def camera(self, camera_id: str, *, prefix: str = ""):
        self.calls.append(("camera", camera_id, prefix))
        for c in self._cams:
            if c["id"] == camera_id:
                return c
        raise AssertionError("unknown camera")

    async def snapshot(self, camera_id: str, *, prefix: str = ""):
        self.calls.append(("snapshot", camera_id, prefix))
        return {"media_id": 7}

    async def hosts(self):
        return self.hosts_data

    async def update_info(self):
        return {}


class _FakeToolCtx:
    def __init__(self, client) -> None:
        self.client = client

    def record_action(self, **kw):
        pass


@pytest.mark.anyio
async def test_mcp_snapshot_routes_through_owning_node():
    from tailcam.mcp.tools import _capture_snapshot

    client = _FakeMcpClient(
        [
            {"id": "remote-cam", "name": "R", "proxy_prefix": "/proxy/abc123"},
            {"id": "local-cam", "name": "L", "proxy_prefix": ""},
        ]
    )
    result = await _capture_snapshot(_FakeToolCtx(client), {"camera_id": "remote-cam"})
    assert ("snapshot", "remote-cam", "/proxy/abc123") in client.calls
    assert result.data["file_url"].startswith("/proxy/abc123/")


@pytest.mark.anyio
async def test_mcp_find_camera_prefers_local_on_id_collision():
    from tailcam.mcp.tools import _find_camera

    client = _FakeMcpClient(
        [
            {"id": "0", "name": "peer", "proxy_prefix": "/proxy/peer1"},
            {"id": "0", "name": "mine", "proxy_prefix": ""},
        ]
    )
    cam, prefix = await _find_camera(_FakeToolCtx(client), "0")
    assert cam["name"] == "mine" and prefix == ""


@pytest.mark.anyio
async def test_mcp_version_drift_orders_numerically():
    from tailcam.mcp.tools import _check_fleet_version_drift

    client = _FakeMcpClient(
        [],
        hosts=[
            {"node_key": "a", "host": "a", "version": "0.9.0"},
            {"node_key": "b", "host": "b", "version": "0.10.0"},
        ],
    )
    result = await _check_fleet_version_drift(_FakeToolCtx(client), {})
    assert result.data["latest"] == "0.10.0"
    assert [d["node_key"] for d in result.data["drift"]] == ["a"]


def test_mcp_proxy_prefix_validation():
    from tailcam.mcp.client import _proxy
    from tailcam.mcp.errors import TailcamMcpError

    assert _proxy("") == ""
    assert _proxy("/proxy/nodekey-1") == "/proxy/nodekey-1"
    with pytest.raises(TailcamMcpError):
        _proxy("/proxy/../v1/node/audit")
    with pytest.raises(TailcamMcpError):
        _proxy("http://evil.example")


# ----------------------------------------------------------------- REST API
def test_detection_api_roundtrip(client):
    info = client.get("/api/detection").json()
    assert info["enabled"] is True
    assert info["overlay_default"] is True

    updated = client.post(
        "/api/detection",
        json={"confidence": 0.6, "classes": [" person ", ""], "overlay_default": False},
    ).json()
    assert updated["confidence"] == 0.6
    assert updated["classes"] == ["person"]
    assert updated["overlay_default"] is False
    # Persisted to the live config object.
    ctx = client.app.state.ctx
    assert ctx.config.detection.confidence == 0.6

    off = client.post("/api/detection", json={"enabled": False}).json()
    assert off["enabled"] is False and off["status"] == "off"
    client.post("/api/detection", json={"enabled": True})


def test_detect_endpoint_serves_builtin_boxes(client):
    ctx = client.app.state.ctx
    det = ctx.detector
    det._status = "ready"
    det._engine = "opencv"
    det._model_name = "yolov4-tiny"
    det._net_model = object()
    det._detect_opencv = lambda image: [Detection("person", 0.9, 0.5, 0.5, 0.2, 0.4)]

    cam_id = client.get("/api/cameras").json()[0]["id"]
    resp = client.post(f"/api/cameras/{cam_id}/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["detector_active"] is True
    assert body["model_name"] == "yolov4-tiny"
    assert body["note"] == ""
    assert body["boxes"][0]["label"] == "person"
    assert body["boxes"][0]["confidence"] == pytest.approx(0.9)


def test_detect_endpoint_reports_download_note(client):
    ctx = client.app.state.ctx
    ctx.detector._status = "downloading"
    ctx.detector._percent = 55.0
    ctx.detector._detail = "downloading yolov4-tiny.weights (23 MB)"

    cam_id = client.get("/api/cameras").json()[0]["id"]
    body = client.post(f"/api/cameras/{cam_id}/detect").json()
    assert body["detector_active"] is True  # UI keeps polling
    assert body["boxes"] == []
    assert "55" in body["note"]
