"""Workload boundaries across real nodes and every HTTP work entry point."""

import sqlite3
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_capture_processes import _port, _wait
from test_capture_processes import process_fleet as process_fleet

from tailcam.config import AppConfig, MotionConfig
from tailcam.motion.worker import MotionWorker
from tailcam.web.app import create_app
from tailcam.web.context import AppContext


def _files(root):
    return [path for path in (root / "media").rglob("*") if path.is_file()]


def _ready(http, base):
    _wait(lambda: http.get(f"{base}/api/cameras?scope=local").status_code == 200)


def _camera(http, base):
    cameras = _wait(lambda: http.get(f"{base}/api/cameras?scope=local").json())
    return cameras[0]["id"]


def _discover(http, base, host):
    _wait(lambda: any(node["host"] == host for node in http.get(f"{base}/api/hosts").json()))


def _assert_disabled(response, role):
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "role_disabled", response.text
    assert response.json()["role"] == role, response.text


def test_capture_only_node_records_and_timelapses_on_camera_free_storage(process_fleet):
    source_port, storage_port = _port(), _port()
    source = f"http://127.0.0.1:{source_port}"
    storage = f"http://127.0.0.1:{storage_port}"
    storage_root = process_fleet(
        "storage-only", storage_port, "--roles", "storage", "--peer", source,
    )
    source_root = process_fleet(
        "capture-only", source_port, "--roles", "capture", "--storage", storage,
    )
    with httpx.Client(timeout=10, trust_env=False) as http:
        _ready(http, storage)
        camera_id = _camera(http, source)
        _discover(http, source, "storage-only")
        _discover(http, storage, "capture-only")
        assert http.get(f"{storage}/api/cameras?scope=local").json() == []
        # Read-only stream access retains the absent-camera response; it must
        # not lazily open this node's synthetic source merely because it exists.
        assert http.get(f"{storage}/stream/{camera_id}/snapshot.jpg").status_code == 404

        response = http.post(f"{source}/api/cameras/{camera_id}/recording/start")
        assert response.status_code == 200, response.text
        _wait(lambda: list((storage_root / "media").glob("*.mp4")))
        response = http.post(f"{source}/api/cameras/{camera_id}/recording/stop")
        assert response.status_code == 200, response.text
        media_id = response.json()["media_id"]
        recording = http.get(f"{source}/proxy/storage-only/media/{media_id}/file")
        assert recording.status_code == 200 and len(recording.content) > 100

        response = http.post(
            f"{source}/api/cameras/{camera_id}/timelapse/start",
            json={"analysis_enabled": False, "auto_smooth": False,
                  "interval_seconds": 0.1, "output_fps": 10},
        )
        assert response.status_code == 200, response.text
        timelapse = response.json()
        assert timelapse["host"] == "storage-only"
        assert timelapse["source_host"] == "capture-only"
        owner = f"{source}{timelapse['proxy_prefix']}"
        tl_id = timelapse["id"]
        _wait(lambda: http.get(f"{owner}/api/timelapse/{tl_id}").json()["frames_captured"] >= 3)
        response = http.post(f"{owner}/api/timelapse/{tl_id}/stop")
        assert response.status_code == 200, response.text
        _wait(lambda: http.get(f"{owner}/api/timelapse/{tl_id}").json()["state"] == "complete")
        video = http.get(f"{owner}/timelapse/{tl_id}/file")
        assert video.status_code == 200 and len(video.content) > 100

        assert http.get(f"{source}/api/media?scope=local").json() == []
        assert http.get(f"{source}/api/timelapse?scope=local").json() == []
        assert _files(source_root) == []
        assert len(list((storage_root / "media").rglob("*.mp4"))) >= 2
        with sqlite3.connect(storage_root / "data/tailcam.db") as db:
            assert db.execute("SELECT source_host FROM media").fetchall() == [("capture-only",)]
            stored = db.execute("SELECT source_host FROM timelapses").fetchall()
            assert stored == [("capture-only",)]


