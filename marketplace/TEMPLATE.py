"""<One line: what your plugin does.>

<A short paragraph: what it integrates with, what the user gets.>

Settings (``config.toml``)::

    [plugins.settings.my_plugin]
    example_option = "value"

This is the marketplace template. To use it:

1. Copy to ``my_plugin.py`` (the file stem MUST equal ``__plugin__["id"]``).
2. Implement one or more hook types below — delete the ones you don't need.
3. Test locally: drop the file into ``<config-dir>/plugins/`` and hit
   **Reload** on the Plugins page (or restart). Errors show on that page.
4. Publish: PR the file + regenerated index into ``marketplace/`` (see
   marketplace/README.md).

Rules of the road: one file, stdlib + tailcam + tailcam's deps (httpx, cv2,
numpy) only, catch your own exceptions, read secrets from plugin_settings().
"""

from __future__ import annotations

# Everything the Plugins page and the registry need to know about you.
__plugin__ = {
    "id": "my_plugin",  # = file stem; lowercase, digits, underscores
    "name": "My plugin",
    "version": "1.0.0",
    "description": "One sentence shown in the marketplace.",
    "author": "you",
    "kinds": ["notification"],  # any of: ai | notification | event
    "settings_example": '[plugins.settings.my_plugin]\nexample_option = "value"\n',
}

from tailcam.plugins.sdk import PluginInfo, get_logger, hookimpl, plugin_settings

log = get_logger("plugin.my_plugin")


# --- capability 1: a notification channel -----------------------------------
class MyChannel:
    id = "my_plugin"
    name = "My channel"

    def configured(self, config) -> bool:
        """Only configured channels are used. Read your own settings table."""
        return bool(plugin_settings("my_plugin").get("example_option"))

    def send(self, event, config) -> None:
        """event: NotificationEvent(kind, title, body, severity, image_path, data).
        NEVER raise — log and move on; a dead integration must not break alerts."""
        try:
            log.info("would send: %s — %s", event.title, event.body)
        except Exception as exc:  # noqa: BLE001 - resilience over purity
            log.warning("send failed: %s", exc)


@hookimpl
def tailcam_notification_channels():
    return [MyChannel()]


# --- capability 2: an event hook (automation) --------------------------------
# class MyHook:
#     id = "my_plugin"
#     name = "My automation"
#
#     def on_motion(self, event) -> None:
#         """event: MotionEventData(camera_id, label, confidence, description,
#         event_id, image_path). Fires on EVERY motion event."""
#
#
# @hookimpl
# def tailcam_event_hooks():
#     return [MyHook()]


# --- capability 3: an AI analyzer provider ------------------------------------
# class MyProvider:
#     id = "my_provider"          # what users put in [ai] provider = "..."
#     name = "My provider"
#     description = "Labels motion frames with ..."
#
#     def build(self, ai_config):
#         """Return an object with .enabled (bool) and .analyze(image) ->
#         tailcam.ai.analyzer.Analysis | None."""
#         raise NotImplementedError
#
#
# @hookimpl
# def tailcam_analyzer_providers():
#     return [MyProvider()]


# --- always: describe yourself -------------------------------------------------
@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id=__plugin__["id"],
            name=__plugin__["name"],
            kind=__plugin__["kinds"][0] if __plugin__["kinds"] else "other",
            description=__plugin__["description"],
            version=__plugin__["version"],
        )
    ]
