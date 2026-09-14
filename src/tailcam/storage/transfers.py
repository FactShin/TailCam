"""Bounded, idempotent offset transfers with checksum-verified atomic commit."""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from tailcam.storage.catalog import ArtifactCatalog
from tailcam.storage.locations import LocationRegistry
from tailcam.storage.models import (
    MAX_CHUNK_BYTES,
    Artifact,
    DestinationRef,
    StorageError,
    Transfer,
    TransferManifest,
)


def _open(root: Path, fd: int | None, name: str, flags: int) -> int:
    flags |= getattr(os, "O_NOFOLLOW", 0)
    opened = os.open(
        name if fd is not None else root / name,
        flags,
        0o600,
        **({"dir_fd": fd} if fd is not None else {}),
    )
    info = os.fstat(opened)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(opened)
        raise StorageError("invalid_file", "Transfer target is not a regular file.")
    return opened


def _unlink(root: Path, fd: int | None, name: str) -> None:
    try:
        os.unlink(
            name if fd is not None else root / name, **({"dir_fd": fd} if fd is not None else {})
        )
    except FileNotFoundError:
        pass


def _digest(stream) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while data := stream.read(MAX_CHUNK_BYTES):
        size += len(data)
        digest.update(data)
    return size, digest.hexdigest()


def artifact_filename(artifact: Artifact) -> str:
    suffix = {
        "recording": ".mp4",
        "snapshot": ".jpg",
        "thumbnail": ".jpg",
        "timelapse_frame": ".jpg",
        "timelapse_video": ".mp4",
        "timelapse_smooth": ".mp4",
        "analysis_evidence": ".jpg",
        "training_sample": ".jpg",
        "annotation": ".json",
        "model_output": ".pt",
        "export": ".zip",
    }[artifact.kind]
    # Manifest files and model directory members need their actual, harmless extension.
    requested = Path(str(artifact.metadata.get("filename", ""))).suffix.lower()
    if requested in {".pt", ".json", ".yaml", ".txt", ".safetensors", ".bin", ".zip"}:
        suffix = requested
    return f".tailcam-object-{artifact.artifact_id}{suffix}"


def content_identity(artifact: Artifact) -> dict[str, Any]:
    """Only delivery observations may change when the same transfer is retried."""
    return artifact.model_dump(
        exclude={
            "owner_node_id",
            "location_id",
            "state",
            "updated_at",
            "replicas",
            "owner_online",
            "last_seen",
            "retention",
        }
    )


