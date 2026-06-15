"""Training data foundation: datasets, samples, collection from cameras, motion
import, and the model registry — exercised through the REST API."""

from __future__ import annotations

import time


def _wait(predicate, timeout=8.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_training_info_reports_engine(client):
    info = client.get("/api/training").json()
    # torch/ultralytics aren't installed in CI — the optional engine is absent.
    assert info["engine_available"] is False
    assert info["framework"] == "ultralytics"
    assert "person" in info["classes"]


def test_dataset_crud_and_relabel(client):
    ds = client.post("/api/datasets", json={"name": "Yard", "note": "front"}).json()
    did = ds["id"]
    assert ds["name"] == "Yard" and ds["sample_count"] == 0

    assert any(d["id"] == did for d in client.get("/api/datasets").json())

    # No samples yet → empty.
    assert client.get(f"/api/datasets/{did}/samples").json() == []

    assert client.delete(f"/api/datasets/{did}").status_code == 200
    assert client.get(f"/api/datasets/{did}").status_code == 404


def test_collection_from_cameras(client, context):
    context.config.training.collect_interval_seconds = 2.0
    # Enable collection — auto-creates an "All cameras" dataset and samples the feed.
    info = client.post("/api/training/collection", json={"enabled": True}).json()
    assert info["collecting"] is True
    did = info["active_dataset_id"]
    assert did

    assert _wait(lambda: client.get(f"/api/datasets/{did}").json()["sample_count"] >= 1), (
        "collection should capture from the synthetic camera"
    )

    # Stop collection.
    off = client.post("/api/training/collection", json={"enabled": False}).json()
    assert off["collecting"] is False

    # A sample is browsable + relabelable + servable.
    samples = client.get(f"/api/datasets/{did}/samples").json()
    assert samples
    sid = samples[0]["id"]
    relabeled = client.patch(f"/api/samples/{sid}", json={"label": "person"}).json()
    assert relabeled["label"] == "person"
    assert client.get(f"/datasets/sample/{sid}/thumbnail").status_code == 200
    assert client.get(f"/datasets/sample/{sid}/image").status_code == 200

    # Label distribution reflects the relabel.
    counts = client.get(f"/api/datasets/{did}").json()["label_counts"]
    assert counts.get("person", 0) >= 1

    assert client.delete(f"/api/samples/{sid}").status_code == 200


def test_import_motion_events(client, context, tmp_path):
    from tailcam.persistence.models import MotionEventRecord

    # Seed a motion event with a thumbnail on disk.
    thumb = tmp_path / "evt.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish bytes
    eid = context.store.add_motion_event(
        MotionEventRecord(id=None, camera_id="cam0", start_ts=time.time(), end_ts=None,
                          peak_score=0.5, recording_id=None)
    )
    context.store.set_event_thumb(eid, str(thumb))
    context.store.set_event_analysis(eid, "person", "a person", 0.9)

    ds = client.post("/api/datasets", json={"name": "FromEvents"}).json()
    did = ds["id"]
    after = client.post(f"/api/datasets/{did}/import-events").json()
    assert after["sample_count"] >= 1
    assert after["label_counts"].get("person", 0) >= 1


def test_model_registry(client, context, tmp_path):
    # The base ("our model") entry is seeded at startup.
    models = client.get("/api/models").json()
    base = [m for m in models if m["kind"] == "base"]
    assert base, "base model should be seeded"
    assert base[0]["has_artifact"] is False

    # Register a bring-your-own model file.
    weights = tmp_path / "mine.pt"
    weights.write_bytes(b"fake-weights")
    byo = client.post("/api/models", json={"name": "My model", "path": str(weights)}).json()
    mid = byo["id"]
    assert byo["kind"] == "byo"

    # Missing file → rejected.
    assert client.post("/api/models", json={"name": "x", "path": "/nope.pt"}).status_code == 400

    # Activate / deactivate.
    activated = client.post(f"/api/models/{mid}/activate").json()
    assert activated["active"] is True
    assert context.config.training.active_model_id == mid
    assert client.post("/api/models/deactivate").status_code == 200
    assert context.config.training.active_model_id == 0

    # Delete BYO; base cannot be deleted.
    assert client.delete(f"/api/models/{mid}").status_code == 200
    base_id = base[0]["id"]
    assert client.delete(f"/api/models/{base_id}").status_code == 400
