"""Receiver durability, exact byte identities and sender/owner separation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tailcam.config import AppConfig
from tailcam.persistence.store import Store
from tailcam.storage.models import (
    MAX_CHUNK_BYTES,
    Artifact,
    DestinationRef,
    StorageError,
    TransferManifest,
)
from tailcam.storage.service import StorageService
from tailcam.storage.transfers import artifact_filename


@pytest.fixture
def nodes(tmp_path):
    result = []
    for name in ("source", "owner", "secondary"):
        root = tmp_path / name
        store = Store(root / "state.db")
        service = StorageService(AppConfig(), store, store.get_node_id())
        service.register_location(str(root / "media"), create=True)
        result.append(service)
    return result


def manifest(owner, payload, **updates):
    origin = str(uuid4())
    destination = DestinationRef(
        node_id=owner.node_id, location_id=owner.locations.get().location_id
    )
    artifact = Artifact(
        artifact_id=str(uuid4()),
        owner_node_id=owner.node_id,
        origin_node_id=origin,
        kind="snapshot",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        created_at=1,
        updated_at=1,
        requested_destination=destination,
        policy_revision=1,
        **updates,
    )
    return TransferManifest(
        artifact=artifact, destination=destination, idempotency_key=str(uuid4())
    )


def append(receiver, transfer, offset, data):
    return receiver.append(transfer.transfer_id, offset, data, hashlib.sha256(data).hexdigest())


def test_chunk_retry_restart_and_idempotent_commit(nodes):
    _, owner, _ = nodes
    declaration = manifest(owner, b"abcdef")
    transfer = owner.transfers.begin(declaration)
    assert owner.transfers.begin(declaration).transfer_id == transfer.transfer_id
    first = append(owner.transfers, transfer, 0, b"abc")
    assert first.offset == 3
    assert append(owner.transfers, transfer, 0, b"abc").offset == 3
    restarted = StorageService(AppConfig(), Store(owner.store.db_path), owner.node_id)
    assert restarted.transfers.status(transfer.transfer_id).offset == 3
    append(restarted.transfers, transfer, 3, b"def")
    committed = restarted.transfers.commit(transfer.transfer_id)
    assert restarted.transfers.commit(transfer.transfer_id) == committed
    assert restarted.resolve(committed.artifact_id).read_bytes() == b"abcdef"
    assert len(restarted.catalog.list()) == 1
    assert restarted.locations.describe().reserved_bytes == 0


def test_unacknowledged_tail_truncated_on_resume(nodes):
    owner = nodes[1]
    transfer = owner.transfers.begin(manifest(owner, b"abcdef"))
    append(owner.transfers, transfer, 0, b"abc")
    incoming = Path(owner.locations.get().path) / f".tailcam-incoming-{transfer.transfer_id}"
    with incoming.open("ab") as stream:
        stream.write(b"unacknowledged interrupted write")
    append(owner.transfers, transfer, 3, b"def")
    artifact = owner.transfers.commit(transfer.transfer_id)
    assert owner.resolve(artifact.artifact_id).read_bytes() == b"abcdef"


@pytest.mark.parametrize(
    "offset,data,code",
    [
        (1, b"x", "offset_mismatch"),
        (0, b"1234567", "offset_mismatch"),
        (-1, b"x", "invalid_chunk"),
        (0, b"", "invalid_chunk"),
        (0, b"x" * (MAX_CHUNK_BYTES + 1), "invalid_chunk"),
    ],
    ids=["wrong-offset", "past-end", "negative-offset", "empty", "over-chunk-limit"],
)
def test_invalid_chunks_never_advance_offset(nodes, offset, data, code):
    owner = nodes[1]
    transfer = owner.transfers.begin(manifest(owner, b"abcdef"))
    with pytest.raises(StorageError) as failure:
        append(owner.transfers, transfer, offset, data)
    assert failure.value.code == code
    assert owner.transfers.status(transfer.transfer_id).offset == 0


def test_checksum_errors_preserve_recoverable_transfer(nodes):
    owner = nodes[1]
    transfer = owner.transfers.begin(manifest(owner, b"right"))
    with pytest.raises(StorageError):
        owner.transfers.append(transfer.transfer_id, 0, b"wrong", "0" * 64)
    append(owner.transfers, transfer, 0, b"wrong")
    with pytest.raises(StorageError) as failure:
        owner.transfers.commit(transfer.transfer_id)
    assert failure.value.code == "checksum_mismatch"
    assert owner.catalog.list() == []
    assert owner.transfers.status(transfer.transfer_id).state == "receiving"
    owner.transfers.cancel(transfer.transfer_id)
    assert owner.locations.describe().reserved_bytes == 0


def test_manifest_identity_conflicts(nodes):
    owner = nodes[1]
    declaration = manifest(owner, b"x")
    owner.transfers.begin(declaration)
    changed = declaration.model_copy(deep=True)
    changed.artifact.sha256 = "0" * 64
    with pytest.raises(StorageError):
        owner.transfers.begin(changed)


def test_crash_after_atomic_rename_before_database_commit(nodes, monkeypatch):
    owner = nodes[1]
    declaration = manifest(owner, b"durable")
    transfer = owner.transfers.begin(declaration)
    append(owner.transfers, transfer, 0, b"durable")
    original = owner.catalog.save

    def crash(*args, **kwargs):
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(owner.catalog, "save", crash)
    with pytest.raises(RuntimeError):
        owner.transfers.commit(transfer.transfer_id)
    root = Path(owner.locations.get().path)
    assert (root / artifact_filename(declaration.artifact)).exists()
    assert owner.catalog.list() == []
    monkeypatch.setattr(owner.catalog, "save", original)
    restarted = StorageService(AppConfig(), Store(owner.store.db_path), owner.node_id)
    artifact = restarted.transfers.commit(transfer.transfer_id)
    assert restarted.resolve(artifact.artifact_id).read_bytes() == b"durable"


def test_unmounted_root_rejects_chunk_without_boot_disk_write(nodes):
    owner = nodes[1]
    transfer = owner.transfers.begin(manifest(owner, b"abc"))
    root = Path(owner.locations.get().path)
    root.rename(root.with_name("detached"))
    root.mkdir()
    with pytest.raises(StorageError):
        append(owner.transfers, transfer, 0, b"abc")
    assert list(root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptor anchoring")
def test_directory_handle_anchors_write_during_root_replacement(nodes, monkeypatch):
    owner = nodes[1]
    transfer = owner.transfers.begin(manifest(owner, b"abc"))
    from tailcam.storage import transfers

    original = transfers._open
    root = Path(owner.locations.get().path)
    detached = root.with_name("detached")

    def replace(root_path, descriptor, name, flags):
        root.rename(detached)
        root.mkdir()
        return original(root_path, descriptor, name, flags)

    monkeypatch.setattr(transfers, "_open", replace)
    append(owner.transfers, transfer, 0, b"abc")
    assert list(root.iterdir()) == []
    assert (detached / f".tailcam-incoming-{transfer.transfer_id}").read_bytes() == b"abc"


def attach_transport(source, owner, *, fail_chunk=False):
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        try:
            if path == "/api/v1/storage/locations":
                return httpx.Response(
                    200, json={"items": [x.model_dump() for x in owner.list_locations()]}
                )
            if path == "/api/v1/transfers":
                result = owner.transfers.begin(json.loads(request.content))
            elif path.endswith("/chunks"):
                if fail_chunk:
                    raise httpx.ConnectError("secret.invalid password=do-not-reflect")
                result = owner.transfers.append(
                    path.split("/")[-2],
                    int(request.url.params["offset"]),
                    request.content,
                    request.headers["X-Chunk-SHA256"],
                )
            elif path.endswith("/commit"):
                result = owner.transfers.commit(path.split("/")[-2])
            elif path.endswith("/content"):
                return httpx.Response(200, content=owner.resolve(path.split("/")[-2]).read_bytes())
            elif path.endswith("/transfer"):
                body = json.loads(request.content)
                result = owner.transfer_artifact(
                    path.split("/")[-2], body["destination"], remove_source=body["remove_source"]
                )
            else:
                result = owner.transfers.status(path.split("/")[-1])
            return httpx.Response(200, json=result.model_dump(mode="json"))
        except StorageError as exc:
            return httpx.Response(exc.status_code, json={"code": exc.code, "detail": exc.detail})

    source.resolve_peer = lambda node_id: (
        "http://approved.invalid" if node_id == owner.node_id else None
    )
    source._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return requests


def test_remote_bytes_and_catalog_live_at_selected_owner(nodes):
    source, owner, _ = nodes
    requests = attach_transport(source, owner)
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.zero_local_media = True
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"remote picture")
    assert artifact.owner_node_id == owner.node_id
    assert owner.resolve(artifact.artifact_id).read_bytes() == b"remote picture"
    assert source.catalog.get(artifact.artifact_id).owner_node_id == owner.node_id
    assert not list(Path(source.locations.get().path).glob(".tailcam-object-*"))
    assert not (source.store.db_path.parent / "storage-workspace").exists()
    assert all(request.url.host == "approved.invalid" for request in requests)


def test_destination_required_does_not_fallback_or_leak_peer_errors(nodes):
    source, owner, _ = nodes
    attach_transport(source, owner, fail_chunk=True)
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    source.set_policy(policy)
    with pytest.raises(StorageError) as failure:
        source.put_bytes("snapshot", b"bytes")
    assert "secret.invalid" not in failure.value.detail
    assert not list(Path(source.locations.get().path).glob(".tailcam-object-*"))


@pytest.mark.parametrize("encoded", [False, True])
def test_materialize_requests_identity_and_rejects_encoding_before_body(nodes, encoded):
    source, owner, _ = nodes
    artifact = owner.put_bytes("model_output", b"weights")
    source.catalog.import_index(owner.node_id, [artifact.model_dump(mode="json")])
    source.resolve_peer = lambda _: "http://approved.invalid"

    class Body(httpx.SyncByteStream):
        reads = 0

        def __iter__(self):
            self.reads += 1
            yield b"weights"

    body = Body()

    def handler(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"} if encoded else {},
            stream=body,
        )

    source._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    with source.workspace_for_artifact(artifact.artifact_id, max_bytes=100) as lease:
        if encoded:
            with pytest.raises(StorageError) as failure:
                source.materialize(artifact.artifact_id, lease)
            assert failure.value.code == "invalid_peer_response"
            assert body.reads == 0
            assert not list(lease.path.iterdir())
        else:
            assert source.materialize(artifact.artifact_id, lease).read_bytes() == b"weights"
            assert body.reads == 1


def test_bounded_spool_and_recovery_preserve_artifact_identity(nodes):
    source, owner, _ = nodes
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.spool_max_bytes = 5
    policy.outage_policy = "local_spool"
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"12345")
    assert artifact.state == "pending_transfer" and artifact.owner_node_id == source.node_id
    with pytest.raises(StorageError):
        source.put_bytes("snapshot", b"x")
    attach_transport(source, owner)
    assert source.retry_pending() == 1
    assert source.catalog.get(artifact.artifact_id).owner_node_id == owner.node_id
    assert owner.resolve(artifact.artifact_id).read_bytes() == b"12345"
    assert not list(Path(source.locations.get().path).glob(".tailcam-object-*"))
    assert source.retry_pending() == 0


def test_source_retries_same_artifact_id_without_duplicate_owner_files(nodes):
    source, owner, _ = nodes
    attach_transport(source, owner, fail_chunk=True)
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    source.set_policy(policy)
    identifier = str(uuid4())
    with pytest.raises(StorageError):
        source.put_bytes("snapshot", b"retry", artifact_id=identifier)
    first_transfer = owner.transfers.list()[0]
    attach_transport(source, owner)
    result = source.put_bytes("snapshot", b"retry", artifact_id=identifier)
    assert source.put_bytes("snapshot", b"retry", artifact_id=identifier) == result
    assert owner.transfers.list()[0].transfer_id == first_transfer.transfer_id
    assert len(owner.catalog.list()) == 1


def test_spools_if_owner_disconnects_after_admission(nodes):
    source, owner, _ = nodes
    attach_transport(source, owner, fail_chunk=True)
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.spool_max_bytes = 5
    policy.outage_policy = "local_spool"
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"12345")
    assert artifact.state == "pending_transfer" and artifact.owner_node_id == source.node_id
    attach_transport(source, owner)
    assert source.retry_pending() == 1
    assert owner.resolve(artifact.artifact_id).read_bytes() == b"12345"
    assert len(owner.transfers.list()) == 1


def test_secondary_owner_handoff_updates_both_catalogs(nodes):
    source, primary, secondary = nodes
    attach_transport(source, secondary)
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=primary.node_id)
    policy.secondary_destination = DestinationRef(node_id=secondary.node_id)
    policy.outage_policy = "secondary"
    policy.zero_local_media = True
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"secondary bytes")
    assert artifact.state == "committed" and artifact.owner_node_id == secondary.node_id
    assert artifact.requested_destination.node_id == primary.node_id
    attach_transport(secondary, primary)
    assert source.retry_pending() == 1
    assert primary.resolve(artifact.artifact_id).read_bytes() == b"secondary bytes"
    assert secondary.catalog.get(artifact.artifact_id).owner_node_id == primary.node_id
    assert source.catalog.get(artifact.artifact_id).owner_node_id == primary.node_id
    assert not list(Path(source.locations.get().path).glob(".tailcam-object-*"))
    assert not list(Path(secondary.locations.get().path).glob(".tailcam-object-*"))


def test_migration_replay_finishes_cleanup_but_preserves_replaced_source(nodes, monkeypatch):
    source, owner, _ = nodes
    attach_transport(source, owner)
    artifact = source.put_bytes("snapshot", b"original bytes")
    old_path = source.resolve(artifact.artifact_id)
    original = source._remove_copy

    def interrupt(*args):
        raise RuntimeError("interrupted after destination commit")

    monkeypatch.setattr(source, "_remove_copy", interrupt)
    target = DestinationRef(node_id=owner.node_id)
    with pytest.raises(RuntimeError):
        source.transfer_artifact(artifact.artifact_id, target, remove_source=True)
    assert source.catalog.get(artifact.artifact_id).owner_node_id == owner.node_id
    monkeypatch.setattr(source, "_remove_copy", original)
    old_path.write_bytes(b"replacement user data")
    with pytest.raises(StorageError) as failure:
        source.transfer_artifact(artifact.artifact_id, target, remove_source=True)
    assert failure.value.code == "source_changed"
    assert old_path.read_bytes() == b"replacement user data"
    old_path.write_bytes(b"original bytes")
    result = source.transfer_artifact(artifact.artifact_id, target, remove_source=True)
    assert result.artifact_id == artifact.artifact_id and not old_path.exists()
    assert len(owner.transfers.list()) == 1


def test_transfer_listing_has_common_public_shape(nodes):
    source, owner, _ = nodes
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.spool_max_bytes = 10
    policy.outage_policy = "local_spool"
    source.set_policy(policy)
    source.put_bytes("snapshot", b"bytes")
    records = source.list_transfers()
    assert {item["direction"] for item in records} == {"receiver", "outbound"}
    for item in records:
        assert {"transfer_id", "artifact_id", "offset", "size_bytes", "state"} <= item.keys()
        assert "path" not in item and "manifest" not in item


def test_zero_byte_transfers_still_obey_queue_limit(nodes, monkeypatch):
    owner = nodes[1]
    monkeypatch.setattr(owner.transfers, "MAX_ACTIVE_TRANSFERS", 2)
    first = owner.transfers.begin(manifest(owner, b""))
    owner.transfers.begin(manifest(owner, b""))
    with pytest.raises(StorageError) as failure:
        owner.transfers.begin(manifest(owner, b""))
    assert failure.value.code == "transfer_queue_full"
    owner.transfers.cancel(first.transfer_id)
    owner.transfers.begin(manifest(owner, b""))


def test_expired_spool_stops_retrying_and_explicit_deletion_frees_budget(nodes):
    source, owner, _ = nodes
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.spool_max_bytes = 5
    policy.outage_policy = "local_spool"
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"12345")
    row = source.catalog.connection.execute("SELECT id,data FROM storage_outbound").fetchone()
    job = json.loads(row[1])
    job["expires_at"] = 1
    with source.catalog.connection:
        source.catalog.connection.execute(
            "UPDATE storage_outbound SET data=? WHERE id=?", (json.dumps(job), row[0])
        )
    assert source.retry_pending() == 0
    assert source.catalog.get(artifact.artifact_id).state == "failed"
    assert source.resolve(artifact.artifact_id).read_bytes() == b"12345"
    assert source.delete(artifact.artifact_id)
    next_artifact = source.put_bytes("snapshot", b"12345")
    assert next_artifact.state == "pending_transfer"


def test_pending_local_spool_pauses_when_storage_role_is_disabled(nodes):
    from tailcam.node import RoleDisabledError

    source, owner, _ = nodes
    policy = source.get_policy()
    policy.default_destination = DestinationRef(node_id=owner.node_id)
    policy.spool_max_bytes = 5
    policy.outage_policy = "local_spool"
    source.set_policy(policy)
    artifact = source.put_bytes("snapshot", b"12345")

    def disabled():
        raise RoleDisabledError("storage")

    restarted = StorageService(
        source.config, Store(source.store.db_path), source.node_id, role_check=disabled
    )
    requests = attach_transport(restarted, owner)
    assert restarted.retry_pending() == 0
    assert requests == []
    assert restarted.resolve(artifact.artifact_id).read_bytes() == b"12345"
    outbound = [r for r in restarted.list_transfers() if r["direction"] == "outbound"]
    assert outbound[0]["error_code"] == "role_disabled"
