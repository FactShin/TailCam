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

console = Console()

_STATUS_COLOR = {"online": "green", "degraded": "yellow", "offline": "red"}


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
    env_port = os.environ.get("TAILCAM_PORT") or os.environ.get("ANYCAM_PORT")
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

    from tailcam.web.app import create_app

    application = create_app(config)
    uvicorn.run(application, host=config.server.host, port=config.server.port, log_level="info")


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
def version() -> None:
    """Print the TailCam version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
