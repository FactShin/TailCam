"""The TailCam plugin SDK — the one import a community plugin needs.

A plugin is a single ``.py`` file (dropped in ``<config-dir>/plugins/`` or
installed from the marketplace) or a pip package exposing the ``tailcam``
entry-point group. It contributes capabilities by implementing hooks:

.. code-block:: python

    from tailcam.plugins.sdk import PluginInfo, hookimpl, plugin_settings

    class MyChannel:
        id = "mychannel"
        name = "My channel"

        def configured(self, config):
            return bool(plugin_settings("mychannel").get("url"))

        def send(self, event, config):
            ...  # POST event somewhere

    @hookimpl
    def tailcam_notification_channels():
        return [MyChannel()]

    @hookimpl
    def tailcam_plugin_info():
        return [PluginInfo(id="mychannel", name="My channel", kind="notification")]

Hook types (implement any subset):

- ``tailcam_analyzer_providers`` — AI motion-analysis backends.
- ``tailcam_notification_channels`` — alert destinations.
- ``tailcam_event_hooks`` — run on every motion event (automation).
- ``tailcam_plugin_info`` — describe the plugin for the Plugins page.

Per-plugin settings live in the user's ``config.toml``::

    [plugins.settings.mychannel]
    url = "https://example.test/hook"

and are read with :func:`plugin_settings`, so plugins never parse config files
themselves and hot-reload picks up edits.

**Security model**: plugins are ordinary Python running with the full
privileges of the TailCam process — there is no sandbox. Only install plugins
you trust: the marketplace registry is curated and every file is
checksum-verified at install time, but review is human, not magic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tailcam.logging_setup import get_logger
from tailcam.plugins.hookspecs import (
    AnalyzerProvider,
    EventHook,
    MotionEventData,
    NotificationChannel,
    PluginInfo,
    hookimpl,
)

if TYPE_CHECKING:
    from tailcam.config import AppConfig

__all__ = [
    "AnalyzerProvider",
    "EventHook",
    "MotionEventData",
    "NotificationChannel",
    "PluginInfo",
    "get_logger",
    "hookimpl",
    "plugin_settings",
]

# The live AppConfig, registered by the app at startup (and by the CLI). Kept
# module-global so a single-file plugin can read its settings without any
# handle-threading through pluggy.
_config: AppConfig | None = None


def _set_config(config: AppConfig) -> None:
    global _config
    _config = config


def plugin_settings(plugin_id: str) -> dict[str, Any]:
    """This plugin's settings table (``[plugins.settings.<plugin_id>]``).

    Returns ``{}`` when nothing is configured — plugins should treat every key
    as optional and document their settings in their header comment.
    """
    if _config is None:
        return {}
    value = _config.plugins.settings.get(plugin_id)
    return dict(value) if isinstance(value, dict) else {}
