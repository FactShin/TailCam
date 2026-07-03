"""Notification dispatch for TailCam.

Pushes alerts to Discord, Telegram, and/or a generic JSON webhook (the latter is
the route for a personal bot like Hermes/OpenClaw). Sending happens on a daemon
thread and never raises into the caller, so motion/training threads are never
blocked or broken by a flaky channel.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tailcam.config import NotificationsConfig
from tailcam.logging_setup import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0
_MAX_IMAGE_BYTES = 8_000_000

_COLORS = {"info": 0x5B7FFF, "warning": 0xFFB224, "success": 0x2EE6A8}


@dataclass
class NotificationEvent:
    kind: str  # motion | camera | training | test
    title: str
    body: str
    severity: str = "info"  # info | warning | success
    image_path: str | None = None
    data: dict[str, Any] = field(default_factory=dict)  # structured payload for the webhook


class NotificationService:
    def __init__(self, config: NotificationsConfig, channels: list | None = None) -> None:
        self._config = config  # live reference — UI edits take effect immediately
        self._lock = threading.Lock()
        self._last_motion: dict[str, float] = {}  # per-camera cooldown
        if channels is None:
            # Default to the built-in channels (lazy import avoids an import cycle).
            from tailcam.plugins.builtin.channels import builtin_channels

            channels = builtin_channels()
        self._channels = list(channels)

    @property
    def config(self) -> NotificationsConfig:
        return self._config

    def channels_configured(self) -> list[str]:
        return [ch.id for ch in self._channels if ch.configured(self._config)]

    def set_channels(self, channels: list) -> None:
        """Swap the channel set (plugin reload). Assignment is atomic; the send
        path iterates whatever list it captured, which stays valid."""
        self._channels = list(channels)

    # -- triggers ----------------------------------------------------------
    def _motion_event(
        self, *, camera_id: str, label: str | None, confidence: float | None,
        description: str | None, event_id: int | None, image_path: str | None,
    ) -> NotificationEvent | None:
        c = self._config
        if not c.enabled or not c.notify_motion:
            return None
        conf = confidence or 0.0
        if c.min_confidence and conf < c.min_confidence:
            return None
        if c.labels and (label or "") not in c.labels:
            return None
        title = f"{(label or 'Motion').capitalize()} · {camera_id}"
        body = description or (f"{label} ({conf:.0%})" if label else "Motion detected")
        return NotificationEvent(
            "motion", title, body, severity="warning", image_path=image_path,
            data={"camera_id": camera_id, "label": label, "confidence": round(conf, 3),
                  "event_id": event_id},
        )

    def notify_motion(
        self, *, camera_id: str, label: str | None = None, confidence: float | None = None,
        description: str | None = None, event_id: int | None = None, image_path: str | None = None,
    ) -> None:
        event = self._motion_event(
            camera_id=camera_id, label=label, confidence=confidence,
            description=description, event_id=event_id, image_path=image_path,
        )
        if event is None:
            return
        now = time.time()
        with self._lock:
            if now - self._last_motion.get(camera_id, 0.0) < self._config.cooldown_seconds:
                return
            self._last_motion[camera_id] = now
        self._dispatch(event)

    def notify_camera_status(self, *, camera_id: str, name: str, old: str | None, new: str) -> None:
        c = self._config
        if not c.enabled or not c.notify_camera_offline:
            return
        bad = new in ("offline", "degraded")
        recovered = new == "online" and (old in ("offline", "degraded"))
        if not bad and not recovered:
            return
        who = name or camera_id
        if bad:
            event = NotificationEvent(
                "camera", f"Camera {new}: {who}", f"{who} is {new}.", severity="warning",
                data={"camera_id": camera_id, "status": new},
            )
        else:
            event = NotificationEvent(
                "camera", f"Camera back online: {who}", f"{who} recovered.", severity="success",
                data={"camera_id": camera_id, "status": "online"},
            )
        self._dispatch(event)

    def notify_node_status(self, *, node_key: str, host: str, online: bool) -> None:
        c = self._config
        if not c.enabled or not c.notify_camera_offline:
            return
        who = host or node_key
        if not online:
            event = NotificationEvent(
                "node", f"Node offline: {who}", f"Fleet node {who} is unreachable.",
                severity="warning", data={"node_key": node_key, "online": False},
            )
        else:
            event = NotificationEvent(
                "node", f"Node back online: {who}", f"Fleet node {who} recovered.",
                severity="success", data={"node_key": node_key, "online": True},
            )
        self._dispatch(event)

    def notify_training(
        self, *, run_id: int, dataset_id: int, status: str,
        model_id: int | None = None, metrics: dict | None = None,
    ) -> None:
        c = self._config
        if not c.enabled or not c.notify_training:
            return
        if status not in ("complete", "error", "stopped"):
            return
        sev = "success" if status == "complete" else "warning"
        body = f"Run #{run_id} (dataset #{dataset_id}) {status}."
        if status == "complete" and metrics:
            top = next(iter(metrics.items()), None)
            if top:
                body += f" {top[0]}={top[1]}"
        event = NotificationEvent(
            "training", f"Training {status} · run #{run_id}", body, severity=sev,
            data={"run_id": run_id, "dataset_id": dataset_id, "status": status,
                  "model_id": model_id, "metrics": metrics or {}},
        )
        self._dispatch(event)

    def send_test(self) -> dict[str, Any]:
        channels = self.channels_configured()
        event = NotificationEvent(
            "test", "TailCam test notification",
            "If you can read this, your TailCam notifications are wired up. 🎉",
            severity="info", data={"test": True},
        )
        # Send synchronously so the UI can report success/failure of the test.
        results = self._send_all(event)
        return {"channels": channels, "results": results}

    # -- dispatch ----------------------------------------------------------
    def _dispatch(self, event: NotificationEvent) -> None:
        threading.Thread(target=self._send_all, args=(event,), daemon=True).start()

    def _send_all(self, event: NotificationEvent) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for ch in self._channels:
            if ch.configured(self._config):
                results[ch.id] = _safe(ch.send, event, self._config)
        return results


def _safe(fn: Callable[..., None], *args: Any) -> bool:
    try:
        fn(*args)
        return True
    except Exception as exc:  # never let a channel break the app
        log.debug("notification channel failed: %s", exc)
        return False


def _read_image(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        p = Path(path)
        if p.is_file() and p.stat().st_size <= _MAX_IMAGE_BYTES:
            return p.read_bytes()
    except OSError:
        return None
    return None


def send_discord(webhook: str, event: NotificationEvent) -> None:
    embed: dict[str, Any] = {
        "title": event.title[:256],
        "description": event.body[:2000],
        "color": _COLORS.get(event.severity, _COLORS["info"]),
    }
    img = _read_image(event.image_path)
    if img:
        embed["image"] = {"url": "attachment://thumb.jpg"}
        r = httpx.post(
            webhook,
            data={"payload_json": json.dumps({"username": "TailCam", "embeds": [embed]})},
            files={"file": ("thumb.jpg", img, "image/jpeg")},
            timeout=_TIMEOUT,
        )
    else:
        r = httpx.post(webhook, json={"username": "TailCam", "embeds": [embed]}, timeout=_TIMEOUT)
    r.raise_for_status()


def send_telegram(token: str, chat_id: str, event: NotificationEvent) -> None:
    base = f"https://api.telegram.org/bot{token}"
    text = f"<b>{_esc(event.title)}</b>\n{_esc(event.body)}"
    img = _read_image(event.image_path)
    if img:
        r = httpx.post(
            f"{base}/sendPhoto",
            data={"chat_id": chat_id, "caption": text, "parse_mode": "HTML"},
            files={"photo": ("thumb.jpg", img, "image/jpeg")},
            timeout=_TIMEOUT,
        )
    else:
        r = httpx.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=_TIMEOUT,
        )
    r.raise_for_status()


def send_webhook(url: str, event: NotificationEvent) -> None:
    payload = {
        "source": "tailcam",
        "kind": event.kind,
        "title": event.title,
        "body": event.body,
        "severity": event.severity,
        "ts": time.time(),
        **event.data,
    }
    httpx.post(url, json=payload, timeout=_TIMEOUT).raise_for_status()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
