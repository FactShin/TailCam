"""Streaming backend contract.

A backend consumes a ``FrameBuffer`` and produces an output stream. The MJPEG
backend yields multipart byte chunks; a future WebRTC backend will consume the
same buffer but negotiate a media track instead. Keeping this contract narrow is
what lets WebRTC slot in without touching capture or the web layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from anycam.camera.frame import FrameBuffer
from anycam.camera.transforms import StreamTransform


class StreamBackend(ABC):
    media_type: str

    @abstractmethod
    def stream(
        self, buffer: FrameBuffer, transform: StreamTransform, target_fps: int, quality: int
    ) -> AsyncIterator[bytes]: ...
