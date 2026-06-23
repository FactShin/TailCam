"""Drop-in TailCam plugin: send alerts to an ntfy.sh topic.

This is a *drop-in* plugin — the simplest kind. Copy this single file into your
TailCam config folder's ``plugins/`` directory and restart:

    cp ntfy_channel.py ~/.config/tailcam/plugins/

Then set your topic (third-party channels read their own config, e.g. env vars):

    export TAILCAM_NTFY_TOPIC=my-tailcam-alerts
    # optional self-hosted server:  export TAILCAM_NTFY_SERVER=https://ntfy.example.com

It will appear in Settings → Plugins and in ``tailcam plugins``, and alerts will
go to https://ntfy.sh/<topic> (great for phone push).
"""

from __future__ import annotations

import os

import httpx

from tailcam.plugins.hookspecs import PluginInfo, hookimpl


class NtfyChannel:
    id = "ntfy"
    name = "ntfy.sh"

    def configured(self, config) -> bool:
        return bool(os.environ.get("TAILCAM_NTFY_TOPIC"))

    def send(self, event, config) -> None:
        topic = os.environ.get("TAILCAM_NTFY_TOPIC")
        if not topic:
            return
        server = os.environ.get("TAILCAM_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        priority = {"warning": "high", "success": "default", "info": "low"}.get(
            event.severity, "default"
        )
        httpx.post(
            f"{server}/{topic}",
            data=event.body.encode("utf-8"),
            headers={"Title": event.title, "Priority": priority, "Tags": event.kind},
            timeout=10.0,
        )


@hookimpl
def tailcam_notification_channels():
    return [NtfyChannel()]


@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id="ntfy",
            name="ntfy.sh channel",
            kind="notification",
            description="Push alerts to an ntfy.sh topic (set TAILCAM_NTFY_TOPIC).",
            version="0.1.0",
        )
    ]
