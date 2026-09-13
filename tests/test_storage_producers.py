"""Producer placement, bounded workspaces, and legacy alias regression checks."""

from __future__ import annotations

import io
import stat
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tailcam import paths
from tailcam.config import AppConfig
from tailcam.media.gallery import MediaGallery
from tailcam.media.recorder import RecordingService, _RecordingSession
from tailcam.media.snapshot import SnapshotService
from tailcam.media.storage import ProducerWorkspace, alias
from tailcam.persistence.models import ModelRecord
from tailcam.storage.models import DestinationRef, PolicyOverride, StorageError, StoragePolicy
from tailcam.storage.service import StorageService
from tailcam.timelapse.service import TimelapseService
from tailcam.training.inference import ManagedModelLease
from tailcam.training.service import TrainingService


@pytest.fixture
def storage(store, tmp_path):
    config = AppConfig()
    service = StorageService(config, store, store.get_node_id())
    location = service.register_location(str(tmp_path / "primary"), create=True, make_default=True)
    service.set_policy(
        StoragePolicy(
            default_destination=DestinationRef(
                node_id=service.node_id, location_id=location.location_id
            ),
            workspace_max_bytes=4 * 1024 * 1024,
        )
    )
    yield service
    service.close()


@pytest.fixture
def camera():
    image = np.full((32, 48, 3), 120, dtype=np.uint8)
    buffer = SimpleNamespace(await_latest=lambda *args, **kwargs: SimpleNamespace(image=image))
    return SimpleNamespace(
        get_buffer=lambda _: buffer,
        get=lambda _: None,
        list=lambda: [],
        image=image,
        buffer=buffer,
    )


def test_snapshot_and_thumbnail_use_separate_physical_destinations(
    storage, store, camera, tmp_path
):
    thumb_location = storage.register_location(str(tmp_path / "thumbs"), create=True)
    policy = storage.get_policy()
    policy.overrides = [
        PolicyOverride(
            content_kind="thumbnail",
            destination=DestinationRef(
                node_id=storage.node_id, location_id=thumb_location.location_id
            ),
        )
    ]
    storage.set_policy(policy)
    record = SnapshotService(camera, store, storage_service=storage).capture("camera/one")
    assert record is not None
    original = storage.catalog.resolve_alias("media", str(record.id), "file")
    thumb = storage.catalog.resolve_alias("media", str(record.id), "thumbnail")
    assert original and thumb and thumb.parent_id == original.artifact_id
    assert Path(record.path).parent == tmp_path / "primary"
    assert Path(record.thumbnail).parent == tmp_path / "thumbs"
    assert not list(paths.media_dir().glob("*.jpg"))
    assert original.origin_node_id == storage.node_id
    assert original.camera_id == "camera/one"


def test_snapshot_sibling_admission_failure_writes_nothing(storage, store, camera, tmp_path):
    location = storage.register_location(str(tmp_path / "thumbs"), create=True)
    policy = storage.get_policy()
    policy.overrides = [
        PolicyOverride(
            content_kind="thumbnail",
            destination=DestinationRef(node_id=storage.node_id, location_id=location.location_id),
        )
    ]
    storage.set_policy(policy)
    (tmp_path / "thumbs").rename(tmp_path / "removed")
    with pytest.raises(StorageError):
        SnapshotService(camera, store, storage_service=storage).capture("cam")
    assert storage.catalog.list() == []
    assert store.list_media() == []
    assert not (tmp_path / "thumbs").exists()


def test_recording_keeps_frozen_destinations_after_policy_change(
    storage, store, camera, monkeypatch, tmp_path
):
    monkeypatch.setattr(_RecordingSession, "start", lambda self: None)
    monkeypatch.setattr(_RecordingSession, "stop", lambda self: None)
    recorder = RecordingService(camera, store, storage_service=storage)
    assert recorder.start("cam")
    session = recorder._sessions["cam"]
    session.path = session.workspace.path / "clip.mp4"
    session.path.write_bytes(b"isolated encoder output")
    session.frames_written = 1
    session._first_image = camera.image
    moved = storage.register_location(str(tmp_path / "new"), create=True)
    policy = storage.get_policy()
    policy.default_destination.location_id = moved.location_id
    storage.set_policy(policy)
    storage.catalog.set_setting("policy_enabled", "false")
    record = recorder.stop("cam")
    assert Path(record.path).parent == tmp_path / "primary"
    assert Path(record.thumbnail).parent == tmp_path / "primary"
    assert not session.workspace.path.exists()


