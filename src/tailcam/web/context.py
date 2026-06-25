"""Shared application context: wires together all services for the web layer."""

from __future__ import annotations

import threading
from typing import cast

from tailcam.ai.analyzer import OllamaAnalyzer
from tailcam.ai.pull import ModelPuller
from tailcam.camera.manager import CameraManager
from tailcam.cluster.service import ClusterService, resolve_local_host
from tailcam.config import AppConfig
from tailcam.integrations.homeassistant import MqttPublisher
from tailcam.integrations.homekit import HomeKitBridge
from tailcam.logging_setup import get_logger
from tailcam.media.gallery import MediaGallery
from tailcam.media.recorder import RecordingService
from tailcam.media.snapshot import SnapshotService
from tailcam.motion.events import EventLog
from tailcam.motion.worker import MotionWorker
from tailcam.notify.service import NotificationService
from tailcam.persistence.store import Store
from tailcam.plugins.registry import PluginRegistry
from tailcam.streaming.mjpeg import MJPEGBackend
from tailcam.tailscale.client import TailscaleClient
from tailcam.timelapse.analyzer import PrinterAnalyzer, TimelapseAnalysisQueue
from tailcam.timelapse.service import TimelapseService
from tailcam.training.inference import InferenceRouter
from tailcam.training.service import TrainingService

log = get_logger(__name__)


class _MotionFanout:
    """Routes a motion event to notifications and (if enabled) HA MQTT, so the
    motion worker stays unaware of either. Anything other than ``notify_motion``
    falls through to the underlying notification service."""

    def __init__(self, notifications: NotificationService, ctx: AppContext) -> None:
        self._notifications = notifications
        self._ctx = ctx

    def notify_motion(self, **kw: object) -> None:
        self._notifications.notify_motion(**kw)  # type: ignore[arg-type]
        mqtt = self._ctx.ha_mqtt
        if mqtt is not None:
            mqtt.publish_motion(
                camera_id=kw.get("camera_id"),  # type: ignore[arg-type]
                label=kw.get("label"),  # type: ignore[arg-type]
                confidence=kw.get("confidence"),  # type: ignore[arg-type]
            )

    def __getattr__(self, name: str) -> object:
        return getattr(self._notifications, name)


