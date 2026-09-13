"""Durable job holds exclude deletion/migration and expire without a live coordinator."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest

from tailcam.config import AppConfig
from tailcam.jobs.models import (
    ArtifactRef,
    JobError,
    JobSpec,
    OutputSlot,
    PreparedOutput,
    ResultManifest,
    SafeError,
    StageSpec,
)
from tailcam.jobs.service import JobService
from tailcam.jobs.transport import JobTransport
from tailcam.node import RoleDisabledError
from tailcam.persistence.store import Store
from tailcam.storage.models import ArtifactPin, DestinationRef, RetentionPolicy, StorageError
from tailcam.storage.service import StorageService


@pytest.fixture
def owner(tmp_path):
    store = Store(tmp_path / "state.db")
    storage = StorageService(AppConfig(), store, store.get_node_id())
    storage.register_location(str(tmp_path / "media"), create=True)
    yield storage
    storage.close()


def hold(storage, artifact, **updates):
    values = dict(
        pin_id=str(uuid4()),
        coordinator_node_id=str(uuid4()),
        expires_at=time.time() + 60,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
    )
    values.update(updates)
    return ArtifactPin(**values)


def test_pin_survives_restart_and_preserves_content_until_its_expiry(owner, monkeypatch):
    artifact = owner.put_bytes("snapshot", b"picture")
    owner.set_retention(artifact.artifact_id, RetentionPolicy(enabled=True, max_age_seconds=1))
    pin = hold(owner, artifact)
    assert owner.pin_artifact(artifact.artifact_id, pin) == pin
    restarted = StorageService(owner.config, Store(owner.store.db_path), owner.node_id)
    assert restarted.pin_artifact(artifact.artifact_id, pin) == pin
    with pytest.raises(StorageError) as failure:
        restarted.delete(artifact.artifact_id)
    assert failure.value.code == "artifact_in_use"
    assert restarted.prune(now=time.time() + 1000) == 0
    other = owner.register_location(str(owner.store.db_path.parent / "other"), create=True)
    with pytest.raises(StorageError) as failure:
        restarted.transfer_artifact(
            artifact.artifact_id,
            DestinationRef(node_id=owner.node_id, location_id=other.location_id),
        )
    assert failure.value.code == "artifact_in_use"
    monkeypatch.setattr("tailcam.storage.pins.time.time", lambda: pin.expires_at + 1)
    assert restarted.prune() == 1


def test_pin_digest_identity_expiry_and_coordinator_cannot_be_overridden(owner):
    artifact = owner.put_bytes("snapshot", b"picture")
    pin = hold(owner, artifact)
    for change in (
        {"sha256": "a" * 64},
        {"size_bytes": 1},
        {"expires_at": time.time() - 1},
        {"expires_at": time.time() + 604900},
    ):
        with pytest.raises(StorageError):
            owner.pin_artifact(artifact.artifact_id, pin.model_copy(update=change))
    owner.pin_artifact(artifact.artifact_id, pin)
    with pytest.raises(StorageError) as failure:
        owner.release_artifact_pin(
            artifact.artifact_id, pin.pin_id, coordinator_node_id=str(uuid4())
        )
    assert failure.value.code == "pin_owner_mismatch"
    with pytest.raises(StorageError):
        owner.pin_artifact(
            artifact.artifact_id, pin.model_copy(update={"coordinator_node_id": str(uuid4())})
        )
    assert owner.release_artifact_pin(
        artifact.artifact_id, pin.pin_id, coordinator_node_id=pin.coordinator_node_id
    )
    assert owner.delete(artifact.artifact_id)


def test_pin_and_delete_are_serialized_across_service_instances(owner):
    artifact = owner.put_bytes("snapshot", b"picture")
    other = StorageService(owner.config, Store(owner.store.db_path), owner.node_id)
    outcomes = []
    with owner.pins.guard(artifact.artifact_id):

        def attempt():
            for action in (
                lambda: other.pin_artifact(artifact.artifact_id, hold(owner, artifact)),
                lambda: other.delete(artifact.artifact_id),
            ):
                try:
                    action()
                except StorageError as exc:
                    outcomes.append(exc.code)

        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        assert outcomes == ["artifact_busy", "artifact_busy"]
    assert owner.delete(artifact.artifact_id)


def test_process_death_releases_artifact_mutation_lock(owner):
    artifact = owner.put_bytes("snapshot", b"picture")
    script = """