def test_zero_local_workspace_rejected_before_recording_thread(storage, store, camera, monkeypatch):
    policy = storage.get_policy()
    policy.zero_local_media = True
    storage.set_policy(policy)
    monkeypatch.setattr(_RecordingSession, "start", lambda _: pytest.fail("thread started"))
    with pytest.raises(StorageError):
        RecordingService(camera, store, storage_service=storage).start("cam")


def test_byte_workspace_refuses_write_before_budget_is_exceeded(storage):
    job = ProducerWorkspace(storage, ("timelapse_frame",))
    target = job.path / "frame.jpg"
    with pytest.raises(StorageError, match="budget"):
        job.write(target, b"x" * (job.max_bytes + 1))
    assert not target.exists()
    job.release()


def test_timelapse_commits_frames_and_reconstructs_from_aliases(
    storage, store, camera, monkeypatch
):
    from tailcam.timelapse.worker import TimelapseCaptureWorker

    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start("cam")
    worker = service._workers[record.id]
    worker._save(camera.image)
    artifact = storage.catalog.resolve_alias("timelapse", str(record.id), "frame/000000")
    assert artifact and artifact.kind == "timelapse_frame"
    committed = storage.resolve(artifact.artifact_id).read_bytes()
    old_job = service._storage_jobs.pop(record.id)
    old_job.release()
    # A fresh service must recover from the catalog, not stale frames_dir.
    restarted = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    job = restarted._prepare_storage_frames(record.id, store.get_timelapse(record.id))
    assert (job.path / "frames" / "000000.jpg").read_bytes() == committed
    job.release()


def test_training_sample_catalog_and_managed_delete(storage, store, camera):
    service = TrainingService(
        camera, store, AppConfig().training, None, "test", storage_service=storage
    )
    dataset = service.create_dataset("test")
    sample_id = service._save_sample(dataset.id, "cam", camera.image, "collect", label="dog")
    sample = store.get_sample(sample_id)
    artifact = storage.catalog.resolve_alias("sample", str(sample_id), "file")
    thumb = storage.catalog.resolve_alias("sample", str(sample_id), "thumbnail")
    assert Path(sample.path) == storage.resolve(artifact.artifact_id)
    assert thumb.parent_id == artifact.artifact_id
    assert not (paths.datasets_dir() / str(dataset.id)).exists()
    assert service.delete_sample(sample_id)
    assert not Path(sample.path).exists()
    assert store.get_sample(sample_id) is None


def test_gallery_preserves_row_on_delete_failure(storage, store, camera, monkeypatch):
    record = SnapshotService(camera, store, storage_service=storage).capture("cam")

    def fail(*args):
        raise StorageError("mount_unavailable", "Unavailable")

    monkeypatch.setattr(storage, "delete_family", fail)
    assert not MediaGallery(store, storage).delete(record.id)
    assert store.get_media(record.id) is not None
    assert Path(record.path).exists()


def test_legacy_retention_cannot_delete_managed_artifacts(storage, store, camera):
    record = SnapshotService(camera, store, storage_service=storage).capture("cam")
    from tailcam.config import RetentionConfig

    assert MediaGallery(store, storage).prune(RetentionConfig(max_age_days=0, max_gb=0)) == 0
    assert store.get_media(record.id) is not None
    assert Path(record.path).exists()


def _model(store, storage, contents):
    artifact = storage.put_bytes(
        "model_output", contents, metadata={"format": "directory-zip", "filename": "model.zip"}
    )
    model = ModelRecord(
        id=None,
        name="Managed",
        kind="trained",
        path="",
        classes_json="[]",
        base_model="florence2",
        metrics_json="{}",
        created_ts=time.time(),
        task="detection",
    )
    model.id = store.add_model(model)
    alias(storage, "model", model.id, "file", artifact)
    return model


