"""Safe starting points for printer-focused single-camera timelapses."""

from __future__ import annotations

from copy import deepcopy

_PRESETS: tuple[dict[str, object], ...] = (
    {
        "name": "Reliable Print",
        "settings": {
            "interval_seconds": 2.0,
            "output_fps": 30,
            "jpeg_quality": 95,
            "max_frames": 0,
            "auto_smooth": True,
            "smooth_target_fps": 60,
            "smooth_interpolate": True,
            "smooth_deflicker": True,
            "smooth_engine": "ffmpeg",
            "smooth_quality": "high",
            "analysis_enabled": False,
            "analysis_cadence_seconds": 60.0,
        },
    },
    {
        "name": "Storage Saver",
        "settings": {
            "interval_seconds": 10.0,
            "output_fps": 24,
            "jpeg_quality": 85,
            "max_frames": 0,
            "auto_smooth": True,
            "smooth_target_fps": 30,
            "smooth_interpolate": False,
            "smooth_deflicker": True,
            "smooth_engine": "ffmpeg",
            "smooth_quality": "standard",
            "analysis_enabled": False,
            "analysis_cadence_seconds": 60.0,
        },
    },
    {
        "name": "Maximum Quality",
        "settings": {
            "interval_seconds": 1.0,
            "output_fps": 30,
            "jpeg_quality": 98,
            "max_frames": 0,
            "auto_smooth": True,
            "smooth_target_fps": 60,
            "smooth_interpolate": True,
            "smooth_deflicker": True,
            "smooth_engine": "rife",
            "smooth_quality": "maximum",
            "analysis_enabled": False,
            "analysis_cadence_seconds": 60.0,
        },
    },
)


def printer_presets() -> list[dict[str, object]]:
    """Return independent payloads so callers cannot mutate shared defaults."""
    return deepcopy(list(_PRESETS))
