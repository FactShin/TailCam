"""Regression tests for the round-2 cross-platform audit (backend)."""

from __future__ import annotations

import threading
import time

import httpx
import numpy as np
import pytest

from tailcam.camera.frame import FrameBuffer

# -- recordings ------------------------------------------------------------


class _Sink:
    codec = "h264"

    def __init__(self):
        self.frames = 0
        self.failed = False
        self.closed = False

    def write(self, img):
        if self.failed:
            return False
        self.frames += 1
        return True

    def close(self):
        self.closed = True
        return self.frames > 0


def test_recording_repeats_frames_when_source_is_slower_than_fps(monkeypatch, tmp_path):
    """A 2 fps source recorded at 10 fps must produce ~10 frames per second
    of wall clock (repeated frames), not a 5x-fast clip."""
    from tailcam.media import recorder as rec

    sink = _Sink()
    monkeypatch.setattr(rec, "open_video_sink", lambda *a, **k: sink)
    monkeypatch.setattr(rec.paths, "media_dir", lambda: tmp_path)
    buffer = FrameBuffer()
    session = rec._RecordingSession("cam", buffer, fps=10, trigger="manual")
    session.start()
    img = np.zeros((48, 64, 3), np.uint8)
    for _ in range(3):
        buffer.publish(img)
        time.sleep(0.5)
    session.stop()
    # 1.5 s at 10 fps ≈ 15 writes; allow generous slack for a loaded CI box.
    assert 8 <= session.frames_written <= 20, session.frames_written


def test_recording_ends_when_encoder_fails(monkeypatch, tmp_path):
    from tailcam.media import recorder as rec

    sink = _Sink()
    monkeypatch.setattr(rec, "open_video_sink", lambda *a, **k: sink)
    monkeypatch.setattr(rec.paths, "media_dir", lambda: tmp_path)
    buffer = FrameBuffer()
    session = rec._RecordingSession("cam", buffer, fps=20, trigger="manual")
    session.start()
    img = np.zeros((48, 64, 3), np.uint8)
    buffer.publish(img)
    time.sleep(0.2)
    sink.failed = True  # disk full
    for _ in range(3):
        buffer.publish(img)
        time.sleep(0.1)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and session._thread.is_alive():
        time.sleep(0.05)
    assert not session._thread.is_alive(), "session kept running after the encoder died"
    assert "disk full" in session.error


def test_ffmpeg_sink_has_no_faststart_and_discards_partials(monkeypatch, tmp_path):
    from tailcam.media import video_sink as vs

    spawned = {}

    class _Proc:
        def __init__(self, cmd, **kw):
            spawned["cmd"] = cmd
            self.stdin = None
            self.stderr = None
            self.returncode = None

        def poll(self):
            return None

        def communicate(self, timeout=None):
            raise vs.subprocess.TimeoutExpired("ffmpeg", timeout)

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(vs.subprocess, "Popen", _Proc)
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"partial")
    sink = vs.FfmpegPipeSink("ffmpeg", out, 15, (64, 48))
    assert "+faststart" not in spawned["cmd"]
    assert "-movflags" not in spawned["cmd"]
    assert sink.close() is False
    assert not out.exists(), "an unfinished clip must not be left on disk"


def test_no_recording_when_disk_is_nearly_full(monkeypatch, tmp_path):
    from tailcam.media import video_sink as vs

    monkeypatch.setattr(vs, "free_bytes", lambda p: 1024)
    assert vs.open_video_sink(tmp_path / "x.mp4", 15, (64, 48)) is None


def test_stop_all_finalizes_sessions_in_parallel(monkeypatch, tmp_path):
    from tailcam.media import recorder as rec

    monkeypatch.setattr(rec, "open_video_sink", lambda *a, **k: _Sink())
    monkeypatch.setattr(rec.paths, "media_dir", lambda: tmp_path)
    svc = rec.RecordingService(manager=None, store=None)  # type: ignore[arg-type]
    slow_stops = []

    class _Slow(rec._RecordingSession):
        def stop(self):
            slow_stops.append(threading.current_thread().name)
            time.sleep(0.4)
            super().stop()

    for i in range(3):
        svc._sessions[f"cam{i}"] = _Slow(f"cam{i}", FrameBuffer(), 5, "manual")
        svc._sessions[f"cam{i}"].start()
    t0 = time.monotonic()
    svc.stop_all()
    assert time.monotonic() - t0 < 1.0  # three 0.4 s stops ran concurrently
    assert len(set(slow_stops)) == 3


