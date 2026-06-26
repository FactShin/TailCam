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
    assert body["auto_record"] is False
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