def test_disabled_destination_never_falls_back_to_allowed_local_storage(process_fleet):
    source_port, hub_port = _port(), _port()
    source, hub = f"http://127.0.0.1:{source_port}", f"http://127.0.0.1:{hub_port}"
    hub_root = process_fleet("hub-only", hub_port, "--roles", "", "--peer", source)
    source_root = process_fleet(
        "capture-with-storage", source_port, "--roles", "capture,storage", "--storage", hub,
    )
    with httpx.Client(timeout=10, trust_env=False) as http:
        _ready(http, hub)
        camera_id = _camera(http, source)
        _discover(http, source, "hub-only")
        for _ in range(2):  # A refusal must not activate fallback backoff on the next request.
            _assert_disabled(http.post(
                f"{source}/api/cameras/{camera_id}/recording/start",
            ), "storage")
            _assert_disabled(http.post(
                f"{source}/api/cameras/{camera_id}/timelapse/start",
                json={"analysis_enabled": False, "auto_smooth": False},
            ), "storage")
        assert http.get(f"{source}/api/media?scope=local").json() == []
        assert http.get(f"{source}/api/timelapse?scope=local").json() == []
        assert _files(source_root) == []
        assert _files(hub_root) == []


def test_unreachable_destination_cannot_write_on_capture_only_node(process_fleet):
    source_port, unavailable_port = _port(), _port()
    source = f"http://127.0.0.1:{source_port}"
    source_root = process_fleet(
        "capture-only", source_port, "--roles", "capture",
        "--storage", f"http://127.0.0.1:{unavailable_port}",
    )
    with httpx.Client(timeout=10, trust_env=False) as http:
        camera_id = _camera(http, source)
        for _ in range(2):
            _assert_disabled(http.post(
                f"{source}/api/cameras/{camera_id}/recording/start",
            ), "storage")
            _assert_disabled(http.post(
                f"{source}/api/cameras/{camera_id}/timelapse/start",
                json={"analysis_enabled": False, "auto_smooth": False},
            ), "storage")
        assert _files(source_root) == []
        assert http.get(f"{source}/api/media?scope=local").json() == []
        assert http.get(f"{source}/api/timelapse?scope=local").json() == []


@pytest.fixture
def role_client(store, monkeypatch):
    contexts = []
    monkeypatch.setattr("tailcam.web.context.resolve_local_host", lambda _: "isolated-hub")

    def make(roles=()):
        config = AppConfig()
        config.node.roles = list(roles)
        config.tailscale.auto_serve = False
        config.peers.auto_discover = False
        config.ai.base_url = "http://127.0.0.1:1"
        config.ai.enabled = True  # Persisted workload settings cannot override node roles.
        ctx = AppContext(config, store)
        monkeypatch.setattr(ctx, "_start_notify_monitor", lambda: None)
        contexts.append(ctx)
        return ctx, TestClient(create_app(config, context=ctx), base_url="http://localhost:8088")

    yield make
    for ctx in contexts:
        ctx.shutdown()


def _forbidden(*args, **kwargs):
    pytest.fail("disabled role reached a workload or hardware boundary")


def test_hub_http_rejects_work_before_streams_models_decoders_or_jobs(role_client, monkeypatch):
    ctx, client = role_client()
    monkeypatch.setattr(ctx.cluster, "peer_base", _forbidden)
    monkeypatch.setattr(ctx.remote_feeds, "get_buffer", _forbidden)
    monkeypatch.setattr(ctx.manager, "get_buffer", _forbidden)
    monkeypatch.setattr(ctx.recorder, "start", _forbidden)
    monkeypatch.setattr(ctx.training, "train", _forbidden)
    monkeypatch.setattr(ctx.active_learning, "start", _forbidden)
    monkeypatch.setattr(ctx.pulls, "start", _forbidden)
    monkeypatch.setattr("cv2.imdecode", _forbidden)
    cases = [
        ("/api/cameras/unused/snapshot", None, "capture"),
        ("/api/cameras/unused/recording/start", None, "capture"),
        ("/api/cameras/unused/timelapse/start", {}, "capture"),
        ("/api/remote/source/cameras/unused/recording/start", {}, "storage"),
        ("/api/remote/source/cameras/unused/timelapse/start", {}, "storage"),
        ("/api/ai/test", {"camera_id": "unused"}, "analysis"),
        ("/api/ai/pull", {"model": "unused"}, "analysis"),
        ("/api/ai/load", {"model": "unused"}, "analysis"),
        ("/api/training/collection", {"enabled": True}, "training"),
        ("/api/datasets", {"name": "unused"}, "training"),
        ("/api/training/runs", {"dataset_id": 1}, "training"),
        ("/api/active-learning/start", None, "training"),
        ("/api/active-learning/train", {}, "training"),
    ]
    with client:
        for path, payload, role in cases:
            _assert_disabled(client.post(path, json=payload), role)
        _assert_disabled(client.post("/api/detect-image", content=b"not an image"), "analysis")
    assert ctx.store.list_runs() == []
    assert ctx.store.list_datasets() == []
    assert ctx.timelapse_analysis._thread is None


