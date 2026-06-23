"""Plugin contracts for TailCam (pluggy-based).

Plugins extend TailCam by contributing **providers** for a capability. Two kinds
ship today:

- ``AnalyzerProvider`` — an AI motion-analysis backend (Ollama is built in).
- ``NotificationChannel`` — an alert destination (Discord/Telegram/webhook are
  built in).

A plugin is any object/module exposing ``@hookimpl`` methods for the hookspecs
below. Built-in plugins are registered directly; third-party plugins are
discovered via the ``tailcam`` setuptools entry-point group, or as single-file
modules dropped in the config dir's ``plugins/`` folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pluggy

if TYPE_CHECKING:
    from tailcam.ai.analyzer import FrameAnalyzer
    from tailcam.config import AIConfig, NotificationsConfig
    from tailcam.notify.service import NotificationEvent

PROJECT = "tailcam"
hookspec = pluggy.HookspecMarker(PROJECT)
hookimpl = pluggy.HookimplMarker(PROJECT)


@dataclass
class PluginInfo:
    """What the Plugins page / CLI shows for a plugin."""

    id: str
    name: str
    kind: str  # "ai" | "notification" | "other"
    description: str = ""
    version: str = ""
    builtin: bool = False


@runtime_checkable
class AnalyzerProvider(Protocol):
    """An AI analyzer backend. ``build`` returns something the motion worker can
    use (the :class:`~tailcam.ai.analyzer.FrameAnalyzer` protocol)."""

    id: str
    name: str
    description: str

    def build(self, config: AIConfig) -> FrameAnalyzer: ...


@runtime_checkable
class NotificationChannel(Protocol):
    """An alert destination."""

    id: str
    name: str

    def configured(self, config: NotificationsConfig) -> bool: ...

    def send(self, event: NotificationEvent, config: NotificationsConfig) -> None: ...


# -- hook specifications ---------------------------------------------------
@hookspec
def tailcam_analyzer_providers() -> list[AnalyzerProvider]:  # type: ignore[empty-body]
    """Return the AI analyzer providers this plugin offers."""


@hookspec
def tailcam_notification_channels() -> list[NotificationChannel]:  # type: ignore[empty-body]
    """Return the notification channels this plugin offers."""


@hookspec
def tailcam_plugin_info() -> list[PluginInfo]:  # type: ignore[empty-body]
    """Describe this plugin (one or more PluginInfo entries)."""
