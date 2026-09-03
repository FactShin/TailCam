"""Decide *where* a camera's recordings and timelapses are produced.

``[storage] node`` names a peer (key, hostname, or base URL). When set and
reachable, this node hands recording / timelapse jobs for its cameras to that
peer, which pulls the camera's MJPEG stream and writes the files on its own
disk (see ``cluster.remote_feed`` and ``web.routes_remote``). The API, the
motion worker, and the UI all go through this router, so "Record" on a
Raspberry Pi camera lands on the NAS box without anyone caring.

Failure mode is always *local*: if the peer is unknown or down at the moment
a capture starts, the job runs here and the storage panel says so.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tailcam.logging_setup import get_logger

if TYPE_CHECKING:
    from tailcam.web.context import AppContext

log = get_logger(__name__)

_TIMEOUT = 8.0
_DOWN_BACKOFF = 20.0


@dataclass
class RemoteMedia:
    """What a remote stop returns: enough for callers that only need the id."""

    id: int | None
    host: str


class CaptureRouter:
    """Local-or-remote recording + timelapse start/stop for this node's cameras."""

    def __init__(self, ctx: AppContext, client: Any = None) -> None:
        self._ctx = ctx
        self._client = client
        self._lock = threading.Lock()
        # camera_id -> peer key for recordings running on the storage node.
        self._remote_recordings: dict[str, str] = {}
        self._down_until = 0.0
        self.last_error = ""

    # -- target resolution ---------------------------------------------------
    @property
    def configured_node(self) -> str:
        return (self._ctx.config.storage.node or "").strip()

    def target(self) -> tuple[str, str] | None:
        """(peer key, base URL) of the storage node, or None → capture locally."""
        node = self.configured_node
        if not node:
            return None
        if time.monotonic() < self._down_until:
            return None
        base = self._ctx.resolve_node_base(node)
        if base is None:
            self.last_error = f"storage node '{node}' is not visible on the tailnet"
            return None
        key = self._peer_key_for_base(base) or node
        return key, base

    def _peer_key_for_base(self, base: str) -> str | None:
        for peer in self._ctx.cluster.cached_peers():
            if peer.base_url == base:
                return peer.key
        return None

    @property
    def local_key(self) -> str:
        from tailcam.cluster.service import _key_for

        return _key_for(self._ctx.local_host)

    def status(self) -> dict[str, Any]:
        node = self.configured_node
        tgt = self.target() if node else None
        return {
            "node": node,
            "node_online": tgt is not None,
            "error": "" if not node or tgt is not None else (self.last_error or "unreachable"),
            "remote_recordings": len(self._remote_recordings),
        }

    # -- http -----------------------------------------------------------------
    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=_TIMEOUT, follow_redirects=False)
        return self._client

    def _post(self, base: str, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        try:
            resp = self._http().post(f"{base}{path}", json=body)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail = str(resp.json().get("detail", ""))
                except ValueError:
                    detail = resp.text[:200]
                raise RuntimeError(f"HTTP {resp.status_code} {detail}".strip())
            data = resp.json()
            self.last_error = ""
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self.last_error = str(exc)
            self._down_until = time.monotonic() + _DOWN_BACKOFF
            log.warning("storage node request %s failed: %s — falling back to local", path, exc)
            return None

    def _camera_name(self, camera_id: str) -> str:
        cam = self._ctx.manager.get(camera_id)
        return cam.name if cam else camera_id

    def _stream_fps(self, camera_id: str) -> int:
        stream = self._ctx.manager.effective_stream_for(camera_id)
        return int(stream["fps"]) if stream else self._ctx.config.stream.default_fps

    # -- recordings -----------------------------------------------------------
    def is_recording(self, camera_id: str) -> bool:
        return camera_id in self._remote_recordings or self._ctx.recorder.is_recording(camera_id)

    def start_recording(self, camera_id: str, trigger: str = "manual") -> bool:
        if self.is_recording(camera_id):
            return False
        if self._ctx.manager.get(camera_id) is None:
            return False
        tgt = self.target()
        if tgt is not None:
            key, base = tgt
            data = self._post(
                base,
                f"/api/remote/{self.local_key}/cameras/{camera_id}/recording/start",
                {
                    "trigger": trigger,
                    "fps": self._stream_fps(camera_id),
                    "camera_name": self._camera_name(camera_id),
                    "source_host": self._ctx.local_host,
                },
            )
            if data is not None and data.get("ok", True):
                with self._lock:
                    self._remote_recordings[camera_id] = key
                log.info("recording %s (%s) on storage node %s", camera_id, trigger, key)
                return True
        return self._ctx.recorder.start(camera_id, fps=self._stream_fps(camera_id), trigger=trigger)

    def stop_recording(self, camera_id: str):
        with self._lock:
            key = self._remote_recordings.pop(camera_id, None)
        if key is not None:
            base = self._ctx.resolve_node_base(key) or self._ctx.resolve_node_base(
                self.configured_node
            )
            if base is not None:
                data = self._post(
                    base, f"/api/remote/{self.local_key}/cameras/{camera_id}/recording/stop", {}
                )
                if data is not None:
                    peer_host = next(
                        (p.host for p in self._ctx.cluster.cached_peers() if p.key == key), key
                    )
                    return RemoteMedia(id=data.get("media_id"), host=peer_host)
            log.warning("could not stop remote recording %s on %s", camera_id, key)
            return None
        return self._ctx.recorder.stop(camera_id)

    # RecordingService-compatible aliases (the motion worker is written against
    # the recorder's start/stop names).
    def start(self, camera_id: str, fps: int | None = None, trigger: str = "manual") -> bool:
        return self.start_recording(camera_id, trigger=trigger)

    def stop(self, camera_id: str):
        return self.stop_recording(camera_id)

    def stop_all(self) -> None:
        for camera_id in list(self._remote_recordings):
            try:
                self.stop_recording(camera_id)
            except Exception as exc:  # pragma: no cover - defensive shutdown
                log.debug("stop remote recording %s: %s", camera_id, exc)
        self._ctx.recorder.stop_all()

    # -- timelapses -----------------------------------------------------------
    def start_timelapse(self, camera_id: str, params: dict[str, Any]):
        """Returns a local ``TimelapseRecord``, a dict (the storage node's
        ``TimelapseInfo``, already tagged with host/proxy_prefix), or None."""
        if self._ctx.manager.get(camera_id) is None:
            return None
        tgt = self.target()
        if tgt is not None:
            key, base = tgt
            body = {k: v for k, v in params.items() if v is not None}
            body.update(
                {
                    "camera_name": self._camera_name(camera_id),
                    "source_host": self._ctx.local_host,
                    "source_fps": self._stream_fps(camera_id),
                }
            )
            data = self._post(
                base, f"/api/remote/{self.local_key}/cameras/{camera_id}/timelapse/start", body
            )
            if data is not None and "id" in data:
                data["proxy_prefix"] = f"/proxy/{key}"
                log.info("timelapse for %s started on storage node %s", camera_id, key)
                return data
        return self._ctx.timelapse.start(camera_id, **params)
