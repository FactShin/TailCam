"""Guards for the TailCam Companion browser extension packages.

The extension ships one shared codebase (``browser-extensions/shared/``) with a
per-browser ``manifest.json`` for chrome/edge/firefox/safari, assembled by
``browser-extensions/build.py``. These tests keep the manifests parseable, in
version lockstep with ``tailcam.__version__``, pointing at files that actually
exist, and the build script runnable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import tailcam

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXT_ROOT = _REPO_ROOT / "browser-extensions"
_SHARED = _EXT_ROOT / "shared"
_TARGETS = ("chrome", "edge", "firefox", "safari")


def _load_manifests() -> dict[str, dict]:
    return {
        target: json.loads(
            (_EXT_ROOT / target / "manifest.json").read_text(encoding="utf-8")
        )
        for target in _TARGETS
    }


def _referenced_paths(value) -> set[str]:
    """Walk manifest values collecting every referenced .html/.js/.png path."""
    found: set[str] = set()
    if isinstance(value, str):
        if value.endswith((".html", ".js", ".png")):
            found.add(value)
    elif isinstance(value, dict):
        for child in value.values():
            found |= _referenced_paths(child)
    elif isinstance(value, list):
        for child in value:
            found |= _referenced_paths(child)
    return found


def test_manifests_parse_and_share_one_version():
    manifests = _load_manifests()
    versions = {target: m["version"] for target, m in manifests.items()}
    assert len(set(versions.values())) == 1, (
        f"extension manifest versions drifted apart: {versions}"
    )
    for target, manifest in manifests.items():
        assert manifest["manifest_version"] == 3, target
        assert manifest["name"] == "TailCam Companion", target


def test_extension_version_matches_app_version():
    manifests = _load_manifests()
    for target, manifest in manifests.items():
        assert manifest["version"] == tailcam.__version__, (
            f"{target}/manifest.json is {manifest['version']} but "
            f"tailcam.__version__ is {tailcam.__version__}; bump them together."
        )


def test_manifest_referenced_files_exist_in_shared():
    # Manifest paths are relative to the ASSEMBLED package root, where the
    # contents of shared/ live at the top level — so each path must resolve
    # under browser-extensions/shared/.
    for target, manifest in _load_manifests().items():
        for ref in sorted(_referenced_paths(manifest)):
            assert (_SHARED / ref).is_file(), (
                f"{target}/manifest.json references {ref!r} which does not "
                f"exist under {_SHARED}"
            )


def test_browser_specific_manifest_shapes():
    manifests = _load_manifests()
    gecko = manifests["firefox"]["browser_specific_settings"]["gecko"]
    assert gecko["id"], "firefox manifest needs a gecko extension id"
    assert manifests["firefox"]["background"]["scripts"] == ["background.js"]
    for target in ("chrome", "edge", "safari"):
        assert (
            manifests[target]["background"]["service_worker"] == "background.js"
        ), target


def test_build_script_assembles_all_targets(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(_EXT_ROOT / "build.py"),
            "--no-zip",
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for target in _TARGETS:
        pkg = tmp_path / target
        assert (pkg / "manifest.json").is_file(), result.stdout
        assert (pkg / "background.js").is_file(), result.stdout