# -- proxy / cluster ----------------------------------------------------------


def test_proxy_strips_browser_origin_headers():
    from starlette.requests import Request

    from tailcam.web.routes_proxy import _forward_request_headers

    scope = {
        "type": "http", "method": "POST", "path": "/x", "headers": [
            (b"origin", b"http://100.64.0.5:8088"), (b"referer", b"http://100.64.0.5:8088/"),
            (b"host", b"100.64.0.5:8088"), (b"content-type", b"application/json"),
            (b"tailscale-user-login", b"x"),
        ],
    }
    fwd = _forward_request_headers(Request(scope))
    assert "origin" not in fwd and "referer" not in fwd and "host" not in fwd
    assert fwd["content-type"] == "application/json"


def test_cluster_ignores_non_tailcam_and_malformed_peers():
    import asyncio

    from tailcam.cluster.service import ClusterService
    from tailcam.config import PeersConfig

    class _TS:
        def is_installed(self):
            return False

        def peers(self):
            return []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "webapp":
            return httpx.Response(200, json="just a string")  # some other :8443 app
        if request.url.path == "/api/system":
            return httpx.Response(200, json={"version": "1.8.2", "host": "pi.ts.net"})
        return httpx.Response(200, json={"not": "a list"})

    svc = ClusterService(
        PeersConfig(auto_discover=False, static=["https://webapp:8443", "https://pi:8443"]),
        _TS(), local_host="me.ts.net",
    )
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go():
        peers = await svc.peers()
        cams = await svc.remote_cameras()
        return peers, cams

    peers, cams = asyncio.run(go())
    assert [p.host for p in peers] == ["pi.ts.net"]
    assert cams == []  # malformed body → skipped, not a 500


# -- storage node ----------------------------------------------------------------


def test_capture_router_adopts_409_and_only_backs_off_on_5xx(client, context):
    from tailcam.cluster.service import Peer

    cam_id = client.get("/api/cameras?scope=local").json()[0]["id"]
    context.config.storage.node = "nas"
    context.cluster._peers = [Peer(key="nas", host="nas.ts.net", base_url="https://nas:8443",
                                   online=True)]
    context.cluster._by_key = {p.key: p for p in context.cluster._peers}
    responses = {"code": 409}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(responses["code"], json={"detail": "already recording"})

    context.capture._client = httpx.Client(transport=httpx.MockTransport(handler))
    # 409: the storage node already has this session → adopt it, no local clip.
    assert context.capture.start_recording(cam_id) is False
    assert context.capture.is_recording(cam_id) is True
    assert not context.recorder.is_recording(cam_id)
    assert context.capture._down_until == 0.0  # a 4xx is not "node down"
    # Stop while the node is unreachable → retried later, never forgotten.
    responses["code"] = 503
    assert context.capture.stop_recording(cam_id) is None
    assert cam_id in context.capture._pending_stops
    responses["code"] = 200
    context.capture._down_until = 0.0

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "media_id": 5})

    context.capture._client = httpx.Client(transport=httpx.MockTransport(ok))
    assert context.capture.retry_pending_stops() == 0


def test_remote_routes_reject_path_traversal_ids(client):
    # httpx/TestClient normalize literal "../" before sending, so the
    # percent-encoded form is the one that reaches the route intact.
    r = client.post("/api/remote/pi/cameras/..%2F..%2Fapi%2Fx/recording/stop")
    assert r.status_code == 400
    r = client.post("/api/remote/pi/cameras/dev%2Fvideo0%3Fx%3D1/recording/stop")
    assert r.status_code == 400


