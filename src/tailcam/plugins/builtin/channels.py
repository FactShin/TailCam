"""Built-in notification channels: Discord, Telegram, generic webhook.

Each wraps the corresponding sender in :mod:`tailcam.notify.service`. Third-party
channels just implement the same tiny interface (``id``/``name``/``configured``/
``send``) and register via the ``tailcam`` entry-point group.
"""

from __future__ import annotations

from tailcam.config import NotificationsConfig
from tailcam.notify.service import (
    NotificationEvent,
    send_discord,
    send_telegram,
    send_webhook,
)
from tailcam.plugins.hookspecs import PluginInfo, hookimpl


class DiscordChannel:
    id = "discord"
    name = "Discord"

    def configured(self, config: NotificationsConfig) -> bool:
        return bool(config.discord_webhook.strip())

    def send(self, event: NotificationEvent, config: NotificationsConfig) -> None:
        send_discord(config.discord_webhook.strip(), event)


class TelegramChannel:
    id = "telegram"
    name = "Telegram"

    def configured(self, config: NotificationsConfig) -> bool:
        return bool(config.telegram_token.strip() and config.telegram_chat_id.strip())

    def send(self, event: NotificationEvent, config: NotificationsConfig) -> None:
        send_telegram(config.telegram_token.strip(), config.telegram_chat_id.strip(), event)


class WebhookChannel:
    id = "webhook"
    name = "Generic webhook"

    def configured(self, config: NotificationsConfig) -> bool:
        return bool(config.webhook_url.strip())

    def send(self, event: NotificationEvent, config: NotificationsConfig) -> None:
        send_webhook(config.webhook_url.strip(), event)


def builtin_channels() -> list:
    return [DiscordChannel(), TelegramChannel(), WebhookChannel()]


class ChannelsPlugin:
    @hookimpl
    def tailcam_notification_channels(self) -> list:
        return builtin_channels()

    @hookimpl
    def tailcam_plugin_info(self) -> list:
        return [
            PluginInfo(
                id="builtin-channels",
                name="Built-in alert channels",
                kind="notification",
                description="Discord, Telegram, and generic webhook notification channels.",
                builtin=True,
            )
        ]
