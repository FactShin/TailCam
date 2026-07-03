"""ntfy.sh push notifications for TailCam — phone push with zero accounts.

`ntfy <https://ntfy.sh>`_ delivers push notifications to your phone from a
simple HTTP POST. Install the ntfy app, subscribe to a topic of your choosing,
and TailCam alerts arrive as native push — no cloud account, and it works with
a self-hosted ntfy server too.

Settings (``config.toml``)::

    [plugins.settings.ntfy]
    topic = "my-tailcam-alerts"        # pick something unguessable
    server = "https://ntfy.sh"         # or your self-hosted server
    token = ""                          # optional access token
    priority = "default"                # min | low | default | high | urgent

Notifications must be enabled (Settings → Notifications); the usual filters
(labels, confidence, cooldown) apply.
"""

from __future__ import annotations

__plugin__ = {
    "id": "ntfy_notifier",
    "name": "ntfy push notifications",
    "version": "1.0.0",
    "description": "Native phone push via ntfy.sh (or self-hosted ntfy) — no accounts needed.",
    "author": "TailCam community",
    "kinds": ["notification"],
    "settings_example": (
        '[plugins.settings.ntfy]\n'
        'topic = "my-tailcam-alerts"\n'
        '# server = "https://ntfy.sh"\n'
    ),
}

import httpx

from tailcam.plugins.sdk import PluginInfo, get_logger, hookimpl, plugin_settings

log = get_logger("plugin.ntfy")

_SEVERITY_TAGS = {"warning": "warning", "success": "white_check_mark", "info": "movie_camera"}


class NtfyChannel:
    id = "ntfy"
    name = "ntfy"

    def configured(self, config) -> bool:
        return bool(str(plugin_settings("ntfy").get("topic") or "").strip())

    def send(self, event, config) -> None:
        s = plugin_settings("ntfy")
        topic = str(s.get("topic") or "").strip()
        if not topic:
            return
        server = str(s.get("server") or "https://ntfy.sh").rstrip("/")
        headers = {
            "Title": event.title,
            "Priority": str(s.get("priority") or "default"),
            "Tags": _SEVERITY_TAGS.get(event.severity, "movie_camera"),
        }
        if s.get("token"):
            headers["Authorization"] = f"Bearer {s['token']}"
        try:
            httpx.post(
                f"{server}/{topic}",
                content=(event.body or event.title).encode(),
                headers=headers,
                timeout=10.0,
            ).raise_for_status()
        except Exception as exc:
            log.warning("ntfy send failed: %s", exc)


@hookimpl
def tailcam_notification_channels():
    return [NtfyChannel()]


@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id="ntfy_notifier",
            name="ntfy push notifications",
            kind="notification",
            description=__plugin__["description"],
            version=__plugin__["version"],
        )
    ]