def test_stale_remote_sessions_are_finalized(context, monkeypatch):
    stopped = []
    monkeypatch.setattr(context.remote_feeds, "stale_keys", lambda age: ["pi|/dev/video0"])
    monkeypatch.setattr(context.recorder, "session_keys", lambda: ["pi|/dev/video0", "local"])
    monkeypatch.setattr(context.recorder, "stop", lambda k: stopped.append(k))
    monkeypatch.setattr(context.recorder, "is_recording", lambda k: False)
    monkeypatch.setattr(context.remote_feeds, "stop_feed", lambda k: stopped.append("feed:" + k))
    context._reap_stale_remote_sessions()
    assert stopped == ["pi|/dev/video0", "feed:pi|/dev/video0"]


def test_remote_feed_uses_a_read_timeout():
    from tailcam.cluster import remote_feed

    feed = remote_feed.RemoteFeed("k", "https://pi:8443/stream/x.mjpg")
    client = feed._client()
    assert client.timeout.read == remote_feed._READ_TIMEOUT
    client.close()


# -- host profile ------------------------------------------------------------------


def test_big_pi_is_not_low_power(monkeypatch):
    from tailcam import hostinfo

    monkeypatch.delenv("TAILCAM_LOW_POWER", raising=False)
    monkeypatch.setattr(hostinfo, "_pi_model", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr(hostinfo.sys, "platform", "linux")
    hostinfo.profile.cache_clear()
    monkeypatch.setattr(hostinfo, "_total_ram", lambda: 8 * 1024**3)
    assert hostinfo.profile().low_power is False
    hostinfo.profile.cache_clear()
    monkeypatch.setattr(hostinfo, "_total_ram", lambda: 1 * 1024**3)
    assert hostinfo.profile().low_power is True
    hostinfo.profile.cache_clear()


# -- camera worker ---------------------------------------------------------------


def test_worker_grabs_without_decoding_when_nobody_watches():
    from tailcam.camera import worker as w
    from tailcam.camera.source import CameraDescriptor, CameraSource

    calls = {"grab": 0, "read": 0}

    class _Src(CameraSource):
        def open(self):
            return True

        def read(self):
            calls["read"] += 1
            time.sleep(0.01)
            return np.zeros((8, 8, 3), np.uint8)

        def grab(self):
            calls["grab"] += 1
            time.sleep(0.01)
            return True

        def set_property(self, n, v):
            pass

        def get_property(self, n):
            return 0.0

        def close(self):
            pass

        @property
        def is_open(self):
            return True

    worker = w.CameraWorker(
        CameraDescriptor(id="x", name="x", backend="synthetic"), source_factory=lambda d, p: _Src()
    )
    worker.start()
    time.sleep(0.3)
    assert calls["grab"] > 0 and calls["read"] == 0  # idle: no decode at all
    worker.buffer.await_latest(-1, timeout=0.5)  # a consumer shows up
    time.sleep(0.2)
    assert calls["read"] > 0
    worker.stop()


def test_timelapse_capture_stops_after_repeated_save_failures(tmp_path, monkeypatch):
    from tailcam.timelapse import worker as tw

    def _enospc(*a, **k):
        raise OSError(28, "No space")

    monkeypatch.setattr(tw, "encode_jpeg", _enospc)
    done = []
    buffer = FrameBuffer()
    cap = tw.TimelapseCaptureWorker(
        1, "cam", buffer, tmp_path / "frames", interval_seconds=0.1, jpeg_quality=80,
        on_complete=lambda: done.append(True),
    )
    cap.start()
    for _ in range(12):
        buffer.publish(np.zeros((8, 8, 3), np.uint8))
        time.sleep(0.11)
    cap.stop()
    assert cap.failed is True and done == [True]
    assert cap.frames_captured == 0


@pytest.mark.parametrize("params", ["", "?zoom=2&w=64&q=50"])
def test_snapshot_accepts_view_params(client, params):
    cam_id = client.get("/api/cameras?scope=local").json()[0]["id"]
    r = client.get(f"/stream/{cam_id}/snapshot.jpg{params}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    if params:
        import cv2

        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[1] == 64
