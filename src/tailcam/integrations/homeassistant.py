"""Home Assistant integration.

Two complementary paths:

1. **Cameras** — added to HA natively via its built-in *MJPEG IP Camera*
   integration, pointed at TailCam's existing stream + snapshot URLs. No extra
   dependency; this module just generates the ready-to-paste config.

2. **Automations** — optional MQTT discovery that publishes each camera's motion
   and connectivity as HA ``binary_sensor`` entities, so HA automations can
   react to TailCam events. Needs the ``mqtt`` extra (paho-mqtt) and a broker.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from tailcam import __version__
from tailcam.integrations.base import (
    CameraRef,
    mjpeg_url,
    public_base_url,
    selected_cameras,
    slugify,
    snapshot_url,
)
from tailcam.logging_setup import get_logger

if TYPE_CHECKING:
    from tailcam.config import HomeAssistantConfig
    from tailcam.web.context import AppContext

log = get_logger(__name__)


# -- camera config generation (no dependency) ------------------------------
def camera_entries(ctx: AppContext) -> list[dict[str, str]]:
    """Per-camera MJPEG + snapshot URLs HA's MJPEG IP Camera integration needs."""
    base = public_base_url(ctx)
    out: list[dict[str, str]] = []
    for cam in selected_cameras(ctx, []):
        out.append(
            {
                "camera_id": cam.id,
                "name": cam.name,
                "mjpeg_url": mjpeg_url(base, cam.id),
                "still_image_url": snapshot_url(base, cam.id),
            }
        )
    return out


def camera_yaml(ctx: AppContext, entries: list[dict[str, str]] | None = None) -> str:
    """A ``configuration.yaml`` snippet adding every camera via the MJPEG platform."""
    if entries is None:
        entries = camera_entries(ctx)
    if not entries:
        return "# No cameras detected yet.\n"
    lines = ["camera:"]
    for e in entries:
        lines += [
            "  - platform: mjpeg",
            f'    name: "{e["name"]} (TailCam)"',
            f"    mjpeg_url: {e['mjpeg_url']}",
            f"    still_image_url: {e['still_image_url']}",
        ]
    return "\n".join(lines) + "\n"


# -- MQTT discovery (optional) ---------------------------------------------
def _device_info(node_id: str, host: str) -> dict[str, Any]:
    return {
        "identifiers": [node_id],
        "name": f"TailCam ({host})" if host else "TailCam",
        "manufacturer": "TailCam",
        "model": "TailCam node",
        "sw_version": __version__,
    }


def discovery_messages(
    *,
    node_id: str,
    prefix: str,
    cameras: list[CameraRef],
    device: dict[str, Any],
    availability_topic: str,
    publish_motion: bool,
    publish_status: bool,
) -> list[tuple[str, dict[str, Any]]]:
    """Build the HA MQTT-discovery config messages (pure; unit-tested).

    Returns ``(config_topic, payload)`` pairs. One ``binary_sensor`` per camera
    for motion (device_class motion) and one for connectivity (device_class
    connectivity), all grouped under a single HA device.
    """
    msgs: list[tuple[str, dict[str, Any]]] = []
    avail = {
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
    }
    for cam in cameras:
        if publish_motion:
            msgs.append(
                (
                    f"{prefix}/binary_sensor/{node_id}/{cam.slug}_motion/config",
                    {
                        "name": f"{cam.name} Motion",
                        "unique_id": f"{node_id}_{cam.slug}_motion",
                        "object_id": f"tailcam_{cam.slug}_motion",
                        "state_topic": f"{node_id}/{cam.slug}/motion",
                        "device_class": "motion",
                        "payload_on": "ON",
                        "payload_off": "OFF",
                        "json_attributes_topic": f"{node_id}/{cam.slug}/motion/attrs",
                        "device": device,
                        **avail,
                    },
                )
            )
        if publish_status:
            msgs.append(
                (
                    f"{prefix}/binary_sensor/{node_id}/{cam.slug}_status/config",
                    {
                        "name": f"{cam.name} Online",
                        "unique_id": f"{node_id}_{cam.slug}_status",
                        "object_id": f"tailcam_{cam.slug}_status",
                        "state_topic": f"{node_id}/{cam.slug}/status",
                        "device_class": "connectivity",
                        "payload_on": "ON",
                        "payload_off": "OFF",
                        "device": device,
                        **avail,
                    },
                )
            )
    return msgs


