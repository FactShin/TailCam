"""Shared, hardware-free configuration for OS installers and containers."""

from __future__ import annotations

from tailcam import __version__, paths
from tailcam.config import AppConfig, NodeConfig
from tailcam.node import ROLE_PRESETS, NodeConfigError, validate_node_name, validate_roles


def configure(
    *,
    preset: str | None = None,
    roles: str | None = None,
    node_name: str | None = None,
    port: int | None = None,
    if_missing: bool = False,
    dry_run: bool = False,
) -> dict:
    if preset is not None and roles is not None:
        raise NodeConfigError("Use either a preset or roles, not both")
    selected = None
    if preset is not None:
        if preset not in ROLE_PRESETS:
            raise NodeConfigError("Unknown preset; choose " + ", ".join(ROLE_PRESETS))
        selected = list(ROLE_PRESETS[preset])
    elif roles is not None:
        selected = validate_roles([item.strip() for item in roles.split(",")] if roles else [])
    if node_name is not None:
        node_name = validate_node_name(node_name)
    if port is not None and not 1 <= port <= 65535:
        raise NodeConfigError("Port must be between 1 and 65535")

    # Even --if-missing validates existing files: never start defaults on a hub
    # whose configuration is corrupt. Loading does not discover cameras/models.
    existing = paths.config_file().exists()
    cfg = AppConfig.load()
    changed = False
    if not (existing and if_missing):
        node = NodeConfig(
            name=cfg.node.name if node_name is None else node_name,
            roles=cfg.node.roles if selected is None else selected,
        )
        changed = node != cfg.node or (port is not None and port != cfg.server.port)
        cfg.node = node
        if port is not None:
            cfg.server.port = port
    if not dry_run and (not existing or changed):
        cfg.save()
    host = cfg.server.host
    if host in {"0.0.0.0", "::"}:
        host = "localhost"
    elif ":" in host:
        host = f"[{host}]"
    return {
        "version": __version__,
        "name": cfg.node.name,
        "roles": cfg.node.roles,
        "config": str(paths.config_file()),
        "port": cfg.server.port,
        "url": f"http://{host}:{cfg.server.port}/",
        "changed": (not existing or changed) and not dry_run,
    }
