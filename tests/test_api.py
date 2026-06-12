import time


def test_list_cameras_returns_synthetic(client):
    resp = client.get("/api/cameras")
    assert resp.status_code == 200
    cams = resp.json()
    assert len(cams) >= 1
    assert cams[0]["backend"] == "synthetic"


def test_system_info(client):
    resp = client.get("/api/system")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert body["local_url"].startswith("http://localhost")


def test_rename_camera(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    resp = client.patch(f"/api/cameras/{cam_id}", json={"name": "Front Door"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Front Door"


def test_update_settings(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    resp = client.patch(
        f"/api/cameras/{cam_id}",
        json={"properties": {"width": 640, "height": 480}, "transform": {"rotation": 90}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transform"]["rotation"] == 90


def test_snapshot_creates_media(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    resp = client.post(f"/api/cameras/{cam_id}/snapshot")
    assert resp.status_code == 200
    media_id = resp.json()["media_id"]
    assert media_id is not None

    listing = client.get("/api/media").json()
    assert any(m["id"] == media_id and m["media_type"] == "snapshot" for m in listing)

    # The file is downloadable.
    fileresp = client.get(f"/media/{media_id}/file")
    assert fileresp.status_code == 200
    assert fileresp.headers["content-type"].startswith("image/")


def test_recording_lifecycle(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    start = client.post(f"/api/cameras/{cam_id}/recording/start")
    assert start.status_code == 200
    time.sleep(1.0)  # capture a few frames
    stop = client.post(f"/api/cameras/{cam_id}/recording/stop")
    assert stop.status_code == 200
    assert stop.json()["media_id"] is not None


def test_snapshot_jpg_endpoint(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    resp = client.get(f"/stream/{cam_id}/snapshot.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) > 100


def test_delete_media(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    media_id = client.post(f"/api/cameras/{cam_id}/snapshot").json()["media_id"]
    resp = client.delete(f"/api/media/{media_id}")
    assert resp.status_code == 200
    assert client.get(f"/media/{media_id}/file").status_code == 404


def test_pages_render(client):
    for path in ("/", "/gallery", "/events"):
        assert client.get(path).status_code == 200


def test_cameras_come_online_without_stream_request(client):
    """Regression: workers must start eagerly. The dashboard only streams
    cameras that report online, so if workers waited for a stream request the
    UI would deadlock with every camera stuck 'offline'."""
    deadline = time.time() + 5.0
    status = "offline"
    while time.time() < deadline:
        status = client.get("/api/cameras").json()[0]["status"]
        if status == "online":
            break
        time.sleep(0.1)
    assert status == "online"


def test_restart_camera(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    assert client.post(f"/api/cameras/{cam_id}/restart").status_code == 200


def test_delete_camera_hides_it(client, context):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    assert client.delete(f"/api/cameras/{cam_id}").status_code == 200
    # Gone from the list and recorded as hidden.
    assert all(c["id"] != cam_id for c in client.get("/api/cameras").json())
    assert cam_id in context.config.cameras.hidden
    # Refresh must not bring it back…
    client.post("/api/cameras/refresh")
    assert all(c["id"] != cam_id for c in client.get("/api/cameras").json())
    # …but restore-hidden does.
    restored = client.post("/api/cameras/restore-hidden").json()
    assert any(c["id"] == cam_id for c in restored)
    assert context.config.cameras.hidden == []


def test_system_reload(client):
    assert client.post("/api/system/reload").status_code == 200


def test_security_headers_present(client):
    h = client.get("/api/system").headers
    assert h["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in h
    assert h["x-frame-options"] == "SAMEORIGIN"


def test_cross_origin_mutation_blocked(client):
    cam_id = client.get("/api/cameras").json()[0]["id"]
    # A foreign Origin (drive-by / CSRF) is rejected on mutations…
    bad = client.post(f"/api/cameras/{cam_id}/snapshot", headers={"origin": "https://evil.example"})
    assert bad.status_code == 403
    # …while localhost / same-origin is allowed.
    ok = client.post(f"/api/cameras/{cam_id}/snapshot", headers={"origin": "http://localhost:8088"})
    assert ok.status_code in (200, 503)  # 503 only if no frame yet


def test_disabling_motion_closes_open_event(client, context):
    """Regression: toggling motion off mid-event left it 'ongoing' forever."""
    cam_id = client.get("/api/cameras").json()[0]["id"]
    client.patch(f"/api/cameras/{cam_id}", json={"motion_enabled": True})
    # The synthetic camera has a moving square, so an event opens quickly.
    deadline = time.time() + 8.0
    while time.time() < deadline:
        events = client.get("/api/events").json()
        if events:
            break
        time.sleep(0.2)
    assert events, "synthetic motion should have opened an event"
    client.patch(f"/api/cameras/{cam_id}", json={"motion_enabled": False})
    events = client.get("/api/events").json()
    assert all(e["end_ts"] is not None for e in events), "no event may stay 'ongoing'"


def test_startup_closes_stale_events(store):
    from tailcam.persistence.models import MotionEventRecord

    store.add_motion_event(
        MotionEventRecord(id=None, camera_id="x", start_ts=123.0, end_ts=None,
                          peak_score=0.5, recording_id=None)
    )
    assert store.close_stale_motion_events() == 1
    (event,) = store.list_motion_events()
    assert event.end_ts == 123.0  # closed at start_ts (true end unknown)
