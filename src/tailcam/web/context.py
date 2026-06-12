"""Shared application context: wires together all services for the web layer."""

from __future__ import annotations

import threading

from tailcam.ai.analyzer import OllamaAnalyzer
from tailcam.camera.manager import CameraManager
from tailcam.cluster.service import ClusterService, resolve_local_host
from tailcam.config import AppConfig
from tailcam.logging_setup import get_logger
from tailcam.media.gallery import MediaGallery
from tailcam.media.recorder import RecordingService
from tailcam.media.snapshot import SnapshotService
from tailcam.motion.events import EventLog
from tailcam.motion.worker import MotionWorker
from tailcam.persistence.store import Store
from tailcam.streaming.mjpeg import MJPEGBackend
from tailcam.tailscale.client import TailscaleClient

log = get_logger(__name__)


class AppContext:
    def __init__(self, config: AppConfig, store: Store | None = None) -> None:
        self.config = config
        self.store = store or Store()
        self.manager = CameraManager(self.store, config)
        self.snapshots = SnapshotService(self.manager, self.store)
        self.recorder = RecordingService(self.manager, self.store)
        self.gallery = MediaGallery(self.store)
        self.event_log = EventLog(self.store)
        self.analyzer = OllamaAnalyzer(config.ai)
        self.tailscale = TailscaleClient()
        self.mjpeg = MJPEGBackend()
        self.local_host = resolve_local_host(self.tailscale)
        self.cluster = ClusterService(
            config.peers, self.tailscale, self.local_host, config.tailscale.serve_port
        )
        self.served = False
        self._motion_workers: dict[str, MotionWorker] = {}
        self._lock = threading.Lock()

    def startup(self) -> None:
        stale = self.store.close_stale_motion_events()
        if stale:
            log.info("Closed %d orphaned motion event(s) from a previous run", stale)
        self.manager.discover()
        # Eager-start workers so status reflects reality from the first poll
        # (the UI only streams cameras that report online).
        self.manager.start_all()
        if self.config.tailscale.auto_serve and self.tailscale.status().running:
            https_port = self.config.tailscale.serve_port
            self.served = self.tailscale.serve(self.config.server.port, https_port)
            if self.served:
                log.info(
                    "Tailscale serve enabled: tailnet :%s -> localhost:%s",
                    https_port,
                    self.config.server.port,
                )

    def shutdown(self) -> None:
        for worker in list(self._motion_workers.values()):
            worker.stop()
        self._motion_workers.clear()
        self.recorder.stop_all()
        self.manager.stop_all()

    async def aclose(self) -> None:
        await self.cluster.aclose()

    # -- motion ------------------------------------------------------------
    def motion_enabled(self, camera_id: str) -> bool:
        return camera_id in self._motion_workers

    def enable_motion(self, camera_id: str) -> bool:
        with self._lock:
            if camera_id in self._motion_workers:
                return True
            buffer = self.manager.get_buffer(camera_id)
            if buffer is None:
                return False
            worker = MotionWorker(
                camera_id, buffer, self.config.motion, self.event_log, self.recorder,
                analyzer=self.analyzer,
            )
            worker.start()
            self._motion_workers[camera_id] = worker
            return True

    def disable_motion(self, camera_id: str) -> None:
        with self._lock:
            worker = self._motion_workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def motion_boxes(self, camera_id: str) -> list[tuple[int, int, int, int]]:
        worker = self._motion_workers.get(camera_id)
        return worker.boxes if worker else []