def _zip(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as out:
        for name, data in entries:
            out.writestr(name, data)
    return target.getvalue()


def test_model_directory_materialization_and_cleanup(storage, store):
    model = _model(
        store, storage, _zip([("config.json", b"{}"), ("weights/model.bin", b"weights")])
    )
    lease = ManagedModelLease(storage)
    result = Path(lease.resolve(model))
    assert (result / "weights/model.bin").read_bytes() == b"weights"
    workspace = lease.lease.path
    lease.close()
    assert not workspace.exists()


@pytest.mark.parametrize(
    "members",
    [
        [("../escape", b"x")],
        [("/absolute", b"x")],
        [("drive:C", b"x")],
        [("a\\b", b"x")],
        [("same", b"x"), ("SAME", b"y")],
        [("a", b"x"), ("a/b", b"y")],
    ],
)
def test_model_archive_rejects_unsafe_paths_before_extraction(storage, store, members):
    model = _model(store, storage, _zip(members))
    lease = ManagedModelLease(storage)
    with pytest.raises(StorageError, match="archive"):
        lease.resolve(model)
    assert lease.lease is None


def test_model_archive_rejects_links_and_excess_extracted_bytes(storage, store):
    entry = zipfile.ZipInfo("link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    model = _model(store, storage, _zip([(entry, b"target")]))
    with pytest.raises(StorageError):
        ManagedModelLease(storage).resolve(model)
    oversized = _zip([("weights", b"x" * (600 * 1024))])
    model = _model(store, storage, oversized)
    with pytest.raises(StorageError, match="budget"):
        ManagedModelLease(storage).resolve(model)


def test_failed_materialization_preserves_active_model(storage, store, camera):
    model = _model(store, storage, _zip([("config.json", b"{}")]))
    config = AppConfig().training
    config.active_model_id = 12
    policy = storage.get_policy()
    policy.zero_local_media = True
    storage.set_policy(policy)
    service = TrainingService(camera, store, config, None, "test", storage_service=storage)
    with pytest.raises(StorageError):
        service.activate_model(model.id)
    assert config.active_model_id == 12


def test_ensure_dirs_does_not_recreate_missing_external_root(isolated_env, tmp_path):
    missing = tmp_path / "detached" / "media"
    paths.set_media_override(str(missing))
    paths.ensure_dirs()
    assert not missing.exists()


def test_annotation_commit_failure_preserves_prior_labels_and_boxes(
    storage, store, camera, monkeypatch
):
    service = TrainingService(
        camera, store, AppConfig().training, None, "test", storage_service=storage
    )
    dataset = service.create_dataset("test")
    sample_id = service._save_sample(dataset.id, "cam", camera.image, "collect", label="dog")
    service.set_annotations(sample_id, [{"label": "dog", "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.3}])
    initial = storage.catalog.resolve_alias("sample", str(sample_id), "annotation")
    parent = storage.catalog.resolve_alias("sample", str(sample_id), "file")
    assert initial.parent_id == parent.artifact_id
    original = storage.put_bytes

    def reject(kind, *args, **kwargs):
        if kind == "annotation":
            raise StorageError("owner_unavailable", "Required owner is unavailable")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(storage, "put_bytes", reject)
    with pytest.raises(StorageError):
        service.relabel_sample(sample_id, "cat")
    with pytest.raises(StorageError):
        service.set_annotations(
            sample_id, [{"label": "cat", "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.3}]
        )
    assert store.get_sample(sample_id).label == "dog"
    assert store.list_annotations(sample_id)[0].label == "dog"
    assert (
        storage.catalog.resolve_alias("sample", str(sample_id), "annotation").artifact_id
        == initial.artifact_id
    )


def test_unified_training_refuses_dependency_download_before_thread(
    storage, store, camera, monkeypatch
):
    service = TrainingService(
        camera, store, AppConfig().training, None, "test", storage_service=storage
    )
    dataset = service.create_dataset("test")
    monkeypatch.setattr(threading.Thread, "start", lambda _: pytest.fail("training thread started"))
    with pytest.raises(StorageError) as failure:
        service.train(dataset.id, base_model="missing-model.pt")
    assert failure.value.code == "unsupported_cache_control"
    assert store.list_runs() == []
    assert not storage.catalog.connection.execute(
        "SELECT id FROM storage_reservations WHERE category='workspace'"
    ).fetchall()


def test_training_uses_bounded_preprovisioned_weights_and_catalogs_export(
    storage, store, camera, monkeypatch, tmp_path
):
    from tailcam.training import engine, runner

    service = TrainingService(
        camera, store, AppConfig().training, None, "test", storage_service=storage
    )
    dataset = service.create_dataset("test")
    for label in ("cat", "cat", "dog", "dog"):
        service._save_sample(dataset.id, "cam", camera.image, "collect", label=label)
    base = tmp_path / "base.pt"
    base.write_bytes(b"preprovisioned weights")

    def train(weights, data_dir, epochs, imgsz, device, run_dir, **kwargs):
        assert Path(weights).is_relative_to(run_dir)
        assert Path(weights).read_bytes() == base.read_bytes()
        assert kwargs["offline"] is True
        assert len(list(data_dir.rglob("*.jpg"))) == 4
        out = run_dir / "best.pt"
        out.write_bytes(b"trained weights")
        return {"model_path": str(out), "metrics": {"accuracy": 1.0}}

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    monkeypatch.setattr(engine, "torch_device", lambda: "cpu")
    monkeypatch.setattr(runner, "train_model", train)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())
    run = service.train(dataset.id, base_model=str(base))
    assert run.status == "complete", run.log
    model = store.get_model(run.model_id)
    artifact = storage.catalog.resolve_alias("model", str(model.id), "file")
    export = storage.catalog.resolve_alias("training_run", str(run.id), "export")
    assert Path(model.path).read_bytes() == b"trained weights"
    assert artifact.kind == "model_output" and export.kind == "export"
    with zipfile.ZipFile(storage.resolve(export.artifact_id)) as archive:
        assert len([name for name in archive.namelist() if name.endswith(".jpg")]) == 4
    assert base.read_bytes() == b"preprovisioned weights"
    assert not service._storage_jobs


def test_recording_failure_retains_recoverable_workspace(
    storage, store, camera, monkeypatch, tmp_path
):
    monkeypatch.setattr(_RecordingSession, "start", lambda self: None)
    monkeypatch.setattr(_RecordingSession, "stop", lambda self: None)
    recorder = RecordingService(camera, store, storage_service=storage)
    recorder.start("cam")
    session = recorder._sessions["cam"]
    session.path = session.workspace.path / "clip.mp4"
    session.path.write_bytes(b"recover me")
    session.frames_written = 1
    (tmp_path / "primary").rename(tmp_path / "detached")
    with pytest.raises(StorageError):
        recorder.stop("cam")
    assert session.path.read_bytes() == b"recover me"
    assert not session.workspace.lease.closed
    assert store.list_media() == []
    assert not (tmp_path / "primary").exists()


def test_managed_motion_import_uses_alias_and_is_idempotent(storage, store, camera):
    from tailcam.media.storage import thumbnail_bytes
    from tailcam.persistence.models import MotionEventRecord

    event_id = store.add_motion_event(
        MotionEventRecord(
            id=None,
            camera_id="cam",
            start_ts=time.time(),
            end_ts=None,
            peak_score=1.0,
            recording_id=None,
            label="dog",
            thumb_path="",
        )
    )
    store.set_event_analysis(event_id, "dog", None, 0.9)
    evidence = storage.put_bytes("analysis_evidence", thumbnail_bytes(camera.image))
    alias(storage, "motion", event_id, "thumbnail", evidence)
    service = TrainingService(
        camera, store, AppConfig().training, None, "test", storage_service=storage
    )
    dataset = service.create_dataset("test")
    assert service.import_from_events(dataset.id) == 1
    assert service.import_from_events(dataset.id) == 0
    sample = store.list_samples(dataset.id)[0]
    assert sample.label == "dog"
    assert Path(sample.path).is_file()
    assert storage.catalog.resolve_alias("sample", str(sample.id), "file") is not None


def test_failed_model_switch_keeps_previous_loaded_model_and_releases_candidates(
    storage, store, monkeypatch
):
    from tailcam.training.inference import InferenceRouter, LocalClassifier

    good = storage.put_bytes("model_output", b"weights")
    record = ModelRecord(
        id=None,
        name="Working",
        kind="trained",
        path="",
        classes_json='["cat"]',
        base_model="yolo",
        metrics_json="{}",
        created_ts=time.time(),
    )
    record.id = store.add_model(record)
    alias(storage, "model", record.id, "file", good)
    config = AppConfig().training
    config.active_model_id = record.id
    monkeypatch.setattr(LocalClassifier, "load", lambda _: True)
    inference = InferenceRouter(
        store, config, SimpleNamespace(enabled=False), storage_service=storage
    )
    assert inference.describe()["model_name"] == "Working"
    original_classifier = inference._classifier
    original_workspace = inference._model_lease.lease.path
    bad = _model(store, storage, _zip([("../escape", b"x")]))
    config.active_model_id = bad.id
    inference.describe()
    assert inference._classifier is original_classifier
    assert original_workspace.exists()
    assert inference._load_error
    assert (
        len(
            storage.catalog.connection.execute(
                "SELECT id FROM storage_reservations WHERE category='workspace'"
            ).fetchall()
        )
        == 1
    )
    inference.shutdown()
    assert not original_workspace.exists()


def test_unified_vlm_training_refused_before_backend_probes(storage, store, camera, monkeypatch):
    import tailcam.activelearning.service as module
    from tailcam.activelearning.service import ActiveLearningService

    config = AppConfig()
    training = TrainingService(
        camera, store, config.training, None, "test", storage_service=storage
    )
    dataset = training.create_dataset("test", task="detection")
    config.active_learning.dataset_id = dataset.id
    config.active_learning.finetune_model = "florence2"
    service = ActiveLearningService(
        camera, store, config, None, None, training, "test", storage_service=storage
    )
    monkeypatch.setattr(
        module, "list_finetune_backends", lambda _: pytest.fail("backend probe ran")
    )
    monkeypatch.setattr(threading.Thread, "start", lambda _: pytest.fail("thread started"))
    with pytest.raises(StorageError) as failure:
        service.train()
    assert failure.value.code == "unsupported_cache_control"
    assert store.list_runs() == []


def test_legacy_snapshot_refuses_disconnected_media_root(store, camera, tmp_path):
    missing = tmp_path / "disconnected"
    paths.set_media_override(str(missing))
    with pytest.raises(FileNotFoundError):
        SnapshotService(camera, store).capture("cam")
    assert not missing.exists()
    assert store.list_media() == []


def test_timelapse_video_and_smoothing_publish_to_their_frozen_destinations(
    storage,
    store,
    camera,
    monkeypatch,
    tmp_path,
):
    import tailcam.timelapse.service as module
    from tailcam.timelapse.worker import TimelapseCaptureWorker

    video_location = storage.register_location(str(tmp_path / "videos"), create=True)
    smooth_location = storage.register_location(str(tmp_path / "smooth"), create=True)
    policy = storage.get_policy()
    policy.overrides = [
        PolicyOverride(
            content_kind="timelapse_video",
            destination=DestinationRef(
                node_id=storage.node_id, location_id=video_location.location_id
            ),
        ),
        PolicyOverride(
            content_kind="timelapse_smooth",
            destination=DestinationRef(
                node_id=storage.node_id, location_id=smooth_location.location_id
            ),
        ),
    ]
    storage.set_policy(policy)
    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start("cam", auto_smooth=True, smooth_engine="ffmpeg")
    service._workers[record.id]._save(camera.image)
    workspace = service._storage_jobs[record.id].path

    def encode(frames, fps):
        video = frames.parent / "video.mp4"
        thumb = frames.parent / "thumbnail.jpg"
        video.write_bytes(b"encoded timelapse")
        thumb.write_bytes(b"thumbnail")
        return video, thumb, (48, 32, 1)

    def smooth(command):
        Path(command[-1]).write_bytes(b"smoothed timelapse")
        return True

    monkeypatch.setattr(module, "_encode_frames", encode)
    monkeypatch.setattr(module, "ffmpeg_path", lambda: "isolated-test-encoder")
    monkeypatch.setattr(module, "run_ffmpeg", smooth)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())
    storage.catalog.set_setting("policy_enabled", "false")
    service._encode_job(record.id)
    completed = store.get_timelapse(record.id)
    assert completed.state == "complete" and completed.smooth_state == "complete"
    assert Path(completed.video_path).parent == tmp_path / "videos"
    assert Path(completed.smooth_path).parent == tmp_path / "smooth"
    assert Path(completed.thumb_path).parent == tmp_path / "primary"
    assert not workspace.exists()
    assert storage.catalog.resolve_alias("timelapse", str(record.id), "frame/000000") is not None


@pytest.mark.parametrize("restart", [False, True])
def test_deleting_empty_timelapse_releases_its_durable_workspace(
    storage,
    store,
    camera,
    monkeypatch,
    restart,
):
    from tailcam.timelapse.worker import TimelapseCaptureWorker

    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    monkeypatch.setattr(TimelapseCaptureWorker, "stop", lambda self: None)
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start("cam")
    workspace = service._storage_jobs[record.id].path
    assert (
        storage.catalog.connection.execute(
            "SELECT count(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 1
    )
    if restart:
        service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    assert service.delete(record.id)
    assert not workspace.exists()
    assert record.id not in service._storage_jobs
    assert store.get_timelapse(record.id) is None
    assert (
        storage.catalog.connection.execute(
            "SELECT count(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 0
    )


def test_failed_timelapse_workspace_cleanup_preserves_job_and_reservation(
    storage, store, camera, monkeypatch
):
    from tailcam.timelapse.worker import TimelapseCaptureWorker

    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    monkeypatch.setattr(TimelapseCaptureWorker, "stop", lambda self: None)
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start("cam")
    job = service._storage_jobs[record.id]

    def fail():
        raise OSError("Cannot remove workspace")

    monkeypatch.setattr(job, "release", fail)
    assert not service.delete(record.id)
    assert store.get_timelapse(record.id) is not None
    assert service._storage_jobs[record.id] is job
    assert job.path.exists()
    assert (
        storage.catalog.connection.execute(
            "SELECT count(*) FROM storage_reservations WHERE category='workspace'"
        ).fetchone()[0]
        == 1
    )


def test_timelapse_delete_never_races_an_active_encoder(storage, store, camera, monkeypatch):
    from tailcam.timelapse.worker import TimelapseCaptureWorker

    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start("cam")
    job = service._storage_jobs[record.id]
    service._encoding.add(record.id)
    assert not service.delete(record.id)
    assert job.path.exists()
    assert store.get_timelapse(record.id) is not None


def test_delegated_recording_uses_verified_origin_camera_override(
    storage, store, camera, monkeypatch, tmp_path
):
    from uuid import uuid4

    origin = str(uuid4())
    location = storage.register_location(str(tmp_path / "camera-specific"), create=True)
    policy = storage.get_policy()
    policy.overrides = [
        PolicyOverride(
            origin_node_id=origin,
            camera_id="/dev/video0",
            destination=DestinationRef(node_id=storage.node_id, location_id=location.location_id),
        )
    ]
    storage.set_policy(policy)
    monkeypatch.setattr(_RecordingSession, "start", lambda self: None)
    monkeypatch.setattr(_RecordingSession, "stop", lambda self: None)
    recorder = RecordingService(camera, store, storage_service=storage)
    assert recorder.start(
        "peer:/dev/video0",
        buffer=camera.buffer,
        media_camera_id="/dev/video0",
        source_host="display-only",
        origin_node_id=origin,
    )
    session = recorder._sessions["peer:/dev/video0"]
    session.path = session.workspace.path / "clip.mp4"
    session.path.write_bytes(b"delegated recording")
    session.frames_written = 1
    session._first_image = camera.image
    record = recorder.stop("peer:/dev/video0")
    artifact = storage.catalog.resolve_alias("media", str(record.id), "file")
    thumb = storage.catalog.resolve_alias("media", str(record.id), "thumbnail")
    assert artifact.origin_node_id == thumb.origin_node_id == origin
    assert artifact.camera_id == "/dev/video0"
    assert Path(record.path).parent == tmp_path / "camera-specific"
    assert Path(record.thumbnail).parent == tmp_path / "camera-specific"


def test_reencoding_delegated_timelapse_recovers_verified_origin(
    storage, store, camera, monkeypatch
):
    from uuid import uuid4

    from tailcam.timelapse.worker import TimelapseCaptureWorker

    origin = str(uuid4())
    monkeypatch.setattr(TimelapseCaptureWorker, "start", lambda self: self.frames_dir.mkdir())
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    record = service.start(
        "/dev/video0", source_host="display-only", buffer=camera.buffer, origin_node_id=origin
    )
    service._workers[record.id]._save(camera.image)
    frame = storage.catalog.resolve_alias("timelapse", str(record.id), "frame/000000")
    assert frame.origin_node_id == origin
    service._storage_jobs.pop(record.id).release()
    restarted = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    job = restarted._prepare_storage_frames(record.id, store.get_timelapse(record.id))
    assert job.origin_node_id == origin
    job.release()


def test_snapshot_cannot_fall_back_to_legacy_write_when_policy_disabled_mid_capture(
    storage, store, camera
):
    def frame(*args, **kwargs):
        storage.catalog.set_setting("policy_enabled", "false")
        return SimpleNamespace(image=camera.image)

    manager = SimpleNamespace(get_buffer=lambda _: SimpleNamespace(await_latest=frame))
    record = SnapshotService(manager, store, storage_service=storage).capture("cam")
    assert storage.catalog.resolve_alias("media", str(record.id), "file") is not None
    assert not list(paths.media_dir().glob("*.jpg"))


@pytest.mark.parametrize("producer", ["snapshot", "sample", "workspace"])
def test_sibling_plans_share_one_policy_even_during_admission_update(
    storage,
    store,
    camera,
    monkeypatch,
    tmp_path,
    producer,
):
    moved = storage.register_location(str(tmp_path / "new-policy-root"), create=True)
    before = storage.get_policy()
    original = storage.admit
    calls = 0

    def changing_admission(kind, **kwargs):
        nonlocal calls
        plan = original(kind, **kwargs)
        calls += 1
        if calls == 1:
            policy = storage.get_policy()
            policy.default_destination.location_id = moved.location_id
            policy.zero_local_media = True
            storage.set_policy(policy)
        return plan

    monkeypatch.setattr(storage, "admit", changing_admission)
    if producer == "snapshot":
        record = SnapshotService(camera, store, storage_service=storage).capture("cam")
        artifacts = [
            storage.catalog.resolve_alias("media", str(record.id), variant)
            for variant in ("file", "thumbnail")
        ]
    elif producer == "sample":
        service = TrainingService(
            camera, store, AppConfig().training, None, "test", storage_service=storage
        )
        dataset = service.create_dataset("test")
        sample_id = service._save_sample(dataset.id, "cam", camera.image, "collect", label="dog")
        artifacts = [
            storage.catalog.resolve_alias("sample", str(sample_id), variant)
            for variant in ("file", "thumbnail", "annotation")
        ]
    else:
        job = ProducerWorkspace(storage, ("recording", "thumbnail"), "cam")
        assert {plan.policy_revision for plan in job.plans.values()} == {before.revision}
        assert {plan.destination.location_id for plan in job.plans.values()} == {
            before.default_destination.location_id
        }
        job.release()
        return
    assert {artifact.policy_revision for artifact in artifacts} == {before.revision}
    assert all(
        storage.resolve(artifact.artifact_id).parent == tmp_path / "primary"
        for artifact in artifacts
    )


@pytest.mark.parametrize("producer", ["recording", "timelapse"])
def test_unified_delegated_capture_requires_verified_origin(
    storage, store, camera, monkeypatch, producer
):
    monkeypatch.setattr(threading.Thread, "start", lambda _: pytest.fail("capture started"))
    service = (
        RecordingService(camera, store, storage_service=storage)
        if producer == "recording"
        else TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    )
    with pytest.raises(StorageError) as failure:
        service.start("cam", source_host="untrusted-hostname", buffer=camera.buffer)
    assert failure.value.code == "source_identity_unavailable"


def test_active_capture_keeps_workspace_until_stop_finishes(storage, store, camera, monkeypatch):
    service = TimelapseService(camera, store, AppConfig().timelapse, storage_service=storage)
    service._workers[9] = SimpleNamespace(alive=True)
    stopped = []
    monkeypatch.setattr(service, "stop", lambda tl_id: stopped.append(tl_id))
    monkeypatch.setattr(
        service, "_finalize_async", lambda _: pytest.fail("encoder bypassed capture stop")
    )
    assert service.encode(9) is None
    assert stopped == [9]
    with pytest.raises(StorageError) as failure:
        service.smooth(9)
    assert failure.value.code == "capture_busy"
    assert 9 not in service._smoothing