class MqttPublisher:
    """Publishes TailCam motion + connectivity to an MQTT broker for HA."""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._cfg: HomeAssistantConfig = ctx.config.homeassistant
        self._client: Any = None
        self._lock = threading.Lock()
        self._motion_timers: dict[str, threading.Timer] = {}
        # camera_id -> deduped slug, kept in sync with what discovery announced
        # so runtime publishes hit the same topics HA subscribed to.
        self._slugs: dict[str, str] = {}
        self._avail_topic = f"{self._cfg.node_id}/availability"
        self._started = False

    @staticmethod
    def available() -> bool:
        try:
            import paho.mqtt.client  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def configured(self) -> bool:
        return bool(self._cfg.mqtt_host.strip())

    @property
    def connected(self) -> bool:
        return bool(self._client is not None and self._client.is_connected())

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._started or not self.configured or not self.available():
            return
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self._cfg.node_id}-tailcam"
        )
        if self._cfg.mqtt_username:
            client.username_pw_set(self._cfg.mqtt_username, self._cfg.mqtt_password or None)
        if self._cfg.mqtt_tls:
            client.tls_set()
        client.will_set(self._avail_topic, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        self._client = client
        try:
            client.connect_async(self._cfg.mqtt_host.strip(), self._cfg.mqtt_port, keepalive=60)
            client.loop_start()
            self._started = True
            log.info("MQTT discovery connecting to %s", self._cfg.mqtt_host)
        except Exception as exc:
            log.warning("MQTT connect failed: %s", exc)
            self._client = None

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason: Any, *_a: Any) -> None:
        try:
            client.publish(self._avail_topic, "online", qos=1, retain=True)
            for topic, payload in self._discovery():
                client.publish(topic, json.dumps(payload), qos=1, retain=True)
            # Seed connectivity state from current camera status.
            for cam in self._cameras():
                online = self._ctx.manager.status(cam.id).value == "online"
                client.publish(
                    f"{self._cfg.node_id}/{cam.slug}/status",
                    "ON" if online else "OFF",
                    qos=1, retain=True,
                )
                # Seed motion OFF only when there is no active motion window —
                # paho re-fires on_connect on every auto-reconnect, and blindly
                # publishing OFF would clobber a live retained ON.
                with self._lock:
                    motion_active = cam.slug in self._motion_timers
                if not motion_active:
                    client.publish(
                        f"{self._cfg.node_id}/{cam.slug}/motion", "OFF", qos=1, retain=True
                    )
            log.info("MQTT discovery published (%s)", reason)
        except Exception as exc:  # never let a callback kill the client thread
            log.debug("MQTT on_connect publish failed: %s", exc)

    def stop(self) -> None:
        with self._lock:
            for t in self._motion_timers.values():
                t.cancel()
            self._motion_timers.clear()
        client = self._client
        if client is not None:
            try:
                client.publish(self._avail_topic, "offline", qos=1, retain=True)
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
        self._client = None
        self._started = False

    # -- publishing --------------------------------------------------------
    def _cameras(self) -> list[CameraRef]:
        cams = selected_cameras(self._ctx, [])
        # Refresh the id -> slug map so publish paths use the same (deduped)
        # slugs that discovery announced.
        with self._lock:
            self._slugs = {c.id: c.slug for c in cams}
        return cams

    def _slug_for(self, camera_id: str) -> str:
        with self._lock:
            slug = self._slugs.get(camera_id)
        return slug if slug is not None else slugify(camera_id)

    def _discovery(self) -> list[tuple[str, dict[str, Any]]]:
        host = self._ctx.local_host or ""
        return discovery_messages(
            node_id=self._cfg.node_id,
            prefix=self._cfg.discovery_prefix,
            cameras=self._cameras(),
            device=_device_info(self._cfg.node_id, host),
            availability_topic=self._avail_topic,
            publish_motion=self._cfg.publish_motion,
            publish_status=self._cfg.publish_status,
        )

    def publish_motion(
        self, *, camera_id: str, label: str | None = None, confidence: float | None = None, **_: Any
    ) -> None:
        if not self.connected or not self._cfg.publish_motion:
            return
        slug = self._slug_for(camera_id)
        attrs = {"camera_id": camera_id, "label": label, "confidence": confidence}
        base = f"{self._cfg.node_id}/{slug}/motion"
        try:
            self._client.publish(f"{base}/attrs", json.dumps(attrs), qos=0)
            self._client.publish(base, "ON", qos=0, retain=True)
        except Exception as exc:
            log.debug("MQTT motion publish failed: %s", exc)
            return
        # Auto-clear after the configured window (HA motion sensors are momentary).
        with self._lock:
            existing = self._motion_timers.pop(slug, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(
                max(1.0, self._cfg.motion_reset_seconds), self._clear_motion, args=(slug,)
            )
            # The timer clears only if it is still the registered one — an
            # already-fired predecessor that lost the lock race must not pop
            # this fresh timer and publish OFF right after our ON.
            timer.args = (slug, timer)
            timer.daemon = True
            self._motion_timers[slug] = timer
            timer.start()

    def _clear_motion(self, slug: str, timer: threading.Timer | None = None) -> None:
        with self._lock:
            current = self._motion_timers.get(slug)
            if timer is not None and current is not timer:
                return  # superseded by a newer motion event
            self._motion_timers.pop(slug, None)
        if self.connected:
            try:
                self._client.publish(
                    f"{self._cfg.node_id}/{slug}/motion", "OFF", qos=0, retain=True
                )
            except Exception:
                pass

    def publish_camera_state(self, *, camera_id: str, online: bool) -> None:
        if not self.connected or not self._cfg.publish_status:
            return
        try:
            self._client.publish(
                f"{self._cfg.node_id}/{self._slug_for(camera_id)}/status",
                "ON" if online else "OFF",
                qos=1, retain=True,
            )
        except Exception as exc:
            log.debug("MQTT status publish failed: %s", exc)
