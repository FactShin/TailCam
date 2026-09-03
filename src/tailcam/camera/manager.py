"""Camera registry and lifecycle: discovery, naming, settings, worker control."""

from __future__ import annotations

import builtins
import json
import threading

from tailcam.camera import enumerate as cam_enumerate
from tailcam.camera.frame import FrameBuffer
from tailcam.camera.properties import CameraProperties
from tailcam.camera.source import CameraDescriptor
from tailcam.camera.transforms import CameraTransform
from tailcam.camera.worker import CameraStatus, CameraWorker
from tailcam.config import AppConfig
from tailcam.logging_setup import get_logger
from tailcam.persistence.models import CameraRecord
from tailcam.persistence.store import Store, now

log = get_logger(__name__)


class ManagedCamera:
    """A discovered camera plus its persisted settings and (optional) worker."""

    def __init__(self, descriptor: CameraDescriptor, name: str) -> None:
        self.descriptor = descriptor
        self.name = name
        self.properties = CameraProperties()
        self.transform = CameraTransform()
        # Persisted so motion detection survives a restart (it used to live
        # only in memory and was silently off again after every reboot).
        self.motion_enabled = False
        # Device-wide stream settings — what every viewer of this camera gets.
        # None = inherit the global default ([stream] in config.toml). These
        # replaced the old per-browser-tab values, which nobody could find or
        # keep in sync across devices.
        self.stream: dict[str, int | None] = {"fps": None, "quality": None, "max_width": None}
        # Object detection for this camera: None = follow the global switch.
        self.detection_enabled: bool | None = None
        self.worker: CameraWorker | None = None

    def effective_stream(self, config: AppConfig | None) -> dict[str, int]:
        """Resolved stream fps / JPEG quality / max width for this camera."""
        defaults = config.stream if config else None
        return {
            "fps": int(self.stream.get("fps") or (defaults.default_fps if defaults else 15)),
            "quality": int(
                self.stream.get("quality") or (defaults.jpeg_quality if defaults else 80)
            ),
            "max_width": int(
                mw if (mw := self.stream.get("max_width")) is not None
                else (defaults.max_width if defaults else 1280)
            ),
        }

    def settings_dict(self) -> dict:
        return {
            "properties": self.properties.to_dict(),
            "transform": {
                "rotation": self.transform.rotation,
                "flip_h": self.transform.flip_h,
                "flip_v": self.transform.flip_v,
            },
            "motion_enabled": self.motion_enabled,
            "stream": dict(self.stream),
            "detection_enabled": self.detection_enabled,
        }

    def load_settings(self, data: dict) -> None:
        if "properties" in data:
            self.properties = CameraProperties.from_dict(data["properties"])
        if "transform" in data:
            t = data["transform"]
            self.transform = CameraTransform(
                rotation=int(t.get("rotation", 0)),
                flip_h=bool(t.get("flip_h", False)),
                flip_v=bool(t.get("flip_v", False)),
            )
        if "motion_enabled" in data:
            self.motion_enabled = bool(data["motion_enabled"])
        if isinstance(data.get("stream"), dict):
            for key in ("fps", "quality", "max_width"):
                if key in data["stream"]:
                    value = data["stream"][key]
                    self.stream[key] = int(value) if value is not None else None
        if "detection_enabled" in data:
            value = data["detection_enabled"]
            self.detection_enabled = None if value is None else bool(value)


