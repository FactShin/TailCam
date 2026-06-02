"""Camera registry and lifecycle: discovery, naming, settings, worker control."""

from __future__ import annotations

import json
import threading

from anycam.camera import enumerate as cam_enumerate
from anycam.camera.frame import FrameBuffer
from anycam.camera.properties import CameraProperties
from anycam.camera.source import CameraDescriptor
from anycam.camera.transforms import CameraTransform
from anycam.camera.worker import CameraStatus, CameraWorker
from anycam.logging_setup import get_logger
from anycam.persistence.models import CameraRecord
from anycam.persistence.store import Store, now

log = get_logger(__name__)


class ManagedCamera:
    """A discovered camera plus its persisted settings and (optional) worker."""

    def __init__(self, descriptor: CameraDescriptor, name: str) -> None:
        self.descriptor = descriptor
        self.name = name
        self.properties = CameraProperties()
        self.transform = CameraTransform()
        self.worker: CameraWorker | None = None

    def settings_dict(self) -> dict:
        return {
            "properties": self.properties.to_dict(),
            "transform": {
                "rotation": self.transform.rotation,
                "flip_h": self.transform.flip_h,
                "flip_v": self.transform.flip_v,
            },
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


class CameraManager:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._cameras: dict[str, ManagedCamera] = {}
        self._lock = threading.RLock()

    def discover(self) -> list[ManagedCamera]:
        """Re-run discovery and merge with persisted settings/names."""
        with self._lock:
            for descriptor in cam_enumerate.discover():
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
            for cam in self._cameras.values():
                if cam.worker:
                    cam.worker.stop()
                    cam.worker = None

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
