"""Training data foundation: datasets, samples, collection from cameras, motion
import, and the model registry — exercised through the REST API."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from tailcam.persistence.models import DatasetRecord, DatasetSampleRecord, ModelRecord


def _labeled_dataset(context, tmp_path, classes=("person", "vehicle"), per_class=3) -> int:
    did = context.store.add_dataset(
        DatasetRecord(id=None, name="T", task="classification", created_ts=time.time())
    )
    for cls in classes:
        for i in range(per_class):
            f = tmp_path / f"{cls}{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff\xd9")
            context.store.add_sample(
                DatasetSampleRecord(
                    id=None, dataset_id=did, path=str(f), thumb=None, label=cls,
                    source="manual", camera_id="c", host="vm", created_ts=time.time(),
                )
            )
    return did


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


# -- training execution + inference (Merge 2) ------------------------------


def test_export_classification_dataset(context, tmp_path):
    from tailcam.training.runner import export_classification_dataset

    did = _labeled_dataset(context, tmp_path)
    out = tmp_path / "ds"
    classes, n_train, n_val = export_classification_dataset(context.store, did, out)
    assert classes == ["person", "vehicle"]
    assert n_train + n_val == 6
    assert (out / "train" / "person").is_dir()
    assert (out / "val" / "vehicle").is_dir()


def test_export_needs_two_classes(context, tmp_path):
    import pytest

    from tailcam.training.runner import export_classification_dataset

    did = _labeled_dataset(context, tmp_path, classes=("person",))
    with pytest.raises(ValueError):
        export_classification_dataset(context.store, did, tmp_path / "ds")


def test_train_requires_engine(client, context, tmp_path):
    # No torch/ultralytics in CI → starting a run is refused with 503.
    did = _labeled_dataset(context, tmp_path)
    assert client.post("/api/training/runs", json={"dataset_id": did}).status_code == 503


def test_train_lifecycle(client, context, monkeypatch, tmp_path):
    from tailcam.training import engine, runner

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    monkeypatch.setattr(engine, "torch_device", lambda: "cpu")

    def fake_train(base, data_dir, epochs, imgsz, device, project_dir, on_epoch,
                   should_stop=None, task="classification"):
        assert (Path(data_dir) / "train").is_dir()  # export ran first
        for e in range(1, epochs + 1):
            on_epoch(e)
        out = Path(project_dir) / "train" / "weights" / "best.pt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"weights")
        return {"model_path": str(out), "metrics": {"top1": 0.95}}

    monkeypatch.setattr(runner, "train_model", fake_train)

    did = _labeled_dataset(context, tmp_path)
    r = client.post("/api/training/runs", json={"dataset_id": did, "epochs": 2})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    assert _wait(
        lambda: client.get(f"/api/training/runs/{rid}").json()["status"] == "complete", timeout=15
    ), client.get(f"/api/training/runs/{rid}").json()
    run = client.get(f"/api/training/runs/{rid}").json()
    assert run["epoch"] == 2
    assert run["metrics"].get("top1") == 0.95
    assert run["model_id"]

    # The trained model is registered and can be activated for inference.
    trained = [m for m in client.get("/api/models").json() if m["kind"] == "trained"]
    assert trained and trained[0]["classes"] == ["person", "vehicle"]
    assert client.post(f"/api/models/{trained[0]['id']}/activate").json()["active"] is True


def test_inference_router_prefers_active_model(context, monkeypatch, tmp_path):
    from tailcam.ai.analyzer import Analysis
    from tailcam.training import inference as inf
    from tailcam.training.inference import InferenceRouter

    weights = tmp_path / "m.pt"
    weights.write_bytes(b"w")
    mid = context.store.add_model(
        ModelRecord(
            id=None, name="t", kind="trained", path=str(weights), classes_json='["person"]',
            base_model="b", metrics_json="{}", created_ts=time.time(),
        )
    )
    context.config.training.active_model_id = mid
    monkeypatch.setattr(inf.LocalClassifier, "load", lambda self: True)
    monkeypatch.setattr(
        inf.LocalClassifier, "analyze",
        lambda self, img: Analysis(label="person", description="x", confidence=0.9),
    )
    router = InferenceRouter(context.store, context.config.training, context.analyzer)
    assert router.enabled is True
    res = router.analyze(np.zeros((4, 4, 3), np.uint8))
    assert res is not None and res.label == "person"


def test_inference_router_disabled_without_model_or_ollama(context):
    from tailcam.training.inference import InferenceRouter

    context.config.training.active_model_id = 0  # no active model; Ollama off by default
    router = InferenceRouter(context.store, context.config.training, context.analyzer)
    assert router.enabled is False
    assert router.analyze(np.zeros((4, 4, 3), np.uint8)) is None


def test_import_events_is_idempotent(context, tmp_path):
    """Clicking Import twice must not duplicate samples."""
    import time as _time

    from tailcam.persistence.models import MotionEventRecord

    ds = context.training.create_dataset("Dedup")
    thumb = tmp_path / "evt.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xdbfake-jpeg")
    eid = context.store.add_motion_event(
        MotionEventRecord(
            id=None, camera_id="cam0", start_ts=_time.time(), end_ts=_time.time(),
            peak_score=0.5, recording_id=None, thumb_path=str(thumb),
            label="person", confidence=0.9,
        )
    )
    context.store.set_event_thumb(eid, str(thumb))
    first = context.training.import_from_events(ds.id)
    second = context.training.import_from_events(ds.id)
    assert first == 1
    assert second == 0  # already imported -> skipped


def test_concurrent_training_runs_rejected(context, monkeypatch):
    import pytest as _pytest

    ds = context.training.create_dataset("Guard")
    monkeypatch.setattr(context.training, "has_active_run", lambda: True)
    with _pytest.raises(RuntimeError):
        context.training.train(ds.id)


def test_pipeline_describe_modes(context):
    d = context.inference.describe()
    assert d["mode"] in ("off", "ollama", "local")
    # default test config: AI disabled + no active model -> off
    assert d["mode"] == "off"
    context.config.ai.enabled = True
    d2 = context.inference.describe()
    assert d2["mode"] == "ollama"
    assert d2["model_name"] == context.config.ai.model
    # a dangling active model id surfaces an error instead of silence
    context.config.training.active_model_id = 99999
    d3 = context.inference.describe()
    assert d3["mode"] == "ollama"
    assert "no longer exists" in d3["error"]


def test_ai_info_includes_pipeline(client):
    body = client.get("/api/ai").json()
    assert "pipeline" in body
    assert body["pipeline"]["mode"] in ("off", "ollama", "local")


def test_ai_test_endpoint(client, context):
    cams = client.get("/api/cameras").json()
    assert cams, "synthetic camera expected"
    cam_id = cams[0]["id"]
    # Pipeline off -> clear error, not a 500.
    r = client.post("/api/ai/test", json={"camera_id": cam_id})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "off" in body["error"] or "enable" in body["error"].lower()
    # Unknown camera -> 404.
    assert client.post("/api/ai/test", json={"camera_id": "nope"}).status_code == 404
