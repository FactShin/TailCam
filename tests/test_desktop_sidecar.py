from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_bundles_tailcam_package_data() -> None:
    spec = ROOT / "desktop" / "sidecars" / "tailcam-node.spec"
    text = spec.read_text(encoding="utf-8")

    assert "src/tailcam/__main__.py" in text
    assert 'name="tailcam-node"' in text
    assert "collect_submodules(\"tailcam\")" in text
    assert "collect_data_files(\"tailcam\"" in text
    assert "web/spa/**/*" in text
    assert "web/static/**/*" in text
    assert "web/templates/**/*" in text
    assert "console=False" in text


def test_build_script_targets_current_tauri_sidecar_name() -> None:
    namespace = runpy.run_path(str(ROOT / "desktop" / "scripts" / "build-sidecar.py"))

    triple = namespace["target_triple"]("Darwin", "arm64")
    assert triple == "aarch64-apple-darwin"

    sidecar_name = namespace["sidecar_filename"]("x86_64-pc-windows-msvc", "Windows")
    assert sidecar_name == "tailcam-node-x86_64-pc-windows-msvc.exe"


def test_smoke_script_uses_no_tailscale_and_owned_temp_dirs() -> None:
    smoke = ROOT / "desktop" / "scripts" / "smoke-sidecar.py"
    text = smoke.read_text(encoding="utf-8")

    assert "TAILCAM_SYNTHETIC" in text
    assert "TAILCAM_DATA_DIR" in text
    assert "TAILCAM_CONFIG_DIR" in text
    assert "auto_discover = false" in text
    assert "\"--no-tailscale\"" in text
    assert '"/api/v1/node/health"' in text
