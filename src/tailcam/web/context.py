"""Shared application context: wires together all services for the web layer."""

from __future__ import annotations

import threading
import time
from functools import partial
from typing import cast

from tailcam import paths
from tailcam.activelearning.service import ActiveLearningService
from tailcam.ai.analyzer import OllamaAnalyzer
from tailcam.ai.detector import BuiltinDetector
from tailcam.ai.pull import ModelPuller
from tailcam.ai.remote import RemoteDetector
from tailcam.camera.manager import CameraManager
from tailcam.camera.source import use_synthetic
from tailcam.cluster.remote_feed import RemoteFeedRegistry
from tailcam.cluster.service import ClusterService, resolve_local_host
from tailcam.config import AppConfig
from tailcam.integrations.homeassistant import MqttPublisher
from tailcam.integrations.homekit import HomeKitBridge
from tailcam.logging_setup import get_logger
from tailcam.media.capture_router import CaptureRouter
from tailcam.media.gallery import MediaGallery
from tailcam.media.recorder import RecordingService
from tailcam.media.snapshot import SnapshotService
from tailcam.motion.events import EventLog
from tailcam.motion.worker import MotionWorker
from tailcam.notify.service import NotificationService
from tailcam.persistence.store import Store
from tailcam.plugins import sdk
from tailcam.plugins.hookspecs import MotionEventData
from tailcam.plugins.market import PluginMarket
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
        # Plugin event hooks see every motion event (automation), independent of
        # the user's notification filters. A broken hook never breaks detection.
        hooks = self._ctx.plugins.event_hooks()
        if hooks:
            data = MotionEventData(
                camera_id=str(kw.get("camera_id") or ""),
                label=cast("str | None", kw.get("label")),
                confidence=cast("float | None", kw.get("confidence")),
                description=cast("str | None", kw.get("description")),
                event_id=cast("int | None", kw.get("event_id")),
                image_path=cast("str | None", kw.get("image_path")),
            )
            for hook in hooks:
                try:
                    hook.on_motion(data)
                except Exception as exc:
                    log.warning("plugin event hook %s failed: %s", getattr(hook, "id", "?"), exc)

    def __getattr__(self, name: str) -> object:
        return getattr(self._notifications, name)


