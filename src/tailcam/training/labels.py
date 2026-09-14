"""Validate class names before they can become exported directory names."""

from __future__ import annotations

import re
import unicodedata

_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", re.I)


def validate_class_label(value: str | None) -> str | None:
    """Keep display names intact, but reject unsafe portable path components.

    Recheck at export too: older databases and imported records may predate the
    input validation. Empty/None labels remain the existing unlabelled state.
    """
    if value is None or value == "":
        return value
    if (
        not isinstance(value, str)
        or len(value) > 64
        or value in {".", ".."}
        or value != value.strip()
        or value.endswith(".")
        or any(c in value for c in '/\\:*?"<>|')
        or any(unicodedata.category(c).startswith("C") for c in value)
        or _RESERVED.fullmatch(value)
    ):
        raise ValueError("Class labels must be safe names of at most 64 characters")
    return value
