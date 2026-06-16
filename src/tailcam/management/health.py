"""Node health snapshots for TailCam management APIs."""

from __future__ import annotations

import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from tailcam import __version__, update
from tailcam.camera.worker import CameraStatus

IssueSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class NodeIssue:
    code: str
    severity: IssueSeverity
    summary: str
    detail: str | None = None


@dataclass(frozen=True)
class NodeHealthSnapshot:
    host: str
    version: str
    platform: str
    python_version: str
    uptime_seconds: float
    tailscale_installed: bool
    tailscale_running: bool
    tailscale_served: bool
    access_url: str
    local_url: str
    camera_total: int
    camera_online: int
    camera_offline: int
    camera_degraded: int
    camera_recording: int
    media_bytes: int
    timelapse_bytes: int
    update_current: str
    update_latest: str | None
    update_available: bool
    ai_enabled: bool
    ai_reachable: bool
    ai_model: str
    ai_model_present: bool
    issues: tuple[NodeIssue, ...]


class NodeHealthService:
    def __init__(
        self,
        context: Any,
        *,
        update_checker: Callable[..., tuple[str, str | None, bool]] = update.update_available,
        started_monotonic: float | None = None,
    ) -> None:
        self._context = context
        self._update_checker = update_checker
        self._started_monotonic = (
            time.monotonic() if started_monotonic is None else started_monotonic
        )

    def snapshot(self) -> NodeHealthSnapshot:
        ctx = self._context
        issues: list[NodeIssue] = []
        server_port = ctx.config.server.port
        tailscale = ctx.tailscale.status()
        cameras = ctx.manager.list()
        camera_statuses = [ctx.manager.status(cam.descriptor.id) for cam in cameras]
        camera_online = sum(1 for status in camera_statuses if status == CameraStatus.ONLINE)
        camera_degraded = sum(1 for status in camera_statuses if status == CameraStatus.DEGRADED)
        camera_offline = sum(1 for status in camera_statuses if status == CameraStatus.OFFLINE)
        camera_recording = sum(
            1 for cam in cameras if ctx.recorder.is_recording(cam.descriptor.id)
        )

        if camera_offline:
            issues.append(
                NodeIssue(
                    code="camera.offline",
                    severity="warning",
                    summary=f"{camera_offline} camera(s) offline",
                )
            )
        if camera_degraded:
            issues.append(
                NodeIssue(
                    code="camera.degraded",
                    severity="warning",
                    summary=f"{camera_degraded} camera(s) degraded",
                )
            )

        if tailscale.installed and not tailscale.running:
            issues.append(
                NodeIssue(
                    code="tailscale.stopped",
                    severity="warning",
                    summary="Tailscale is installed but not running",
                )
            )
        elif not tailscale.installed:
            issues.append(
                NodeIssue(
                    code="tailscale.missing",
                    severity="info",
                    summary="Tailscale is not installed",
                )
            )

        ai_reachable, ai_model_present_name = ctx.analyzer.health()
        ai_model_present = bool(ai_model_present_name)
        if ctx.config.ai.enabled and not ai_reachable:
            issues.append(
                NodeIssue(
                    code="ai.unreachable",
                    severity="warning",
                    summary="Configured Ollama endpoint is not reachable",
                    detail=ctx.config.ai.base_url,
                )
            )
        elif ctx.config.ai.enabled and not ai_model_present:
            issues.append(
                NodeIssue(
                    code="ai.model_missing",
                    severity="warning",
                    summary=f"Configured Ollama model is not available: {ctx.config.ai.model}",
                )
            )

        current, latest, newer = self._check_update()
        if newer:
            issues.append(
                NodeIssue(
                    code="update.available",
                    severity="info",
                    summary=f"TailCam {latest} is available",
                )
            )

        return NodeHealthSnapshot(
            host=ctx.local_host,
            version=__version__,
            platform=f"{platform.system()} {platform.machine()}".strip(),
            python_version=sys.version.split()[0],
            uptime_seconds=max(0.0, time.monotonic() - self._started_monotonic),
            tailscale_installed=tailscale.installed,
            tailscale_running=tailscale.running,
            tailscale_served=bool(ctx.served),
            access_url=ctx.tailscale.access_url(
                server_port, ctx.served, ctx.config.tailscale.serve_port
            ),
            local_url=f"http://localhost:{server_port}/",
            camera_total=len(cameras),
            camera_online=camera_online,
            camera_offline=camera_offline,
            camera_degraded=camera_degraded,
            camera_recording=camera_recording,
            media_bytes=ctx.gallery.total_bytes(),
            timelapse_bytes=ctx.store.total_timelapse_bytes(),
            update_current=current,
            update_latest=latest,
            update_available=newer,
            ai_enabled=ctx.config.ai.enabled,
            ai_reachable=ai_reachable,
            ai_model=ctx.config.ai.model,
            ai_model_present=ai_model_present,
            issues=tuple(issues),
        )

    def _check_update(self) -> tuple[str, str | None, bool]:
        try:
            return self._update_checker(use_cache=True)
        except Exception:
            return (__version__, None, False)
