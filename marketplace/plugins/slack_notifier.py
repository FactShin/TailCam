"""Slack notifications for TailCam motion/offline/training alerts.

Sends TailCam alerts to a Slack channel via an incoming webhook. Create one at
https://api.slack.com/messaging/webhooks and paste the URL below.

Settings (``config.toml``)::

    [plugins.settings.slack]
    webhook_url = "https://hooks.slack.com/services/T000/B000/XXXX"

Notifications must be enabled (Settings → Notifications); this adds Slack as a
destination alongside Discord/Telegram/webhook, honoring the same filters.
"""

from __future__ import annotations

__plugin__ = {
    "id": "slack_notifier",
    "name": "Slack notifications",
    "version": "1.0.0",
    "description": "Send motion/offline/training alerts to a Slack channel via incoming webhook.",
    "author": "TailCam community",
    "kinds": ["notification"],
    "settings_example": (
        '[plugins.settings.slack]\n'
        'webhook_url = "https://hooks.slack.com/services/..."\n'
    ),
}

import httpx

from tailcam.plugins.sdk import PluginInfo, get_logger, hookimpl, plugin_settings

log = get_logger("plugin.slack")


class SlackChannel:
    id = "slack"
    name = "Slack"

    def configured(self, config) -> bool:
        return str(plugin_settings("slack").get("webhook_url") or "").startswith("https://")

    def send(self, event, config) -> None:
        url = str(plugin_settings("slack").get("webhook_url") or "")
        if not url:
            return
        text = f"*{event.title}*\n{event.body}" if event.body else f"*{event.title}*"
        try:
            httpx.post(url, json={"text": text}, timeout=10.0).raise_for_status()
        except Exception as exc:
            log.warning("slack send failed: %s", exc)


@hookimpl
def tailcam_notification_channels():
    return [SlackChannel()]


@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id="slack_notifier",
            name="Slack notifications",
            kind="notification",
            description=__plugin__["description"],
            version=__plugin__["version"],
        )
    ]
