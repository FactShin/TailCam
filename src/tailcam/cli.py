"""TailCam command-line interface."""

from __future__ import annotations

import os
import subprocess
import sys

import typer
from rich.console import Console
from rich.table import Table

from tailcam import __version__, paths
from tailcam.config import AppConfig
from tailcam.logging_setup import setup_logging

app = typer.Typer(
    help="TailCam — view any webcam from anywhere over Tailscale.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
tailscale_app = typer.Typer(help="Tailscale integration commands.", no_args_is_help=True)
app.add_typer(tailscale_app, name="tailscale")
mcp_app = typer.Typer(help="Model Context Protocol server for agents.", no_args_is_help=True)
app.add_typer(mcp_app, name="mcp")
desktop_app = typer.Typer(
    help="Desktop app: menu-bar/tray icon + dashboard window (pip install 'tailcam[desktop]').",
    invoke_without_command=True,
)
app.add_typer(desktop_app, name="app")

console = Console()

_STATUS_COLOR = {"online": "green", "degraded": "yellow", "offline": "red"}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the TailCam version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """TailCam — view any webcam from anywhere over Tailscale.

    Run [bold]tailcam run[/bold] to start the server, or [bold]tailcam status[/bold]
    to see your cameras and tailnet nodes.
    """
    # First run after a clean reinstall from the old AnyCam build: pull the old
    # config/media/database across. Cheap marker check, then a one-time move.
    from tailcam import migrate

    if migrate.needs_migration():
        for line in migrate.migrate():
            console.print(f"[dim]· {line}[/dim]")


@app.command()
def run(
    host: str | None = typer.Option(None, help="Bind address (default from config)."),
    port: int | None = typer.Option(None, help="Bind port (default from config / $TAILCAM_PORT)."),
    no_tailscale: bool = typer.Option(False, "--no-tailscale", help="Do not run tailscale serve."),
) -> None:
    """Start the TailCam web server."""
    import uvicorn

    paths.ensure_dirs()
    if sys.stdout is None or sys.stderr is None:
        # Windowless service (pythonw on Windows): stdout/stderr don't exist,
        # which breaks uvicorn's logging and hides crashes. Send both to a log
        # file so the service runs reliably and failures are inspectable.
        log_file = open(  # noqa: SIM115 - lives for the process lifetime
            paths.data_dir() / "tailcam.log", "a", buffering=1, encoding="utf-8"
        )
        sys.stdout = sys.stdout or log_file
        sys.stderr = sys.stderr or log_file

    setup_logging()
    if not paths.config_file().exists():
        AppConfig().save()  # write an editable default config on first run
    config = AppConfig.load()
    if host:
        config.server.host = host
    env_port = os.environ.get("TAILCAM_PORT")
    if port:
        config.server.port = port
    elif env_port and env_port.isdigit():
        config.server.port = int(env_port)
    if no_tailscale:
        config.tailscale.auto_serve = False

    # Friendly guard: if the port is taken, TailCam is probably already running
    # as the background service (don't dump a bind traceback).
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((config.server.host, config.server.port))
    except OSError:
        typer.echo(
            f"Port {config.server.port} is already in use — TailCam is likely already "
            f"running (the background service). Check it with `tailcam doctor`, or use "
            f"a different port: `tailcam run --port <N>`."
        )
        raise typer.Exit(code=1) from None
    finally:
        probe.close()

    # Preflight OpenCV before importing the app: several modules import cv2 at
    # module level, so a broken/missing build (e.g. a hand-built native ARM64
    # Windows env — no cv2 wheels exist there) would otherwise die in a raw
    # ImportError traceback instead of an actionable message.
    try:
        import cv2  # noqa: F401
    except Exception as exc:
        typer.echo(f"OpenCV (cv2) failed to load: {exc}")
        typer.echo(
            "TailCam's camera stack needs OpenCV. Re-run the installer; on an "
            "ARM64 Windows PC (Surface/Snapdragon X) it must use x64 Python — "
            "OpenCV publishes no native ARM64 wheels yet."
        )
        raise typer.Exit(code=1) from None

    from tailcam.web.app import create_app

    application = create_app(config)
    # proxy_headers=False so request.client.host is always the real socket peer.
    # The management API's principal parser trusts Tailscale identity headers only
    # on loopback (Tailscale Serve -> 127.0.0.1); honoring X-Forwarded-For here
    # would let a forwarded address spoof that loopback anchor.
    uvicorn.run(
        application,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        proxy_headers=False,
    )


def _fleet_tables(config: AppConfig, local_descriptors) -> None:
    """Render the cameras + tailnet-nodes tables (shared by status/doctor)."""
    from tailcam.cluster.fleet import gather_fleet

    fleet = gather_fleet(config, len(local_descriptors))

    cams = Table(title="Cameras", title_style="bold", header_style="bold cyan", expand=False)
    cams.add_column("Host")
    cams.add_column("Name")
    cams.add_column("Backend")
    cams.add_column("Status")
    for d in local_descriptors:
        cams.add_row(fleet.local_host, d.name, d.backend, "[dim]local[/dim]")
    for c in fleet.remote_cameras:
        color = _STATUS_COLOR.get(c.get("status", "offline"), "red")
        cams.add_row(c.get("host", "?"), c.get("name", "?"), c.get("backend", "?"),
                     f"[{color}]{c.get('status', '?')}[/{color}]")
    if local_descriptors or fleet.remote_cameras:
        console.print(cams)
    else:
        console.print("[yellow]No cameras detected.[/yellow]")

    if len(fleet.nodes) > 1:
        nodes = Table(title="Tailnet nodes", title_style="bold", header_style="bold cyan")
        nodes.add_column("Host")
        nodes.add_column("Role")
        nodes.add_column("Reachable")
        nodes.add_column("Version")
        nodes.add_column("Cameras", justify="right")
        for n in fleet.nodes:
            ok = "[green]yes[/green]" if n.reachable else "[red]no[/red]"
            nodes.add_row(n.host, n.role, ok, n.version or "[dim]?[/dim]", str(n.camera_count))
        console.print(nodes)


@app.command()
def status() -> None:
    """Show cameras, tailnet nodes, and the access URL."""
    setup_logging("WARNING")
    paths.ensure_dirs()
    config = AppConfig.load()

    from tailcam.camera import enumerate as cam_enumerate
    from tailcam.tailscale.client import TailscaleClient

    console.print(f"[bold]TailCam {__version__}[/bold]")
    _fleet_tables(config, cam_enumerate.discover())

    ts = TailscaleClient()
    st = ts.status()
    serve_port = config.tailscale.serve_port
    ts_state = "[green]running[/green]" if st.running else (
        "[yellow]installed, stopped[/yellow]" if st.installed else "[red]not installed[/red]"
    )
    console.print(f"\nTailscale: {ts_state}")
    access = ts.access_url(config.server.port, st.running, serve_port)
    console.print(f"Local URL:  [cyan]http://localhost:{config.server.port}/[/cyan]")
    console.print(f"Access URL: [cyan]{access}[/cyan]")


@app.command()
def doctor() -> None:
    """Run diagnostic checks (Python, OpenCV, cameras, Tailscale, fleet)."""
    setup_logging("ERROR")
    paths.ensure_dirs()
    config = AppConfig.load()

    def ok(label: str, detail: str = "") -> None:
        console.print(f"[green]✓[/green] {label}" + (f"  [dim]{detail}[/dim]" if detail else ""))

    def bad(label: str, detail: str = "") -> None:
        console.print(f"[red]✗[/red] {label}" + (f"  [dim]{detail}[/dim]" if detail else ""))

    pyv = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    (ok if sys.version_info[:2] >= (3, 10) else bad)("Python 3.10+", pyv)

    # Desktop app backends (optional — the server runs fine without them).
    from tailcam.desktop import have_tray, have_webview

    tray_ok, tray_detail = have_tray()
    web_ok, web_detail = have_webview()
    if tray_ok:
        ok("Desktop tray backend", tray_detail)
    else:
        console.print(
            f"[dim]·[/dim] Desktop tray backend  "
            f"[dim]{tray_detail} — pip install 'tailcam\\[desktop]'[/dim]"
        )
    if web_ok:
        ok("Desktop webview backend", web_detail)
    elif tray_ok:
        console.print(
            f"[dim]·[/dim] Desktop webview  "
            f"[dim]{web_detail} — dashboard opens in the browser[/dim]"
        )
    if sys.platform.startswith("linux") and not (tray_ok and web_ok):
        from tailcam.desktop.linux_desktop import gui_diagnostics

        for row_ok, label, hint in gui_diagnostics():
            if row_ok:
                ok(label)
            else:
                console.print(f"[dim]·[/dim] {label}  [dim]{hint}[/dim]")

    try:
        import cv2

        ok("OpenCV import", f"cv2 {cv2.__version__}")
    except Exception as exc:
        bad("OpenCV import", str(exc))

    from tailcam.camera import enumerate as cam_enumerate

    descriptors = cam_enumerate.discover()
    real = [d for d in descriptors if d.backend != "synthetic"]
    cam_detail = f"{len(real)} device(s)" if real else "none (synthetic only)"
    (ok if real else bad)("Cameras detected", cam_detail)
    if not real and sys.platform == "darwin":
        console.print(
            "  [dim]macOS: if you see 'not authorized to capture video' above, grant camera "
            "access in[/dim]\n"
            "  [dim]System Settings › Privacy & Security › Camera (enable Terminal, or the "
            "TailCam/Python entry), then re-run.[/dim]"
        )

    data_writable = os.access(paths.data_dir(), os.W_OK)
    (ok if data_writable else bad)("Data dir writable", str(paths.data_dir()))
    ok("Config file", str(paths.config_file()))

    # Is a server (the background service) actually serving on the configured port?
    import httpx

    port = config.server.port
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/system", timeout=2.0)
        if r.status_code == 200:
            ok("Server running", f"serving on http://localhost:{port}/")
        else:
            bad("Server running", f"port {port} returned HTTP {r.status_code}")
    except Exception:
        bad("Server running", f"nothing on :{port} (start it: `tailcam install-service`)")

    from tailcam.tailscale.client import TailscaleClient

    st = TailscaleClient().status()
    if st.running:
        ok("Tailscale running", st.magic_dns or st.ipv4 or "")
    elif st.installed:
        bad("Tailscale", "installed but not running (run `tailscale up`)")
    else:
        bad("Tailscale", "not installed")

    from tailcam.cluster.fleet import gather_fleet

    fleet = gather_fleet(config, len(descriptors))
    peers = [n for n in fleet.nodes if n.role == "peer"]
    reachable = [n for n in peers if n.reachable]
    if peers:
        ok("Tailnet peers", f"{len(reachable)}/{len(peers)} reachable")
    else:
        console.print("[dim]·[/dim] No peer nodes discovered (single-host).")


@app.command()
def cameras() -> None:
    """List detected cameras."""
    from tailcam.camera import enumerate as cam_enumerate

    for desc in cam_enumerate.discover():
        typer.echo(f"{desc.id}\t{desc.backend}\t{desc.name}")


@app.command(name="install-service")
def install_service() -> None:
    """Install and start the TailCam background service (uses the configured port)."""
    setup_logging()
    from tailcam.service import installer

    typer.echo(installer.install())


@app.command(name="uninstall-service")
def uninstall_service() -> None:
    """Stop and remove the TailCam background service."""
    setup_logging()
    from tailcam.service import installer

    typer.echo(installer.uninstall())


def _wait_for_server(port: int, timeout: float = 8.0) -> bool:
    """Poll the local API until the server answers (used after start/restart)."""
    import time

    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/system", timeout=1.5).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def _report_server(port: int) -> None:
    if _wait_for_server(port):
        console.print(f"[green]✓[/green] Serving on [cyan]http://localhost:{port}/[/cyan]")
    else:
        console.print(
            f"[yellow]![/yellow] Server not answering on :{port} yet — check `tailcam doctor`."
        )


@app.command()
def start() -> None:
    """Start the TailCam background service."""
    setup_logging("WARNING")
    from tailcam.service import installer

    msg = installer.start()
    typer.echo(msg)
    if "install-service" not in msg:
        _report_server(AppConfig.load().server.port)


@app.command()
def stop() -> None:
    """Stop the TailCam background service (it starts again at next login/boot)."""
    setup_logging("WARNING")
    from tailcam.service import installer

    typer.echo(installer.stop())


@app.command()
def restart() -> None:
    """Restart the TailCam background service (e.g. after changing config)."""
    setup_logging("WARNING")
    from tailcam.service import installer

    msg = installer.restart()
    typer.echo(msg)
    if "install-service" not in msg:
        _report_server(AppConfig.load().server.port)


@tailscale_app.command("serve")
def tailscale_serve(
    https_port: int | None = typer.Option(
        None, "--https-port", help="Tailnet HTTPS port (443, 8443, or 10000). Saved to config."
    ),
) -> None:
    """Expose the TailCam web UI over HTTPS within your tailnet.

    By default TailCam serves on port 8443 (https://<host>.ts.net:8443/) so it
    won't take over the root URL another app may be using.
    """
    from tailcam.tailscale.client import TailscaleClient

    config = AppConfig.load()
    if https_port is not None:
        config.tailscale.serve_port = https_port
        config.save()
    serve_port = config.tailscale.serve_port

    ts = TailscaleClient()
    if not ts.status().running:
        typer.echo("Tailscale is not running. Run `sudo tailscale up` first.")
        raise typer.Exit(code=1)
    if ts.serve(config.server.port, serve_port):
        typer.echo(f"Serving on {ts.access_url(config.server.port, True, serve_port)}")
    else:
        typer.echo("Failed to start tailscale serve.")
        if sys.platform != "win32":
            import getpass

            typer.echo(
                f"  If it says 'Access denied', grant operator rights: "
                f"sudo tailscale set --operator={getpass.getuser()}"
            )
        raise typer.Exit(code=1)


@tailscale_app.command("serve-off")
def tailscale_serve_off(
    https_port: int | None = typer.Option(
        None, "--https-port", help="Port to stop serving (defaults to the configured one)."
    ),
) -> None:
    """Remove TailCam's tailnet handler on its HTTPS port (leaves other apps intact)."""
    from tailcam.tailscale.client import TailscaleClient

    config = AppConfig.load()
    port = https_port if https_port is not None else config.tailscale.serve_port
    if TailscaleClient().serve_off(port):
        typer.echo(f"Stopped serving on tailnet port {port}.")
    else:
        typer.echo(f"Could not stop serving on port {port} (was it active?).")


@tailscale_app.command("status")
def tailscale_status() -> None:
    """Show Tailscale status."""
    from tailcam.tailscale.client import TailscaleClient

    st = TailscaleClient().status()
    typer.echo(
        f"installed={st.installed} running={st.running} ip={st.ipv4} magicdns={st.magic_dns}"
    )


@app.command()
def config(
    init: bool = typer.Option(False, "--init", help="Write a default config file if missing."),
    port: int | None = typer.Option(None, "--port", help="Set the local web UI port and save."),
    serve_port: int | None = typer.Option(
        None, "--serve-port", help="Set the tailnet HTTPS port and save."
    ),
    host: str | None = typer.Option(None, "--host", help="Set the bind address and save."),
    reset: bool = typer.Option(False, "--reset", help="Overwrite config.toml with clean defaults."),
    edit: bool = typer.Option(False, "--edit", help="Open config.toml in your $EDITOR."),
) -> None:
    """Show the config file path and values; --port/--serve-port/--host persist changes."""
    paths.ensure_dirs()
    cfg_path = paths.config_file()
    if reset:
        AppConfig().save()
        typer.echo(f"Reset config to defaults at {cfg_path}")
    if init and not cfg_path.exists():
        AppConfig().save()
        typer.echo(f"Wrote default config to {cfg_path}")
    if edit:
        if not cfg_path.exists():
            AppConfig().save()
        editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "nano")
        subprocess.run([editor, str(cfg_path)], check=False)
    cfg = AppConfig.load()
    if port is not None:
        cfg.server.port = port
    if host is not None:
        cfg.server.host = host
    if serve_port is not None:
        cfg.tailscale.serve_port = serve_port
    if port is not None or host is not None or serve_port is not None:
        cfg.save()
        typer.echo("Saved config.")

    typer.echo(f"Config file: {cfg_path}  (exists={cfg_path.exists()})")
    typer.echo(f"  server.host          = {cfg.server.host}")
    typer.echo(f"  server.port          = {cfg.server.port}        # local web UI port")
    typer.echo(f"  tailscale.auto_serve = {cfg.tailscale.auto_serve}")
    typer.echo(f"  tailscale.serve_port = {cfg.tailscale.serve_port}     # tailnet HTTPS port")
    typer.echo(f"  peers.auto_discover  = {cfg.peers.auto_discover}     # find other TailCam nodes")
    typer.echo(f"  peers.static         = {cfg.peers.static}")