class TransferReceiver:
    MAX_ACTIVE_TRANSFERS = 128

    def __init__(
        self,
        catalog: ArtifactCatalog,
        locations: LocationRegistry,
        *,
        role_check=None,
        max_artifact_bytes=None,
    ) -> None:
        self.catalog = catalog
        self.locations = locations
        self.role_check = role_check or (lambda: None)
        self.max_artifact_bytes = max_artifact_bytes or (lambda: 16 * 1024**3)

    def _row(self, transfer_id: str):
        try:
            transfer_id = str(UUID(transfer_id))
        except ValueError:
            raise StorageError("transfer_missing", "Transfer does not exist.", 404) from None
        row = self.catalog.connection.execute(
            "SELECT * FROM storage_transfers WHERE id=?", (transfer_id,)
        ).fetchone()
        if not row:
            raise StorageError("transfer_missing", "Transfer does not exist.", 404)
        return row

    def begin(self, manifest: TransferManifest | dict[str, Any]) -> Transfer:
        self.role_check()
        manifest = TransferManifest.model_validate(manifest)
        if manifest.destination.node_id != self.catalog.node_id:
            raise StorageError("owner_mismatch", "Transfer destination is another node.")
        if manifest.artifact.size_bytes > self.max_artifact_bytes():
            raise StorageError(
                "artifact_too_large", "Artifact exceeds the receiver's size limit.", 413
            )
        location = self.locations.get(manifest.destination.location_id)
        self.locations.verify(location)
        manifest = manifest.model_copy(deep=True)
        manifest.destination.location_id = location.location_id
        encoded = manifest.model_dump_json()
        with self.catalog.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM storage_transfers WHERE (origin=? AND idempotency_key=?) "
                "OR (artifact_id=? AND location=?)",
                (
                    manifest.artifact.origin_node_id,
                    manifest.idempotency_key,
                    manifest.artifact.artifact_id,
                    location.location_id,
                ),
            ).fetchone()
            if row:
                old = TransferManifest.model_validate_json(row["manifest"])
                # Mutable delivery observations do not create a different content identity.
                if (
                    content_identity(old.artifact) != content_identity(manifest.artifact)
                    or old.destination != manifest.destination
                ):
                    raise StorageError(
                        "transfer_conflict", "Transfer identity has a different manifest."
                    )
                return Transfer.model_validate_json(row["data"])
            existing = self.catalog.get(manifest.artifact.artifact_id)
            if existing and (
                existing.sha256 != manifest.artifact.sha256
                or existing.size_bytes != manifest.artifact.size_bytes
                or existing.origin_node_id != manifest.artifact.origin_node_id
                or existing.state == "deleted"
            ):
                raise StorageError("artifact_conflict", "Artifact identity is already registered.")
            active = conn.execute(
                "SELECT COUNT(*) FROM storage_transfers "
                "WHERE json_extract(data,'$.state') IN ('receiving','verifying')"
            ).fetchone()[0]
            if active >= self.MAX_ACTIVE_TRANSFERS:
                raise StorageError("transfer_queue_full", "Receiver transfer queue is full.", 429)
            token = str(uuid4())
            self.locations.reserve(
                token,
                location.location_id,
                manifest.artifact.size_bytes,
                "transfer",
                connection=conn,
            )
            now = time.time()
            transfer = Transfer(
                transfer_id=token,
                artifact_id=manifest.artifact.artifact_id,
                location_id=location.location_id,
                size_bytes=manifest.artifact.size_bytes,
                state="receiving",
                created_at=now,
                updated_at=now,
            )
            conn.execute(
                "INSERT INTO storage_transfers VALUES(?,?,?,?,?,?,?)",
                (
                    token,
                    manifest.artifact.origin_node_id,
                    manifest.idempotency_key,
                    manifest.artifact.artifact_id,
                    encoded,
                    transfer.model_dump_json(),
                    location.location_id,
                ),
            )
        return transfer

    def status(self, transfer_id: str) -> Transfer:
        return Transfer.model_validate_json(self._row(transfer_id)["data"])

    def manifest(self, transfer_id: str) -> TransferManifest:
        return TransferManifest.model_validate_json(self._row(transfer_id)["manifest"])

    def list(self, *, limit: int = 50, offset: int = 0) -> list[Transfer]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise StorageError("invalid_page", "Invalid transfer page.", 422)
        rows = self.catalog.connection.execute(
            "SELECT data FROM storage_transfers ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [Transfer.model_validate_json(row[0]) for row in rows]

    def _save(self, transfer: Transfer, conn) -> None:
        transfer.updated_at = time.time()
        conn.execute(
            "UPDATE storage_transfers SET data=? WHERE id=?",
            (transfer.model_dump_json(), transfer.transfer_id),
        )

    def append(self, transfer_id: str, offset: int, data: bytes, sha256: str) -> Transfer:
        self.role_check()
        if type(offset) is not int or offset < 0 or not data or len(data) > MAX_CHUNK_BYTES:
            raise StorageError("invalid_chunk", "Chunk or offset is outside transfer limits.", 413)
        if hashlib.sha256(data).hexdigest() != sha256:
            raise StorageError("checksum_mismatch", "Chunk checksum does not match.", 422)
        with self.catalog.transaction() as conn:
            transfer = self.status(transfer_id)
            if transfer.state != "receiving":
                raise StorageError("transfer_closed", "Transfer does not accept further chunks.")
            if offset > transfer.offset or offset + len(data) > transfer.size_bytes:
                raise StorageError(
                    "offset_mismatch", "Resume from the acknowledged transfer offset."
                )
            with self.locations.root_handle(transfer.location_id) as (root, root_fd):
                name = f".tailcam-incoming-{transfer.transfer_id}"
                descriptor = _open(root, root_fd, name, os.O_RDWR | os.O_CREAT)
                with os.fdopen(descriptor, "r+b") as stream:
                    length = os.fstat(stream.fileno()).st_size
                    if length < transfer.offset:
                        raise StorageError(
                            "transfer_damaged", "Acknowledged transfer bytes are missing."
                        )
                    if length > transfer.offset:
                        stream.truncate(transfer.offset)
                    if offset < transfer.offset:
                        if offset + len(data) > transfer.offset:
                            raise StorageError(
                                "offset_mismatch", "Chunk overlaps unacknowledged bytes."
                            )
                        stream.seek(offset)
                        if stream.read(len(data)) != data:
                            raise StorageError(
                                "chunk_conflict", "Retried bytes differ from acknowledged bytes."
                            )
                        return transfer
                    stream.seek(offset)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                transfer.offset += len(data)
                self._save(transfer, conn)
        return transfer

    def commit(self, transfer_id: str) -> Artifact:
        self.role_check()
        with self.catalog.transaction() as conn:
            transfer = self.status(transfer_id)
            manifest = self.manifest(transfer_id)
            if transfer.state == "committed":
                artifact = self.catalog.get(transfer.artifact_id)
                if artifact is None:
                    raise StorageError("catalog_damaged", "Committed artifact metadata is missing.")
                return artifact
            if transfer.state != "receiving" or transfer.offset != transfer.size_bytes:
                raise StorageError(
                    "transfer_incomplete", "All declared bytes must arrive before commit."
                )
            with self.locations.root_handle(transfer.location_id) as (root, root_fd):
                incoming = f".tailcam-incoming-{transfer.transfer_id}"
                final = artifact_filename(manifest.artifact)
                recovered = False
                try:
                    descriptor = _open(root, root_fd, incoming, os.O_RDWR)
                except FileNotFoundError:
                    try:
                        # Recovery still flushes verified bytes before publishing
                        # metadata. Windows fsync requires a writable handle.
                        descriptor = _open(root, root_fd, final, os.O_RDWR)
                        recovered = True
                    except FileNotFoundError:
                        if transfer.size_bytes:
                            raise StorageError(
                                "transfer_damaged", "Transfer bytes are missing."
                            ) from None
                        descriptor = _open(
                            root, root_fd, incoming, os.O_RDWR | os.O_CREAT | os.O_EXCL
                        )
                with os.fdopen(descriptor, "rb") as stream:
                    size, digest = _digest(stream)
                    if size != transfer.size_bytes or digest != manifest.artifact.sha256:
                        raise StorageError(
                            "checksum_mismatch", "Final artifact checksum does not match.", 422
                        )
                    os.fsync(stream.fileno())
                if not recovered:
                    if root_fd is not None:
                        os.replace(incoming, final, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                    else:
                        self.locations.verify(self.locations.get(transfer.location_id))
                        os.replace(root / incoming, root / final)
                if root_fd is not None:
                    os.fsync(root_fd)
                source_ref = DestinationRef(
                    node_id=manifest.artifact.owner_node_id,
                    location_id=manifest.artifact.location_id,
                )
                replicas = (
                    [source_ref]
                    if (
                        manifest.retain_source
                        and source_ref.location_id
                        and source_ref != manifest.destination
                    )
                    else []
                )
                artifact = manifest.artifact.model_copy(
                    deep=True,
                    update={
                        "owner_node_id": self.catalog.node_id,
                        "location_id": transfer.location_id,
                        "state": "replicated" if replicas else "committed",
                        "updated_at": time.time(),
                        "replicas": replicas,
                        "owner_online": True,
                        "last_seen": time.time(),
                    },
                )
                self.catalog.save(artifact, str(root / final), connection=conn)
                transfer.state = "committed"
                self._save(transfer, conn)
                self.locations.release(transfer.transfer_id, connection=conn)
        return artifact

    def cancel(self, transfer_id: str) -> Transfer:
        self.role_check()
        with self.catalog.transaction() as conn:
            transfer = self.status(transfer_id)
            if transfer.state == "committed":
                raise StorageError("already_committed", "Delete the committed artifact explicitly.")
            with self.locations.root_handle(transfer.location_id) as (root, root_fd):
                _unlink(root, root_fd, f".tailcam-incoming-{transfer.transfer_id}")
            transfer.state = "cancelled"
            self._save(transfer, conn)
            self.locations.release(transfer.transfer_id, connection=conn)
        return transfer
