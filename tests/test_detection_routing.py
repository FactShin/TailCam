"""Per-camera detection switch, shared result cache, and detection-node routing."""

from __future__ import annotations

import json

import httpx
import numpy as np

from tailcam.ai.analyzer import Detection
from tailcam.ai.remote import RemoteDetector


def _cam(client) -> str:
    return client.get("/api/cameras?scope=local").json()[0]["id"]


def test_per_camera_detection_switch(client, context):
    cam_id = _cam(client)
    context.config.detection.enabled = True
    info = client.get(f"/api/cameras/{cam_id}").json()
    assert info["detection_enabled"] is True and info["detection_override"] is None

    # Turn it off for this camera only: the overlay endpoint reports inactive
    # without touching a frame or a model.
    r = client.patch(f"/api/cameras/{cam_id}", json={"detection_enabled": False})
    assert r.json()["detection_enabled"] is False and r.json()["detection_override"] is False
    assert json.loads(context.store.get_camera(cam_id).settings_json)["detection_enabled"] is False
    assert client.post(f"/api/cameras/{cam_id}/detect").json()["detector_active"] is False

    # Back to "follow global".
    r = client.patch(f"/api/cameras/{cam_id}", json={"clear_detection_override": True})
    assert r.json()["detection_override"] is None and r.json()["detection_enabled"] is True
    context.config.detection.enabled = False
    assert client.get(f"/api/cameras/{cam_id}").json()["detection_enabled"] is False


def test_detect_results_are_cached_per_camera(client, context, monkeypatch):
    cam_id = _cam(client)
    calls = []

    def fake_detect(image):
        calls.append(1)
        return [Detection(label="cup", confidence=0.9, cx=0.5, cy=0.5, w=0.2, h=0.2)]

    monkeypatch.setattr(type(context.inference), "detection_active", property(lambda self: True))
    monkeypatch.setattr(context.inference, "detect", fake_detect)
    monkeypatch.setattr(context.inference, "detection_note", lambda: "")
    a = client.post(f"/api/cameras/{cam_id}/detect").json()
    b = client.post(f"/api/cameras/{cam_id}/detect").json()
    assert a["boxes"][0]["label"] == "cup" and b == a
    assert len(calls) == 1  # second viewer reused the cached result


def test_detect_image_endpoint_runs_local_pipeline(client, context, monkeypatch):
    import cv2

    monkeypatch.setattr(type(context.inference), "detection_active", property(lambda self: True))
    monkeypatch.setattr(
        context.inference, "detect",
        lambda img: [Detection(label="person", confidence=0.8, cx=0.4, cy=0.4, w=0.1, h=0.3)],
    )
    ok, buf = cv2.imencode(".jpg", np.zeros((120, 160, 3), np.uint8))
    r = client.post(
        "/api/detect-image", content=buf.tobytes(), headers={"Content-Type": "image/jpeg"}
    )
    assert r.status_code == 200
    assert r.json()["boxes"][0]["label"] == "person"
    assert client.post("/api/detect-image", content=b"nope").status_code == 400


def test_remote_detector_round_trip_and_backoff():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["mode"] = request.url.params.get("mode")
        seen["size"] = len(request.content)
        if request.url.params.get("mode") == "analyze":
            return httpx.Response(
                200, json={"label": "dog", "confidence": 0.7, "description": "a dog"}
            )
        return httpx.Response(
            200,
            json={"detector_active": True, "boxes": [
                {"label": "cat", "confidence": 0.9, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}
            ]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rd = RemoteDetector(
        lambda: "https://gpu-box.tailnet.ts.net:8443", label="gpu-box", client=client
    )
    frame = np.zeros((720, 1280, 3), np.uint8)
    boxes = rd.detect(frame)
    assert boxes and boxes[0].label == "cat"
    assert seen["path"] == "/api/detect-image" and seen["mode"] == "detect"
    assert 0 < seen["size"] < 60_000  # frame was downscaled before upload
    analysis = rd.analyze(frame)
    assert analysis and analysis.label == "dog"

    # A dead peer → None once, then quiet for the backoff window.
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502)))
    rd2 = RemoteDetector(lambda: "https://gone:8443", label="gone", client=dead)
    assert rd2.detect(frame) is None and not rd2.available and rd2.last_error


def test_detection_node_routes_and_skips_local_provisioning(client, context, monkeypatch):
    cam_id = _cam(client)
    provisioned = []
    monkeypatch.setattr(context.detector, "ensure_ready", lambda: provisioned.append(1))
    r = client.post("/api/detection", json={"enabled": True, "node": "gpu-box"})
    assert r.status_code == 200 and r.json()["node"] == "gpu-box"
    assert r.json()["status"] == "remote"
    assert not provisioned  # this node must not download a model
    assert context.detector.enabled is False  # routed → local engine stays cold

    # Unknown peer: the overlay says so instead of silently showing nothing.
    res = client.post(f"/api/cameras/{cam_id}/detect").json()
    assert res["detector_active"] is True and "unreachable" in res["note"]

    # Once the peer is known, frames go there.
    from tailcam.cluster.service import Peer

    context.cluster._peers = [Peer(key="gpu-box", host="gpu-box.tailnet.ts.net",
                                   base_url="https://gpu-box.tailnet.ts.net:8443", online=True)]
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(str(request.url))
        return httpx.Response(200, json={"detector_active": True, "boxes": [
            {"label": "person", "confidence": 0.9, "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}]})

    remote = context.remote_detector()
    remote._client = httpx.Client(transport=httpx.MockTransport(handler))
    context._detect_cache.clear()
    res = client.post(f"/api/cameras/{cam_id}/detect").json()
    assert res["boxes"] and res["boxes"][0]["label"] == "person"
    assert posted and posted[0].startswith("https://gpu-box.tailnet.ts.net:8443/api/detect-image")
    assert res["model_name"] == "via gpu-box"

    # Switching back to local clears the route.
    client.post("/api/detection", json={"node": ""})
    assert context.remote_detector() is None
