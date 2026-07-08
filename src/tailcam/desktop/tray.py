"""pystray adapter: renders the pure MenuSpec model as the OS tray/menu-bar.

All pystray/PIL imports stay inside functions so this module imports cleanly
on hosts without GUI backends (the --smoke/--check paths and headless CI).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from importlib import resources
from typing import TYPE_CHECKING

from tailcam.desktop.state import MenuSpec
from tailcam.logging_setup import get_logger

if TYPE_CHECKING:  # pragma: no cover
    import pystray

log = get_logger(__name__)


def _icon_image():
    """Tray icon: monochrome template on macOS (auto-adapts to the menu bar's
    light/dark appearance), the color mark elsewhere."""
    from PIL import Image

    name = "tray-icon-template.png" if sys.platform == "darwin" else "tray-icon.png"
    path = resources.files("tailcam.desktop") / "assets" / name
    with resources.as_file(path) as p:
        return Image.open(p).copy()


def to_pystray_items(specs: list[MenuSpec], dispatch: Callable[[str], None]) -> list:
    """Translate MenuSpecs into pystray.MenuItem objects."""
    import pystray

    def entry(spec: MenuSpec):
        if spec.separator:
            return pystray.Menu.SEPARATOR
        if spec.children:
            return pystray.MenuItem(
                spec.label, pystray.Menu(*[entry(c) for c in spec.children])
            )
        action = spec.action

        def on_click(icon, item, _action=action):
            if _action:
                dispatch(_action)

        return pystray.MenuItem(spec.label, on_click, enabled=spec.enabled)

    return [entry(s) for s in specs]


def run_tray(
    build_specs: Callable[[], list[MenuSpec]],
    dispatch: Callable[[str], None],
    on_ready: Callable[[pystray.Icon], None] | None = None,
) -> None:
    """Blocking: runs the tray event loop on the calling (main) thread.

    The menu is a single callable, which pystray re-evaluates every time the
    user opens it — state (running/stopped/update badge) is always fresh
    without a polling repaint.
    """
    import pystray

    icon = pystray.Icon(
        "tailcam",
        icon=_icon_image(),
        title="TailCam",
        menu=pystray.Menu(lambda: to_pystray_items(build_specs(), dispatch)),
    )

    def setup(i: pystray.Icon) -> None:
        i.visible = True
        if on_ready is not None:
            on_ready(i)

    icon.run(setup=setup)
