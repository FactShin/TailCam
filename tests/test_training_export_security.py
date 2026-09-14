from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tailcam.persistence.models import DatasetRecord, DatasetSampleRecord, SampleAnnotationRecord
from tailcam.training.labels import validate_class_label
from tailcam.training.runner import export_classification_dataset, export_detection_dataset


def _samples(store, tmp_path: Path, task: str = "classification") -> tuple[int, list[int]]:
    dataset_id = store.add_dataset(DatasetRecord(None, "Examples", task, 1.0))
    ids = []
    for index, label in enumerate(("normal", "normal", "issue", "issue")):
        image = tmp_path / f"source-{index}.jpg"
        image.write_bytes(b"test image")
        ids.append(store.add_sample(DatasetSampleRecord(
            None, dataset_id, str(image), None, label, "manual", "camera", "host", 1.0,
        )))
    return dataset_id, ids


@pytest.mark.parametrize("label", [
    "../escape", "/absolute", "C:\\escape", "a/b", "a\\b", ".", "..",
    "CON", "con.txt", "NUL", "COM1", "LPT9", "file.", "a:b", "a\nkey: x",
    " space", "space ", "a\x00b", "x" * 65,
])
def test_unsafe_class_names_rejected_at_storage_boundary(store, tmp_path, label):
    _, ids = _samples(store, tmp_path)
    with pytest.raises(ValueError, match="Class labels"):
        store.set_sample_label(ids[0], label)
    with pytest.raises(ValueError, match="Class labels"):
        store.set_sample_machine_label(ids[0], label, 0.9)
    assert store.get_sample(ids[0]).label == "normal"


@pytest.mark.parametrize("label", [None, "", "person", "fire truck", "正常", "printer-ok"])
def test_safe_display_labels_are_preserved(label):
    assert validate_class_label(label) == label


@pytest.mark.parametrize("label", ["../outside", "/tmp/outside", "C:\\outside"])
def test_legacy_unsafe_label_cannot_escape_or_destroy_previous_export(store, tmp_path, label):
    dataset_id, ids = _samples(store, tmp_path)
    # Simulate records saved by an older release or an imported database.
    with store._conn() as conn:
        conn.execute("UPDATE dataset_samples SET label=? WHERE id IN (?,?)", (label, *ids[:2]))
    output = tmp_path / "export"
    output.mkdir()
    previous = output / "previous.txt"
    previous.write_text("preserved")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="Class labels"):
        export_classification_dataset(store, dataset_id, output)
    assert previous.read_text() == "preserved"
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_relabel_api_rejects_path_traversal_before_mutation(client, context, tmp_path):
    _, ids = _samples(context.store, tmp_path)
    response = client.patch(f"/api/samples/{ids[0]}", json={"label": "../escape"})
    assert response.status_code == 422
    assert context.store.get_sample(ids[0]).label == "normal"


def test_detection_export_quotes_labels_and_path_as_data(store, tmp_path):
    dataset_id, ids = _samples(store, tmp_path, "detection")
    label = 'thing\ntrain: /private\ndownload: "injected"'
    for sid in ids:
        store.replace_annotations(sid, [
            SampleAnnotationRecord(None, sid, label, 0.5, 0.5, 0.2, 0.2, 1.0),
        ])
    output = tmp_path / "export # folder"
    classes, train, validation = export_detection_dataset(store, dataset_id, output)
    manifest = yaml.safe_load((output / "data.yaml").read_text())
    assert manifest == {
        "path": str(output), "train": "images/train", "val": "images/val",
        "names": {0: label},
    }
    assert classes == [label] and train + validation == 4


@pytest.mark.parametrize("task", ["classification", "detection"])
def test_export_rejects_symlink_root_without_touching_target(store, tmp_path, task):
    dataset_id, ids = _samples(store, tmp_path, task)
    for sid in ids:
        store.replace_annotations(sid, [
            SampleAnnotationRecord(None, sid, "item", 0.5, 0.5, 0.2, 0.2, 1.0),
        ])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("untouched")
    output = tmp_path / "export"
    try:
        output.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    export = export_classification_dataset if task == "classification" else export_detection_dataset
    with pytest.raises(ValueError, match="symbolic link"):
        export(store, dataset_id, output)
    assert list(outside.iterdir()) == [outside / "keep"]
    assert (outside / "keep").read_text() == "untouched"
