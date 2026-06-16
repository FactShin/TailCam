"""Generic reverse proxy to peer TailCam nodes.

One route forwards any request for a remote camera's resources — MJPEG streams,
snapshots, media files, and control actions (PATCH / snapshot / record) — to the
node that owns it, so the browser only ever talks to the node it opened.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from tailcam.web.context import AppContext
from tailcam.web.deps import get_context

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


@router.api_route(
    "/proxy/{key}/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "PUT"]
)
async def proxy(
    key: str, path: str, request: Request, ctx: AppContext = Depends(get_context)
) -> StreamingResponse:
    if _management_path(path):
        raise HTTPException(status_code=403, detail="management API cannot use generic proxy")

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

    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
        background=BackgroundTask(resp.aclose),
    )