class AppContext:
    def __init__(self, config: AppConfig, store: Store | None = None) -> None:
        self.config = config
        # Send recordings/snapshots to a custom drive if configured (before any
        # service computes a media path).
        paths.set_media_override(config.storage.media_dir)
        self.store = store or Store()
        self.manager = CameraManager(self.store, config)
        self.snapshots = SnapshotService(self.manager, self.store)
        self.recorder = RecordingService(self.manager, self.store)
        self.gallery = MediaGallery(self.store)
        self.event_log = EventLog(self.store)
        # Plugins extend AI providers, notification channels, and event hooks
        # (pluggy registry). Register the config with the SDK first so plugins
        # can read their [plugins.settings.*] tables at import time.
        sdk._set_config(config)
        self.plugins = PluginRegistry(
            disabled=config.plugins.disabled, load_dropins=config.plugins.load_dropins
        )
        self.market = PluginMarket(config.plugins)
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
        self._ha_mqtt_lock = threading.Lock()
        self._motion_fanout = _MotionFanout(self.notifications, self)
        self.training = TrainingService(
            self.manager, self.store, config.training, self.analyzer, self.local_host,
            notifier=self.notifications,
        )
        # Built-in plug-and-play object detection (boxes + labels, zero setup).
        # Provisions itself in the background on first use.
        self.detector = BuiltinDetector(config.detection)
        # Human-in-the-loop active learning: watch frames, auto-label confident
        # detections, send uncertain ones to Label Studio for review.
        self.active_learning = ActiveLearningService(
            self.manager, self.store, config, self.detector, self.analyzer,
            self.training, self.local_host,
        )
        self.cluster = ClusterService(
            config.peers, self.tailscale, self.local_host, config.tailscale.serve_port
        )
        # Detection node: when [detection] node points at a peer, boxes and
        # motion labels come from that node's /api/detect-image.
        self._remote_detector: RemoteDetector | None = None
        self._remote_detector_for = ""
        # Motion analysis routes through the active trained/BYO model if set,
        # else Ollama, else the detection node, else the built-in detector.
        self.inference = InferenceRouter(
            self.store, config.training, self.analyzer, builtin=self.detector,
            remote=self.remote_detector,
        )
        # Per-camera detection result cache: N open viewers of one camera share
        # a single inference per second instead of each running their own.
        self._detect_cache: dict[str, tuple[float, object]] = {}
        self._detect_locks: dict[str, threading.Lock] = {}
        # Storage node support: pulled peer-camera feeds (when THIS node is the
        # storage node) and the router that sends this node's own captures to
        # the configured storage node.
        self.remote_feeds = RemoteFeedRegistry(self.cluster.peer_base)
        self.capture = CaptureRouter(self)
        self.served = False
        self._motion_workers: dict[str, MotionWorker] = {}
        self._lock = threading.Lock()
        # Offline-detection monitor (camera + fleet-node up/down transitions).
        self._notify_stop = threading.Event()
        self._notify_thread: threading.Thread | None = None
        self._cam_status: dict[str, str] = {}
        self._peer_online: dict[str, bool] = {}
        self._last_prune = 0.0
        self._last_rediscover = 0.0

    def startup(self) -> None:
        from tailcam import hostinfo

        prof = hostinfo.profile()
        log.info(
            "Host: %s · %.1f GB RAM · %d CPU · profile=%s",
            prof.model or "generic", prof.total_ram_gb, prof.cpu_count,
            "low-power" if prof.low_power else "standard",
        )
        if prof.low_power:
            # OpenCV's internal thread pool (resize/imencode/dnn) competes with
            # the capture and encode threads on a 4-core Pi; two is the sweet spot.
            try:
                import cv2

                cv2.setNumThreads(2)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("cv2.setNumThreads: %s", exc)
        # Warm the built-in detector at boot so the model is downloaded and
        # loaded before anyone opens a camera view (no-op once provisioned).
        # Skipped in synthetic mode (CI/tests) — there it provisions lazily on
        # the first real detect request instead of hitting the network per run
        # — and when detection is off or routed to another node (a Pi that
        # ships frames elsewhere never loads a model into its 1 GB).
        if not use_synthetic() and self.detector.enabled:
            self.detector.ensure_ready()
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
        # Re-arm motion detection on the cameras it was enabled for.
        restored = 0
        for camera_id in self.manager.motion_enabled_ids():
            if self.enable_motion(camera_id, persist=False):
                restored += 1
        if restored:
            log.info("Motion detection restored on %d camera(s)", restored)
        self._prune_media()  # enforce the retention budget on boot
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
        self.capture.stop_all()
        self.timelapse.shutdown()
        self.remote_feeds.shutdown()
        self.active_learning.shutdown()
        self.training.shutdown()
        self.manager.stop_all()
        # Release the analyzer's keep-alive HTTP pool (a plugin analyzer may
        # not implement close()).
        closer = getattr(self.analyzer, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("analyzer close failed: %s", exc)

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
                        # Bind locally: a concurrent settings save can swap
                        # self.ha_mqtt to None between check and call.
                        mqtt = self.ha_mqtt
                        if mqtt is not None:
                            mqtt.publish_camera_state(
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
                # Enforce the retention budget periodically (every ~5 min).
                if time.monotonic() - self._last_prune > 300:
                    self._prune_media()
                self._reap_stale_remote_sessions()
                self.capture.retry_pending_stops()
                self._rediscover_if_offline()
            except Exception as exc:  # never let the monitor die
                log.debug("notify monitor: %s", exc)
            self._notify_stop.wait(8.0)

    # A storage-node recording whose source stopped delivering frames for this
    # long is finalized so a dead Pi can't leave it "recording" forever.
    _REMOTE_STALE_SECONDS = 180.0

    def _reap_stale_remote_sessions(self) -> None:
        stale = set(self.remote_feeds.stale_keys(self._REMOTE_STALE_SECONDS))
        if not stale:
            return
        for key in self.recorder.session_keys():
            if "|" in key and key in stale:
                log.warning("remote recording %s: no frames for %.0fs; finalizing",
                            key, self._REMOTE_STALE_SECONDS)
                self.recorder.stop(key)
        for key in stale:
            if not self.recorder.is_recording(key):
                self.remote_feeds.stop_feed(key)

    _REDISCOVER_EVERY = 30.0

    def _rediscover_if_offline(self) -> None:
        """Linux: a replugged webcam often comes back on a new /dev/video node;
        re-run the (cheap, ioctl-based) discovery while any camera is offline
        so it shows up without a manual re-scan."""
        import sys

        if not sys.platform.startswith("linux") or use_synthetic():
            return
        now = time.monotonic()
        if now - self._last_rediscover < self._REDISCOVER_EVERY:
            return
        if not any(self.manager.status(c.descriptor.id).value == "offline"
                   for c in self.manager.list()):
            return
        self._last_rediscover = now
        before = {c.descriptor.id for c in self.manager.list()}
        self.manager.discover()
        self.manager.start_all()
        new = {c.descriptor.id for c in self.manager.list()} - before
        if new:
            log.info("Discovered camera(s) after replug: %s", ", ".join(sorted(new)))

    def _prune_media(self) -> None:
        """Delete media beyond the retention budget (size + age). Opt-in: never
        deletes anything unless the user enabled auto-cleanup."""
        self._last_prune = time.monotonic()
        if not self.config.retention.enabled:
            return
        try:
            removed = self.gallery.prune(self.config.retention)
            if removed:
                log.info("Retention: pruned %d media file(s)", removed)
        except Exception as exc:
            log.debug("retention prune failed: %s", exc)

    def reload_plugins(self) -> None:
        """Rebuild the plugin registry (after install/uninstall/enable/disable)
        and re-point live consumers at the new hook set — no restart needed.

        The AI *analyzer provider* is the one plugin capability still bound at
        startup (it's threaded through training + inference); switching
        ``ai.provider`` to a newly installed provider takes a restart, and the
        UI says so.
        """
        with self._lock:
            self.plugins = PluginRegistry(
                disabled=self.config.plugins.disabled,
                load_dropins=self.config.plugins.load_dropins,
            )
            self.notifications.set_channels(self.plugins.notification_channels())

    def apply_homeassistant_config(self) -> None:
        """(Re)start or stop the MQTT publisher to match the current config.

        The single owner of the ha_mqtt lifecycle after startup — the settings
        route calls this instead of swapping the publisher itself. Serialized so
        concurrent saves can't start duplicate paho clients (same client_id).
        """
        with self._ha_mqtt_lock:
            old = self.ha_mqtt
            self.ha_mqtt = None
            if old is not None:
                old.stop()
            if self.config.homeassistant.enabled:
                publisher = MqttPublisher(self)
                publisher.start()  # no-ops when no broker host / paho missing
                self.ha_mqtt = publisher

    async def aclose(self) -> None:
        await self.cluster.aclose()

    # -- detection routing -------------------------------------------------
    def resolve_node_base(self, node: str) -> str | None:
        """Base URL for a configured node reference: a peer key, a hostname
        (MagicDNS or short), or a full http(s) URL. None while unknown."""
        ref = (node or "").strip().rstrip("/")
        if not ref:
            return None
        if ref.startswith("http://") or ref.startswith("https://"):
            return ref
        low = ref.lower()
        for peer in self.cluster.cached_peers():
            if low in (peer.key, peer.host.lower(), peer.host.split(".")[0].lower()):
                return peer.base_url
        return None

    def remote_detector(self) -> RemoteDetector | None:
        """The detection-node client when [detection] routes elsewhere and the
        global switch is on; None means detect locally."""
        node = (self.config.detection.node or "").strip()
        if not node or not self.config.detection.enabled:
            self._remote_detector = None
            self._remote_detector_for = ""
            return None
        if self._remote_detector is None or self._remote_detector_for != node:
            self._remote_detector = RemoteDetector(
                lambda: self.resolve_node_base(node), label=node
            )
            self._remote_detector_for = node
        return self._remote_detector

    def detect_lock(self, camera_id: str) -> threading.Lock:
        with self._lock:
            lock = self._detect_locks.get(camera_id)
            if lock is None:
                lock = self._detect_locks[camera_id] = threading.Lock()
            return lock

    def cached_detection(self, camera_id: str, max_age: float):
        entry = self._detect_cache.get(camera_id)
        if entry and (time.monotonic() - entry[0]) <= max_age:
            return entry[1]
        return None

    def store_detection(self, camera_id: str, result: object) -> None:
        self._detect_cache[camera_id] = (time.monotonic(), result)

    # -- motion ------------------------------------------------------------
    def motion_enabled(self, camera_id: str) -> bool:
        return camera_id in self._motion_workers

    def enable_motion(self, camera_id: str, persist: bool = True) -> bool:
        if persist:
            self.manager.set_motion_enabled(camera_id, True)
        with self._lock:
            if camera_id in self._motion_workers:
                return True
            buffer = self.manager.get_buffer(camera_id)
            if buffer is None:
                return False
            worker = MotionWorker(
                camera_id, buffer, self.config.motion, self.event_log, self.capture,
                analyzer=self.inference,
                notifier=cast("NotificationService", self._motion_fanout),
                reacquire=partial(self.manager.get_buffer, camera_id),
            )
            worker.start()
            self._motion_workers[camera_id] = worker
            return True

    def disable_motion(self, camera_id: str, persist: bool = True) -> None:
        if persist:
            self.manager.set_motion_enabled(camera_id, False)
        with self._lock:
            worker = self._motion_workers.pop(camera_id, None)
        if worker:
            worker.stop()

    def motion_boxes(self, camera_id: str) -> list[tuple[int, int, int, int]]:
        worker = self._motion_workers.get(camera_id)
        return worker.boxes if worker else []