@app.command()
def update(
    check: bool = typer.Option(
        False, "--check", help="Only check for a new version; don't install."
    ),
) -> None:
    """Update TailCam to the latest version and restart the service."""
    setup_logging("WARNING")
    from tailcam import update as upd

    current, latest, newer = upd.update_available(use_cache=False)
    if latest is None:
        typer.echo(f"Current version {current} — couldn't reach GitHub to check for updates.")
        raise typer.Exit(code=1)
    if not newer:
        typer.echo(f"Up to date ({current}).")
        return
    typer.echo(f"Update available: {current} → {latest}")
    if check:
        return

    if sys.platform == "win32":
        # A running tailcam.exe can't replace itself; hand off to the installer.
        upd.spawn_windows_installer()
        typer.echo("Updating via the installer in a background window.")
        typer.echo("Check `tailcam version` in a minute or two (open a new terminal).")
        return

    typer.echo("Installing…")
    if not upd.run_pip_upgrade():
        typer.echo("pip install failed — try re-running the install script.")
        raise typer.Exit(code=1)
    from tailcam.service import installer

    # install(), not restart(): re-rendering the unit migrates nodes still on
    # the legacy anycam.service/com.anycam names, and it restarts either way.
    typer.echo(installer.install() if installer.is_installed() else installer.restart())
    typer.echo(f"Updated to {latest}.")


