"""Read-only timelapse placement and execution-node configuration checks.

The capabilities endpoint always describes its own node, so checking a storage
node cannot recursively follow that node's storage configuration. No model is
loaded, downloaded, or called, and configured AI is never reported as healthy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import httpx

from tailcam.web.schemas import (
    EngineInfo,
    PostprocessInfo,
    PrinterAnalyzerInfo,
    TimelapseCapabilities,
    TimelapsePreflight,
)

if TYPE_CHECKING:
    from tailcam.web.context import AppContext


def display_endpoint(value: str) -> str:
    """Display a configured endpoint without URL credentials or query secrets."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "Invalid endpoint"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "Invalid endpoint"


def local_capabilities(ctx: AppContext) -> TimelapseCapabilities:
    from tailcam.timelapse.ffmpeg import ffmpeg_source
    from tailcam.timelapse.rife import rife_available

    config = ctx.printer_analyzer.config
    tl = ctx.config.timelapse
    ff_source = ffmpeg_source()
    storage = ctx.has_role("storage")
    analysis = ctx.has_role("analysis")
    rife_ok = analysis and storage and rife_available(tl.rife_path)
    return TimelapseCapabilities(
        host=ctx.local_host,
        capture_enabled=storage,
        capture_reason="" if storage else "Storage role is disabled on this device.",
        printer_analyzer=PrinterAnalyzerInfo(
            enabled=config.enabled and analysis,
            endpoint=display_endpoint(config.base_url),
            model=config.model,
        ),
        postprocess=PostprocessInfo(
            # Both smoothing paths need FFmpeg to produce the final video.
            available=storage and ff_source != "missing",
            default_engine=tl.smooth_engine,
            default_target_fps=tl.smooth_target_fps,
            engines=[
                EngineInfo(id="ffmpeg", label="FFmpeg",
                           available=storage and ff_source != "missing",
                           source=ff_source),
                EngineInfo(id="rife", label="RIFE", available=rife_ok,
                           source="system" if rife_ok else "missing"),
            ],
        ),
    )


async def camera_preflight(ctx: AppContext) -> TimelapsePreflight:
    """Inspect the source node's route without starting work or changing it."""
    configured = ctx.capture.configured_node
    if configured.startswith(("http://", "https://")):
        await ctx.cluster.peers()
    display_storage = display_endpoint(configured) if "://" in configured else configured
    target = ctx.capture.target()
    if target is None:
        return TimelapsePreflight(
            camera_host=ctx.local_host,
            capture_host=ctx.local_host,
            configured_storage=display_storage,
            route_status="unknown" if configured else "local",
            message=(
                "Storage destination unresolved or in connection backoff. Capture currently "
                "falls back to this camera node; the actual destination is confirmed on start."
                if configured else "Capture and storage run on the camera node."
            ),
            capabilities=local_capabilities(ctx),
        )

    key, base = target
    peer = next((p for p in ctx.cluster.cached_peers() if p.base_url == base), None)
    result = TimelapsePreflight(
        camera_host=ctx.local_host,
        capture_host=peer.host if peer else (display_endpoint(base) or key),
        configured_storage=display_storage,
        route_status="unknown",
    )
    try:
        response = await ctx.cluster.client().get(
            f"{base}/api/timelapse-capabilities", timeout=3.0, follow_redirects=False
        )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        result.route_status = "unreachable"
        result.message = (
            "Cannot connect to the storage node. A connection failure on start may use "
            "the camera node's local storage and capabilities."
        )
        return result
    except (httpx.HTTPError, httpx.InvalidURL):
        result.message = "Storage capability check did not complete; reachability is unknown."
        return result
    if response.status_code != 200:
        result.message = (
            f"Storage node returned HTTP {response.status_code} to the capability check. "
            "Its capture capabilities are unknown."
        )
        return result
    try:
        capabilities = TimelapseCapabilities.model_validate(response.json())
    except (ValueError, TypeError):
        result.message = "Storage node returned an unsupported capability response."
        return result
    # An older or independently deployed peer may not redact its own endpoint.
    capabilities.printer_analyzer.endpoint = display_endpoint(
        capabilities.printer_analyzer.endpoint
    )
    result.capture_host = capabilities.host
    result.capabilities = capabilities
    result.route_status = "reachable"
    result.message = "Storage node responded to the capability check."
    return result
