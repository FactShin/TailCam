"""Motion event logger — append every motion event to a JSONL file.

The simplest possible **event hook** plugin: every motion event (label,
confidence, camera, thumbnail path) is appended as one JSON line, ready for
jq, spreadsheets, or your own automation to consume. It also doubles as the
reference implementation for the ``tailcam_event_hooks`` capability — copy it
to build your own automations (turn on lights, ring a bell, feed the cat…).

Settings (``config.toml``)::

    [plugins.settings.event_logger]
    path = ""    # blank = <data-dir>/motion-events.jsonl

Event hooks fire on EVERY motion event, regardless of notification filters.
"""

from __future__ import annotations

__plugin__ = {
    "id": "event_logger",
    "name": "Motion event logger",
    "version": "1.0.0",
    "description": (
        "Append every motion event to a JSONL file for automation/analysis — also the "
        "reference example for building event-hook plugins."
    ),
    "author": "TailCam community",
    "kinds": ["event"],
    "settings_example": (
        '[plugins.settings.event_logger]\n'
        '# path = "/somewhere/motion-events.jsonl"\n'
    ),
}

import json
import time
from pathlib import Path

from tailcam import paths
from tailcam.plugins.sdk import PluginInfo, get_logger, hookimpl, plugin_settings

log = get_logger("plugin.event_logger")


class EventLoggerHook:
    id = "event_logger"
    name = "Motion event logger"

    def _path(self) -> Path:
        configured = str(plugin_settings("event_logger").get("path") or "").strip()
        return Path(configured) if configured else paths.data_dir() / "motion-events.jsonl"

    def on_motion(self, event) -> None:
        line = json.dumps(
            {
                "ts": time.time(),
                "camera_id": event.camera_id,
                "label": event.label,
                "confidence": event.confidence,
                "description": event.description,
                "event_id": event.event_id,
                "image_path": event.image_path,
            }
        )
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(line + "\n")


@hookimpl
def tailcam_event_hooks():
    return [EventLoggerHook()]


@hookimpl
def tailcam_plugin_info():
    return [
        PluginInfo(
            id="event_logger",
            name="Motion event logger",
            kind="other",
            description=__plugin__["description"],
            version=__plugin__["version"],
        )
    ]