@app.command()
def migrate() -> None:
    """Move data from a pre-rename AnyCam install into the TailCam locations.

    Runs automatically on the first command after a reinstall; use this to
    re-run it or to migrate explicitly.
    """
    from tailcam import migrate as mig

    if not mig.needs_migration():
        legacy = paths.legacy_config_dir()
        if legacy.exists() or paths.legacy_data_dir().exists():
            typer.echo("Nothing to migrate (TailCam already has its data).")
        else:
            typer.echo("No pre-rename AnyCam install found.")
        return
    actions = mig.migrate()
    for line in actions:
        typer.echo(line)
    typer.echo(f"Migrated {len(actions)} item(s) from AnyCam to TailCam.")


@mcp_app.command("stdio")
def mcp_stdio() -> None:
    """Run the TailCam MCP server over stdio (for Codex, Claude Desktop, Hermes).

    Connects to a running TailCam node at $TAILCAM_URL (default
    http://127.0.0.1:8088) and speaks MCP on stdin/stdout. Add this to your MCP
    client config as command "tailcam", args ["mcp", "stdio"].
    """
    setup_logging()
    config = AppConfig.load()
    if not config.mcp.enabled:
        typer.echo("MCP is disabled in config ([mcp] enabled = false).", err=True)
        raise typer.Exit(code=1)
    from tailcam.mcp.transport_stdio import run_stdio

    run_stdio(config)


