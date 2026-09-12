"""Shared, hardware-free configuration for OS installers and containers."""

from __future__ import annotations

from tailcam import __version__, paths
from tailcam.config import AppConfig, NodeConfig
from tailcam.node import ROLE_PRESETS, NodeConfigError, validate_node_name, validate_roles


def _validate_port(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 65535:
        raise NodeConfigError("Port must be an integer between 1 and 65535")


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
    if port is not None:
        _validate_port(port)

    # Even --if-missing validates existing files: never start defaults on a hub
    # whose configuration is corrupt. Loading does not discover cameras/models.
    config_path = paths.config_file()
    existing = config_path.exists()
    if dry_run and not existing:
        from tailcam import migrate

        # Match a real setup's upcoming migration without moving any files.
        # Use the config pair's own eligibility; pending media migration alone
        # does not mean the legacy config will be copied.
        legacy_config = migrate.pending_config_file()
        if legacy_config is not None:
            config_path = legacy_config
            existing = True
    cfg = AppConfig.load(config_path)
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
    # Validate the effective value even when preserving an existing config.
    # A provided valid override may repair it; --if-missing must not bypass it.
    _validate_port(cfg.server.port)
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
