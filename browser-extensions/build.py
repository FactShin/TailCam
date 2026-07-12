#!/usr/bin/env python3
"""Assemble TailCam Companion extension packages for each browser.

For every target (chrome, edge, firefox, safari) this script copies the
contents of ``shared/`` to ``<out>/<target>/`` (so ``background.js``,
``lib/``, ``popup/`` … sit at the package root), drops the target's
``manifest.json`` alongside them, and — unless ``--no-zip`` is given —
zips the result to ``<out>/tailcam-companion-<target>-<version>.zip``.

Standard library only; Python 3.10+.

Usage:
    python build.py                          # build all targets into ./dist
    python build.py --targets chrome firefox
    python build.py --out /tmp/pkg --no-zip
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
SHARED = ROOT / "shared"
ALL_TARGETS = ("chrome", "edge", "firefox", "safari")


def fail(message: str) -> NoReturn:
    """Print an error message and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_manifest(target: str) -> dict:
    """Parse and minimally validate a target's manifest.json."""
    path = ROOT / target / "manifest.json"
    if not path.is_file():
        fail(f"missing manifest: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")
    for key in ("manifest_version", "name", "version"):
        if key not in manifest:
            fail(f"{path} is missing required key {key!r}")
    return manifest


def assemble(target: str, out_dir: Path) -> Path:
    """Copy shared/ contents + the target manifest into <out>/<target>/."""
    pkg_dir = out_dir / target
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    shutil.copytree(SHARED, pkg_dir)
    shutil.copy2(ROOT / target / "manifest.json", pkg_dir / "manifest.json")
    return pkg_dir


def make_zip(pkg_dir: Path, out_dir: Path, target: str, version: str) -> Path:
    """Zip a package directory (paths relative to the package root)."""
    zip_path = out_dir / f"tailcam-companion-{target}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(pkg_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(pkg_dir))
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build TailCam Companion browser extension packages."
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=ALL_TARGETS,
        default=list(ALL_TARGETS),
        metavar="TARGET",
        help=f"targets to build (default: all of {', '.join(ALL_TARGETS)})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="output directory (default: ./dist next to this script)",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="assemble package directories but skip creating zips",
    )
    args = parser.parse_args(argv)

    if not SHARED.is_dir():
        fail(f"shared source directory not found: {SHARED}")

    manifests = {target: load_manifest(target) for target in args.targets}
    versions = {m["version"] for m in manifests.values()}
    if len(versions) != 1:
        detail = ", ".join(
            f"{t}={m['version']}" for t, m in sorted(manifests.items())
        )
        fail(f"manifest versions differ across targets: {detail}")
    version = versions.pop()

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[str, str, str]] = []
    for target in args.targets:
        pkg_dir = assemble(target, out_dir)
        if args.no_zip:
            rows.append((target, version, str(pkg_dir)))
        else:
            zip_path = make_zip(pkg_dir, out_dir, target, version)
            size_kib = zip_path.stat().st_size / 1024
            rows.append((target, version, f"{zip_path} ({size_kib:.0f} KiB)"))

    width = max(len(r[0]) for r in rows)
    print(f"TailCam Companion {version}")
    for target, ver, artifact in rows:
        print(f"  {target:<{width}}  {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