@app.command()
def plugins() -> None:
    """List installed TailCam plugins (AI providers, channels, event hooks)."""
    from tailcam.plugins import sdk
    from tailcam.plugins.registry import PluginRegistry

    config = AppConfig.load()
    sdk._set_config(config)
    reg = PluginRegistry(
        disabled=config.plugins.disabled, load_dropins=config.plugins.load_dropins
    )
    table = Table(title="Plugins", title_style="bold", header_style="bold cyan")
    table.add_column("Plugin")
    table.add_column("Kind")
    table.add_column("Source")
    table.add_column("Description")
    for info in sorted(reg.plugin_infos(), key=lambda i: (i.kind, i.id)):
        table.add_row(
            info.name, info.kind,
            "built-in" if info.builtin else "external",
            info.description,
        )
    console.print(table)
    providers = ", ".join(p.id for p in reg.analyzer_providers())
    channels = ", ".join(c.id for c in reg.notification_channels())
    console.print(f"[dim]AI providers:[/dim] {providers}")
    console.print(f"[dim]Notification channels:[/dim] {channels}")
    console.print(f"[dim]Active AI provider:[/dim] {config.ai.provider}")
    if reg.errors:
        for err in reg.errors:
            console.print(f"[yellow]plugin error:[/yellow] {err}")