def test_hub_status_http_never_probes_models_or_training_runtime(role_client, monkeypatch):
    ctx, client = role_client()
    monkeypatch.setattr(ctx.analyzer, "health", _forbidden)
    monkeypatch.setattr(ctx.analyzer, "installed_models", _forbidden)
    monkeypatch.setattr("tailcam.training.engine.engine_info", _forbidden)
    monkeypatch.setattr("tailcam.web.routes_active.platform_summary", _forbidden)
    monkeypatch.setattr("tailcam.web.routes_active.list_labeling_backends", _forbidden)
    monkeypatch.setattr("tailcam.web.routes_active.list_finetune_backends", _forbidden)
    monkeypatch.setattr("tailcam.training.inference.LocalClassifier.load", _forbidden)
    monkeypatch.setattr("tailcam.training.inference.LocalDetector.load", _forbidden)
    ctx.config.training.active_model_id = 123
    with client:
        for path in (
            "/api/ai", "/api/ai/models", "/api/detection", "/api/training",
            "/api/active-learning", "/api/active-learning/backends",
            "/api/active-learning/finetune-backends",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
    assert ctx.timelapse_analysis._thread is None


def test_training_only_active_learning_returns_structured_analysis_refusal(role_client):
    _, client = role_client(["training"])
    with client:
        _assert_disabled(client.post("/api/active-learning/start"), "analysis")


@pytest.mark.parametrize("roles,missing_role", [
    (["capture"], "storage"),
    (["capture", "storage"], "analysis"),
])
@pytest.mark.parametrize("request_settings", [{}, {"analysis_enabled": True}])
def test_local_timelapse_role_refusal_precedes_ollama_configuration_error(
    role_client, monkeypatch, roles, missing_role, request_settings
):
    ctx, client = role_client(roles)
    ctx.config.ai.enabled = False
    ctx.config.timelapse.analysis_enabled = True
    monkeypatch.setattr(ctx.timelapse, "start", _forbidden)
    with client:
        camera_id = client.get("/api/cameras?scope=local").json()[0]["id"]
        response = client.post(
            f"/api/cameras/{camera_id}/timelapse/start", json=request_settings
        )
        _assert_disabled(response, missing_role)
        assert client.get("/api/timelapse?scope=local").json() == []
    assert ctx.timelapse_analysis._thread is None


def test_homekit_pairing_reset_cannot_restart_capture_on_hub(role_client, monkeypatch):
    ctx, client = role_client()
    starts = []
    ctx.config.homekit.enabled = True
    monkeypatch.setattr(ctx.homekit, "available", lambda: True)
    monkeypatch.setattr(ctx.homekit, "_start_locked", lambda: starts.append(True))
    with client:
        response = client.post("/api/integrations/homekit/reset")
        assert response.status_code in (200, 503), response.text
    assert not ctx.homekit.running
    assert starts == []


def test_motion_without_storage_keeps_notifications_but_writes_no_thumbnail(
    isolated_env, monkeypatch
):
    notifications = []
    worker = MotionWorker(
        "synthetic-0", None, MotionConfig(), SimpleNamespace(set_thumb=_forbidden),
        notifier=SimpleNamespace(notify_motion=lambda **data: notifications.append(data)),
        storage_enabled=lambda: False,
    )
    monkeypatch.setattr("tailcam.motion.worker.paths.thumbnails_dir", _forbidden)
    worker._enrich_event(1, np.zeros((4, 4, 3), dtype=np.uint8))
    assert len(notifications) == 1
    assert notifications[0]["event_id"] == 1
    assert notifications[0]["image_path"] is None
