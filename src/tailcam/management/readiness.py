"""Conservative task diagnostics: observing a node must never start its workloads."""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from tailcam import hostinfo, paths
from tailcam.camera.worker import CameraStatus
from tailcam.management.runtime_probe import probe_ollama
from tailcam.timelapse.ffmpeg import passive_ffmpeg_present as _ffmpeg_present

ReadinessState = Literal["ready", "unavailable", "disabled", "unchecked"]


@dataclass(frozen=True)
class TaskReadiness:
    id: str
    label: str
    state: ReadinessState
    code: str
    detail: str
    checked_at: float


@dataclass(frozen=True)
class ReadinessCapacity:
    cpu_count: int
    total_ram_bytes: int
    media_free_bytes: int | None
    media_total_bytes: int | None
    media_writable: bool | None


@dataclass(frozen=True)
class ReadinessSnapshot:
    checked_at: float
    capacity: ReadinessCapacity
    tasks: tuple[TaskReadiness, ...]
    probe_supported: bool = True


def _installed(name: str) -> bool:
    """Top-level discovery only; dotted names could import their parent package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, OSError):
        return False


class NodeReadinessService:
    """Passive snapshots plus an explicit, short-lived Ollama inventory probe.

    Resource totals are observations, not reservations or job admission decisions.
    Runtime probes never load weights, run inference, or download models.
    """

    CACHE_SECONDS = 30.0
    PROBE_INTERVAL = 3.0

    def __init__(self, context: Any) -> None:
        self._ctx = context
        self._host = hostinfo.profile()
        self._probe_lock = threading.Lock()
        self._cache: tuple[tuple[str, str, str], float, TaskReadiness] | None = None
        self._last_attempt = float("-inf")

    def snapshot(self, *, probe: bool = False) -> ReadinessSnapshot:
        ctx = self._ctx
        now = time.time()
        tasks: list[TaskReadiness] = []

        def task(id: str, label: str, state: ReadinessState, code: str, detail: str) -> None:
            tasks.append(TaskReadiness(id, label, state, code, detail, now))

        def disabled(id: str, label: str, role: str) -> bool:
            if ctx.has_role(role):
                return False
            task(
                id,
                label,
                "disabled",
                "role_disabled",
                f"The {role} role is disabled in this process.",
            )
            return True

        if not disabled("capture", "Camera capture", "capture"):
            cameras = ctx.manager.list()
            online = sum(
                ctx.manager.status(c.descriptor.id) == CameraStatus.ONLINE for c in cameras
            )
            if online:
                task(
                    "capture",
                    "Camera capture",
                    "ready",
                    "camera_online",
                    f"{online} camera(s) currently online; individual cameras may differ.",
                )
            else:
                task(
                    "capture",
                    "Camera capture",
                    "unavailable",
                    "no_online_cameras",
                    "No camera is currently online. Check camera connections and the Cameras page.",
                )

        free = total = None
        writable = None
        if not disabled("storage.local", "Local media storage", "storage"):
            media = (
                Path(ctx.config.storage.media_dir).expanduser()
                if ctx.config.storage.media_dir
                else paths.default_media_dir()
            )
            try:
                if not media.is_dir():
                    task(
                        "storage.local",
                        "Local media storage",
                        "unavailable",
                        "media_directory_missing",
                        "The media directory is missing. Check the configured drive or mount.",
                    )
                else:
                    usage = shutil.disk_usage(media)
                    free, total = usage.free, usage.total
                    writable = os.access(media, os.W_OK | os.X_OK)
                    if not writable or free == 0:
                        task(
                            "storage.local",
                            "Local media storage",
                            "unavailable",
                            "media_not_writable" if not writable else "media_full",
                            "No write access or free space. Check storage settings.",
                        )
                    else:
                        task(
                            "storage.local",
                            "Local media storage",
                            "ready",
                            "media_accessible",
                            "Access and free space available. "
                            "Speed, mount identity, and job space untested.",
                        )
            except OSError:
                task(
                    "storage.local",
                    "Local media storage",
                    "unavailable",
                    "media_unreadable",
                    "Cannot read storage information. Check the drive and permissions.",
                )

        if not disabled("detection.builtin", "Built-in object detection", "analysis"):
            if not ctx.config.detection.enabled:
                task(
                    "detection.builtin",
                    "Built-in object detection",
                    "disabled",
                    "feature_disabled",
                    "Object detection is switched off.",
                )
            elif ctx.config.detection.node.strip():
                task(
                    "detection.builtin",
                    "Built-in object detection",
                    "unchecked",
                    "remote_execution",
                    "Detection is routed to another node. Check that node's readiness.",
                )
            else:
                status = ctx.detector.status().status
                state: ReadinessState = (
                    "ready"
                    if status == "ready"
                    else "unavailable"
                    if status == "error"
                    else "unchecked"
                )
                detail = (
                    "The local detector is loaded."
                    if state == "ready"
                    else "The detector reported an error. Check the Models page."
                    if state == "unavailable"
                    else "Detector not loaded. This check does not download or load weights."
                )
                task(
                    "detection.builtin",
                    "Built-in object detection",
                    state,
                    "detector_loaded"
                    if state == "ready"
                    else "detector_error"
                    if state == "unavailable"
                    else "model_not_loaded",
                    detail,
                )

        if not disabled("analysis.ollama", "Ollama model", "analysis"):
            tasks.append(self._ollama(probe=probe, now=now))
        if not disabled("processing.ffmpeg", "FFmpeg encoding", "storage"):
            present = _ffmpeg_present()
            task(
                "processing.ffmpeg",
                "FFmpeg encoding",
                "unchecked" if present else "unavailable",
                "encoder_untested" if present else "encoder_missing",
                "An FFmpeg executable is present; codecs and encoding have not been tested."
                if present
                else "FFmpeg missing. Install FFmpeg or repair TailCam.",
            )
        if not disabled("training", "Model training", "training"):
            installed = _installed("torch") and _installed("ultralytics")
            task(
                "training",
                "Model training",
                "unchecked" if installed else "unavailable",
                "training_untested" if installed else "training_dependencies_missing",
                "Training packages present; GPU support and available memory untested."
                if installed
                else "Training packages missing. Install the TailCam training extra.",
            )
        return ReadinessSnapshot(
            now,
            ReadinessCapacity(
                self._host.cpu_count, self._host.total_ram_bytes, free, total, writable
            ),
            tuple(tasks),
        )

    def _key(self) -> tuple[str, str, str]:
        ai = self._ctx.config.ai
        return ai.provider, ai.base_url, ai.model

    def _ollama(self, *, probe: bool, now: float) -> TaskReadiness:
        def result(state: ReadinessState, code: str, detail: str) -> TaskReadiness:
            return TaskReadiness("analysis.ollama", "Ollama model", state, code, detail, now)

        ai = self._ctx.config.ai
        if not ai.enabled:
            self._cache = None
            return result("disabled", "feature_disabled", "AI event analysis is switched off.")
        key = self._key()
        if key[0] != "ollama":
            self._cache = None
            return result(
                "unchecked",
                "probe_unsupported",
                "This analyzer provider does not support runtime inventory checks.",
            )
        if not key[2].strip():
            return result(
                "unavailable", "model_not_configured", "Select an Ollama model on the Models page."
            )
        cached = self._cache
        valid = (
            cached is not None
            and cached[0] == key
            and time.monotonic() - cached[1] < self.CACHE_SECONDS
        )
        if not probe or time.monotonic() - self._last_attempt < self.PROBE_INTERVAL:
            return (
                cached[2]
                if valid and cached
                else result(
                    "unchecked",
                    "runtime_unchecked",
                    "Use Check runtimes to query the configured Ollama model inventory.",
                )
            )
        if not self._probe_lock.acquire(blocking=False):
            return (
                cached[2]
                if valid and cached
                else result(
                    "unchecked",
                    "probe_in_progress",
                    "A runtime check is already in progress. Refresh shortly.",
                )
            )
        try:
            self._last_attempt = time.monotonic()
            state, code, detail = probe_ollama(key[1], key[2])
            if (
                self._key() != key
                or not self._ctx.config.ai.enabled
                or not self._ctx.has_role("analysis")
            ):
                self._cache = None
                return result(
                    "unchecked",
                    "configuration_changed",
                    "Settings changed during the check. Check runtimes again.",
                )
            observed = TaskReadiness(
                "analysis.ollama",
                "Ollama model",
                cast(ReadinessState, state),
                code,
                detail,
                time.time(),
            )
            self._cache = (key, time.monotonic(), observed)
            return observed
        finally:
            self._probe_lock.release()