@app.command(name="plugin-install")
def plugin_install(plugin_id: str) -> None:
    """Install a plugin from the marketplace registry (sha256-verified)."""
    from tailcam.plugins.market import MarketError, PluginMarket

    config = AppConfig.load()
    market = PluginMarket(config.plugins)
    try:
        installed = market.install(plugin_id)
    except MarketError as exc:
        console.print(f"[red]install failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]installed[/green] {installed.id} v{installed.version} "
        f"({installed.file}) — restart the service (or hit Reload in the UI) to load it"
    )


@app.command(name="plugin-remove")
def plugin_remove(plugin_id: str) -> None:
    """Uninstall a drop-in plugin by id (file stem)."""
    from tailcam.plugins.market import MarketError, PluginMarket

    config = AppConfig.load()
    market = PluginMarket(config.plugins)
    try:
        removed = market.uninstall(plugin_id)
    except MarketError as exc:
        console.print(f"[red]remove failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    if not removed:
        console.print(f"[yellow]no plugin named[/yellow] {plugin_id}")
        raise typer.Exit(1)
    console.print(f"[green]removed[/green] {plugin_id}")


@app.command()
def homekit() -> None:
    """Show the Apple HomeKit bridge configuration and readiness."""
    import shutil

    from tailcam.integrations.homekit import HomeKitBridge, valid_pin

    config = AppConfig.load()
    cfg = config.homekit
    table = Table(title="Apple HomeKit", title_style="bold", header_style="bold cyan")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Enabled", "yes" if cfg.enabled else "no")
    table.add_row("Bridge name", cfg.bridge_name)
    table.add_row("Setup code", cfg.pin if valid_pin(cfg.pin) else "(generated on first enable)")
    table.add_row("Port", str(cfg.port))
    table.add_row("Cameras", ", ".join(cfg.cameras) or "all")
    hap = "installed" if HomeKitBridge.available() else "missing — pip install 'tailcam[homekit]'"
    table.add_row("HAP-python", hap)
    ff = "found" if shutil.which(cfg.ffmpeg) else f"missing — install ffmpeg ({cfg.ffmpeg})"
    table.add_row("ffmpeg (live video)", ff)
    console.print(table)
    console.print(
        "[dim]Pair in the Home app: + → Add Accessory → More options, then scan the QR "
        "from Settings → Integrations (or type the setup code). Apple Home consumes "
        "camera video over HAP — Matter does not carry camera streams.[/dim]"
    )


@app.command()
def version() -> None:
    """Print the TailCam version."""
    typer.echo(__version__)


# --------------------------------------------------------------------------
# desktop app (issue #38) — `tailcam app`
# --------------------------------------------------------------------------
# Note the escaped bracket: rich would otherwise parse [desktop] as markup.
_DESKTOP_HINT = (
    "The desktop app needs its optional backends. Install them into this venv:\n"
    "    pip install 'tailcam\\[desktop]'"
)


@desktop_app.callback()
def desktop_run(
    ctx: typer.Context,
    url: str = typer.Option(
        "",
        "--url",
        help="Client mode: front a REMOTE node at its Tailscale Serve HTTPS URL.",
    ),
    no_window: bool = typer.Option(
        False, "--no-window", help="Start the tray without opening the dashboard window."
    ),
    check: bool = typer.Option(
        False, "--check", help="Verify GUI backends are importable and exit."
    ),
    smoke: bool = typer.Option(
        False, "--smoke", help="Build the real state + menu model headlessly and exit (CI)."
    ),
) -> None:
    """Run the TailCam desktop app (menu-bar on macOS, tray elsewhere)."""
    if ctx.invoked_subcommand is not None:
        return
    from tailcam.desktop import have_tray, have_webview

    if check:
        tray_ok, tray_detail = have_tray()
        web_ok, web_detail = have_webview()
        tray_flag = "[green]ok[/green]" if tray_ok else "[red]missing[/red]"
        web_flag = "[green]ok[/green]" if web_ok else "[yellow]missing[/yellow]"
        web_note = "" if web_ok else " (dashboard opens in the browser instead)"
        console.print(f"tray:    {tray_flag} — {tray_detail}")
        console.print(f"webview: {web_flag} — {web_detail}{web_note}")
        if not tray_ok:
            console.print(_DESKTOP_HINT)
        raise typer.Exit(0 if tray_ok else 1)

    from tailcam.desktop.app import DesktopApp

    application = DesktopApp(client_url=url or None)
    if smoke:
        result = application.smoke()
        console.print_json(data=result)
        raise typer.Exit(0)

    tray_ok, tray_detail = have_tray()
    if not tray_ok:
        console.print(f"[red]{tray_detail}[/red]")
        console.print(_DESKTOP_HINT)
        raise typer.Exit(1)
    raise typer.Exit(application.run(open_window_on_launch=not no_window))


@desktop_app.command("install")
def desktop_install(
    autostart: bool = typer.Option(
        False, "--autostart", help="Linux/Windows: also start the tray at login."
    ),
) -> None:
    """Create the OS launcher: TailCam.app (macOS) / .desktop entry (Linux) /
    Start-menu shortcut (Windows)."""
    if sys.platform == "darwin":
        from tailcam.desktop.macos_bundle import install_app

        bundle = install_app()
        console.print(f"[green]Installed[/green] {bundle}")
        console.print("Launch it from Spotlight/Launchpad as “TailCam”.")
        return
    if sys.platform.startswith("linux"):
        from tailcam.desktop.linux_desktop import install_entries

        entry = install_entries(autostart=autostart)
        console.print(f"[green]Installed[/green] {entry}")
        console.print(
            "Launch it from your app grid as “TailCam”"
            + (" — the tray also starts at login." if autostart else ".")
        )
        return
    if sys.platform == "win32":
        from tailcam.desktop.windows_shortcut import install_shortcut

        lnk = install_shortcut(autostart=autostart)
        console.print(f"[green]Installed[/green] {lnk}")
        console.print(
            "Launch it from the Start menu as “TailCam”"
            + (" — the tray also starts at login." if autostart else ".")
        )
        return
    console.print(f"`tailcam app install` doesn't support this platform ({sys.platform}).")
    raise typer.Exit(1)


@desktop_app.command("uninstall")
def desktop_uninstall() -> None:
    """Remove the desktop launcher."""
    if sys.platform == "darwin":
        from tailcam.desktop.macos_bundle import uninstall_app

        removed = uninstall_app()
    elif sys.platform.startswith("linux"):
        from tailcam.desktop.linux_desktop import uninstall_entries

        removed = uninstall_entries()
    elif sys.platform == "win32":
        from tailcam.desktop.windows_shortcut import uninstall_shortcut

        removed = uninstall_shortcut()
    else:
        console.print(f"`tailcam app uninstall` doesn't support this platform ({sys.platform}).")
        raise typer.Exit(1)
    console.print("[green]Removed[/green]" if removed else "Nothing was installed.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
