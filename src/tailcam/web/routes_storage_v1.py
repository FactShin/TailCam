"""Revisioned storage policy, stable artifacts and resumable transfers."""

from __future__ import annotations

import re
from typing import Literal, TypeVar
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from tailcam.management.audit import AuditLog
from tailcam.security.principal import RequestPrincipal, TailCamRole
from tailcam.storage.models import (
    MAX_CHUNK_BYTES,
    MAX_INTEGER,
    Artifact,
    ArtifactState,
    ContentKind,
    Contract,
    DestinationRef,
    RetentionPolicy,
    StorageError,
    StoragePolicy,
    TransferManifest,
)
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.routes_node_v1 import get_principal, require_admin

router = APIRouter(prefix="/api/v1")
Model = TypeVar("Model", bound=BaseModel)


def require_viewer(principal: RequestPrincipal = Depends(get_principal)) -> RequestPrincipal:
    if not principal.verified or TailCamRole.VIEWER not in principal.roles:
        raise HTTPException(403, "viewer role required")
    return principal


def require_operator(principal: RequestPrincipal = Depends(get_principal)) -> RequestPrincipal:
    if not principal.verified or TailCamRole.OPERATOR not in principal.roles:
        raise HTTPException(403, "operator role required")
    return principal


async def _bounded_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if not 0 <= int(length) <= maximum:
                raise HTTPException(413, "Request exceeds the storage protocol limit")
        except ValueError:
            raise HTTPException(400, "Invalid content length") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise HTTPException(413, "Request exceeds the storage protocol limit")
        body.extend(chunk)
    return bytes(body)


async def _body(request: Request, model: type[Model]) -> Model:
    raw = await _bounded_body(request, 1024 * 1024)
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        # Pydantic's default error payload echoes input values; policies can
        # include private paths and transfer metadata, so return a safe error.
        raise HTTPException(
            422, "Invalid storage request; check the documented fields and limits"
        ) from None


class PolicyUpdate(Contract):
    expected_revision: int = Field(ge=1)
    policy: StoragePolicy


class LocationCreate(Contract):
    path: str = Field(min_length=1, max_length=4096)
    label: str = Field(default="", max_length=128)
    quota_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    reserve_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    make_default: bool = True


class LocationUpdate(Contract):
    label: str | None = Field(default=None, max_length=128)
    quota_bytes: int | None = Field(default=None, ge=0, le=MAX_INTEGER)
    reserve_bytes: int | None = Field(default=None, ge=0, le=MAX_INTEGER)
    make_default: bool | None = None


class AdmissionRequest(Contract):
    origin_node_id: UUID | None = None
    camera_id: str = Field(default="", max_length=256)
    kind: ContentKind
    size_bytes: int = Field(default=0, ge=0, le=MAX_INTEGER)
    requires_workspace: bool = False


class MigrationPreviewRequest(Contract):
    source_location_id: UUID
    destination: DestinationRef
    content_kinds: list[ContentKind] | None = Field(default=None, max_length=11)
    remove_source: bool = False


class ArtifactTransfer(Contract):
    destination: DestinationRef
    remove_source: bool = False


class MigrationStart(Contract):
    preview_id: UUID


def _policy(ctx: AppContext) -> dict:
    return {
        "enabled": ctx.storage_service.enabled,
        "source_node_id": ctx.node_id,
        "policy": ctx.storage_service.get_policy().model_dump(mode="json"),
    }


