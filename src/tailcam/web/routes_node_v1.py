"""Versioned node-management API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tailcam.management.audit import AuditLog
from tailcam.management.capabilities import NodeCapabilityService, NodeCapabilitySet
from tailcam.management.health import NodeHealthService, NodeHealthSnapshot
from tailcam.persistence.models import AuditRecord
from tailcam.security.principal import RequestPrincipal, TailCamRole, principal_from_request
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    AuditEventInfo,
    NodeActionResponse,
    NodeCapabilitiesInfo,
    NodeHealthInfo,
    NodeIssueInfo,
    PrincipalInfo,
)

router = APIRouter(prefix="/api/v1/node")


def get_principal(request: Request) -> RequestPrincipal:
    return principal_from_request(request)


def require_admin(
    principal: RequestPrincipal = Depends(get_principal),
) -> RequestPrincipal:
    if not principal.verified or TailCamRole.ADMIN not in principal.roles:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


@router.get("/capabilities", response_model=NodeCapabilitiesInfo)
def capabilities(
    principal: RequestPrincipal = Depends(get_principal),
) -> NodeCapabilitiesInfo:
    return _capabilities_info(NodeCapabilityService().snapshot(principal), principal)


@router.get("/health", response_model=NodeHealthInfo)
def health(ctx: AppContext = Depends(get_context)) -> NodeHealthInfo:
    return _health_info(NodeHealthService(ctx).snapshot())


@router.get("/audit", response_model=list[AuditEventInfo])
def audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AppContext = Depends(get_context),
    _: RequestPrincipal = Depends(require_admin),
) -> list[AuditEventInfo]:
    return [_audit_info(record) for record in AuditLog(ctx.store).list(limit, offset)]


@router.post("/actions/reload", response_model=NodeActionResponse)
async def reload(
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(require_admin),
) -> NodeActionResponse:
    return await reload_current_node(ctx, principal)


async def reload_current_node(
    ctx: AppContext,
    principal: RequestPrincipal,
) -> NodeActionResponse:
    audit_log = AuditLog(ctx.store)
    action = "node.reload"
    try:
        for cam in ctx.manager.list():
            ctx.manager.restart(cam.descriptor.id)
        ctx.manager.discover()
        ctx.manager.start_all()
        health_snapshot = NodeHealthService(ctx).snapshot()
    except Exception as exc:
        audit_log.record(
            actor=principal.actor,
            source=principal.source,
            action=action,
            target=ctx.local_host,
            result="failure",
            detail=str(exc),
            metadata={},
        )
        raise HTTPException(status_code=500, detail="node reload failed") from exc

    audit_log.record(
        actor=principal.actor,
        source=principal.source,
        action=action,
        target=ctx.local_host,
        result="success",
        detail="capture workers reloaded",
        metadata={"camera_count": len(ctx.manager.list())},
    )
    return NodeActionResponse(
        action=action,
        target=ctx.local_host,
        result="success",
        detail="capture workers reloaded",
        health=_health_info(health_snapshot),
    )


def _principal_info(principal: RequestPrincipal) -> PrincipalInfo:
    return PrincipalInfo(
        actor=principal.actor,
        display_name=principal.display_name,
        source=principal.source,
        verified=principal.verified,
        roles=sorted(role.value for role in principal.roles),
    )


def _capabilities_info(
    capabilities: NodeCapabilitySet,
    principal: RequestPrincipal,
) -> NodeCapabilitiesInfo:
    return NodeCapabilitiesInfo(
        api_version=capabilities.api_version,
        capabilities=sorted(capabilities.capabilities),
        actions=sorted(capabilities.actions),
        principal=_principal_info(principal),
    )


def _health_info(snapshot: NodeHealthSnapshot) -> NodeHealthInfo:
    return NodeHealthInfo(
        host=snapshot.host,
        version=snapshot.version,
        platform=snapshot.platform,
        python_version=snapshot.python_version,
        uptime_seconds=snapshot.uptime_seconds,
        tailscale_installed=snapshot.tailscale_installed,
        tailscale_running=snapshot.tailscale_running,
        tailscale_served=snapshot.tailscale_served,
        access_url=snapshot.access_url,
        local_url=snapshot.local_url,
        camera_total=snapshot.camera_total,
        camera_online=snapshot.camera_online,
        camera_offline=snapshot.camera_offline,
        camera_degraded=snapshot.camera_degraded,
        camera_recording=snapshot.camera_recording,
        media_bytes=snapshot.media_bytes,
        timelapse_bytes=snapshot.timelapse_bytes,
        update_current=snapshot.update_current,
        update_latest=snapshot.update_latest,
        update_available=snapshot.update_available,
        ai_enabled=snapshot.ai_enabled,
        ai_reachable=snapshot.ai_reachable,
        ai_model=snapshot.ai_model,
        ai_model_present=snapshot.ai_model_present,
        issues=[
            NodeIssueInfo(
                code=issue.code,
                severity=issue.severity,
                summary=issue.summary,
                detail=issue.detail,
            )
            for issue in snapshot.issues
        ],
    )


def _audit_info(record: AuditRecord) -> AuditEventInfo:
    try:
        metadata = json.loads(record.metadata_json)
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return AuditEventInfo(
        id=record.id or 0,
        created_ts=record.created_ts,
        actor=record.actor,
        source=record.source,
        action=record.action,
        target=record.target,
        result=record.result,
        detail=record.detail,
        metadata=metadata,
    )
