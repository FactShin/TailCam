"""Explicit allowlisted fleet-management relay."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tailcam.management.audit import AuditLog
from tailcam.security.principal import RequestPrincipal
from tailcam.web import routes_node_v1 as node_routes
from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import (
    AuditEventInfo,
    NodeActionResponse,
    NodeCapabilitiesInfo,
    NodeHealthInfo,
)

router = APIRouter(prefix="/api/v1/fleet")

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "content-length",
    "content-encoding",
    "host",
}


@router.get("/nodes/{node_key}/capabilities", response_model=NodeCapabilitiesInfo)
async def node_capabilities(
    node_key: str,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(node_routes.get_principal),
) -> NodeCapabilitiesInfo:
    if node_key == "local":
        return node_routes.capabilities(principal)
    data = await _relay_json(ctx, node_key, request, "GET", "/api/v1/node/capabilities")
    return NodeCapabilitiesInfo.model_validate(data)


@router.get("/nodes/{node_key}/health", response_model=NodeHealthInfo)
async def node_health(
    node_key: str,
    request: Request,
    ctx: AppContext = Depends(get_context),
) -> NodeHealthInfo:
    if node_key == "local":
        return node_routes.health(ctx)
    data = await _relay_json(ctx, node_key, request, "GET", "/api/v1/node/health")
    return NodeHealthInfo.model_validate(data)


@router.get("/nodes/{node_key}/audit", response_model=list[AuditEventInfo])
async def node_audit(
    node_key: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(node_routes.require_admin),
) -> list[AuditEventInfo]:
    if node_key == "local":
        return node_routes.audit(limit=limit, offset=offset, ctx=ctx, _=principal)
    data = await _relay_json(
        ctx,
        node_key,
        request,
        "GET",
        "/api/v1/node/audit",
        params={"limit": limit, "offset": offset},
    )
    return [AuditEventInfo.model_validate(item) for item in data]


@router.post("/nodes/{node_key}/actions/reload", response_model=NodeActionResponse)
async def node_reload(
    node_key: str,
    request: Request,
    ctx: AppContext = Depends(get_context),
    principal: RequestPrincipal = Depends(node_routes.require_admin),
) -> NodeActionResponse:
    if node_key == "local":
        return await node_routes.reload_current_node(ctx, principal)

    audit_log = AuditLog(ctx.store)
    try:
        data = await _relay_json(ctx, node_key, request, "POST", "/api/v1/node/actions/reload")
        response = NodeActionResponse.model_validate(data)
    except HTTPException as exc:
        audit_log.record(
            actor=principal.actor,
            source=principal.source,
            action="fleet.node.reload",
            target=node_key,
            result="failure",
            detail=str(exc.detail),
            metadata={"status_code": exc.status_code},
        )
        raise

    audit_log.record(
        actor=principal.actor,
        source=principal.source,
        action="fleet.node.reload",
        target=node_key,
        result=response.result,
        detail=response.detail,
        metadata={"remote_action": response.action},
    )
    return response


async def _relay_json(
    ctx: AppContext,
    node_key: str,
    request: Request,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:
    base = ctx.cluster.peer_base(node_key)
    if base is None:
        await ctx.cluster.peers()
        base = ctx.cluster.peer_base(node_key)
    if base is None:
        raise HTTPException(status_code=404, detail="unknown node")

    try:
        response = await ctx.cluster.client().request(
            method,
            f"{base}{path}",
            params=params,
            headers=_relay_headers(request),
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"peer unreachable: {exc}") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=_response_detail(response))
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="peer returned invalid JSON") from exc


def _relay_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP or lowered.startswith("tailscale-"):
            continue
        headers[key] = value
    return headers


def _response_detail(response: httpx.Response) -> Any:
    try:
        data = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(data, dict) and "detail" in data:
        return data["detail"]
    return data
