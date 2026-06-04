"""Discover connected webcams on Linux (V4L2), macOS (AVFoundation), and
Windows (DirectShow)."""

from __future__ import annotations

import glob
import sys
from pathlib import Path

from anycam.camera.source import CameraDescriptor, use_synthetic
from anycam.logging_setup import get_logger

log = get_logger(__name__)

SYNTHETIC_DESCRIPTOR = CameraDescriptor(
    id="synthetic-0", name="Synthetic Camera", backend="synthetic"
)


def _linux_name(video_path: str) -> str:
    node = Path(video_path).name  # e.g. "video0"
    sysfs = Path(f"/sys/class/video4linux/{node}/name")
    try:
        return sysfs.read_text().strip() or node
    except OSError:
        return node


def _enumerate_linux() -> list[CameraDescriptor]:
    descriptors: list[CameraDescriptor] = []
    for path in sorted(glob.glob("/dev/video*")):
        # Probe capability cheaply: a capture node must be openable. We avoid
        # opening here (slow, exclusive) and instead rely on sysfs presence;
        # non-capture nodes get filtered when a worker fails to start.
        descriptors.append(
            CameraDescriptor(id=path, name=_linux_name(path), backend="v4l2")
        )
    return descriptors


def _enumerate_macos(max_probe: int = 8) -> list[CameraDescriptor]:
    import cv2

    descriptors: list[CameraDescriptor] = []
    for index in range(max_probe):
        cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        opened = cap.isOpened()
        cap.release()
        if opened:
            descriptors.append(
                CameraDescriptor(id=str(index), name=f"Camera {index}", backend="avfoundation")
            )
        elif index > 0:
            # Indices are contiguous; stop at the first gap after index 0.
            break
    return descriptors


def _windows_names() -> list[str]:
    """Friendly camera names via the optional pygrabber DirectShow helper."""
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore

        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def _enumerate_windows(max_probe: int = 8) -> list[CameraDescriptor]:
    import cv2

    names = _windows_names()
    descriptors: list[CameraDescriptor] = []
    for index in range(max_probe):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        opened = cap.isOpened()
        cap.release()
        if opened:
            name = names[index] if index < len(names) else f"Camera {index}"
            descriptors.append(CameraDescriptor(id=str(index), name=name, backend="dshow"))
        elif index > 0:
            # Indices are contiguous; stop at the first gap after index 0.
            break
    return descriptors


def discover() -> list[CameraDescriptor]:
    if use_synthetic():
        return [SYNTHETIC_DESCRIPTOR]
    try:
        if sys.platform == "darwin":
            found = _enumerate_macos()
        elif sys.platform == "win32":
            found = _enumerate_windows()
        else:
            found = _enumerate_linux()
    except Exception as exc:  # pragma: no cover - hardware/driver dependent
        log.warning("Camera discovery failed: %s", exc)
        found = []
    if not found:
        log.info("No physical cameras detected; offering synthetic camera.")
        return [SYNTHETIC_DESCRIPTOR]
    return found
