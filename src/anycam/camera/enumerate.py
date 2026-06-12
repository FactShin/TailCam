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


# V4L2 VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability) — 104-byte struct.
_VIDIOC_QUERYCAP = 0x80685600
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_DEVICE_CAPS = 0x80000000


def _caps_has_capture(capabilities: int, device_caps: int) -> bool:
    # device_caps describes *this* node when the DEVICE_CAPS bit is set in the
    # device-wide capabilities; otherwise fall back to the device-wide field.
    effective = device_caps if (capabilities & _V4L2_CAP_DEVICE_CAPS) else capabilities
    return bool(effective & _V4L2_CAP_VIDEO_CAPTURE)


def _v4l2_is_capture(path: str) -> bool:
    """True if the V4L2 node can actually capture video.

    A Raspberry Pi exposes many /dev/video* nodes (codec/ISP/metadata, e.g.
    video10-23) that are NOT webcams. Opening them as cameras causes select()
    timeouts and phantom offline cameras, so we query V4L2 capabilities and keep
    only VIDEO_CAPTURE nodes. A device that's busy (already held by our own
    worker) is assumed to be a real capture device.
    """
    import errno
    import fcntl
    import os

    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        buf = bytearray(104)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buf, True)
        capabilities = int.from_bytes(buf[84:88], "little")
        device_caps = int.from_bytes(buf[88:92], "little")
        return _caps_has_capture(capabilities, device_caps)
    except OSError as exc:
        return exc.errno == errno.EBUSY  # busy real device -> keep it
    except Exception:
        return False
    finally:
        if fd >= 0:
            os.close(fd)


def _enumerate_linux() -> list[CameraDescriptor]:
    descriptors: list[CameraDescriptor] = []
    for path in sorted(glob.glob("/dev/video*")):
        if not _v4l2_is_capture(path):
            continue  # skip codec/ISP/metadata nodes (common on Raspberry Pi)
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