@router.get("/storage/policy")
def get_policy(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return _policy(ctx)


@router.patch("/storage/policy")
async def set_policy(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    update = await _body(request, PolicyUpdate)
    await anyio.to_thread.run_sync(
        lambda: ctx.storage_service.set_policy(
            update.policy.model_dump(mode="json"),
            expected_revision=update.expected_revision,
        )
    )
    AuditLog(ctx.store).record(
        actor=principal.actor,
        source=principal.source,
        action="storage.policy",
        target=ctx.node_id,
        result="success",
        detail="storage policy saved for new work",
        metadata={"revision": ctx.storage_service.get_policy().revision},
    )
    return _policy(ctx)


@router.get("/storage/locations")
def locations(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return {"items": ctx.storage_service.list_locations()}


@router.get("/storage/destinations")
async def destinations(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    await ctx.cluster.peers()
    return {"items": await anyio.to_thread.run_sync(ctx.storage_peers.destinations)}


@router.post("/storage/peer-identities/{node_id}/reset")
def reset_peer_identity(
    node_id: UUID,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    ctx.storage_peers.reset_identity(str(node_id))
    AuditLog(ctx.store).record(
        actor=principal.actor,
        source=principal.source,
        action="storage.peer_identity.reset",
        target=str(node_id),
        result="success",
        detail="stored peer address binding cleared",
        metadata={},
    )
    return {"ok": True}


@router.post("/storage/locations")
async def register_location(
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_admin),
):
    body = await _body(request, LocationCreate)
    return await anyio.to_thread.run_sync(
        lambda: ctx.storage_service.register_location(
            body.path,
            label=body.label,
            quota_bytes=body.quota_bytes,
            reserve_bytes=body.reserve_bytes,
            make_default=body.make_default,
        )
    )


@router.patch("/storage/locations/{location_id}")
async def update_location(
    location_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_admin),
):
    body = await _body(request, LocationUpdate)
    return await anyio.to_thread.run_sync(
        lambda: ctx.storage_service.update_location(
            str(location_id),
            **body.model_dump(exclude_none=True),
        )
    )


@router.post("/storage/admission")
async def preview_admission(
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
):
    body = await _body(request, AdmissionRequest)
    try:
        admission = await anyio.to_thread.run_sync(
            lambda: ctx.storage_service.admit(
                body.kind,
                origin_node_id=str(body.origin_node_id) if body.origin_node_id else None,
                camera_id=body.camera_id,
                expected_bytes=body.size_bytes,
                requires_workspace=body.requires_workspace,
            )
        )
        return {
            **admission.model_dump(mode="json"),
            "code": "available",
            "detail": "Destination is available",
        }
    except StorageError as exc:
        return {"allowed": False, "code": exc.code, "detail": exc.detail}


def _page(items: list, offset: int, limit: int) -> dict:
    return {"items": items, "next_cursor": str(offset + limit) if len(items) == limit else None}


@router.get("/artifacts/changes")
def artifact_changes(
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
):
    return ctx.storage_service.catalog.changes(after, limit)


@router.get("/artifacts")
@router.get("/fleet/artifacts")
async def artifacts(
    request: Request,
    kind: ContentKind | None = None,
    origin_node_id: UUID | None = None,
    camera_id: str | None = Query(None, max_length=256),
    state: ArtifactState | None = None,
    cursor: int = Query(0, ge=0, le=MAX_INTEGER),
    limit: int = Query(50, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
):
    fleet = "/fleet/" in request.url.path
    if fleet:
        await ctx.cluster.peers()
        await anyio.to_thread.run_sync(ctx.storage_peers.refresh_catalog)
    items = ctx.storage_service.catalog.list(
        kind=kind,
        origin_node_id=str(origin_node_id) if origin_node_id else None,
        camera_id=camera_id,
        state=state,
        limit=limit,
        offset=cursor,
        owner_node_id=None if fleet else ctx.node_id,
    )
    return _page(items, cursor, limit)


@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id: UUID, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    item = ctx.storage_service.catalog.get(str(artifact_id))
    if item is None:
        raise HTTPException(404, "Artifact not found")
    return item


def artifact_response(item: Artifact, request: Request, ctx: AppContext):
    if item.state == "deleted":
        raise HTTPException(410, "Artifact was deleted")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    inline = item.mime_type in {"image/jpeg", "image/png", "image/webp", "video/mp4"}
    headers["Content-Disposition"] = "inline" if inline else "attachment"
    media_type = item.mime_type if inline else "application/octet-stream"
    try:
        path = ctx.storage_service.resolve(item.artifact_id)
    except StorageError as exc:
        if item.owner_node_id == ctx.node_id:
            raise exc
    else:
        return FileResponse(path, media_type=media_type, headers=headers)
    if request.headers.get("x-tailcam-artifact-hop"):
        raise HTTPException(409, "Artifact ownership changed; refresh its catalog entry")
    base = ctx.storage_peers.resolve(item.owner_node_id)
    if base is None:
        raise HTTPException(503, "Artifact owner is unavailable; its catalog entry is retained")
    requested_range = request.headers.get("range")
    if requested_range and not re.fullmatch(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)", requested_range):
        raise HTTPException(416, "Only one byte range is supported for remote content")
    requested_bounds = None
    if requested_range:
        left, right = requested_range.removeprefix("bytes=").split("-")
        if len(left) > 20 or len(right) > 20 or item.size_bytes == 0:
            raise HTTPException(416, "Requested range is unavailable")
        if left:
            first = int(left)
            last = min(int(right), item.size_bytes - 1) if right else item.size_bytes - 1
        else:
            first, last = max(0, item.size_bytes - int(right)), item.size_bytes - 1
        if not 0 <= first <= last < item.size_bytes:
            raise HTTPException(416, "Requested range is unavailable")
        requested_bounds = (first, last)
    client = httpx.Client(timeout=15, trust_env=False, follow_redirects=False)
    response = None
    try:
        outgoing = {"X-TailCam-Artifact-Hop": "1", "Accept-Encoding": "identity"}
        if "range" in request.headers:
            outgoing["Range"] = request.headers["range"]
        response = client.send(
            client.build_request(
                "GET",
                base + f"/api/v1/artifacts/{item.artifact_id}/content",
                headers=outgoing,
            ),
            stream=True,
        )
        if response.status_code not in (200, 206):
            response.close()
            raise HTTPException(503, "Artifact owner could not serve this content")
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Encoded artifact response")
        expected = item.size_bytes
        if response.status_code == 206:
            match = re.fullmatch(
                r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", response.headers.get("content-range", "")
            )
            if match is None or not requested_range:
                raise ValueError("Invalid artifact range")
            first, last, total = map(int, match.groups())
            if (
                not 0 <= first <= last < total
                or total != item.size_bytes
                or (first, last) != requested_bounds
            ):
                raise ValueError("Invalid artifact range")
            expected = last - first + 1
            headers["Content-Range"] = response.headers["content-range"]
        elif "content-range" in response.headers:
            raise ValueError("Unexpected artifact range")
        length = response.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) != expected):
            raise ValueError("Invalid artifact length")
        headers["Content-Length"] = str(expected)
        headers["Accept-Ranges"] = "bytes"
    except (httpx.HTTPError, HTTPException, ValueError):
        if response is not None:
            response.close()
        client.close()
        raise HTTPException(503, "Artifact owner could not serve this content") from None

    def chunks():
        received = 0
        try:
            for chunk in response.iter_bytes(65536):
                received += len(chunk)
                if received > expected:
                    raise OSError("Artifact response exceeded its declared length")
                yield chunk
            if received != expected:
                raise OSError("Artifact response ended before its declared length")
        finally:
            response.close()
            client.close()

    return StreamingResponse(
        chunks(), status_code=response.status_code, media_type=media_type, headers=headers
    )


@router.get("/artifacts/{artifact_id}/content")
@router.get("/artifacts/{artifact_id}/file", include_in_schema=False)
def content(
    artifact_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
):
    item = ctx.storage_service.catalog.get(str(artifact_id))
    if item is None:
        raise HTTPException(404, "Artifact not found")
    return artifact_response(item, request, ctx)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(
    artifact_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_admin),
):
    return {"deleted": ctx.storage_service.delete(str(artifact_id))}


@router.patch("/artifacts/{artifact_id}/retention")
async def artifact_retention(
    artifact_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, RetentionPolicy)
    result = ctx.storage_service.set_retention(str(artifact_id), body)
    AuditLog(ctx.store).record(
        actor=principal.actor,
        source=principal.source,
        action="storage.retention",
        target=str(artifact_id),
        result="success",
        detail="artifact retention updated",
        metadata=body.model_dump(),
    )
    return result


@router.post("/artifacts/{artifact_id}/transfer")
async def transfer_artifact(
    artifact_id: UUID,
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    ctx.require_role("storage")
    body = await _body(request, ArtifactTransfer)
    # A forwarded handoff is only valid at the current byte owner. This also
    # prevents stale ownership metadata from creating recursive peer calls.
    item = ctx.storage_service.catalog.get(str(artifact_id))
    if item is None or item.owner_node_id != ctx.node_id:
        raise HTTPException(409, "Transfer must be requested from the current artifact owner")
    return await anyio.to_thread.run_sync(
        lambda: ctx.storage_service.transfer_artifact(
            str(artifact_id),
            body.destination,
            remove_source=body.remove_source,
        )
    )


@router.get("/transfers")
def transfers(
    cursor: int = Query(0, ge=0, le=MAX_INTEGER),
    limit: int = Query(50, ge=1, le=100),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_viewer),
):
    return _page(ctx.storage_service.list_transfers(limit=limit, offset=cursor), cursor, limit)


@router.post("/transfers")
async def begin_transfer(
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    manifest = await _body(request, TransferManifest)
    ctx.require_role("storage")
    return await anyio.to_thread.run_sync(lambda: ctx.storage_service.transfers.begin(manifest))


@router.get("/transfers/{transfer_id}")
def transfer_status(
    transfer_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    return ctx.storage_service.transfers.status(str(transfer_id))


@router.put("/transfers/{transfer_id}/chunks")
async def transfer_chunk(
    transfer_id: UUID,
    request: Request,
    offset: int = Query(ge=0, le=MAX_INTEGER),
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    ctx.require_role("storage")
    chunk = await _bounded_body(request, MAX_CHUNK_BYTES)
    digest = request.headers.get("x-chunk-sha256", "")
    return await anyio.to_thread.run_sync(
        lambda: ctx.storage_service.transfers.append(
            str(transfer_id),
            offset,
            chunk,
            digest,
        )
    )


@router.post("/transfers/{transfer_id}/commit")
def commit_transfer(
    transfer_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    ctx.require_role("storage")
    return ctx.storage_service.transfers.commit(str(transfer_id))


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    ctx.storage_service.transfers.cancel(str(transfer_id))
    return {"ok": True}


@router.post("/transfers/{transfer_id}/retry")
def retry_transfer(
    transfer_id: UUID,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_operator),
):
    return ctx.storage_service.retry_pending(str(transfer_id))


@router.post("/storage/migrations/preview")
async def migration_preview(
    request: Request,
    ctx: AppContext = Depends(get_context),
    _=Depends(require_admin),
):
    body = await _body(request, MigrationPreviewRequest)
    return await anyio.to_thread.run_sync(
        lambda: ctx.storage_migration.preview(
            str(body.source_location_id),
            body.destination,
            kinds=[str(kind) for kind in body.content_kinds]
            if body.content_kinds is not None
            else None,
            remove_source=body.remove_source,
        )
    )


@router.post("/storage/migrations")
async def migration_start(
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
):
    body = await _body(request, MigrationStart)
    job = ctx.storage_migration.start(str(body.preview_id))
    AuditLog(ctx.store).record(
        actor=principal.actor,
        source=principal.source,
        action="storage.migrate",
        target=job["migration_id"],
        result="success",
        detail="reviewed media migration started",
        metadata={"move": job["remove_source"]},
    )
    return job


@router.get("/storage/migrations")
def migrations(ctx: AppContext = Depends(get_context), _=Depends(require_viewer)):
    return {"items": ctx.storage_migration.list()}


@router.get("/storage/migrations/{migration_id}")
def migration(
    migration_id: UUID, ctx: AppContext = Depends(get_context), _=Depends(require_viewer)
):
    return ctx.storage_migration.get(str(migration_id))


@router.post("/storage/migrations/{migration_id}/{action}")
def migration_action(
    migration_id: UUID,
    action: Literal["cancel", "resume"],
    ctx: AppContext = Depends(get_context),
    _=Depends(require_admin),
):
    return getattr(ctx.storage_migration, action)(str(migration_id))
