"""Reviewed migrations use isolated SQLite and files; no hardware or peer services."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tailcam.config import AppConfig
from tailcam.persistence.store import Store
from tailcam.storage.migration import MigrationService
from tailcam.storage.models import DestinationRef, StorageError
from tailcam.storage.service import StorageService


@pytest.fixture
def migration(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    storage = StorageService(AppConfig(), store, store.get_node_id())
    source = storage.register_location(str(tmp_path / "source"), create=True)
    target = storage.register_location(str(tmp_path / "target"), create=True, make_default=False)
    service = MigrationService(storage, store)
    # Most tests execute the real worker synchronously, making persistence boundaries exact.
    monkeypatch.setattr(service, "_ensure_worker", lambda: None)
    result = SimpleNamespace(
        storage=storage,
        store=store,
        service=service,
        source=source,
        target=target,
        destination=DestinationRef(node_id=storage.node_id, location_id=target.location_id),
    )
    yield result
    service.shutdown()
    storage.close()


def add_content(migration, payload=b"reviewed payload"):
    artifact = migration.storage.put_bytes("snapshot", payload)
    return artifact, migration.storage.resolve(artifact.artifact_id)


def preview(migration, *, move=False):
    return migration.service.preview(
        migration.source.location_id, migration.destination, remove_source=move
    )


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Isolated worker did not reach the expected state")


def test_preview_does_not_copy_move_or_expose_private_manifest(migration):
    artifact, source = add_content(migration)
    before = {
        str(p): p.read_bytes()
        for root in (migration.source.path, migration.target.path)
        for p in Path(root).rglob("*")
        if p.is_file()
    }
    plan = preview(migration)
    after = {
        str(p): p.read_bytes()
        for root in (migration.source.path, migration.target.path)
        for p in Path(root).rglob("*")
        if p.is_file()
    }
    assert before == after and source.exists()
    assert plan["can_start"] and plan["item_count"] == 1
    assert plan["items"][0]["artifact_id"] == artifact.artifact_id
    assert not {"manifest", "source_identity", "target_identity"} & plan.keys()
    assert migration.service.list() == []


def test_default_destination_resolved_and_frozen_in_preview(migration):
    add_content(migration)
    migration.storage.locations.update(migration.target.location_id, make_default=True)
    plan = migration.service.preview(
        migration.source.location_id, DestinationRef(node_id=migration.storage.node_id)
    )
    assert plan["destination"]["location_id"] == migration.target.location_id
    migration.storage.locations.update(migration.source.location_id, make_default=True)
    migration.service.start(plan["preview_id"])
    migration.service._run()
    assert migration.service.get(plan["preview_id"])["state"] == "completed"


@pytest.mark.parametrize("move", [False, True])
def test_reviewed_copy_and_move_keep_uuid_and_commit_verified_destination(migration, move):
    artifact, source = add_content(migration)
    migration.storage.catalog.alias("media", "12", "file", artifact.artifact_id)
    plan = preview(migration, move=move)
    first = migration.service.start(plan["preview_id"])
    assert migration.service.start(plan["preview_id"])["migration_id"] == first["migration_id"]
    migration.service._run()
    job = migration.service.get(first["migration_id"])
    saved = migration.storage.catalog.get(artifact.artifact_id)
    assert job["state"] == "completed" and job["completed_items"] == 1
    assert job["bytes_done"] == len(b"reviewed payload")
    assert saved.artifact_id == artifact.artifact_id
    assert saved.location_id == migration.target.location_id
    assert migration.storage.resolve(saved.artifact_id).read_bytes() == b"reviewed payload"
    assert source.exists() is not move
    assert (
        migration.storage.catalog.resolve_alias("media", "12", "file").artifact_id
        == saved.artifact_id
    )


@pytest.mark.parametrize("change", ["bytes", "missing", "expired", "target_mount", "source_mount"])
def test_stale_or_expired_preview_never_enqueues(migration, change):
    _, source = add_content(migration)
    plan = preview(migration, move=True)
    if change == "bytes":
        source.write_bytes(b"replacement bytes")
    elif change == "missing":
        source.unlink()
    elif change == "expired":
        private = migration.service._load(plan["preview_id"], preview=True)
        private["expires_at"] = 0
        migration.service._save(private, preview=True)
    else:
        root = Path(migration.target.path if change == "target_mount" else migration.source.path)
        root.rename(root.with_name(root.name + "-unmounted"))
    with pytest.raises(StorageError):
        migration.service.start(plan["preview_id"])
    assert migration.service.list() == []


def test_restart_preserves_private_reviewed_plan_and_pauses_queued_work(migration, monkeypatch):
    _, source = add_content(migration)
    plan = preview(migration, move=True)
    migration.service.start(plan["preview_id"])
    restarted = MigrationService(migration.storage, migration.store)
    monkeypatch.setattr(restarted, "_ensure_worker", lambda: None)
    private = restarted._load(plan["preview_id"])
    assert private["manifest"][0]["path"] == str(source)
    assert private["source_identity"]["location_id"] == migration.source.location_id
    assert private["target_identity"]["location_id"] == migration.target.location_id
    assert restarted.get(plan["preview_id"])["state"] == "paused"
    assert source.exists()
    restarted.resume(plan["preview_id"])
    restarted._run()
    assert restarted.get(plan["preview_id"])["state"] == "completed"
    assert not source.exists()


def test_resume_reconciles_commit_and_deleted_source_before_progress_write(migration, monkeypatch):
    artifact, source = add_content(migration)
    plan = preview(migration, move=True)
    migration.service.start(plan["preview_id"])
    real_save = migration.service._save
    interrupted = False

    def save(value, **kwargs):
        nonlocal interrupted
        if value.get("completed_items") == 1 and not interrupted:
            interrupted = True
            raise OSError("simulated journal interruption")
        return real_save(value, **kwargs)

    monkeypatch.setattr(migration.service, "_save", save)
    migration.service._run()
    assert not source.exists()
    assert (
        migration.storage.catalog.get(artifact.artifact_id).location_id
        == migration.target.location_id
    )
    assert migration.service.get(plan["preview_id"])["state"] == "failed"
    restarted = MigrationService(migration.storage, migration.store)
    monkeypatch.setattr(restarted, "_ensure_worker", lambda: None)
    restarted.resume(plan["preview_id"])
    restarted._run()
    assert restarted.get(plan["preview_id"])["state"] == "completed"
    assert restarted.get(plan["preview_id"])["completed_items"] == 1


def test_cancel_during_transfer_preserved_and_resume_waits_for_worker(migration, monkeypatch):
    add_content(migration, b"first")
    add_content(migration, b"second")
    plan = preview(migration, move=True)
    entered, finish = threading.Event(), threading.Event()
    real_transfer = migration.storage.transfer_artifact

    def transfer(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return real_transfer(*args, **kwargs)

    monkeypatch.setattr(migration.storage, "transfer_artifact", transfer)
    migration.service.start(plan["preview_id"])
    worker = threading.Thread(target=migration.service._run)
    worker.start()
    try:
        assert entered.wait(5)
        cancelled = migration.service.cancel(plan["preview_id"])
        assert cancelled["state"] == "cancelled" and not cancelled["can_resume"]
        with pytest.raises(StorageError, match="active work"):
            migration.service.resume(plan["preview_id"])
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    stopped = migration.service.get(plan["preview_id"])
    assert stopped["state"] == "cancelled" and stopped["completed_items"] == 1
    assert stopped["can_resume"]
    migration.service.resume(plan["preview_id"])
    migration.service._run()
    assert migration.service.get(plan["preview_id"])["completed_items"] == 2


def test_enqueue_racing_worker_retirement_starts_another_worker(migration, monkeypatch):
    add_content(migration)
    plan = preview(migration)
    idle, retire = threading.Event(), threading.Event()
    real_jobs = migration.service._jobs
    first = True

    def jobs():
        nonlocal first
        if first and threading.current_thread().name == "storage-migration":
            first = False
            idle.set()
            assert retire.wait(5)
            return []
        return real_jobs()

    monkeypatch.setattr(migration.service, "_jobs", jobs)
    monkeypatch.setattr(
        migration.service,
        "_ensure_worker",
        MigrationService._ensure_worker.__get__(migration.service),
    )
    migration.service._ensure_worker()
    assert idle.wait(5)
    submit = threading.Thread(target=lambda: migration.service.start(plan["preview_id"]))
    submit.start()
    retire.set()
    submit.join(5)
    wait_for(lambda: migration.service.get(plan["preview_id"])["state"] == "completed")


def test_unexpected_transfer_exception_is_durable_and_sanitized(migration, monkeypatch):
    _, source = add_content(migration)
    plan = preview(migration, move=True)
    migration.service.start(plan["preview_id"])

    def fail(*args, **kwargs):
        raise RuntimeError("https://secret:token@private.example/internal-path")

    monkeypatch.setattr(migration.storage, "transfer_artifact", fail)
    migration.service._run()
    job = migration.service.get(plan["preview_id"])
    assert job["state"] == "failed" and job["can_resume"]
    assert "token" not in json.dumps(job) and "private.example" not in json.dumps(job)
    assert source.read_bytes() == b"reviewed payload"


def timelapse_row(migration, path, state="capturing"):
    with migration.store._conn() as conn:
        return conn.execute(
            "INSERT INTO timelapses(camera_id,name,state,interval_seconds,output_fps,"
            "created_ts,start_ts,frames_dir,video_path) VALUES('cam','fixture',?,1,30,1,1,'',?)",
            (state, str(path)),
        ).lastrowid


def test_active_producer_is_excluded_even_when_already_aliased(migration):
    artifact, source = add_content(migration)
    identifier = timelapse_row(migration, source)
    migration.storage.catalog.alias("timelapse", str(identifier), "video", artifact.artifact_id)
    plan = preview(migration)
    assert plan["item_count"] == 0 and not plan["can_start"]
    assert any(x.get("reason") == "active output" for x in plan["items"])
    assert source.exists()


def test_producer_becoming_active_after_preview_blocks_start(migration):
    artifact, source = add_content(migration)
    identifier = timelapse_row(migration, source, "completed")
    migration.storage.catalog.alias("timelapse", str(identifier), "video", artifact.artifact_id)
    plan = preview(migration)
    with migration.store._conn() as conn:
        conn.execute("UPDATE timelapses SET state='encoding' WHERE id=?", (identifier,))
    with pytest.raises(StorageError):
        migration.service.start(plan["preview_id"])
    assert source.exists() and migration.service.list() == []


def test_legacy_index_uses_real_store_columns_and_does_not_move_files(migration):
    root = Path(migration.source.path)
    files = [root / f"legacy-{i}.jpg" for i in range(10)]
    for path in files:
        path.write_bytes(b"legacy")
    frames = root / "frames"
    frames.mkdir()
    (frames / "000001.jpg").write_bytes(b"frame")
    with migration.store._conn() as conn:
        conn.execute(
            "INSERT INTO media(camera_id,media_type,path,thumbnail,created_ts) "
            "VALUES('cam','snapshot',?,?,1)",
            (str(files[0]), str(files[1])),
        )
        conn.execute(
            "INSERT INTO timelapses(camera_id,name,state,interval_seconds,output_fps,"
            "created_ts,start_ts,frames_dir,video_path,smooth_path,thumb_path) "
            "VALUES('cam','fixture','completed',1,30,1,1,?,?,?,?)",
            (str(frames), str(files[2]), str(files[3]), str(files[4])),
        )
        conn.execute(
            "INSERT INTO dataset_samples(dataset_id,path,thumb,created_ts) VALUES(1,?,?,1)",
            (str(files[5]), str(files[6])),
        )
        conn.execute(
            "INSERT INTO motion_events(camera_id,start_ts,end_ts,thumb_path) VALUES('cam',1,2,?)",
            (str(files[7]),),
        )
        conn.execute(
            "INSERT INTO models(name,path,created_ts,active) VALUES('fixture',?,1,0)",
            (str(files[8]),),
        )
    plan = preview(migration)
    assert plan["item_count"] == 10
    assert all(path.exists() for path in files)
    assert {x["kind"] for x in plan["items"]} >= {
        "snapshot",
        "thumbnail",
        "timelapse_video",
        "timelapse_smooth",
        "timelapse_frame",
        "training_sample",
        "analysis_evidence",
        "model_output",
    }


@pytest.mark.parametrize("nested", [False, True])
def test_symlink_sources_are_excluded_without_touching_external_bytes(migration, tmp_path, nested):
    artifact, source = add_content(migration)
    outside = tmp_path / "outside"
    outside.mkdir()
    replacement = outside / source.name
    replacement.write_bytes(b"external private bytes")
    if nested:
        folder = Path(migration.source.path) / "link"
        folder.symlink_to(outside, target_is_directory=True)
        migration.storage.catalog.save(artifact, str(folder / source.name))
    else:
        source.unlink()
        source.symlink_to(replacement)
    plan = preview(migration, move=True)
    assert plan["item_count"] == 0 and not plan["can_start"]
    assert replacement.read_bytes() == b"external private bytes"


def test_missing_mount_preview_never_recreates_source(migration):
    add_content(migration)
    root = Path(migration.source.path)
    root.rename(root.with_name("detached-source"))
    with pytest.raises(StorageError):
        preview(migration)
    assert not root.exists()


def test_move_cleans_only_the_reviewed_source_and_retains_other_replicas(migration, tmp_path):
    artifact, original = add_content(migration)
    # Copy B -> A, then review moving A -> C. B was never approved for cleanup.
    copied = migration.storage.transfer_artifact(artifact.artifact_id, migration.destination)
    source_a = migration.storage.resolve(copied.artifact_id)
    third = migration.storage.register_location(
        str(tmp_path / "third"), create=True, make_default=False
    )
    plan = migration.service.preview(
        migration.target.location_id,
        DestinationRef(node_id=migration.storage.node_id, location_id=third.location_id),
        remove_source=True,
    )
    migration.service.start(plan["preview_id"])
    migration.service._run()
    assert migration.service.get(plan["preview_id"])["state"] == "completed"
    assert not source_a.exists()
    assert original.read_bytes() == b"reviewed payload"
    assert migration.storage.resolve(artifact.artifact_id).read_bytes() == b"reviewed payload"


def test_shutdown_between_files_pauses_without_claiming_completion(migration, monkeypatch):
    add_content(migration, b"first")
    add_content(migration, b"second")
    plan = preview(migration, move=True)
    real_transfer = migration.storage.transfer_artifact

    def stop_after_transfer(*args, **kwargs):
        result = real_transfer(*args, **kwargs)
        migration.service._stop.set()
        return result

    monkeypatch.setattr(migration.storage, "transfer_artifact", stop_after_transfer)
    migration.service.start(plan["preview_id"])
    migration.service._run()
    job = migration.service.get(plan["preview_id"])
    assert job["state"] == "paused" and job["completed_items"] == 1
    assert job["can_resume"]


def test_changed_owner_after_preview_requires_a_new_plan(migration):
    artifact, source = add_content(migration)
    plan = preview(migration, move=True)
    # A different operation moved ownership after this preview was reviewed.
    migration.storage.transfer_artifact(artifact.artifact_id, migration.destination)
    with pytest.raises(StorageError):
        migration.service.start(plan["preview_id"])
    assert source.exists()


def test_unverified_destination_result_never_marks_migration_complete(migration, monkeypatch):
    artifact, source = add_content(migration)
    plan = preview(migration, move=True)
    migration.service.start(plan["preview_id"])
    monkeypatch.setattr(migration.storage, "transfer_artifact", lambda *args, **kwargs: artifact)
    migration.service._run()
    assert migration.service.get(plan["preview_id"])["state"] == "failed"
    assert migration.service.get(plan["preview_id"])["completed_items"] == 0
    assert source.exists()
