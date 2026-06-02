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