class AppContext:
    def __init__(self, config: AppConfig, store: Store | None = None) -> None:
        self.config = config
        self.store = store or Store()
        self.manager = CameraManager(self.store, config)
        self.snapshots = SnapshotService(self.manager, self.store)
        self.recorder = RecordingService(self.manager, self.store)
        self.gallery = MediaGallery(self.store)
        self.event_log = EventLog(self.store)
        # Plugins extend AI providers + notification channels (pluggy registry).
        self.plugins = PluginRegistry(
            disabled=config.plugins.disabled, load_dropins=config.plugins.load_dropins
        )
        provider = self.plugins.analyzer_provider(config.ai.provider)
        if provider is None:
            provider = self.plugins.analyzer_provider("ollama")
        self.analyzer = provider.build(config.ai) if provider else OllamaAnalyzer(config.ai)
        self.pulls = ModelPuller(config.ai)
        self.printer_analyzer = PrinterAnalyzer(config.ai)
        self.timelapse_analysis = TimelapseAnalysisQueue(self.store, self.printer_analyzer)
        self.timelapse = TimelapseService(
            self.manager,
            self.store,
            config.timelapse,
            analysis_queue=self.timelapse_analysis,
        )
        self.tailscale = TailscaleClient()
        self.mjpeg = MJPEGBackend()
        self.local_host = resolve_local_host(self.tailscale)
        self.notifications = NotificationService(
            config.notifications, channels=self.plugins.notification_channels()
        )
        # Home-automation integrations (Apple HomeKit via HAP, Home Assistant via
        # MJPEG cameras + optional MQTT discovery). Constructed always; started in
        # startup() only when enabled.
        self.homekit = HomeKitBridge(self)
        self.ha_mqtt: MqttPublisher | None = (
            MqttPublisher(self) if config.homeassistant.enabled else None
        )
        self._motion_fanout = _MotionFanout(self.notifications, self)
        self.training = TrainingService(
            self.manager, self.store, config.training, self.analyzer, self.local_host,
            notifier=self.notifications,
        )
        # Motion analysis routes through the active trained/BYO model if set, else Ollama.
        self.inference = InferenceRouter(self.store, config.training, self.analyzer)
        self.cluster = ClusterService(
            config.peers, self.tailscale, self.local_host, config.tailscale.serve_port
        )
        self.served = False
        self._motion_workers: dict[str, MotionWorker] = {}
        self._lock = threading.Lock()
        # Offline-detection monitor (camera + fleet-node up/down transitions).
        self._notify_stop = threading.Event()
        self._notify_thread: threading.Thread | None = None
        self._cam_status: dict[str, str] = {}
        self._peer_online: dict[str, bool] = {}

    def startup(self) -> None:
        stale = self.store.close_stale_motion_events()
        if stale:
            log.info("Closed %d orphaned motion event(s) from a previous run", stale)
        interrupted = self.store.interrupt_active_timelapses()
        if interrupted:
            log.info("Marked %d timelapse(s) interrupted (encode them to finish)", interrupted)
        interrupted_runs = self.store.interrupt_active_runs()
        if interrupted_runs:
            log.info("Marked %d training run(s) interrupted (re-run to finish)", interrupted_runs)
        self.training.startup()
        self.manager.discover()
        # Eager-start workers so status reflects reality from the first poll
        # (the UI only streams cameras that report online).
        self.manager.start_all()
        self._start_notify_monitor()
        if self.config.homekit.enabled:
            self.homekit.start()
        if self.ha_mqtt is not None:
            self.ha_mqtt.start()
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
        self._stop_notify_monitor()
        self.homekit.stop()
        if self.ha_mqtt is not None:
            self.ha_mqtt.stop()
        for worker in list(self._motion_workers.values()):
            worker.stop()
        self._motion_workers.clear()
        self.recorder.stop_all()
        self.timelapse.shutdown()
        self.training.shutdown()
        self.manager.stop_all()

    # -- offline monitor ---------------------------------------------------
    def _start_notify_monitor(self) -> None:
        if self._notify_thread is not None:
            return
        self._notify_stop.clear()
        self._notify_thread = threading.Thread(
            target=self._notify_monitor_loop, name="notify-monitor", daemon=True
        )
        self._notify_thread.start()

    def _stop_notify_monitor(self) -> None:
        self._notify_stop.set()
        thread = self._notify_thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._notify_thread = None

    def _notify_monitor_loop(self) -> None:
        """Poll camera + fleet-node status and fire on up/down transitions.

        The first observation of each subject only seeds the baseline (no alert),
        so we never notify for state that was already true at startup.
        """
        while not self._notify_stop.is_set():
            try:
                for cam in self.manager.list():
                    cid = cam.descriptor.id
                    status = self.manager.status(cid).value
                    prev = self._cam_status.get(cid)
                    if prev is not None and status != prev:
                        self.notifications.notify_camera_status(
                            camera_id=cid, name=cid, old=prev, new=status
                        )
                        if self.ha_mqtt is not None:
                            self.ha_mqtt.publish_camera_state(
                                camera_id=cid, online=(status == "online")
                            )
                    self._cam_status[cid] = status
                for peer in self.cluster.cached_peers():
                    prev_online = self._peer_online.get(peer.key)
                    if prev_online is not None and peer.online != prev_online:
                        self.notifications.notify_node_status(
                            node_key=peer.key, host=peer.host, online=peer.online
                        )
                    self._peer_online[peer.key] = peer.online
            except Exception as exc:  # never let the monitor die
                log.debug("notify monitor: %s", exc)
            self._notify_stop.wait(8.0)

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
                analyzer=self.inference,
                notifier=cast("NotificationService", self._motion_fanout),
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
