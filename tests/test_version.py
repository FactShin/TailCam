"""Guard: the web-ui package version must track the app version.

The runtime version (shown in the UI, CLI, and /api/system) comes from
``tailcam.__version__``. ``web-ui/package.json`` is dev metadata that should be
bumped alongside it on every release — this test fails if the two drift, so a
release can't ship a stale dashboard version number.
"""

from __future__ import annotations

import json
from pathlib import Path

import tailcam

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_web_ui_version_matches_app_version():
    pkg = json.loads((_REPO_ROOT / "web-ui" / "package.json").read_text())
    assert pkg["version"] == tailcam.__version__, (
        f"web-ui/package.json is {pkg['version']} but tailcam.__version__ is "
        f"{tailcam.__version__}; bump them together."
    )


def test_version_is_release_string():
    """Dotted numeric version, 3 or 4 segments (e.g. 1.6.5 or 1.6.5.1).

    Four-segment versions are valid PEP 440 and compare correctly in
    ``tailcam.update.parse_version``; npm tolerates them in package.json
    (they only break ``npm publish``, which this project never does).
    """
    parts = tailcam.__version__.split(".")
    assert len(parts) in (3, 4) and all(p.isdigit() for p in parts), tailcam.__version__
