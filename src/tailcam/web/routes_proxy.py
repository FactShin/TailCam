"""Generic reverse proxy to peer TailCam nodes.

One route forwards any request for a remote camera's resources — MJPEG streams,
snapshots, media files, and control actions (PATCH / snapshot / record) — to the
node that owns it, so the browser only ever talks to the node it opened.
"""

from __future__ import annotations

from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from tailcam.web.context import AppContext
from tailcam.web.deps import get_context
from tailcam.web.schemas import TimelapseInfo

router = APIRouter()

# Headers we must not copy verbatim across the proxy hop.
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
    # The browser's Origin/Referer describe *this* node; forwarded to the peer
    # they trip its CSRF guard whenever the dashboard was opened by IP (the
    # peer only trusts an IP-literal origin equal to its own Host). This node
    # already enforced the cross-origin check on the way in.
    "origin",
    "referer",
}


def _forward_request_headers(request: Request) -> dict[str, str]:
    return {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and not k.lower().startswith("tailscale-")
    }


def _management_path(path: str) -> bool:
    normalized = path.lstrip("/")
    return normalized.startswith("api/v1/node") or normalized.startswith("api/v1/fleet")


def _validate_proxy_path(path: str) -> None:
    # Validate the path the peer will interpret, including another decoding
    # pass at its HTTP boundary. Never let normalization change the endpoint
    # after the management guard has run. Device paths may contain empty
    # segments (e.g. stream//dev/video0.mjpg), but never dot segments.
    decoded = unquote(path)
    if (
        any(segment in {".", ".."} for segment in decoded.split("/"))
        or any(char in decoded for char in ("\\", "%", "?", "#"))
        or any(ord(char) < 32 for char in decoded)
    ):
        raise HTTPException(status_code=400, detail="invalid proxy path")
    if _management_path(decoded):
        raise HTTPException(status_code=403, detail="management API cannot use generic proxy")
    # The generic proxy does not preserve the caller's authenticated role.
    # MCP must be reached directly, and nested proxies must not turn this
    # check into a multi-hop authorization bypass.
    root = decoded.lstrip("/").split("/", 1)[0]
    if root in {"mcp", "proxy"}:
        raise HTTPException(status_code=403, detail="endpoint cannot use generic proxy")


@router.api_route(
    "/proxy/{key}/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "PUT"]
)
async def proxy(
    key: str, path: str, request: Request, ctx: AppContext = Depends(get_context)
) -> Response:
    _validate_proxy_path(path)

    await ctx.cluster.peers()  # ensure discovery has run at least once
    base = ctx.cluster.peer_base(key)
    if base is None:
        raise HTTPException(status_code=404, detail="unknown host")

    client = ctx.cluster.client()
    body = await request.body()
    upstream = client.build_request(
        request.method,
        f"{base}/{path}",
        params=request.query_params,
        content=body or None,
        headers=_forward_request_headers(request),
    )
    try:
        resp = await client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"peer unreachable: {exc}") from exc

    if (
        resp.status_code == 200 and request.method == "POST"
        and path.startswith("api/cameras/") and path.endswith("/timelapse/start")
    ):
        # This response was authored for the source dashboard. Rebase its
        # artifact route for the current dashboard, which might itself be the
        # storage owner. Otherwise its next action can point back at itself as
        # an unknown peer instead of the timelapse that just started.
        try:
            await resp.aread()
            info = TimelapseInfo.model_validate(resp.json())
            if not info.host.strip() or info.id < 1:
                raise ValueError("missing storage owner")
            data = info.model_dump()
            owner, prefix = ctx.cluster.media_owner_reference(data["host"])
            if prefix is None:
                raise ValueError("unknown storage owner")
            data["host"], data["proxy_prefix"] = owner, prefix
            return JSONResponse(data)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=502,
                detail="Timelapse may have started, but its storage owner could not be resolved. "
                "Check the storage node before retrying.",
            ) from exc
        finally:
            await resp.aclose()

    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
        background=BackgroundTask(resp.aclose),
    )