import sys,time
from tailcam.persistence.store import Store
from tailcam.storage.catalog import ArtifactCatalog
from tailcam.storage.pins import ArtifactPins
from pathlib import Path
store=Store(Path(sys.argv[1]))
pins=ArtifactPins(ArtifactCatalog(store,sys.argv[2]))
with pins.guard(sys.argv[3]):
 Path(sys.argv[4]).write_text('locked')
 time.sleep(30)
"""
    ready = owner.store.db_path.parent / "lock-ready"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(owner.store.db_path),
            owner.node_id,
            artifact.artifact_id,
            str(ready),
        ],
        stdout=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(StorageError) as failure:
            owner.pin_artifact(artifact.artifact_id, hold(owner, artifact))
        assert failure.value.code == "artifact_busy"
    finally:
        child.kill()
        child.wait(timeout=5)
    owner.pin_artifact(artifact.artifact_id, hold(owner, artifact))


def test_job_holds_input_and_intermediate_output_until_terminal(owner):
    original = owner.put_bytes("snapshot", b"picture")
    jobs = JobService(owner.config, owner.store, owner.node_id, owner)
    record = jobs.submit(
        JobSpec(
            task="training",
            origin_node_id=owner.node_id,
            stages=[
                StageSpec(
                    stage_id="prepare",
                    task="labeling",
                    input_artifacts=[ArtifactRef.from_artifact(original)],
                    outputs=[OutputSlot(slot="prepared", kind="export")],
                ),
                StageSpec(stage_id="train", task="training", depends_on=["prepare"]),
            ],
        ),
        idempotency_key="pipeline",
    )
    with pytest.raises(StorageError):
        owner.delete(original.artifact_id)
    lease = jobs.claim_local()
    output = owner.put_bytes(
        "export",
        b"dataset",
        artifact_id=lease.spec.outputs[0].artifact_id,
        admission=lease.spec.outputs[0].admission,
    )
    permit = jobs.prepare_result(
        lease,
        ResultManifest(
            outputs=[
                PreparedOutput(
                    slot="prepared",
                    path="dataset.zip",
                    size_bytes=output.size_bytes,
                    sha256=output.sha256,
                )
            ]
        ),
    )
    jobs.commit_result(permit, [ArtifactRef.from_artifact(output, "prepared")])
    with pytest.raises(StorageError):
        owner.delete(output.artifact_id)
    jobs.request_cancel(record.job_id)
    assert jobs.release_terminal_pins(limit=100) == 2
    assert owner.delete(original.artifact_id)
    assert owner.delete(output.artifact_id)


def test_default_legacy_output_root_is_lazy_and_never_recreates_external_mount(tmp_path):
    config, store = AppConfig(), Store(tmp_path / "state.db")
    storage = StorageService(config, store, store.get_node_id())
    assert storage.list_locations() == []
    storage.admit("analysis_evidence")
    assert not storage.enabled
    assert storage.locations.get().path == str(tmp_path / "media")
    configured = AppConfig()
    configured.storage.media_dir = str(tmp_path / "missing-drive" / "media")
    external_store = Store(tmp_path / "external" / "state.db")
    external = StorageService(configured, external_store, external_store.get_node_id())
    with pytest.raises(StorageError):
        external.admit("analysis_evidence")
    assert not (tmp_path / "missing-drive").exists()
    configured.node.roles = ["analysis"]
    isolated_store = Store(tmp_path / "analysis" / "state.db")
    isolated = StorageService(configured, isolated_store, isolated_store.get_node_id())
    with pytest.raises(RoleDisabledError):
        isolated.admit("analysis_evidence")


def test_remote_coordinators_keep_independent_holds_and_recover_release(owner, tmp_path):
    artifact = owner.put_bytes("snapshot", b"frame")
    ref, job_id, deadline = ArtifactRef.from_artifact(artifact), str(uuid4()), time.time() + 60
    coordinators = []
    for name in ("source", "worker"):
        store = Store(tmp_path / name / "state.db")
        storage = StorageService(AppConfig(), store, store.get_node_id())
        jobs = JobService(storage.config, store, storage.node_id, storage)

        def handler(request, jobs=jobs):
            assert not jobs.journal.connection.in_transaction
            assert not owner.catalog.connection.in_transaction
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json=owner.pin_artifact(
                        artifact.artifact_id, json.loads(request.content)
                    ).model_dump(mode="json"),
                )
            released = owner.release_artifact_pin(
                artifact.artifact_id,
                request.url.path.rsplit("/", 1)[1],
                coordinator_node_id=request.url.params["coordinator_node_id"],
            )
            return httpx.Response(200, json={"released": released})

        jobs.transport = JobTransport(
            lambda _: "http://approved.invalid",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        jobs._pin_references(job_id, [ref], deadline)
        coordinators.append(jobs)
    assert (
        owner.catalog.connection.execute("SELECT count(*) FROM storage_artifact_pins").fetchone()[0]
        == 2
    )
    first = coordinators[0]
    restarted = JobService(
        first.config,
        Store(first.journal.store.db_path),
        first.node_id,
        first.storage,
        transport=first.transport,
    )
    assert restarted._release_pins(job_id) == 1
    with pytest.raises(StorageError):
        owner.delete(artifact.artifact_id)
    assert coordinators[1]._release_pins(job_id) == 1
    assert owner.delete(artifact.artifact_id)


def test_failed_admission_releases_only_its_own_input_hold(owner):
    artifact = owner.put_bytes("snapshot", b"frame")
    jobs = JobService(owner.config, owner.store, owner.node_id, owner)
    spec = JobSpec(
        task="training",
        origin_node_id=owner.node_id,
        input_artifacts=[ArtifactRef.from_artifact(artifact)],
    )
    accepted = jobs.submit(spec, idempotency_key="winner")
    with pytest.raises((JobError, StorageError)):
        jobs.submit(spec, idempotency_key="loser")
    with pytest.raises(StorageError):
        owner.delete(artifact.artifact_id)
    jobs.request_cancel(accepted.job_id)
    jobs.release_terminal_pins(limit=100)
    jobs.config.jobs.max_queued = 1
    jobs.submit(JobSpec(task="training", origin_node_id=owner.node_id), idempotency_key="fill")
    with pytest.raises((JobError, StorageError)):
        jobs.submit(
            JobSpec(
                task="training",
                origin_node_id=owner.node_id,
                input_artifacts=[ArtifactRef.from_artifact(artifact)],
            ),
            idempotency_key="overflow",
        )
    assert owner.delete(artifact.artifact_id)


def test_receiver_commit_cannot_move_an_artifact_while_pinned(owner):
    import hashlib

    from tailcam.storage.models import TransferManifest

    artifact = owner.put_bytes("snapshot", b"frame")
    pin = hold(owner, artifact)
    owner.pin_artifact(artifact.artifact_id, pin)
    destination = owner.register_location(
        str(owner.store.db_path.parent / "destination"), create=True
    )
    transfer = owner.transfers.begin(
        TransferManifest(
            artifact=artifact,
            destination=DestinationRef(node_id=owner.node_id, location_id=destination.location_id),
            idempotency_key="move",
        )
    )
    owner.transfers.append(transfer.transfer_id, 0, b"frame", hashlib.sha256(b"frame").hexdigest())
    with pytest.raises(StorageError) as failure:
        owner.transfers.commit(transfer.transfer_id)
    assert failure.value.code == "artifact_in_use"
    assert owner.catalog.get(artifact.artifact_id).location_id == artifact.location_id


def test_manual_retry_reacquires_inputs_before_queue_and_excludes_cleanup(owner, monkeypatch):
    artifact = owner.put_bytes("snapshot", b"frame")
    jobs = JobService(owner.config, owner.store, owner.node_id, owner)
    record = jobs.submit(
        JobSpec(
            task="training",
            origin_node_id=owner.node_id,
            input_artifacts=[ArtifactRef.from_artifact(artifact)],
        ),
        idempotency_key="retry",
    )
    lease = jobs.claim_local()
    jobs.fail(lease, SafeError(code="engine_error", detail="Try another attempt"))
    assert jobs.release_terminal_pins(limit=100) == 1
    original = jobs._pin_references
    failures = []

    def inspect(*args, **kwargs):
        original(*args, **kwargs)

        def cleanup():
            assert jobs.release_terminal_pins(limit=100) == 0
            try:
                owner.delete(artifact.artifact_id)
            except StorageError as exc:
                failures.append(exc.code)

        thread = threading.Thread(target=cleanup)
        thread.start()
        thread.join(3)
        assert not thread.is_alive()

    monkeypatch.setattr(jobs, "_pin_references", inspect)
    assert jobs.retry(record.job_id).state == "queued"
    assert failures == ["artifact_in_use"]
    with pytest.raises(StorageError):
        owner.delete(artifact.artifact_id)
