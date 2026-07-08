"""The pure menu model: (ServerState, nodes) -> list[MenuSpec].

Everything the tray shows is decided here, with no GUI imports, so every
state combination is unit-testable on a headless CI box. tray.py only
translates these specs into pystray objects.
"""

from __future__ import annotations

from tailcam.desktop.state import MenuSpec, Node, ServerState

# Action ids dispatched to DesktopApp.dispatch(). Node entries use the
# "open-node:<url>" form.
OPEN_DASHBOARD = "open-dashboard"
SERVICE_START = "service-start"
SERVICE_STOP = "service-stop"
SERVICE_RESTART = "service-restart"
SERVICE_INSTALL = "service-install"
CHECK_UPDATES = "check-updates"
APPLY_UPDATE = "apply-update"
QUIT = "quit"
OPEN_NODE_PREFIX = "open-node:"


def _status_line(state: ServerState) -> MenuSpec:
    if state.client_mode:
        where = state.base_url.removeprefix("https://").removeprefix("http://").rstrip("/")
        label = f"TailCam @ {where}" + ("" if state.running else " — unreachable")
    elif state.running:
        label = f"TailCam {state.version or ''} — running".rstrip()
    elif state.installed:
        label = "TailCam — stopped"
    else:
        label = "TailCam — service not installed"
    return MenuSpec(label=label, enabled=False)


def _nodes_submenu(nodes: list[Node]) -> MenuSpec | None:
    others = [n for n in nodes if n.kind != "local"]
    if not others:
        return None
    children = []
    for n in others:
        cams = f" · {n.camera_count} cam{'s' if n.camera_count != 1 else ''}"
        if not n.online:
            children.append(MenuSpec(label=f"{n.host} — offline", enabled=False))
        elif n.url is None:
            # No routable HTTPS dashboard for this peer (Tailscale Serve not
            # up / non-DNS host) — visible but disabled, with the reason.
            children.append(
                MenuSpec(label=f"{n.host}{cams} — enable Tailscale Serve to open", enabled=False)
            )
        else:
            children.append(MenuSpec(label=f"{n.host}{cams}", action=OPEN_NODE_PREFIX + n.url))
    return MenuSpec(label="Nodes", children=children)


def build_menu(state: ServerState, nodes: list[Node]) -> list[MenuSpec]:
    items: list[MenuSpec] = [
        _status_line(state),
        MenuSpec.sep(),
        MenuSpec(label="Open Dashboard", action=OPEN_DASHBOARD, enabled=state.running),
    ]
    sub = _nodes_submenu(nodes)
    if sub is not None:
        items.append(sub)
    items.append(MenuSpec.sep())

    if not state.client_mode:
        if not state.installed:
            items.append(MenuSpec(label="Install && Start Service", action=SERVICE_INSTALL))
        elif state.running:
            items.append(MenuSpec(label="Restart Service", action=SERVICE_RESTART))
            items.append(MenuSpec(label="Stop Service", action=SERVICE_STOP))
        else:
            items.append(MenuSpec(label="Start Service", action=SERVICE_START))
        items.append(MenuSpec.sep())

    if state.update_available:
        label = f"Update available — install {state.update_latest}".rstrip()
        # Updating remotely isn't supported from the shell; admins update the
        # node from its own dashboard/CLI.
        items.append(
            MenuSpec(label=label, action=APPLY_UPDATE, enabled=not state.client_mode)
        )
    else:
        items.append(
            MenuSpec(label="Check for Updates", action=CHECK_UPDATES, enabled=state.running)
        )
    items.append(MenuSpec.sep())
    items.append(MenuSpec(label="Quit TailCam", action=QUIT))
    return items
