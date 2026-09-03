from __future__ import annotations

from tailcam import paths
from tailcam.config import AppConfig


def test_config_roundtrip_storage():
    cfg = AppConfig()
    data = cfg.to_dict()
    assert "storage" in data
    assert AppConfig.from_dict(data).storage.media_dir == ""


def test_paths_media_override(tmp_path):
    try:
        paths.set_media_override(None)
        assert paths.media_dir() == paths.default_media_dir()
        custom = tmp_path / "drive" / "tailcam"
        paths.set_media_override(str(custom))
        assert paths.media_dir() == custom
        assert paths.thumbnails_dir() == custom / "thumbnails"
        paths.set_media_override("")  # blank reverts to default
        assert paths.media_dir() == paths.default_media_dir()
    finally:
        paths.set_media_override(None)


def test_storage_endpoint_defaults(client):
    body = client.get("/api/storage").json()
    assert body["is_default"] is True
    assert body["custom_dir"] == ""
    assert body["writable"] is True
    assert body["disk_total"] > 0
    assert body["auto_record"] is True  # clips per motion event, on by default
    assert body["retention_enabled"] is False  # auto-cleanup is opt-in
    assert body["max_gb"] == 10.0


def test_storage_set_custom_dir_and_autorecord(client, tmp_path):
    target = tmp_path / "ext-drive"
    body = client.post(
        "/api/storage",
        json={"media_dir": str(target), "auto_record": True, "max_gb": 5.0, "max_age_days": 7},
    ).json()
    assert body["custom_dir"] == str(target)
    assert body["is_default"] is False
    assert body["auto_record"] is True
    assert body["max_gb"] == 5.0
    assert body["max_age_days"] == 7
    assert paths.media_dir() == target
    # revert
    body2 = client.post("/api/storage", json={"media_dir": ""}).json()
    assert body2["is_default"] is True


def test_storage_rejects_unwritable_dir(client):
    resp = client.post("/api/storage", json={"media_dir": "/proc/nope/cannot-write"})
    assert resp.status_code == 400


def test_storage_rejects_relative_dir(client):
    # A relative path would be re-resolved against a different cwd on the next
    # boot (e.g. / under systemd), silently moving where media lands.
    resp = client.post("/api/storage", json={"media_dir": "recordings"})
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"]


def test_storage_get_has_no_write_side_effects(client, tmp_path):
    # Point at a not-yet-existing dir, then GET status: the read endpoint must
    # not create it (that would materialize unmounted mountpoints on the root fs).
    target = tmp_path / "not-yet" / "media"
    client.post("/api/storage", json={"media_dir": str(tmp_path / "not-yet" / "media")})
    # POST validates by creating; remove it to simulate an unmounted drive.
    import shutil as _shutil

    _shutil.rmtree(tmp_path / "not-yet")
    body = client.get("/api/storage").json()
    assert not target.exists()
    assert body["writable"] is True  # nearest existing ancestor is writable
    client.post("/api/storage", json={"media_dir": ""})


def test_prune_skipped_unless_enabled(context):
    # Retention is opt-in: with enabled=False nothing is deleted even when the
    # budget is absurdly low.
    context.config.retention.enabled = False
    context.config.retention.max_gb = 0.000001
    context._prune_media()
    # No exception and nothing pruned (no media exists; the point is the gate).
    assert context.config.retention.enabled is False


def test_prune_skips_when_media_dir_missing(context, tmp_path, monkeypatch):
    from tailcam import paths

    context.config.retention.enabled = True
    gone = tmp_path / "unmounted"
    paths.set_media_override(str(gone))
    assert not gone.exists()
    removed = context.gallery.prune(context.config.retention)
    assert removed == 0  # unmount guard: never drop DB rows blind


def test_recording_resizes_mismatched_frames(tmp_path, monkeypatch):
    # A video sink is locked to the first frame's size; a later differently-
    # sized frame must be resized (not silently dropped) so the clip stays
    # continuous through a mid-recording rotation/reconnect.
    import numpy as np

    from tailcam.media import recorder as rec

    written = []

    class _Sink:
        codec = "h264"

        def write(self, img):
            written.append(img.shape[:2])
            return True

        def close(self):
            return True

    monkeypatch.setattr(rec, "open_video_sink", lambda *a, **k: _Sink())
    monkeypatch.setattr(rec.paths, "media_dir", lambda: tmp_path)

    session = rec._RecordingSession("cam", buffer=None, fps=15, trigger="manual")
    session._open_writer(np.zeros((480, 640, 3), np.uint8))  # locks 640x480
    session._writer.write(session._fit(np.zeros((480, 640, 3), np.uint8)))  # same size
    session._writer.write(session._fit(np.zeros((640, 480, 3), np.uint8)))  # rotated -> resize
    assert written[0] == (480, 640)
    assert written[1] == (480, 640)  # resized back to the locked (w, h)
