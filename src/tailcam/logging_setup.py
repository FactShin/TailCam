"""Logging configuration for TailCam."""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once. Level may come from ``TAILCAM_LOG_LEVEL``."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    env_level = os.environ.get("TAILCAM_LOG_LEVEL") or os.environ.get("ANYCAM_LOG_LEVEL")
    resolved = (level or env_level or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
