"""Object detection: annotation storage, YOLO export, inference routing, API."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _dataset_with_sample(store, tmp_path: Path, task: str = "detection"):
    from tailcam.persistence.models import DatasetRecord, DatasetSampleRecord

    ds_id = store.add_dataset(
        DatasetRecord(id=None, name="Objects", task=task, created_ts=1.0)
    )
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"jpeg")
    sample_id = store.add_sample(
        DatasetSampleRecord(
            id=None, dataset_id=ds_id, path=str(img), thumb=None, label=None,
            source="manual", camera_id="cam", host="h", created_ts=1.0,
        )
    )
    return ds_id, sample_id, img


def test_annotations_roundtrip_and_replace(store, tmp_path):
    from tailcam.persistence.models import SampleAnnotationRecord

    _, sample_id, _ = _dataset_with_sample(store, tmp_path)
    store.replace_annotations(
        sample_id,
        [
            SampleAnnotationRecord(None, sample_id, "person", 0.5, 0.5, 0.2, 0.4, 1.0),
            SampleAnnotationRecord(None, sample_id, "dog", 0.1, 0.1, 0.1, 0.1, 1.0),
        ],
    )
    boxes = store.list_annotations(sample_id)
    assert [b.label for b in boxes] == ["person", "dog"]

    # Replace swaps the whole set, doesn't append.
    store.replace_annotations(
        sample_id,
        [SampleAnnotationRecord(None, sample_id, "cat", 0.5, 0.5, 0.3, 0.3, 2.0)],
    )
    boxes = store.list_annotations(sample_id)
    assert [b.label for b in boxes] == ["cat"]


def test_annotations_cascade_on_sample_delete(store, tmp_path):
    from tailcam.persistence.models import SampleAnnotationRecord

    ds_id, sample_id, _ = _dataset_with_sample(store, tmp_path)
    store.replace_annotations(
        sample_id, [SampleAnnotationRecord(None, sample_id, "x", 0.5, 0.5, 0.2, 0.2, 1.0)]
    )
    store.delete_sample(sample_id)
    assert store.list_annotations(sample_id) == []

    # Deleting the whole dataset also clears its samples' boxes.
    _, sid2, _ = _dataset_with_sample(store, tmp_path)
    store.replace_annotations(
        sid2, [SampleAnnotationRecord(None, sid2, "y", 0.5, 0.5, 0.2, 0.2, 1.0)]
    )
    ds2 = store.get_sample(sid2).dataset_id
    store.delete_dataset(ds2)
    assert store.list_annotations(sid2) == []


def test_annotation_batched_counts(store, tmp_path):
    from tailcam.persistence.models import SampleAnnotationRecord

    ds_id, sample_id, _ = _dataset_with_sample(store, tmp_path)
    store.replace_annotations(
        sample_id,
        [
            SampleAnnotationRecord(None, sample_id, "person", 0.5, 0.5, 0.2, 0.2, 1.0),
            SampleAnnotationRecord(None, sample_id, "person", 0.2, 0.2, 0.1, 0.1, 1.0),
            SampleAnnotationRecord(None, sample_id, "vehicle", 0.8, 0.8, 0.1, 0.1, 1.0),
        ],
    )
    assert store.annotation_counts(ds_id) == {sample_id: 3}
    assert store.dataset_annotation_label_counts(ds_id) == {"person": 2, "vehicle": 1}


def test_set_annotations_clamps_and_drops_degenerate_boxes(context, tmp_path):
    _, sample_id, _ = _dataset_with_sample(context.store, tmp_path)
    stored = context.training.set_annotations(
        sample_id,
        [
            {"label": "person", "cx": 1.4, "cy": -0.2, "w": 0.3, "h": 0.3},  # clamped
            {"label": "", "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2},  # no label → dropped
            {"label": "ghost", "cx": 0.5, "cy": 0.5, "w": 0.0, "h": 0.2},  # zero w → dropped
        ],
    )
    assert stored is not None
    assert len(stored) == 1
    assert stored[0].label == "person"
    assert stored[0].cx == 1.0 and stored[0].cy == 0.0


def test_set_annotations_missing_sample_returns_none(context):
    assert context.training.set_annotations(999999, []) is None


def test_export_detection_dataset_yolo_layout(store, tmp_path):
    from tailcam.persistence.models import (
        DatasetRecord,
        DatasetSampleRecord,
        SampleAnnotationRecord,
    )
    from tailcam.training.runner import export_detection_dataset

    ds_id = store.add_dataset(
        DatasetRecord(id=None, name="D", task="detection", created_ts=1.0)
    )
    for i in range(6):
        img = tmp_path / f"img{i}.jpg"
        img.write_bytes(b"jpeg")
        sid = store.add_sample(
            DatasetSampleRecord(
                id=None, dataset_id=ds_id, path=str(img), thumb=None, label=None,
                source="manual", camera_id="cam", host="h", created_ts=float(i),
            )
        )
        store.replace_annotations(
            sid,
            [SampleAnnotationRecord(None, sid, "person" if i % 2 else "dog",
                                    0.5, 0.5, 0.2, 0.2, 1.0)],
        )

    out = tmp_path / "export"
    classes, n_train, n_val = export_detection_dataset(store, ds_id, out)

    assert classes == ["dog", "person"]
    assert n_train + n_val == 6
    assert n_val >= 1
    data_yaml = (out / "data.yaml").read_text()
    assert "0: dog" in data_yaml and "1: person" in data_yaml
    # One label file per exported image, in YOLO "<idx> cx cy w h" form.
    train_labels = list((out / "labels" / "train").glob("*.txt"))
    assert len(train_labels) == n_train
    first = train_labels[0].read_text().strip().split()
    assert len(first) == 5 and first[0] in ("0", "1")


def test_export_detection_dataset_requires_boxes(store, tmp_path):
    import pytest

    from tailcam.training.runner import export_detection_dataset

    _, sample_id, _ = _dataset_with_sample(store, tmp_path)  # sample but no boxes
    ds_id = store.get_sample(sample_id).dataset_id
    with pytest.raises(ValueError):
        export_detection_dataset(store, ds_id, tmp_path / "export")


def test_inference_router_detect_uses_active_detection_model(store, monkeypatch):
    import json
    import time

    from tailcam.ai.analyzer import OllamaAnalyzer
    from tailcam.config import AIConfig, TrainingConfig
    from tailcam.persistence.models import ModelRecord
    from tailcam.training import inference
    from tailcam.training.inference import Detection, InferenceRouter

    model_id = store.add_model(
        ModelRecord(
            id=None, name="Det", kind="byo", path="/tmp/det.pt",
            classes_json="[]", base_model="", metrics_json="{}",
            created_ts=time.time(), task="detection",
        )
    )
    store.set_active_model(model_id)

    cfg = TrainingConfig()
    cfg.active_model_id = model_id

    boxes = [Detection("person", 0.9, 0.5, 0.5, 0.2, 0.4)]

    class FakeDetector:
        def __init__(self, path, conf):
            pass

        def load(self):
            return True

        def detect(self, image):
            return boxes

    monkeypatch.setattr(inference, "LocalDetector", FakeDetector)
    router = InferenceRouter(store, cfg, OllamaAnalyzer(AIConfig()))

    assert router.detection_active is True
    out = router.detect(np.zeros((4, 4, 3), dtype=np.uint8))
    assert out == boxes
    # A detection model also drives motion analysis via its top box.
    analysis = router.analyze(np.zeros((4, 4, 3), dtype=np.uint8))
    assert analysis is not None and analysis.label == "person"
    # classes_json is irrelevant for detection models (names come from the model).
    assert json.loads(store.get_model(model_id).classes_json) == []


def test_inference_router_no_detection_model(store):
    from tailcam.ai.analyzer import OllamaAnalyzer
    from tailcam.config import AIConfig, TrainingConfig
    from tailcam.training.inference import InferenceRouter

    router = InferenceRouter(store, TrainingConfig(), OllamaAnalyzer(AIConfig()))
    assert router.detection_active is False
    assert router.detect(np.zeros((4, 4, 3), dtype=np.uint8)) is None


def test_annotation_api_roundtrip(client):
    # Create a detection dataset.
    ds = client.post(
        "/api/datasets", json={"name": "Boxes", "task": "detection"}
    ).json()
    assert ds["task"] == "detection"

    # Manually insert a sample to annotate (no upload endpoint needed for this test).
    from tailcam.persistence.models import DatasetSampleRecord

    sample_path = str(Path(client.app.state.ctx.store.db_path).parent / "anno-sample.jpg")
    Path(sample_path).write_bytes(b"jpeg")
    store = client.app.state.ctx.store
    sid = store.add_sample(
        DatasetSampleRecord(
            id=None, dataset_id=ds["id"], path=sample_path, thumb=None, label=None,
            source="manual", camera_id="cam", host="h", created_ts=1.0,
        )
    )

    # PUT boxes, then GET them back.
    put = client.put(
        f"/api/samples/{sid}/annotations",
        json={"boxes": [{"label": "person", "cx": 0.5, "cy": 0.5, "w": 0.3, "h": 0.4}]},
    )
    assert put.status_code == 200
    assert put.json()["boxes"][0]["label"] == "person"

    got = client.get(f"/api/samples/{sid}/annotations").json()
    assert len(got["boxes"]) == 1

    # The sample list reports the box count.
    samples = client.get(f"/api/datasets/{ds['id']}/samples").json()
    assert samples[0]["annotation_count"] == 1

    # Out-of-range coordinates are rejected by validation.
    bad = client.put(
        f"/api/samples/{sid}/annotations",
        json={"boxes": [{"label": "x", "cx": 2.0, "cy": 0.5, "w": 0.3, "h": 0.4}]},
    )
    assert bad.status_code == 422


def test_detection_train_lifecycle_uses_detection_export(context, client, tmp_path, monkeypatch):
    import time

    from tailcam.persistence.models import DatasetSampleRecord, SampleAnnotationRecord
    from tailcam.training import engine, runner

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    monkeypatch.setattr(engine, "torch_device", lambda: "cpu")

    seen: dict = {}

    def fake_train(base, data_dir, epochs, imgsz, device, project_dir, on_epoch,
                   should_stop=None, task="classification"):
        seen["task"] = task
        seen["base"] = base
        assert (Path(data_dir) / "data.yaml").exists()  # detection export ran
        on_epoch(epochs)
        out = Path(project_dir) / "train" / "weights" / "best.pt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"weights")
        return {"model_path": str(out), "metrics": {"map50": 0.8}}

    monkeypatch.setattr(runner, "train_model", fake_train)

    ds = context.training.create_dataset("Det", task="detection")
    for i in range(4):
        img = tmp_path / f"d{i}.jpg"
        img.write_bytes(b"jpeg")
        sid = context.store.add_sample(
            DatasetSampleRecord(
                id=None, dataset_id=ds.id, path=str(img), thumb=None, label=None,
                source="manual", camera_id="cam", host="h", created_ts=float(i),
            )
        )
        context.store.replace_annotations(
            sid, [SampleAnnotationRecord(None, sid, "person", 0.5, 0.5, 0.2, 0.2, 1.0)]
        )

    run = client.post("/api/training/runs", json={"dataset_id": ds.id, "epochs": 1}).json()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = client.get(f"/api/training/runs/{run['id']}").json()
        if r["status"] in ("complete", "error"):
            break
        time.sleep(0.05)

    assert seen["task"] == "detection"
    assert seen["base"] == context.config.training.detect_base_model
    r = client.get(f"/api/training/runs/{run['id']}").json()
    assert r["status"] == "complete"
    # The produced model is registered as a detection model.
    models = client.get("/api/models").json()
    trained = [m for m in models if m["kind"] == "trained"]
    assert trained and trained[0]["task"] == "detection"


def test_detect_endpoint_without_active_model(client):
    # With the built-in detector disabled and no trained model, there is no
    # box source at all -> detector_active False and the UI shows no overlay.
    client.app.state.ctx.config.detection.enabled = False
    cam = client.get("/api/cameras").json()[0]
    res = client.post(f"/api/cameras/{cam['id']}/detect")
    assert res.status_code == 200
    body = res.json()
    assert body["detector_active"] is False
    assert body["boxes"] == []
