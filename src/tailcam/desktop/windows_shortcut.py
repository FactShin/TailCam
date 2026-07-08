"""Windows desktop integration: Start-menu shortcut + optional login autostart.

A .lnk in the per-user Start Menu, created via WScript.Shell from a PowerShell
here-string (same subprocess pattern service/installer.py uses for the
Scheduled Task). The target is ``pythonw.exe -m tailcam app`` — pythonw so no
console window ever flashes (the v0.99.9 lesson). Optional autostart adds an
HKCU ``...\\Run`` value. The PowerShell scripts are built as pure strings so
they're unit-testable on any OS; only the actual execution is Windows-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tailcam.logging_setup import get_logger
from tailcam.proc import run as run_hidden

log = get_logger(__name__)

APP_NAME = "TailCam"
_RUN_KEY = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"


def _pythonw(python: str | None = None) -> Path:
    """pythonw.exe next to the interpreter (no console), else the interpreter."""
    exe = Path(python or sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else exe


def _ps_single_quote(s: str) -> str:
    """Quote a string for a PowerShell single-quoted literal ('' escapes ')."""
    return "'" + str(s).replace("'", "''") + "'"


def start_menu_dir(home: Path | None = None) -> Path:
    h = home or Path.home()
    return h / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"


def shortcut_path(home: Path | None = None) -> Path:
    return start_menu_dir(home) / f"{APP_NAME}.lnk"


def _create_shortcut_ps(lnk: Path, target: Path, icon: Path | None) -> str:
    """PowerShell that (re)creates the .lnk. Uses -m tailcam app so it never
    depends on a console-script launcher stub (which embeds an absolute venv
    path that an upgrade can invalidate)."""
    icon_line = (
        f"$s.IconLocation = {_ps_single_quote(str(icon))}\n" if icon is not None else ""
    )
    return (
        f"$W = New-Object -ComObject WScript.Shell\n"
        f"$s = $W.CreateShortcut({_ps_single_quote(str(lnk))})\n"
        f"$s.TargetPath = {_ps_single_quote(str(target))}\n"
        f"$s.Arguments = '-m tailcam app'\n"
        f"$s.WorkingDirectory = {_ps_single_quote(str(target.parent))}\n"
        f"{icon_line}"
        f"$s.Description = 'TailCam — view your webcams from anywhere over Tailscale'\n"
        f"$s.Save()\n"
    )


def _set_autostart_ps(target: Path) -> str:
    # pythonw + --no-window: tray only at login, no console, no window popping up.
    value = f'"{target}" -m tailcam app --no-window'
    return (
        f"Set-ItemProperty -Path '{_RUN_KEY}' -Name '{APP_NAME}' "
        f"-Value {_ps_single_quote(value)}\n"
    )


def _remove_autostart_ps() -> str:
    return (
        f"Remove-ItemProperty -Path '{_RUN_KEY}' -Name '{APP_NAME}' "
        f"-ErrorAction SilentlyContinue\n"
    )


def _icon_path(home: Path | None = None) -> Path:
    """Copy the app icon into the app data dir so the .lnk has a stable path."""
    from importlib import resources

    from tailcam import paths

    dest = paths.data_dir() / "tailcam.ico"
    if not dest.exists():
        # Ship a .png; Windows accepts it for IconLocation on modern builds.
        src = resources.files("tailcam.desktop") / "assets" / "app-icon-512.png"
        dest.write_bytes(src.read_bytes())
    return dest


def install_shortcut(
    home: Path | None = None, python: str | None = None, autostart: bool = False
) -> Path:
    """Create the Start-menu shortcut (and optional login autostart).
    Idempotent; re-run after upgrades to re-bake the interpreter path."""
    lnk = shortcut_path(home)
    lnk.parent.mkdir(parents=True, exist_ok=True)
    target = _pythonw(python)
    try:
        icon = _icon_path(home)
    except Exception:  # pragma: no cover - icon copy is best effort
        icon = None
    script = _create_shortcut_ps(lnk, target, icon)
    if autostart:
        script += _set_autostart_ps(target)
    _run_ps(script)
    log.info("installed Start-menu shortcut %s", lnk)
    return lnk


def uninstall_shortcut(home: Path | None = None) -> bool:
    lnk = shortcut_path(home)
    _run_ps(_remove_autostart_ps())
    if lnk.exists():
        lnk.unlink()
        return True
    return False


def _run_ps(script: str) -> None:
    run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=False
    )