class CameraManager:
    def __init__(self, store: Store, config: AppConfig | None = None) -> None:
        self._store = store
        self._config = config
        self._cameras: dict[str, ManagedCamera] = {}
        self._lock = threading.RLock()

    def _hidden(self) -> set[str]:
        return set(self._config.cameras.hidden) if self._config else set()

    def discover(self) -> list[ManagedCamera]:
        """Re-run discovery and merge with persisted settings/names."""
        with self._lock:
            hidden = self._hidden()
            for descriptor in cam_enumerate.discover():
                if descriptor.id in hidden:
                    continue  # user deleted/forgot this camera
                existing = self._cameras.get(descriptor.id)
                if existing:
                    existing.descriptor = descriptor
                    continue
                record = self._store.get_camera(descriptor.id)
                name = record.name if record else descriptor.name
                cam = ManagedCamera(descriptor, name)
                if record:
                    try:
                        cam.load_settings(json.loads(record.settings_json))
                    except (ValueError, KeyError):
                        pass
                self._cameras[descriptor.id] = cam
                self._persist(cam)
            return list(self._cameras.values())

    def list(self) -> list[ManagedCamera]:
        with self._lock:
            return list(self._cameras.values())

    def get(self, camera_id: str) -> ManagedCamera | None:
        with self._lock:
            return self._cameras.get(camera_id)

    def get_buffer(self, camera_id: str) -> FrameBuffer | None:
        """Lazily start the camera's worker and return its frame buffer."""
        with self._lock:
            cam = self._cameras.get(camera_id)
            if cam is None:
                return None
            if cam.worker is None or not cam.worker.running:
                cam.worker = CameraWorker(
                    cam.descriptor, properties=cam.properties, transform=cam.transform
                )
                cam.worker.start()
            return cam.worker.buffer

    def start_all(self) -> None:
        """Start a capture worker for every known camera.

        Workers must run for /api/cameras to report real status. Without this,
        the dashboard deadlocks: the UI won't request a stream from an
        "offline" camera, but only a stream request would start the worker
        that brings it online.
        """
        with self._lock:
            ids = list(self._cameras)
        for camera_id in ids:
            self.get_buffer(camera_id)

    def restart(self, camera_id: str) -> bool:
        """Stop and re-create a camera's capture worker (recover a stuck feed)."""
        with self._lock:
            cam = self._cameras.get(camera_id)
            if cam is None:
                return False
            worker, cam.worker = cam.worker, None
        # Join the capture thread outside the lock: every /api/cameras poll and
        # new stream takes the lock, and a stuck V4L2 read holds the join for
        # seconds.
        if worker:
            worker.stop()
        self.get_buffer(camera_id)  # lazily recreates + starts
        return True

    def remove(self, camera_id: str) -> bool:
        """Forget a camera: stop it, drop its registry + DB record, and hide it
        from future discovery (persisted to config)."""
        with self._lock:
            cam = self._cameras.pop(camera_id, None)
            self._store.delete_camera(camera_id)
            hide = self._config is not None and camera_id not in self._config.cameras.hidden
            if hide:
                self._config.cameras.hidden.append(camera_id)  # type: ignore[union-attr]
        if cam and cam.worker:
            cam.worker.stop()
        if hide:
            self._config.save()  # type: ignore[union-attr]
        return cam is not None

    def status(self, camera_id: str) -> CameraStatus:
        cam = self.get(camera_id)
        if cam is None or cam.worker is None:
            return CameraStatus.OFFLINE
        return cam.worker.state.status

    def rename(self, camera_id: str, name: str) -> bool:
        with self._lock:
            cam = self._cameras.get(camera_id)
            if cam is None:
                return False
            cam.name = name
            self._store.set_camera_name(camera_id, name)
            return True

    def effective_stream_for(self, camera_id: str) -> dict[str, int] | None:
        cam = self.get(camera_id)
        return cam.effective_stream(self._config) if cam else None

    def detection_enabled_for(self, camera_id: str) -> bool:
        """Per-camera object detection, falling back to the global switch."""
        cam = self.get(camera_id)
        if cam is None:
            return False
        if cam.detection_enabled is not None:
            return cam.detection_enabled
        return bool(self._config.detection.enabled) if self._config else False

    def set_motion_enabled(self, camera_id: str, enabled: bool) -> None:
        """Remember the motion toggle for this camera (restored at startup)."""
        with self._lock:
            cam = self._cameras.get(camera_id)
            if cam is None or cam.motion_enabled == enabled:
                return
            cam.motion_enabled = enabled
            self._store.set_camera_settings(camera_id, cam.settings_dict())

    def motion_enabled_ids(self) -> builtins.list[str]:
        with self._lock:
            return [cid for cid, cam in self._cameras.items() if cam.motion_enabled]

    def update_settings(self, camera_id: str, settings: dict) -> bool:
        with self._lock:
            cam = self._cameras.get(camera_id)
            if cam is None:
                return False
            cam.load_settings(settings)
            self._store.set_camera_settings(camera_id, cam.settings_dict())
            if cam.worker and cam.worker.running:
                for name, value in cam.properties.to_dict().items():
                    if value is not None:
                        cam.worker.set_property(name, float(value))
                cam.worker.set_transform(cam.transform)
            return True

    def stop_all(self) -> None:
        with self._lock:
            workers = [cam.worker for cam in self._cameras.values() if cam.worker]
            for cam in self._cameras.values():
                cam.worker = None
        for worker in workers:
            worker.stop()

    def _persist(self, cam: ManagedCamera) -> None:
        self._store.upsert_camera(
            CameraRecord(
                id=cam.descriptor.id,
                name=cam.name,
                backend=cam.descriptor.backend,
                settings_json=json.dumps(cam.settings_dict()),
                last_seen=now(),
            )
        )
