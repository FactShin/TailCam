"""Storage node end-to-end: a *source* node (the Pi) routes its recordings and
timelapses to a *storage* node, which pulls the camera's MJPEG stream over HTTP
and writes the files on its own disk. Two real TailCam servers run in-process
on loopback ports so every hop (router → remote endpoint → MJPEG pull → sink →
fleet-aggregated listing → proxy) is the production code path.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import numpy as np
import pytest

from tailcam.cluster.remote_feed import RemoteFeedRegistry, iter_mjpeg_frames


def _wait(pred, timeout=15.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Node:
    """One TailCam server on a loopback port with its own DB + identity."""

    def __init__(self, tmp_path, name: str, port: int, peer_url: str, storage_node: str = ""):
        import uvicorn

        from tailcam.config import AppConfig
        from tailcam.persistence.store import Store
        from tailcam.web.app import create_app
        from tailcam.web.context import AppContext

        self.name = name
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        os.environ["TAILCAM_HOST"] = name  # identity is read at context construction
        cfg = AppConfig()
        cfg.tailscale.auto_serve = False
        cfg.peers.auto_discover = False
        cfg.peers.static = [peer_url]
        cfg.storage.node = storage_node
        cfg.timelapse.default_interval_seconds = 0.2
        cfg.server.port = port
        self.ctx = AppContext(cfg, store=Store(db_path=tmp_path / f"{name}.db"))
        app = create_app(cfg, context=self.ctx)
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        assert _wait(lambda: self._server.started, 20), f"{name} did not start"
        self.http = httpx.Client(base_url=self.url, timeout=20.0)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)
        self.http.close()


@pytest.fixture
def fleet(isolated_env, tmp_path, monkeypatch):
    """(source, storage): the storage node knows the source (to pull its
    stream); the source routes captures to the storage node."""
    monkeypatch.delenv("TAILCAM_HOST", raising=False)
    src_port, sto_port = _free_port(), _free_port()
    storage = _Node(tmp_path, "storage-box", sto_port, f"http://127.0.0.1:{src_port}")
    source = _Node(
        tmp_path, "source-pi", src_port, storage.url, storage_node="storage-box"
    )
    assert _wait(
        lambda: any(h["host"] == "storage-box" for h in source.http.get("/api/hosts").json())
    )
    assert _wait(
        lambda: any(h["host"] == "source-pi" for h in storage.http.get("/api/hosts").json())
    )
    try:
        yield source, storage
    finally:
        source.stop()
        storage.stop()
        os.environ.pop("TAILCAM_HOST", None)


def test_mjpeg_frame_parser_handles_split_chunks():
    jpeg = b"\xff\xd8" + b"x" * 50 + b"\xff\xd9"
    stream = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n" + jpeg
    chunks = [stream[i : i + 7] for i in range(0, len(stream), 7)]  # split everywhere
    assert list(iter_mjpeg_frames(iter(chunks))) == [jpeg, jpeg]


def test_registry_stream_url_encodes_camera_id():
    reg = RemoteFeedRegistry(lambda key: "https://pi:8443" if key == "pi" else None)
    assert reg.stream_url("https://pi:8443", "/dev/video0", 5) == (
        "https://pi:8443/stream//dev/video0.mjpg?fps=5"
    )
    assert reg.get_buffer("unknown", "/dev/video0") is None


def test_recording_lands_on_storage_node(fleet):
    source, storage = fleet
    cam_id = source.http.get("/api/cameras?scope=local").json()[0]["id"]
    assert source.http.get("/api/storage").json()["node_online"] is True

    r = source.http.post(f"/api/cameras/{cam_id}/recording/start")
    assert r.status_code == 200, r.text
    assert "storage-box" in r.json()["detail"]
    # The source reports "recording" even though nothing is written locally.
    cam = source.http.get(f"/api/cameras/{cam_id}").json()
    assert cam["recording"] is True
    assert not source.ctx.recorder.is_recording(cam_id)
    assert _wait(lambda: any(f["frames"] > 3 for f in storage.ctx.remote_feeds.status()), 20), (
        storage.ctx.remote_feeds.status()
    )
    time.sleep(1.0)
    r = source.http.post(f"/api/cameras/{cam_id}/recording/stop")
    assert r.status_code == 200, r.text
    media_id = r.json()["media_id"]
    assert media_id

    # The file + row live on the storage node, attributed to the source camera.
    rec = storage.ctx.store.get_media(media_id)
    assert rec is not None and rec.camera_id == cam_id and rec.source_host == "source-pi"
    assert os.path.exists(rec.path) and rec.size_bytes > 0
    assert source.ctx.store.get_media(media_id) is None or source.ctx.store.count_media() == 0

    # …and the source's gallery shows it via the fleet aggregation.
    rows = source.http.get("/api/media").json()
    mine = [m for m in rows if m["id"] == media_id and m["host"] == "storage-box"]
    assert mine and mine[0]["source_host"] == "source-pi"
    assert mine[0]["proxy_prefix"] == "/proxy/storage-box"
    assert source.http.get(f"/proxy/storage-box/media/{media_id}/file").status_code == 200


def test_timelapse_runs_on_storage_node_and_is_stoppable_from_source(fleet):
    source, storage = fleet
    cam_id = source.http.get("/api/cameras?scope=local").json()[0]["id"]
    r = source.http.post(
        f"/api/cameras/{cam_id}/timelapse/start",
        json={"interval_seconds": 0.2, "output_fps": 10, "name": "remote print"},
    )
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["host"] == "storage-box" and info["proxy_prefix"] == "/proxy/storage-box"
    assert info["source_host"] == "source-pi"
    tl_id = info["id"]
    assert source.ctx.store.get_timelapse(tl_id) is None  # nothing local

    # Visible from the source's list (the bug: it used to vanish here).
    assert _wait(
        lambda: any(
            t["id"] == tl_id and t["frames_captured"] >= 3
            for t in source.http.get("/api/timelapse").json()
        ),
        30,
    )
    # Stop through the proxy prefix the row carries — exactly what the UI does.
    r = source.http.post(f"/proxy/storage-box/api/timelapse/{tl_id}/stop")
    assert r.status_code == 200, r.text
    assert _wait(lambda: storage.ctx.timelapse.get(tl_id).state == "complete", 40)
    done = storage.ctx.timelapse.get(tl_id)
    assert done.video_path and os.path.exists(done.video_path)
    assert done.source_host == "source-pi"


def test_router_falls_back_to_local_when_node_unreachable(context, monkeypatch):
    context.config.storage.node = "nowhere-box"
    cam_id = context.manager.list()[0].descriptor.id
    assert context.capture.target() is None
    assert "not visible" in context.capture.status()["error"]
    assert context.capture.start_recording(cam_id) is True
    assert context.recorder.is_recording(cam_id)  # ran locally
    assert _wait(lambda: context.recorder._sessions[cam_id].frames_written > 0, 10)
    rec = context.capture.stop_recording(cam_id)
    assert rec is not None and rec.source_host == ""


def test_remote_feed_shuts_down_when_idle(monkeypatch):
    from tailcam.cluster import remote_feed as rf

    monkeypatch.setattr(rf, "_IDLE_TIMEOUT", 0.3)
    jpeg = _tiny_jpeg()
    body = b"".join(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n" for _ in range(40))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    feed = rf.RemoteFeed(
        "k", "http://peer/stream/x.mjpg",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    feed.start()
    assert feed.buffer.await_latest(-1, timeout=5.0) is not None
    # Nobody waits any more → the feed closes itself.
    assert _wait(lambda: not feed.alive, 10)


def _tiny_jpeg() -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return buf.tobytes()
