#!/usr/bin/env python3
"""Regenerate marketplace/index.json from the plugin files.

Run after adding or changing any plugin under ``marketplace/plugins/``::

    python marketplace/build_index.py

Each plugin declares its metadata in a module-level ``__plugin__`` dict; this
script parses it statically (no imports, so it runs anywhere) and pins the
file's sha256 — the checksum TailCam verifies at install time. CI/reviewers
re-run this and diff: a stale or hand-edited index fails review.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
REQUIRED = ("id", "name", "version", "description")


def plugin_meta(path: Path) -> dict:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__plugin__" for t in node.targets)
        ):
            meta = ast.literal_eval(node.value)
            break
    else:
        raise SystemExit(f"{path.name}: missing module-level __plugin__ dict")
    for key in REQUIRED:
        if not meta.get(key):
            raise SystemExit(f"{path.name}: __plugin__ is missing required key {key!r}")
    if meta["id"] != path.stem:
        raise SystemExit(f"{path.name}: __plugin__ id {meta['id']!r} must equal the file stem")
    return meta


def main() -> None:
    entries = []
    for path in sorted((ROOT / "plugins").glob("*.py")):
        if path.name.startswith("_"):
            continue
        meta = plugin_meta(path)
        payload = path.read_bytes()
        entries.append(
            {
                "id": meta["id"],
                "name": meta["name"],
                "version": meta["version"],
                "description": meta["description"],
                "author": meta.get("author", ""),
                "kinds": meta.get("kinds", []),
                "file": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "settings_example": meta.get("settings_example", ""),
                "homepage": meta.get(
                    "homepage",
                    "https://github.com/FactShin/TailCam/blob/main/marketplace/plugins/"
                    + path.name,
                ),
                "min_tailcam": meta.get("min_tailcam", ""),
            }
        )
    index = {"schema_version": 1, "plugins": entries}
    out = ROOT / "index.json"
    out.write_text(json.dumps(index, indent=2) + "\n")
    print(f"wrote {out} ({len(entries)} plugin(s))")


if __name__ == "__main__":
    sys.exit(main())
