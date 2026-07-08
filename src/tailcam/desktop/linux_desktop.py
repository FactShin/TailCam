"""Linux desktop integration: launcher .desktop entry, icon, optional autostart.

Freedesktop-standard files under the user's home — no root, no packaging
toolchain, and they survive the venv wipe-and-recreate reinstall flow because
``tailcam app install`` re-bakes the absolute paths (same contract as the
macOS bundle). AppImage was considered and rejected: the shell lives in the
pip-managed venv by design, sharing tailcam.update instead of needing its own
bundled Python and update channel.
"""

from __future__ import annotations

import shlex
import sys
from importlib import resources
from pathlib import Path

from tailcam.logging_setup import get_logger

log = get_logger(__name__)

APP_ID = "tailcam"


def _tailcam_exec(python: str | None = None) -> str:
    """The Exec= command: the venv's ``tailcam`` console script when present
    (clean, no quoting surprises), else ``<python> -m tailcam``."""
    py = Path(python or sys.executable)
    script = py.with_name("tailcam")
    if script.exists():
        return f"{shlex.quote(str(script))} app"
    return f"{shlex.quote(str(py))} -m tailcam app"


def desktop_entry_text(exec_cmd: str, icon_path: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=TailCam\n"
        "Comment=View your webcams from anywhere over Tailscale\n"
        f"Exec={exec_cmd}\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Video;Network;\n"
        "Keywords=camera;webcam;tailscale;monitoring;\n"
        f"StartupWMClass={APP_ID}\n"
    )


def autostart_entry_text(exec_cmd: str, icon_path: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=TailCam Tray\n"
        "Comment=TailCam tray icon (starts at login)\n"
        f"Exec={exec_cmd} --no-window\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _paths(home: Path | None = None) -> dict[str, Path]:
    h = home or Path.home()
    return {
        "entry": h / ".local/share/applications/tailcam.desktop",
        "icon": h / ".local/share/icons/hicolor/512x512/apps/tailcam.png",
        "autostart": h / ".config/autostart/tailcam-tray.desktop",
    }


def install_entries(
    home: Path | None = None, python: str | None = None, autostart: bool = False
) -> Path:
    """Write the launcher entry + icon (and optionally the autostart entry).
    Idempotent; re-run after upgrades to re-bake the venv path."""
    p = _paths(home)
    exec_cmd = _tailcam_exec(python)

    p["icon"].parent.mkdir(parents=True, exist_ok=True)
    icon_src = resources.files("tailcam.desktop") / "assets" / "app-icon-512.png"
    p["icon"].write_bytes(icon_src.read_bytes())

    p["entry"].parent.mkdir(parents=True, exist_ok=True)
    p["entry"].write_text(desktop_entry_text(exec_cmd, p["icon"]))

    if autostart:
        p["autostart"].parent.mkdir(parents=True, exist_ok=True)
        p["autostart"].write_text(autostart_entry_text(exec_cmd, p["icon"]))

    log.info("installed %s", p["entry"])
    return p["entry"]


def uninstall_entries(home: Path | None = None) -> bool:
    p = _paths(home)
    removed = False
    for path in p.values():
        if path.exists():
            path.unlink()
            removed = True
    return removed


# -- doctor diagnostics ---------------------------------------------------------
def gui_diagnostics() -> list[tuple[bool, str, str]]:
    """(ok, label, remediation) rows for `tailcam doctor` on Linux.

    Checks the system pieces pywebview/pystray need — these are apt/dnf
    packages, not pip installs, which is the number-one Linux gotcha.
    """
    rows: list[tuple[bool, str, str]] = []
    try:
        import gi  # noqa: F401

        rows.append((True, "PyGObject (gi)", ""))
    except Exception:
        rows.append((
            False, "PyGObject (gi)",
            "apt: python3-gi python3-gi-cairo | dnf: python3-gobject "
            "(and create the venv with --system-site-packages, or pip install PyGObject)",
        ))
        return rows  # nothing below can work without gi

    def probe(namespace: str, versions: list[str], label: str, hint: str) -> None:
        import gi

        for ver in versions:
            try:
                gi.require_version(namespace, ver)
                __import__(f"gi.repository.{namespace}", fromlist=[namespace])
                rows.append((True, f"{label} ({namespace} {ver})", ""))
                return
            except Exception:
                continue
        rows.append((False, label, hint))

    probe("Gtk", ["3.0"], "GTK 3", "apt: gir1.2-gtk-3.0 | dnf: gtk3")
    probe(
        "WebKit2", ["4.1", "4.0"], "WebKit2GTK (embedded window)",
        "apt: gir1.2-webkit2-4.1 (24.04+) / gir1.2-webkit2gtk-4.1 (22.04) | dnf: "
        "webkit2gtk4.1 — without it the dashboard opens in the browser",
    )
    probe(
        "AyatanaAppIndicator3", ["0.1"], "AppIndicator (tray)",
        "apt: gir1.2-ayatanaappindicator3-0.1 | GNOME also needs the 'AppIndicator support' "
        "shell extension — without it pystray falls back to XEmbed, which some desktops hide",
    )
    return rows
