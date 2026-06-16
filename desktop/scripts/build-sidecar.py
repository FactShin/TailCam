#!/usr/bin/env python3
"""Build the TailCam node sidecar for the current Tauri target."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "desktop" / "sidecars" / "tailcam-node.spec"
DIST_EXE = ROOT / "dist" / (
    "tailcam-node.exe" if platform.system() == "Windows" else "tailcam-node"
)
BINARIES = ROOT / "desktop" / "src-tauri" / "binaries"


def target_triple(system: str | None = None, machine: str | None = None) -> str:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    machine = {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }.get(machine, machine)

    if system == "Darwin":
        if machine == "aarch64":
            return "aarch64-apple-darwin"
        if machine == "x86_64":
            return "x86_64-apple-darwin"
    if system == "Windows":
        if machine == "aarch64":
            return "aarch64-pc-windows-msvc"
        if machine == "x86_64":
            return "x86_64-pc-windows-msvc"
    if system == "Linux":
        if machine == "aarch64":
            return "aarch64-unknown-linux-gnu"
        if machine == "x86_64":
            return "x86_64-unknown-linux-gnu"
    raise SystemExit(f"Unsupported sidecar target: {system} {machine}")


def sidecar_filename(triple: str, system: str | None = None) -> str:
    suffix = ".exe" if (system or platform.system()) == "Windows" else ""
    return f"tailcam-node-{triple}{suffix}"


def verify_not_cross_compiling(current: str) -> None:
    requested = os.environ.get("TAURI_ENV_TARGET_TRIPLE") or os.environ.get("TARGET")
    if requested and requested != current:
        raise SystemExit(
            f"Refusing to cross-compile TailCam sidecar: host is {current}, requested {requested}"
        )


def build() -> Path:
    triple = target_triple()
    verify_not_cross_compiling(triple)
    BINARIES.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=ROOT,
        check=True,
    )
    if not DIST_EXE.exists():
        raise SystemExit(f"PyInstaller did not create expected executable: {DIST_EXE}")

    target = BINARIES / sidecar_filename(triple)
    shutil.copy2(DIST_EXE, target)
    target.chmod(target.stat().st_mode | 0o755)
    print(target)
    return target


if __name__ == "__main__":
    build()
