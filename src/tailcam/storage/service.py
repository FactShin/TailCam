"""Producer admission, owner-aware publication and persistent delivery recovery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

import httpx

from tailcam.config import AppConfig
from tailcam.node import RoleDisabledError
from tailcam.persistence.store import Store
from tailcam.storage.catalog import ArtifactCatalog
from tailcam.storage.locations import LocationRegistry
from tailcam.storage.models import (
    CONTENT_KINDS,
    MAX_CHUNK_BYTES,
    Admission,
    Artifact,
    ContentKind,
    DestinationRef,
    RetentionPolicy,
    StorageError,
    StorageLocation,
    StoragePolicy,
    Transfer,
    TransferManifest,
)
from tailcam.storage.policy import destination_for
from tailcam.storage.transfers import TransferReceiver, _digest, content_identity


class WorkspaceLease:
    """Explicit bounded scratch. Producers must check before/after bounded write batches.

    A pathname alone cannot constrain an external encoder: callers must also configure
    its output-size bound. Failed work retains its reservation and files for recovery.
    """

    def __init__(
        self,
        service: StorageService,
        token: str,
        path: Path,
        admission: Admission,
        max_bytes: int,
        location_id: str,
    ) -> None:
        self.service, self.token, self.path = service, token, path
        self.admission, self.max_bytes, self.location_id = admission, max_bytes, location_id
        self.closed = False

    def check(self) -> int:
        if self.closed:
            raise StorageError("workspace_closed", "Workspace reservation has been released.")
        self.service.locations.verify(self.service.locations.get(self.location_id))
        size = 0
        for directory, dirs, files in os.walk(self.path, followlinks=False):
            for name in (*dirs, *files):
                candidate = Path(directory) / name
                if candidate.is_symlink():
                    raise StorageError("unsafe_workspace", "Workspace contains a symbolic link.")
            for name in files:
                size += (Path(directory) / name).stat().st_size
                if size > self.max_bytes:
                    raise StorageError(
                        "workspace_full", "Workspace byte limit has been reached.", 507
                    )
        return size

    def release(self, remove: bool = True) -> None:
        if self.closed:
            return
        self.service.locations.verify(self.service.locations.get(self.location_id))
        if remove:
            shutil.rmtree(self.path)
        elif self.path.exists() and any(self.path.iterdir()):
            raise StorageError("workspace_not_empty", "Retained files must keep their reservation.")
        self.service.locations.release(self.token)
        self.closed = True

    def __enter__(self) -> WorkspaceLease:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.release()


class StorageService:
    def __init__(
        self,
        config: AppConfig,
        store: Store,
        node_id: str,
        *,
        resolve_peer: Callable[[str], str | None] | None = None,
        role_check: Callable[[], None] | None = None,
        http_client: Any = None,
    ) -> None:
        self.config, self.store, self.node_id = config, store, str(UUID(node_id))
        self.resolve_peer = resolve_peer or (lambda _: None)
        self.role_check = role_check or (lambda: None)
        self.active_roles = frozenset(config.node.roles)
        self.catalog = ArtifactCatalog(store, self.node_id)
        self.locations = LocationRegistry(self.catalog)
        self.transfers = TransferReceiver(
            self.catalog,
            self.locations,
            role_check=self.role_check,
            max_artifact_bytes=lambda: self.get_policy().artifact_max_bytes,
        )
        self._client = http_client
        self._retry_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        enabled = self.catalog.setting("policy_enabled")
        return enabled == "true" if enabled is not None else self.config.storage.unified_enabled

    def get_policy(self) -> StoragePolicy:
        saved = self.catalog.setting("policy")
        if saved:
            return StoragePolicy.model_validate_json(saved)
        if self.config.storage.policy:
            return StoragePolicy.model_validate(self.config.storage.policy)
        return StoragePolicy(default_destination=DestinationRef(node_id=self.node_id))

    def set_policy(
        self,
        policy: dict[str, Any] | StoragePolicy,
        expected_revision: int | None = None,
    ) -> StoragePolicy:
        candidate = StoragePolicy.model_validate(policy).model_copy(deep=True)
        with self.catalog.transaction() as conn:
            current = self.get_policy()
            if expected_revision is not None and expected_revision != current.revision:
                raise StorageError(
                    "policy_changed", "Storage settings changed; reload before applying."
                )
            candidate.revision = current.revision + 1
            self.catalog.set_setting("policy", candidate.model_dump_json(), connection=conn)
            self.catalog.set_setting("policy_enabled", "true", connection=conn)
        self.config.storage.policy = candidate.model_dump(exclude_none=True)
        self.config.storage.unified_enabled = True
        return candidate

    def register_location(self, path: str, **kwargs) -> StorageLocation:
        self.role_check()
        return self.locations.register(path, **kwargs)

    def list_locations(self) -> list[StorageLocation]:
        return self.locations.list()

    def update_location(self, location_id: str, **kwargs) -> StorageLocation:
        self.role_check()
        return self.locations.update(location_id, **kwargs)

    def _http(self):
        if self._client is None:
            self._client = httpx.Client(timeout=5.0, trust_env=False, follow_redirects=False)
        return self._client

    def _base(self, node_id: str) -> str:
        base = self.resolve_peer(node_id)
        try:
            parsed = urlsplit(base or "")
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
        except ValueError:
            raise StorageError(
                "owner_unavailable", "The selected owner is unavailable.", 503
            ) from None
        return str(base).rstrip("/")

    def _json(self, node_id: str, method: str, path: str, **kwargs) -> Any:
        kwargs["headers"] = {"Accept-Encoding": "identity", **kwargs.get("headers", {})}
        try:
            with self._http().stream(method, self._base(node_id) + path, **kwargs) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise StorageError(
                        "invalid_peer_response", "Compressed protocol response rejected.", 502
                    )
                body = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    if len(body) + len(part) > 2 * 1024 * 1024:
                        raise StorageError(
                            "invalid_peer_response", "Storage peer response is too large.", 502
                        )
                    body.extend(part)
                if response.status_code >= 300:
                    # Never expose peer URLs, proxy pages, credentials or untrusted exception text.
                    raise StorageError(
                        "owner_unavailable",
                        "Storage owner rejected the operation.",
                        503 if response.status_code >= 500 else 409,
                    )
                return json.loads(body)
        except (httpx.HTTPError, OSError):
            raise StorageError(
                "owner_unavailable", "Storage owner could not be reached.", 503
            ) from None
        except (ValueError, TypeError):
            raise StorageError(
                "invalid_peer_response", "Storage peer returned invalid data.", 502
            ) from None

    def _destination(self, target: DestinationRef, size: int) -> DestinationRef:
        if target.node_id == self.node_id:
            self.role_check()
            location = self.locations.describe(target.location_id)
        else:
            body = self._json(target.node_id, "GET", "/api/v1/storage/locations")
            try:
                items = body["items"] if isinstance(body, dict) else body
                if not isinstance(items, list) or len(items) > 1000:
                    raise ValueError
                locations = [StorageLocation.model_validate(item) for item in items]
                location = next(
                    loc
                    for loc in locations
                    if (
                        loc.location_id == target.location_id
                        if target.location_id
                        else loc.is_default
                    )
                )
                if location.node_id != target.node_id:
                    raise ValueError
            except (ValueError, TypeError, KeyError, StopIteration):
                raise StorageError(
                    "owner_unavailable", "Storage owner has no matching location.", 503
                ) from None
        if location.state != "ready":
            raise StorageError(
                "mount_unavailable", "The selected storage mount is unavailable.", 503
            )
        if location.allocatable_bytes is None or size > location.allocatable_bytes:
            raise StorageError(
                "quota_exceeded", "Selected destination cannot admit these bytes.", 507
            )
        return DestinationRef(node_id=target.node_id, location_id=location.location_id)

    def admit(
        self,
        kind: ContentKind,
        *,
        origin_node_id: str | None = None,
        camera_id: str = "",
        expected_bytes: int = 0,
        requires_workspace: bool = False,
        policy_snapshot: StoragePolicy | None = None,
    ) -> Admission:
        if kind not in CONTENT_KINDS or type(expected_bytes) is not int or expected_bytes < 0:
            raise StorageError("invalid_artifact", "Invalid content kind or byte count.", 422)
        policy = (policy_snapshot or self.get_policy()).model_copy(deep=True)
        if expected_bytes > policy.artifact_max_bytes:
            raise StorageError("artifact_too_large", "Artifact exceeds the configured limit.", 413)
        if requires_workspace and (policy.zero_local_media or not policy.workspace_max_bytes):
            raise StorageError(
                "workspace_forbidden", "This execution needs local scratch; policy forbids it.", 409
            )
        primary = destination_for(policy, kind, origin_node_id or self.node_id, camera_id)
        if policy.zero_local_media and primary.node_id == self.node_id:
            raise StorageError(
                "local_media_forbidden", "Zero-local-media policy requires another owner."
            )
        spooled = False
        try:
            destination = self._destination(primary, expected_bytes)
            primary = destination.model_copy(deep=True)
        except StorageError:
            if policy.outage_policy == "secondary" and policy.secondary_destination:
                secondary = policy.secondary_destination
                if policy.zero_local_media and secondary.node_id == self.node_id:
                    raise StorageError(
                        "local_media_forbidden", "Secondary owner would create local media."
                    ) from None
                destination = self._destination(secondary, expected_bytes)
            elif policy.outage_policy == "local_spool" and not policy.zero_local_media:
                if expected_bytes > policy.spool_max_bytes:
                    raise StorageError(
                        "spool_full", "Artifact exceeds the local spool budget.", 507
                    ) from None
                destination = self._destination(
                    DestinationRef(node_id=self.node_id), expected_bytes
                )
                self._check_spool(expected_bytes, max_bytes=policy.spool_max_bytes)
                spooled = True
            else:
                raise
        return Admission(
            kind=kind,
            destination=destination,
            requested_destination=primary,
            policy_revision=policy.revision,
            outage_policy=policy.outage_policy,
            max_bytes=policy.artifact_max_bytes,
            spooled=spooled,
            workspace_allowed=not policy.zero_local_media and policy.workspace_max_bytes > 0,
            workspace_max_bytes=policy.workspace_max_bytes,
            spool_max_bytes=policy.spool_max_bytes,
            spool_max_age_seconds=policy.spool_max_age_seconds,
            source_cleanup=policy.source_cleanup,
            retention=policy.retention.model_copy(deep=True),
            secondary_destination=policy.secondary_destination,
        )

    def _check_spool(self, additional: int, *, max_bytes: int, connection=None) -> None:
        conn = connection or self.catalog.connection
        total = conn.execute(
            "SELECT COALESCE(SUM(bytes),0) FROM storage_reservations WHERE category='spool'"
        ).fetchone()[0]
        if total + additional > max_bytes:
            raise StorageError("spool_full", "The bounded local spool is full.", 507)

    def _artifact(
        self,
        kind: ContentKind,
        size: int,
        digest: str,
        admission: Admission,
        *,
        origin_node_id: str | None = None,
        camera_id: str = "",
        metadata=None,
        parent_id: str | None = None,
        artifact_id: str | None = None,
        mime_type: str = "application/octet-stream",
    ) -> Artifact:
        if kind != admission.kind or size > admission.max_bytes:
            raise StorageError(
                "admission_mismatch", "Artifact does not match its admitted plan.", 413
            )
        now = time.time()
        artifact = Artifact(
            artifact_id=artifact_id or str(uuid4()),
            owner_node_id=admission.destination.node_id,
            origin_node_id=origin_node_id or self.node_id,
            camera_id=camera_id,
            kind=kind,
            mime_type=mime_type,
            size_bytes=size,
            sha256=digest,
            created_at=now,
            updated_at=now,
            location_id=admission.destination.location_id,
            requested_destination=admission.requested_destination,
            policy_revision=admission.policy_revision,
            parent_id=parent_id,
            metadata=metadata or {},
            retention=admission.retention.model_copy(deep=True),
        )
        existing = self.catalog.get(artifact.artifact_id)
        if existing:
            artifact.created_at = existing.created_at
            if content_identity(existing) != content_identity(artifact):
                raise StorageError("artifact_conflict", "Artifact UUID has different content.")
            if existing.state == "deleted":
                raise StorageError("artifact_deleted", "A deleted artifact UUID cannot be reused.")
            artifact = existing
        return artifact

    def _send(
        self,
        artifact: Artifact,
        destination: DestinationRef,
        stream,
        *,
        retain_source: bool = False,
    ) -> Artifact:
        manifest = TransferManifest(
            artifact=artifact,
            destination=destination,
            idempotency_key=f"{artifact.artifact_id}:{destination.location_id or 'default'}",
            retain_source=retain_source,
        )
        local = destination.node_id == self.node_id
        transfer = (
            self.transfers.begin(manifest)
            if local
            else Transfer.model_validate(
                self._json(
                    destination.node_id,
                    "POST",
                    "/api/v1/transfers",
                    json=manifest.model_dump(mode="json"),
                )
            )
        )
        if (
            transfer.artifact_id != artifact.artifact_id
            or transfer.size_bytes != artifact.size_bytes
        ):
            raise StorageError(
                "invalid_peer_response", "Peer transfer identity does not match.", 502
            )
        if not 0 <= transfer.offset <= artifact.size_bytes:
            raise StorageError("invalid_peer_response", "Peer transfer offset is invalid.", 502)
        stream.seek(transfer.offset)
        while transfer.offset < artifact.size_bytes:
            if self._stop.is_set():
                raise StorageError("service_stopping", "Storage service is stopping.", 503)
            chunk = stream.read(min(MAX_CHUNK_BYTES, artifact.size_bytes - transfer.offset))
            if not chunk:
                raise StorageError("source_changed", "Source content changed during transfer.")
            digest = hashlib.sha256(chunk).hexdigest()
            expected = transfer.offset + len(chunk)
            transfer = (
                self.transfers.append(transfer.transfer_id, transfer.offset, chunk, digest)
                if local
                else Transfer.model_validate(
                    self._json(
                        destination.node_id,
                        "PUT",
                        f"/api/v1/transfers/{transfer.transfer_id}/chunks",
                        params={"offset": transfer.offset},
                        content=chunk,
                        headers={
                            "X-Chunk-SHA256": digest,
                            "Content-Type": "application/octet-stream",
                        },
                    )
                )
            )
            if transfer.offset != expected:
                raise StorageError(
                    "invalid_peer_response", "Peer acknowledged an unexpected offset.", 502
                )
        committed = (
            self.transfers.commit(transfer.transfer_id)
            if local
            else Artifact.model_validate(
                self._json(
                    destination.node_id, "POST", f"/api/v1/transfers/{transfer.transfer_id}/commit"
                )
            )
        )
        if (
            content_identity(committed) != content_identity(artifact)
            or committed.owner_node_id != destination.node_id
            or committed.location_id != destination.location_id
            or committed.state not in {"committed", "replicated"}
        ):
            raise StorageError(
                "invalid_commit", "Storage owner did not confirm this artifact.", 502
            )
        if not local:
            self.catalog.save(committed)
        return committed

    def _publish(self, artifact: Artifact, admission: Admission, stream) -> Artifact:
        saved = self.catalog.get(artifact.artifact_id)
        if saved and saved.state in {"committed", "replicated"}:
            return saved
        if saved is None:
            artifact.state = "pending_transfer"
            with self.catalog.transaction() as conn:
                self.catalog.save(artifact, connection=conn, publish=False)
                self.catalog.set_setting(
                    "admission:" + artifact.artifact_id,
                    admission.model_dump_json(),
                    connection=conn,
                )
        spool_token = None
        if admission.spooled:
            spool_token = "spool:" + artifact.artifact_id
            with self.catalog.transaction() as conn:
                if not conn.execute(
                    "SELECT 1 FROM storage_reservations WHERE id=?", (spool_token,)
                ).fetchone():
                    self._check_spool(
                        artifact.size_bytes, max_bytes=admission.spool_max_bytes, connection=conn
                    )
                    # The receiver reserves physical bytes; this row bounds the spool policy.
                    conn.execute(
                        "INSERT INTO storage_reservations VALUES(?,?,?,?,?,?)",
                        (
                            spool_token,
                            "spool-budget",
                            artifact.size_bytes,
                            "spool",
                            None,
                            time.time(),
                        ),
                    )
        try:
            result = self._send(artifact, admission.destination, stream)
        except StorageError as exc:
            if spool_token:
                self.locations.release(spool_token)
            if (
                exc.status_code >= 500
                and not admission.spooled
                and admission.destination == admission.requested_destination
            ):
                fallback = None
                if admission.outage_policy == "local_spool" and admission.spool_max_bytes:
                    fallback = self._destination(
                        DestinationRef(node_id=self.node_id), artifact.size_bytes
                    )
                elif admission.outage_policy == "secondary" and admission.secondary_destination:
                    fallback = self._destination(
                        admission.secondary_destination, artifact.size_bytes
                    )
                if fallback:
                    recovery = admission.model_copy(
                        deep=True,
                        update={
                            "destination": fallback,
                            "spooled": admission.outage_policy == "local_spool",
                        },
                    )
                    artifact = artifact.model_copy(
                        deep=True,
                        update={
                            "owner_node_id": fallback.node_id,
                            "location_id": fallback.location_id,
                            "state": "pending_transfer",
                        },
                    )
                    self.catalog.save(artifact, publish=False)
                    self.catalog.set_setting(
                        "admission:" + artifact.artifact_id, recovery.model_dump_json()
                    )
                    stream.seek(0)
                    return self._publish(artifact, recovery, stream)
            raise
        except BaseException:
            if spool_token:
                self.locations.release(spool_token)
            raise
        secondary = admission.destination != admission.requested_destination
        if admission.spooled or secondary:
            result.state = "pending_transfer" if admission.spooled else "committed"
            result.updated_at = time.time()
            local_path = self.catalog.local_path(result.artifact_id)
            self.catalog.save(result, local_path)
            job = {
                "id": str(uuid4()),
                "artifact_id": result.artifact_id,
                "destination": admission.requested_destination.model_dump(),
                "state": "pending_transfer",
                "created_at": time.time(),
                "updated_at": time.time(),
                "attempts": 0,
                "error_code": None,
                "spooled": admission.spooled,
                "spool_token": spool_token,
                "source_cleanup": admission.source_cleanup,
                "expires_at": time.time() + admission.spool_max_age_seconds,
                "next_attempt": 0,
                "source_location_id": result.location_id
                if result.owner_node_id == self.node_id
                else None,
            }
            with self.catalog.connection:
                self.catalog.connection.execute(
                    "INSERT INTO storage_outbound VALUES(?,?,?) "
                    "ON CONFLICT(artifact_id) DO NOTHING",
                    (job["id"], result.artifact_id, json.dumps(job)),
                )
            self._wake.set()
        return result

    def _plan(
        self,
        kind: ContentKind,
        admission: Admission | None,
        kwargs: dict[str, Any],
        size: int,
    ) -> Admission:
        identifier = kwargs.get("artifact_id")
        saved = self.catalog.setting("admission:" + str(identifier)) if identifier else None
        if saved:
            return Admission.model_validate_json(saved)
        return admission or self.admit(
            kind,
            origin_node_id=kwargs.get("origin_node_id"),
            camera_id=kwargs.get("camera_id", ""),
            expected_bytes=size,
        )

    def put_bytes(
        self,
        kind: ContentKind,
        data: bytes,
        *,
        admission: Admission | None = None,
        **kwargs,
    ) -> Artifact:
        import io

        if len(data) > 64 * 1024 * 1024:
            raise StorageError(
                "payload_too_large", "Use a file transfer for content above 64 MiB.", 413
            )
        admission = self._plan(kind, admission, kwargs, len(data))
        artifact = self._artifact(
            kind, len(data), hashlib.sha256(data).hexdigest(), admission, **kwargs
        )
        return self._publish(artifact, admission, io.BytesIO(data))

    def finalize(
        self,
        path: Path | str,
        kind: ContentKind,
        *,
        admission: Admission | None = None,
        remove_source: bool = False,
        **kwargs,
    ) -> Artifact:
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise StorageError("invalid_source", "Artifact source must be a regular file.", 422)
        size = source.stat().st_size
        admission = self._plan(kind, admission, kwargs, size)
        if size > admission.max_bytes:
            raise StorageError("artifact_too_large", "Artifact exceeds its admitted limit.", 413)
        with source.open("rb") as stream:
            verified_size, digest = _digest(stream)
            if size != verified_size:
                raise StorageError("source_changed", "Artifact source changed during verification.")
            artifact = self._artifact(kind, size, digest, admission, **kwargs)
            stream.seek(0)
            result = self._publish(artifact, admission, stream)
        if remove_source:
            source.unlink()
        return result

    def _runtime_location(self) -> StorageLocation:
        saved = self.catalog.setting("workspace_location")
        if saved:
            return self.locations.get(saved)
        root = self.store.db_path.parent / "storage-workspace"
        location = self.locations.register(
            str(root), label="Bounded runtime workspace", create=True, make_default=False
        )
        self.catalog.set_setting("workspace_location", location.location_id)
        return location

    def workspace(
        self,
        kind: ContentKind,
        *,
        max_bytes: int,
        origin_node_id: str | None = None,
        camera_id: str = "",
        admission: Admission | None = None,
        _artifact_cache: bool = False,
    ) -> WorkspaceLease:
        if admission is None and self.get_policy().zero_local_media:
            raise StorageError(
                "workspace_forbidden", "Zero-local-media policy forbids scratch files."
            )
        runtime_kind = kind in {"model_output", "training_sample", "annotation", "export"}
        if not ((_artifact_cache or runtime_kind) and self.active_roles & {"analysis", "training"}):
            self.role_check()
        admission = admission or self.admit(
            kind, origin_node_id=origin_node_id, camera_id=camera_id, requires_workspace=True
        )
        if not admission.workspace_allowed:
            raise StorageError(
                "workspace_forbidden", "This admission does not allow a local workspace."
            )
        if max_bytes <= 0 or max_bytes > admission.workspace_max_bytes:
            raise StorageError(
                "workspace_forbidden", "Requested scratch exceeds its admitted limit.", 507
            )
        location = self._runtime_location()
        token = str(uuid4())
        path = Path(location.path) / f"work-{token}"
        with self.catalog.transaction() as conn:
            reserved = conn.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM storage_reservations WHERE category='workspace'"
            ).fetchone()[0]
            if reserved + max_bytes > admission.workspace_max_bytes:
                raise StorageError(
                    "workspace_full", "Concurrent workspaces consume the scratch budget.", 507
                )
            self.locations.reserve(
                token, location.location_id, max_bytes, "workspace", connection=conn
            )
            with self.locations.root_handle(location.location_id) as (root, root_fd):
                os.mkdir(
                    path.name if root_fd is not None else path,
                    **({"dir_fd": root_fd} if root_fd is not None else {}),
                )
            conn.execute("UPDATE storage_reservations SET path=? WHERE id=?", (str(path), token))
        return WorkspaceLease(self, token, path, admission, max_bytes, location.location_id)

    def workspace_for_artifact(self, artifact_id: str, *, max_bytes: int) -> WorkspaceLease:
        artifact = self.catalog.get(artifact_id)
        if artifact is None or artifact.state == "deleted":
            raise StorageError("artifact_missing", "Artifact does not exist.", 404)
        policy = self.get_policy()
        if policy.zero_local_media or not policy.workspace_max_bytes:
            raise StorageError("workspace_forbidden", "Policy forbids local artifact caches.")
        plan = Admission(
            kind=artifact.kind,
            destination=DestinationRef(
                node_id=artifact.owner_node_id, location_id=artifact.location_id
            ),
            requested_destination=artifact.requested_destination,
            policy_revision=policy.revision,
            outage_policy="destination_required",
            max_bytes=policy.artifact_max_bytes,
            workspace_allowed=True,
            workspace_max_bytes=policy.workspace_max_bytes,
        )
        return self.workspace(
            artifact.kind, max_bytes=max_bytes, admission=plan, _artifact_cache=True
        )

    def release_workspace(self, path: Path | str) -> bool:
        """Clean exactly one journaled workspace, including after an interrupted process."""
        requested = Path(path)
        row = self.catalog.connection.execute(
            "SELECT id,location,path FROM storage_reservations "
            "WHERE category='workspace' AND path=?",
            (str(requested),),
        ).fetchone()
        if row is None:
            return False
        root = self.locations.verify(self.locations.get(row["location"]))
        if (
            requested.is_symlink()
            or requested.parent != root
            or requested.name != f"work-{row['id']}"
        ):
            raise StorageError(
                "unsafe_workspace", "Workspace does not match its recorded reservation."
            )
        try:
            if requested.exists():
                shutil.rmtree(requested)
        except OSError:
            raise StorageError(
                "workspace_cleanup_failed", "Workspace files could not be removed.", 503
            ) from None
        self.locations.release(row["id"])
        return True

    def resolve(self, artifact_id: str) -> Path:
        artifact = self.catalog.get(artifact_id)
        if artifact is None or artifact.state == "deleted":
            raise StorageError("artifact_missing", "Artifact does not exist.", 404)
        if artifact.owner_node_id != self.node_id:
            raise StorageError("remote_owner", "Artifact bytes belong to another node.", 409)
        local = self.catalog.local_path(artifact_id)
        if not local or not artifact.location_id:
            raise StorageError("artifact_missing", "Artifact bytes are unavailable.", 404)
        root = self.locations.verify(self.locations.get(artifact.location_id))
        path = Path(local)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise StorageError(
                "artifact_missing", "Artifact bytes are unavailable on their registered mount.", 404
            )
        return path

    def materialize(self, artifact_id: str, workspace_lease: WorkspaceLease) -> Path:
        workspace_lease.check()
        artifact = self.catalog.get(artifact_id)
        if artifact is None or artifact.state == "deleted":
            raise StorageError("artifact_missing", "Artifact does not exist.", 404)
        from tailcam.storage.transfers import artifact_filename

        destination = workspace_lease.path / artifact_filename(artifact)
        if destination.exists():
            with destination.open("rb") as source:
                size, existing_digest = _digest(source)
            if (size, existing_digest) == (artifact.size_bytes, artifact.sha256):
                return destination
            raise StorageError("workspace_conflict", "Cached artifact verification failed.")
        if workspace_lease.check() + artifact.size_bytes > workspace_lease.max_bytes:
            raise StorageError(
                "workspace_full", "Artifact exceeds the remaining scratch budget.", 507
            )
        pending = destination.with_suffix(destination.suffix + ".pending")
        digest, size = hashlib.sha256(), 0
        try:
            with pending.open("xb") as target:
                if artifact.owner_node_id == self.node_id:
                    with self.resolve(artifact_id).open("rb") as source:
                        while chunk := source.read(MAX_CHUNK_BYTES):
                            size += len(chunk)
                            if size > artifact.size_bytes:
                                raise StorageError(
                                    "source_changed", "Artifact bytes exceed the catalog size."
                                )
                            digest.update(chunk)
                            target.write(chunk)
                else:
                    with self._http().stream(
                        "GET",
                        self._base(artifact.owner_node_id)
                        + f"/api/v1/artifacts/{artifact_id}/content",
                        headers={"Accept-Encoding": "identity"},
                    ) as response:
                        if response.status_code != 200:
                            raise StorageError(
                                "owner_unavailable", "Artifact owner is unavailable.", 503
                            )
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise StorageError(
                                "invalid_peer_response",
                                "Encoded artifact responses are not accepted.",
                                502,
                            )
                        for chunk in response.iter_bytes(chunk_size=MAX_CHUNK_BYTES):
                            size += len(chunk)
                            if size > artifact.size_bytes:
                                raise StorageError(
                                    "source_changed", "Artifact bytes exceed the catalog size."
                                )
                            digest.update(chunk)
                            target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise StorageError(
                    "checksum_mismatch", "Downloaded artifact verification failed.", 422
                )
            pending.replace(destination)
            workspace_lease.check()
            return destination
        except BaseException:
            pending.unlink(missing_ok=True)
            raise

    def adopt_existing(
        self,
        path: Path | str,
        kind: ContentKind,
        *,
        namespace: str,
        legacy_id: str,
        variant: str = "",
        **kwargs,
    ) -> Artifact:
        previous = self.catalog.resolve_alias(namespace, str(legacy_id), variant)
        if previous:
            return previous
        source = Path(path).resolve(strict=True)
        if not source.is_file():
            raise StorageError("invalid_source", "Legacy artifact is not a regular file.", 422)
        locations = [loc for loc in self.locations.list() if source.is_relative_to(Path(loc.path))]
        location = (
            max(locations, key=lambda loc: len(loc.path))
            if locations
            else self.locations.register(
                str(source.parent),
                label="Legacy media",
                make_default=False,
            )
        )
        self.locations.verify(location)
        with source.open("rb") as stream:
            size, digest = _digest(stream)
        policy = self.get_policy()
        destination = DestinationRef(node_id=self.node_id, location_id=location.location_id)
        admission = Admission(
            kind=kind,
            destination=destination,
            requested_destination=destination,
            policy_revision=policy.revision,
            outage_policy="destination_required",
            max_bytes=max(size, policy.artifact_max_bytes),
        )
        identifier = str(uuid5(UUID(self.node_id), f"legacy:{namespace}:{legacy_id}:{variant}"))
        artifact = self._artifact(kind, size, digest, admission, artifact_id=identifier, **kwargs)
        self.catalog.save(artifact, str(source))
        self.catalog.alias(namespace, str(legacy_id), variant, artifact.artifact_id)
        return artifact

    def transfer_artifact(
        self,
        artifact_id: str,
        destination: DestinationRef | dict[str, Any],
        *,
        remove_source: bool = False,
        source_location_id: str | None = None,
    ) -> Artifact:
        artifact = self.catalog.get(artifact_id)
        if artifact is None or artifact.state == "deleted":
            raise StorageError("artifact_missing", "Artifact does not exist.", 404)
        if artifact.owner_node_id == self.node_id:
            self.role_check()
        desired = DestinationRef.model_validate(destination)
        already_committed = (
            desired.node_id == artifact.owner_node_id
            and (desired.location_id is None or desired.location_id == artifact.location_id)
            and artifact.state in {"committed", "replicated"}
        )
        if already_committed:
            target = DestinationRef(
                node_id=artifact.owner_node_id, location_id=artifact.location_id
            )
            result = artifact
        elif artifact.owner_node_id != self.node_id:
            result = Artifact.model_validate(
                self._json(
                    artifact.owner_node_id,
                    "POST",
                    f"/api/v1/artifacts/{artifact_id}/transfer",
                    json={
                        "destination": desired.model_dump(mode="json"),
                        "remove_source": remove_source,
                    },
                )
            )
            if (
                content_identity(result) != content_identity(artifact)
                or result.owner_node_id != desired.node_id
                or (desired.location_id and result.location_id != desired.location_id)
                or result.state not in {"committed", "replicated"}
            ):
                raise StorageError(
                    "invalid_commit", "Owner did not confirm the requested handoff.", 502
                )
            self.catalog.save(result, publish=False)
            return result
        else:
            if source_location_id and artifact.location_id != source_location_id:
                raise StorageError(
                    "source_changed", "Artifact is no longer on the reviewed source location."
                )
            source = self.resolve(artifact_id)
            target = self._destination(desired, artifact.size_bytes)
            with source.open("rb") as stream:
                size, digest = _digest(stream)
                if (size, digest) != (artifact.size_bytes, artifact.sha256):
                    raise StorageError(
                        "source_changed", "Source no longer matches its artifact checksum."
                    )
                stream.seek(0)
                result = self._send(artifact, target, stream, retain_source=not remove_source)
            # Persist the authoritative ownership handoff before source cleanup. A migration
            # journal interrupted immediately afterwards can replay this durable evidence.
            self.catalog.save(result, self.catalog.local_path(artifact_id), publish=True)
        copies = self.catalog.connection.execute(
            "SELECT location,path FROM storage_copies WHERE artifact_id=?", (artifact_id,)
        ).fetchall()
        remaining = [
            row
            for row in copies
            if not (target.node_id == self.node_id and row["location"] == target.location_id)
        ]
        if remove_source:
            removed = [
                row
                for row in remaining
                if source_location_id is None or row["location"] == source_location_id
            ]
            if removed:
                self.role_check()
            remaining_count = 1 + len(remaining) - len(removed)
            if remaining_count < artifact.retention.min_replicas:
                raise StorageError(
                    "replicas_required", "Source copy is required by the replica policy."
                )
            for row in removed:
                self._remove_copy(artifact, row["location"], Path(row["path"]))
            remaining = [row for row in remaining if row not in removed]
        result.replicas = [
            DestinationRef(node_id=self.node_id, location_id=row["location"]) for row in remaining
        ]
        result.state = "replicated" if result.replicas else "committed"
        self.catalog.save(
            result,
            self.catalog.local_path(artifact_id),
            publish=artifact.owner_node_id == self.node_id,
        )
        return result

    def _remove_copy(self, artifact: Artifact, location_id: str, path: Path) -> None:
        root = self.locations.verify(self.locations.get(location_id))
        if path.is_symlink() or path.resolve() != path or not path.is_relative_to(root):
            raise StorageError("unsafe_path", "Artifact copy escaped its registered location.")
        if path.exists():
            before = path.stat()
            with path.open("rb") as source:
                size, digest = _digest(source)
            after = path.stat()
            if (size, digest) != (artifact.size_bytes, artifact.sha256) or (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise StorageError(
                    "source_changed", "Source changed after destination commit; original retained."
                )
            path.unlink()
        with self.catalog.connection:
            self.catalog.connection.execute(
                "DELETE FROM storage_copies WHERE artifact_id=? AND location=?",
                (artifact.artifact_id, location_id),
            )

    def _delete_preflight(self, artifact: Artifact) -> None:
        self.role_check()
        if artifact.retention.protect:
            raise StorageError("artifact_protected", "Artifact is protected from deletion.")
        if artifact.owner_node_id != self.node_id:
            raise StorageError("remote_owner", "Delete this artifact through its owner.")
        for row in self.catalog.connection.execute(
            "SELECT location FROM storage_copies WHERE artifact_id=?", (artifact.artifact_id,)
        ).fetchall():
            self.locations.verify(self.locations.get(row[0]))

    def set_retention(
        self,
        artifact_id: str,
        retention: RetentionPolicy | dict[str, Any],
    ) -> Artifact:
        self.role_check()
        selected = RetentionPolicy.model_validate(retention)
        with self.catalog.transaction() as conn:
            artifact = self.catalog.get(artifact_id)
            if artifact is None or artifact.state == "deleted":
                raise StorageError("artifact_missing", "Artifact does not exist.", 404)
            if artifact.owner_node_id != self.node_id:
                raise StorageError("remote_owner", "Edit retention through the artifact's owner.")
            artifact.retention, artifact.updated_at = selected, time.time()
            self.catalog.save(artifact, self.catalog.local_path(artifact_id), connection=conn)
        return artifact

    def delete(self, artifact_id: str) -> bool:
        artifact = self.catalog.get(artifact_id)
        if artifact is None or artifact.state == "deleted":
            return False
        self._delete_preflight(artifact)
        rows = self.catalog.connection.execute(
            "SELECT location,path FROM storage_copies WHERE artifact_id=?", (artifact_id,)
        ).fetchall()
        for row in rows:
            root = self.locations.verify(self.locations.get(row[0]))
            path = Path(row[1])
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise StorageError(
                    "unsafe_path", "Stored artifact path escaped its registered location."
                )
            path.unlink(missing_ok=True)
        artifact.state, artifact.updated_at = "deleted", time.time()
        with self.catalog.transaction() as conn:
            self.catalog.save(artifact, connection=conn)
            conn.execute("DELETE FROM storage_copies WHERE artifact_id=?", (artifact_id,))
            outbound = conn.execute(
                "SELECT data FROM storage_outbound WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if outbound:
                job = json.loads(outbound[0])
                job["state"], job["updated_at"] = "cancelled", time.time()
                conn.execute(
                    "UPDATE storage_outbound SET data=? WHERE artifact_id=?",
                    (json.dumps(job), artifact_id),
                )
                if job.get("spool_token"):
                    self.locations.release(job["spool_token"], connection=conn)
        return True

    def delete_family(self, namespace: str, legacy_id: str) -> int:
        records = [
            self.catalog.get(a["artifact_id"]) for a in self.catalog.aliases(namespace, legacy_id)
        ]
        unique = {a.artifact_id: a for a in records if a and a.state != "deleted"}
        for artifact in unique.values():
            self._delete_preflight(artifact)
        return sum(self.delete(artifact_id) for artifact_id in unique)

    def list_transfers(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise StorageError("invalid_page", "Invalid transfer page.", 422)
        rows = self.catalog.connection.execute(
            "SELECT * FROM (SELECT 'receiver' AS direction,data FROM storage_transfers UNION ALL "
            "SELECT 'outbound' AS direction,data FROM storage_outbound) "
            "ORDER BY json_extract(data,'$.created_at') DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data"])
            if row["direction"] == "receiver":
                result.append(Transfer.model_validate(data).model_dump(mode="json"))
                continue
            artifact = self.catalog.get(data["artifact_id"])
            if artifact is None:
                continue
            finished = data["state"] == "replicated"
            result.append(
                {
                    "transfer_id": data["id"],
                    "artifact_id": artifact.artifact_id,
                    "location_id": artifact.location_id,
                    "offset": artifact.size_bytes if finished else 0,
                    "size_bytes": artifact.size_bytes,
                    "state": "committed"
                    if finished
                    else data["state"]
                    if data["state"] in {"failed", "cancelled"}
                    else "receiving",
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                    "error_code": data["error_code"],
                    "direction": "outbound",
                    "requested_destination": data["destination"],
                    "actual_owner_node_id": artifact.owner_node_id,
                }
            )
        return result

    def retry_pending(self, transfer_id: str | None = None, *, limit: int = 10) -> int:
        if not 1 <= limit <= 100:
            raise StorageError("invalid_page", "Retry batch must contain 1 to 100 jobs.", 422)
        if not self._retry_lock.acquire(blocking=False):
            return 0
        completed = 0
        try:
            rows = self.catalog.connection.execute(
                "SELECT data FROM storage_outbound "
                + (
                    "WHERE id=? "
                    if transfer_id
                    else "WHERE json_extract(data,'$.state') NOT IN ('replicated','cancelled') "
                    "AND COALESCE(json_extract(data,'$.error_code'),'')!='spool_expired' "
                    "AND COALESCE(json_extract(data,'$.next_attempt'),0)<=? "
                )
                + "ORDER BY json_extract(data,'$.next_attempt') LIMIT ?",
                (transfer_id, limit) if transfer_id else (time.time(), limit),
            ).fetchall()
            for row in rows:
                job = json.loads(row[0])
                if job["state"] in {"replicated", "cancelled"}:
                    continue
                if not transfer_id and (
                    job.get("error_code") == "spool_expired"
                    or time.time() < job.get("next_attempt", 0)
                ):
                    continue
                artifact = self.catalog.get(job["artifact_id"])
                if artifact is None:
                    continue
                job["attempts"] += 1
                try:
                    if job["spooled"] and time.time() > job["expires_at"]:
                        raise StorageError(
                            "spool_expired", "Spool age limit reached; inspect this artifact."
                        )
                    self.transfer_artifact(
                        artifact.artifact_id,
                        job["destination"],
                        remove_source=job["source_cleanup"] == "after_primary_commit",
                        source_location_id=job.get("source_location_id"),
                    )
                    job["state"], job["error_code"] = "replicated", None
                    if job.get("spool_token"):
                        self.locations.release(job["spool_token"])
                    completed += 1
                except (StorageError, RoleDisabledError) as exc:
                    job["state"], job["error_code"] = (
                        "failed",
                        getattr(exc, "code", "role_disabled"),
                    )
                    current = self.catalog.get(artifact.artifact_id) or artifact
                    if job["spooled"] and current.owner_node_id == self.node_id:
                        current.state = "failed"
                    current.updated_at = time.time()
                    self.catalog.save(current, self.catalog.local_path(current.artifact_id))
                job["updated_at"] = time.time()
                job["next_attempt"] = time.time() + min(60, 2 ** min(job["attempts"], 6))
                with self.catalog.connection:
                    self.catalog.connection.execute(
                        "UPDATE storage_outbound SET data=? WHERE id=?",
                        (json.dumps(job), job["id"]),
                    )
            return completed
        finally:
            self._retry_lock.release()

    def start(self, retry_interval: float = 5.0) -> None:
        """Start one bounded delivery worker explicitly during application startup."""
        if not 0.01 <= retry_interval <= 300:
            raise ValueError("retry interval must be between 0.01 and 300 seconds")
        with self._lifecycle_lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run,
                args=(retry_interval,),
                name="storage-delivery",
                daemon=True,
            )
            self._worker.start()

    def _run(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                self.retry_pending(limit=10)
            except (StorageError, OSError, ValueError):
                # Keep the durable journal; never log URLs, manifests or private source paths.
                pass
            self._wake.wait(interval)
            self._wake.clear()

    def prune(self, *, now: float | None = None, limit: int = 100) -> int:
        """Expire explicitly opted-in canonical artifacts, preserving active dependencies.

        min_replicas guards copies while content is retained; explicit canonical expiry
        ends that retention period. A spool or in-progress transfer never expires here.
        """
        moment = time.time() if now is None else now
        rows = self.catalog.connection.execute(
            "SELECT data FROM storage_artifacts WHERE owner=? "
            "AND state IN ('committed','replicated') "
            "AND json_extract(data,'$.retention.enabled')=1 "
            "AND json_extract(data,'$.retention.protect')=0 "
            "AND json_extract(data,'$.retention.max_age_seconds')>0 "
            "AND created + json_extract(data,'$.retention.max_age_seconds')<=? "
            "ORDER BY created LIMIT ?",
            (self.node_id, moment, max(1, min(limit, 1000))),
        ).fetchall()
        removed = 0
        for row in rows:
            artifact = Artifact.model_validate_json(row[0])
            retention = artifact.retention
            if (
                not retention.enabled
                or retention.protect
                or not retention.max_age_seconds
                or artifact.created_at + retention.max_age_seconds > moment
            ):
                continue
            pending = self.catalog.connection.execute(
                "SELECT 1 FROM storage_outbound WHERE artifact_id=? "
                "AND json_extract(data,'$.state')!='replicated'",
                (artifact.artifact_id,),
            ).fetchone()
            incoming = self.catalog.connection.execute(
                "SELECT 1 FROM storage_transfers WHERE artifact_id=? "
                "AND json_extract(data,'$.state') IN ('receiving','verifying')",
                (artifact.artifact_id,),
            ).fetchone()
            children = self.catalog.connection.execute(
                "SELECT 1 FROM storage_artifacts WHERE json_extract(data,'$.parent_id')=? "
                "AND state!='deleted'",
                (artifact.artifact_id,),
            ).fetchone()
            if pending or incoming or children:
                continue
            try:
                removed += self.delete(artifact.artifact_id)
            except (StorageError, OSError):
                continue
        return removed

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=6)
        if self._client is not None:
            self._client.close()
