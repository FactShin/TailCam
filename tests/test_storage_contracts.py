"""Storage policy, catalog and physical admission without cameras or network services."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tailcam.config import AppConfig
from tailcam.persistence.store import Store
from tailcam.storage.models import (
    CONTENT_KINDS,
    Artifact,
    DestinationRef,
    StorageError,
    StoragePolicy,
)
from tailcam.storage.policy import destination_for
from tailcam.storage.service import StorageService


@pytest.fixture
def storage(tmp_path):
    config = AppConfig()
    store = Store(tmp_path / "state.db")
    service = StorageService(config, store, store.get_node_id())
    service.register_location(str(tmp_path / "media"), create=True)
    return service


def test_explicit_policy_enable_revision_and_restart(storage):
    assert not storage.enabled
    initial = storage.get_policy()
    selected = storage.set_policy(initial, expected_revision=1)
    assert selected.revision == 2 and storage.enabled
    with pytest.raises(StorageError, match="changed"):
        storage.set_policy(initial, expected_revision=1)
    restarted = StorageService(AppConfig(), Store(storage.store.db_path), storage.node_id)
    assert restarted.enabled and restarted.get_policy() == selected


def test_policy_specificity_and_origin_camera_identity():
    origin, other = str(uuid4()), str(uuid4())
    destinations = [DestinationRef(node_id=str(uuid4())) for _ in range(4)]
    policy = StoragePolicy(
        default_destination=destinations[0],
        overrides=[
            {"content_kind": "snapshot", "destination": destinations[1]},
            {"origin_node_id": origin, "camera_id": "cam", "destination": destinations[2]},
            {
                "origin_node_id": origin,
                "camera_id": "cam",
                "content_kind": "snapshot",
                "destination": destinations[3],
            },
        ],
    )
    assert destination_for(policy, "snapshot", origin, "cam") == destinations[3]
    assert destination_for(policy, "recording", origin, "cam") == destinations[2]
    assert destination_for(policy, "snapshot", other, "cam") == destinations[1]
    assert destination_for(policy, "recording", other, "cam") == destinations[0]


@pytest.mark.parametrize(
    "update",
    [
        {"outage_policy": "secondary"},
        {"outage_policy": "local_spool"},
        {"outage_policy": "local_spool", "spool_max_bytes": 10, "zero_local_media": True},
        {"spool_max_bytes": -1},
        {"workspace_max_bytes": -1},
        {"artifact_max_bytes": 0},
        {"overrides": [{"camera_id": "cam", "destination": {"node_id": str(uuid4())}}]},
    ],
)
def test_invalid_policies_rejected(update):
    with pytest.raises(ValidationError):
        StoragePolicy(default_destination=DestinationRef(node_id=str(uuid4())), **update)


@pytest.mark.parametrize("kind", CONTENT_KINDS)
def test_every_content_kind_is_committed_and_addressable(storage, kind):
    artifact = storage.put_bytes(kind, b"payload", camera_id="cam")
    assert artifact.kind == kind
    assert artifact.state == "committed"
    assert artifact.owner_node_id == storage.node_id
    assert artifact.sha256 == hashlib.sha256(b"payload").hexdigest()
    assert storage.resolve(artifact.artifact_id).read_bytes() == b"payload"
    assert len(storage.catalog.list(kind=kind, camera_id="cam")) == 1


def test_location_default_changes_preserve_existing_bytes_and_frozen_plan(storage, tmp_path):
    first = storage.locations.get()
    plan = storage.admit("snapshot")
    artifact = storage.put_bytes("snapshot", b"old")
    second = storage.register_location(str(tmp_path / "second"), create=True)
    assert first.location_id != second.location_id
    assert storage.resolve(artifact.artifact_id).read_bytes() == b"old"
    frozen = storage.put_bytes("snapshot", b"frozen", admission=plan)
    assert frozen.location_id == first.location_id
    assert storage.put_bytes("snapshot", b"new").location_id == second.location_id


def test_missing_root_cannot_be_recreated_by_status_admission_or_registration(storage):
    root = Path(storage.locations.get().path)
    moved = root.with_name("unmounted")
    root.rename(moved)
    assert storage.locations.describe().state == "missing"
    for action in (
        lambda: storage.admit("snapshot"),
        lambda: storage.register_location(str(root), create=True),
    ):
        with pytest.raises(StorageError):
            action()
        assert not root.exists()


def test_replacement_root_and_copied_marker_still_rejected(storage):
    root = Path(storage.locations.get().path)
    moved = root.with_name("original-drive")
    root.rename(moved)
    root.mkdir()
    marker = ".tailcam-storage-location"
    (root / marker).write_bytes((moved / marker).read_bytes())
    assert storage.locations.describe().state == "changed"
    with pytest.raises(StorageError):
        storage.put_bytes("snapshot", b"must not land on boot disk")
    assert list(root.iterdir()) == [root / marker]


def test_quota_and_reserve_are_write_admission(storage, monkeypatch):
    location = storage.locations.get()
    storage.update_location(location.location_id, quota_bytes=5)
    storage.put_bytes("snapshot", b"12345")
    with pytest.raises(StorageError) as failure:
        storage.put_bytes("snapshot", b"x")
    assert failure.value.code == "quota_exceeded"
    with pytest.raises(StorageError):
        storage.update_location(location.location_id, quota_bytes=4)
    from tailcam.storage import locations

    monkeypatch.setattr(locations.shutil, "disk_usage", lambda _: SimpleNamespace(free=10))
    storage.update_location(location.location_id, quota_bytes=0, reserve_bytes=8)
    with pytest.raises(StorageError):
        storage.put_bytes("snapshot", b"123")


def test_concurrent_quota_reservations_do_not_overcommit(storage):
    location = storage.locations.get()
    storage.update_location(location.location_id, quota_bytes=10)

    def reserve(_):
        try:
            storage.locations.reserve(str(uuid4()), location.location_id, 6, "test")
            return True
        except StorageError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(4)))
    assert results.count(True) == 1
    assert storage.locations.describe().reserved_bytes == 6


def test_workspace_budget_restart_and_zero_local_preflight(storage):
    policy = storage.get_policy()
    policy.workspace_max_bytes = 10
    storage.set_policy(policy)
    lease = storage.workspace("model_output", max_bytes=6)
    (lease.path / "weights.pt").write_bytes(b"123456")
    assert lease.check() == 6
    restarted = StorageService(storage.config, Store(storage.store.db_path), storage.node_id)
    with pytest.raises(StorageError):
        restarted.workspace("export", max_bytes=6)
    lease.release()
    policy.zero_local_media = True
    policy.default_destination = DestinationRef(node_id=str(uuid4()))
    storage.set_policy(policy)
    before = set(storage.store.db_path.parent.rglob("*"))
    with pytest.raises(StorageError):
        storage.workspace("model_output", max_bytes=1)
    assert set(storage.store.db_path.parent.rglob("*")) == before


def test_workspace_overflow_and_failed_context_retains_recovery_state(storage):
    lease = storage.workspace("model_output", max_bytes=2)
    with pytest.raises(StorageError):
        with lease:
            (lease.path / "weights").write_bytes(b"123")
            lease.check()
    assert lease.path.exists()
    assert storage.locations.describe(lease.location_id).reserved_bytes == 2
    lease.release()


def test_restarted_workspace_cleanup_releases_only_recorded_reservation(storage, tmp_path):
    policy = storage.get_policy()
    policy.workspace_max_bytes = 8
    storage.set_policy(policy)
    lease = storage.workspace("timelapse_frame", max_bytes=8)
    (lease.path / "frame.jpg").write_bytes(b"frame")
    other = tmp_path / "unowned"
    other.mkdir()
    (other / "keep").write_text("keep")
    restarted = StorageService(storage.config, Store(storage.store.db_path), storage.node_id)
    assert not restarted.release_workspace(other)
    assert (other / "keep").read_text() == "keep"
    assert restarted.release_workspace(lease.path)
    assert not lease.path.exists()
    assert restarted.locations.describe(lease.location_id).reserved_bytes == 0
    assert not restarted.release_workspace(lease.path)
    with restarted.workspace("timelapse_frame", max_bytes=8):
        pass


def test_workspace_cleanup_failure_preserves_reservation(storage, monkeypatch):
    from tailcam.storage import service

    lease = storage.workspace("timelapse_frame", max_bytes=8)
    (lease.path / "frame.jpg").write_bytes(b"frame")

    def fail_cleanup(path):
        raise PermissionError("private filesystem path")

    with monkeypatch.context() as patch:
        patch.setattr(service.shutil, "rmtree", fail_cleanup)
        with pytest.raises(StorageError) as failure:
            storage.release_workspace(lease.path)
    assert failure.value.code == "workspace_cleanup_failed"
    assert "private" not in str(failure.value)
    assert storage.locations.describe(lease.location_id).reserved_bytes == 8
    assert (lease.path / "frame.jpg").read_bytes() == b"frame"
    assert storage.release_workspace(lease.path)


def test_workspace_cleanup_rejects_replacement_mount(storage):
    lease = storage.workspace("timelapse_frame", max_bytes=8)
    root = lease.path.parent
    original = root.with_name("original-workspace")
    root.rename(original)
    root.mkdir()
    (root / ".tailcam-storage-location").write_bytes(
        (original / ".tailcam-storage-location").read_bytes()
    )
    replacement = root / lease.path.name
    replacement.mkdir()
    (replacement / "keep").write_text("unrelated")
    with pytest.raises(StorageError):
        storage.release_workspace(lease.path)
    assert storage.locations.describe(lease.location_id).reserved_bytes == 8
    assert (replacement / "keep").read_text() == "unrelated"


def test_sibling_admissions_share_explicit_policy_snapshot(storage):
    original = storage.get_policy()
    original.workspace_max_bytes = 100
    original.retention.max_age_seconds = 10
    snapshot = storage.set_policy(original).model_copy(deep=True)
    first = storage.admit("training_sample", policy_snapshot=snapshot)
    changed = storage.get_policy()
    changed.workspace_max_bytes = 200
    changed.retention.max_age_seconds = 20
    storage.set_policy(changed)
    second = storage.admit("annotation", policy_snapshot=snapshot)
    assert first.policy_revision == second.policy_revision == snapshot.revision
    assert first.workspace_max_bytes == second.workspace_max_bytes == 100
    assert first.retention.max_age_seconds == second.retention.max_age_seconds == 10
    snapshot.retention.max_age_seconds = 30
    assert second.retention.max_age_seconds == 10
    assert storage.admit("annotation").workspace_max_bytes == 200


def test_legacy_aliases_and_migration_to_new_root(storage, tmp_path):
    legacy = Path(storage.locations.get().path) / "old.jpg"
    legacy.write_bytes(b"older picture")
    artifact = storage.adopt_existing(legacy, "snapshot", namespace="media", legacy_id="1")
    assert storage.adopt_existing(legacy, "snapshot", namespace="media", legacy_id="1") == artifact
    other = storage.register_location(str(tmp_path / "second"), create=True)
    moved = storage.transfer_artifact(
        artifact.artifact_id,
        DestinationRef(node_id=storage.node_id, location_id=other.location_id),
        remove_source=True,
    )
    assert not legacy.exists()
    assert storage.resolve(moved.artifact_id).read_bytes() == b"older picture"
    assert storage.catalog.resolve_alias("media", "1").artifact_id == artifact.artifact_id


def test_delete_preserves_catalog_if_drive_missing_and_filters_tombstones(storage):
    artifact = storage.put_bytes("snapshot", b"one")
    root = Path(storage.locations.get().path)
    moved = root.with_name("gone")
    root.rename(moved)
    with pytest.raises(StorageError):
        storage.delete(artifact.artifact_id)
    assert storage.catalog.get(artifact.artifact_id).state == "committed"
    moved.rename(root)
    assert storage.delete(artifact.artifact_id)
    assert storage.catalog.list(kind="snapshot") == []
    assert len(storage.catalog.list(state="deleted")) == 1


def test_foreign_catalog_no_feedback_and_conflicting_owner_refused(storage):
    owner, wrong = str(uuid4()), str(uuid4())
    now = 100.0
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=owner,
        origin_node_id=owner,
        kind="snapshot",
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
        created_at=now,
        updated_at=now,
        requested_destination=DestinationRef(node_id=owner),
        policy_revision=1,
    )
    storage.catalog.import_index(owner, [artifact])
    storage.catalog.mark_owner_offline(owner)
    assert storage.catalog.changes()["artifacts"] == []
    assert storage.catalog.get(artifact.artifact_id).owner_online is False
    hijack = artifact.model_copy(update={"owner_node_id": wrong, "updated_at": 101.0})
    with pytest.raises(StorageError):
        storage.catalog.import_index(wrong, [hijack])
    assert storage.catalog.get(artifact.artifact_id).owner_node_id == owner
    storage.catalog.mark_owner_online(owner)
    assert storage.catalog.get(artifact.artifact_id).owner_online is True
    assert storage.catalog.changes()["artifacts"] == []


def test_mime_header_injection_rejected(storage):
    with pytest.raises(ValidationError):
        storage.put_bytes("snapshot", b"x", mime_type="image/jpeg\r\nX-Injection: yes")


def test_frozen_admission_keeps_retention_and_workspace_limit(storage):
    policy = storage.get_policy()
    policy.retention.enabled = True
    policy.retention.max_age_seconds = 60
    storage.set_policy(policy)
    plan = storage.admit("model_output", requires_workspace=True)
    policy.retention.enabled = False
    policy.workspace_max_bytes = 0
    storage.set_policy(policy)
    lease = storage.workspace("model_output", max_bytes=10, admission=plan)
    (lease.path / "weight.pt").write_bytes(b"weights")
    artifact = storage.finalize(lease.path / "weight.pt", "model_output", admission=plan)
    assert artifact.retention.enabled and artifact.retention.max_age_seconds == 60
    lease.release()


def test_artifact_cache_does_not_require_new_output_destination(storage):
    artifact = storage.put_bytes("model_output", b"weights")
    policy = storage.get_policy()
    policy.default_destination = DestinationRef(node_id=str(uuid4()))
    storage.set_policy(policy)
    with storage.workspace_for_artifact(artifact.artifact_id, max_bytes=7) as lease:
        materialized = storage.materialize(artifact.artifact_id, lease)
        assert materialized.read_bytes() == b"weights"
    assert not lease.path.exists()


def test_retention_skips_protected_and_linked_parents(storage):
    policy = storage.get_policy()
    policy.retention.enabled = True
    policy.retention.max_age_seconds = 1
    storage.set_policy(policy)
    parent = storage.put_bytes("timelapse_video", b"video")
    child = storage.put_bytes("thumbnail", b"thumb", parent_id=parent.artifact_id)
    policy.retention.protect = True
    storage.set_policy(policy)
    protected = storage.put_bytes("training_sample", b"sample")
    future = max(parent.created_at, child.created_at, protected.created_at) + 2
    assert storage.prune(now=future) == 1  # children expire before their parent
    assert storage.catalog.get(child.artifact_id).state == "deleted"
    assert storage.catalog.get(parent.artifact_id).state == "committed"
    assert storage.prune(now=future) == 1
    assert storage.catalog.get(protected.artifact_id).state == "committed"


def test_background_recovery_worker_is_explicit_singleton_and_stoppable(storage, monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(storage, "retry_pending", lambda **kwargs: called.set())
    assert storage._worker is None
    storage.get_policy()
    storage.list_locations()
    assert storage._worker is None
    storage.start(retry_interval=0.01)
    first = storage._worker
    storage.start(retry_interval=0.01)
    assert storage._worker is first and called.wait(1)
    storage.close()
    assert not first.is_alive()


def test_existing_artifact_retention_can_be_changed_without_rewriting_bytes(storage):
    artifact = storage.put_bytes("snapshot", b"original")
    path = storage.resolve(artifact.artifact_id)
    original_stat = path.stat()
    updated = storage.set_retention(artifact.artifact_id, {"protect": True})
    assert updated.retention.protect
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    with pytest.raises(StorageError):
        storage.delete(artifact.artifact_id)
    storage.set_retention(artifact.artifact_id, {"protect": False})
    assert storage.delete(artifact.artifact_id)
