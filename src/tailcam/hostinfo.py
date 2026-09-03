"""Host capability probe: memory, CPU count, Raspberry Pi model.

TailCam runs on anything from a 1 GB Raspberry Pi to a GPU workstation. A few
defaults (stream size, encoder preset, whether object detection provisions
itself at boot) should differ between those, so services ask here instead of
guessing. Everything is best-effort and cached; nothing here can raise.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Below this much RAM the host is treated as "low power" (Pi Zero 2 / Pi 4 1-2 GB
# / Pi 5 1 GB). Can be forced either way with TAILCAM_LOW_POWER=1|0.
_LOW_POWER_RAM_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class HostProfile:
    total_ram_bytes: int
    cpu_count: int
    model: str  # e.g. "Raspberry Pi 5 Model B Rev 1.0" or ""
    is_raspberry_pi: bool
    low_power: bool

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_bytes / 1024**3, 2)


def _total_ram() -> int:
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page > 0:
                return int(pages * page)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        text = Path("/proc/meminfo").read_text()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _pi_model() -> str:
    try:
        raw = Path("/proc/device-tree/model").read_bytes()
        return raw.decode("utf-8", "ignore").strip("\x00").strip()
    except OSError:
        return ""


@lru_cache(maxsize=1)
def profile() -> HostProfile:
    ram = _total_ram()
    model = _pi_model() if sys.platform.startswith("linux") else ""
    is_pi = "raspberry pi" in model.lower()
    override = os.environ.get("TAILCAM_LOW_POWER")
    if override in ("1", "true", "yes"):
        low = True
    elif override in ("0", "false", "no"):
        low = False
    else:
        low = (0 < ram < _LOW_POWER_RAM_BYTES) or is_pi
    return HostProfile(
        total_ram_bytes=ram,
        cpu_count=os.cpu_count() or 1,
        model=model,
        is_raspberry_pi=is_pi,
        low_power=low,
    )


def is_low_power() -> bool:
    return profile().low_power


def x264_preset() -> str:
    """libx264 preset for live encodes (recordings, HomeKit): cheapest on a Pi."""
    return "ultrafast" if is_low_power() else "veryfast"


def encode_threads() -> int:
    """Thread budget for background encoders so they can't starve capture."""
    return 2 if is_low_power() else max(1, min(4, (os.cpu_count() or 2) // 2))
