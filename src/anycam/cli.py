"""AnyCam command-line interface."""

from __future__ import annotations

import os

import typer
from rich.console import Console
from rich.table import Table

from anycam import __version__, paths
from anycam.config import AppConfig
from anycam.logging_setup import setup_logging

app = typer.Typer(
    help="AnyCam — view any webcam from anywhere over Tailscale.",
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
    port: int | None = typer.Option(None, help="Bind port (default from config / $ANYCAM_PORT)."),
    no_tailscale: bool = typer.Option(False, "--no-tailscale", help="Do not run tailscale serve."),
) -> None:
    """Start the AnyCam web server."""
    import uvicorn

    setup_logging()
    paths.ensure_dirs()
    if not paths.config_file().exists():
        AppConfig().save()  # write an editable default config on first run
    config = AppConfig.load()
    if host:
        config.server.host = host
    env_port = os.environ.get("ANYCAM_PORT")
    if port:
        config.server.port = port
    elif env_port and env_port.isdigit():
        config.server.port = int(env_port)
    if no_tailscale:
        config.tailscale.auto_serve = False

    from anycam.web.app import create_app

    application = create_app(config)
    uvicorn.run(application, host=config.server.host, port=config.server.port, log_level="info")


def _fleet_tables(config: AppConfig, local_descriptors) -> None:
    """Render the cameras + tailnet-nodes tables (shared by status/doctor)."""
    from anycam.cluster.fleet import gather_fleet

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

    from anycam.camera import enumerate as cam_enumerate
    from anycam.tailscale.client import TailscaleClient

    console.print(f"[bold]AnyCam {__version__}[/bold]")
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

    import sys

    pyv = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    (ok if sys.version_info[:2] >= (3, 10) else bad)("Python 3.10+", pyv)

    try:
        import cv2

        ok("OpenCV import", f"cv2 {cv2.__version__}")
    except Exception as exc:
        bad("OpenCV import", str(exc))

    from anycam.camera import enumerate as cam_enumerate

    descriptors = cam_enumerate.discover()
    real = [d for d in descriptors if d.backend != "synthetic"]
    cam_detail = f"{len(real)} device(s)" if real else "none (synthetic only)"
    (ok if real else bad)("Cameras detected", cam_detail)

    data_writable = os.access(paths.data_dir(), os.W_OK)
    (ok if data_writable else bad)("Data dir writable", str(paths.data_dir()))
    ok("Config file", str(paths.config_file()))

    from anycam.tailscale.client import TailscaleClient

    st = TailscaleClient().status()
    if st.running:
        ok("Tailscale running", st.magic_dns or st.ipv4 or "")
    elif st.installed:
        bad("Tailscale", "installed but not running (run `tailscale up`)")
    else:
        bad("Tailscale", "not installed")

    from anycam.cluster.fleet import gather_fleet

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
    from anycam.camera import enumerate as cam_enumerate

    for desc in cam_enumerate.discover():
        typer.echo(f"{desc.id}\t{desc.backend}\t{desc.name}")


@app.command(name="install-service")
def install_service() -> None:
    """Install and start the AnyCam background service (uses the configured port)."""
    setup_logging()
    from anycam.service import installer

    typer.echo(installer.install())


@app.command(name="uninstall-service")
def uninstall_service() -> None:
    """Stop and remove the AnyCam background service."""
    setup_logging()
    from anycam.service import installer

    typer.echo(installer.uninstall())


@tailscale_app.command("serve")
def tailscale_serve(
    https_port: int | None = typer.Option(
        None, "--https-port", help="Tailnet HTTPS port (443, 8443, or 10000). Saved to config."
    ),
) -> None:
    """Expose the AnyCam web UI over HTTPS within your tailnet.

    By default AnyCam serves on port 8443 (https://<host>.ts.net:8443/) so it
    won't take over the root URL another app may be using.
    """
    from anycam.tailscale.client import TailscaleClient

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
        raise typer.Exit(code=1)


@tailscale_app.command("serve-off")
def tailscale_serve_off(
    https_port: int | None = typer.Option(
        None, "--https-port", help="Port to stop serving (defaults to the configured one)."
    ),
) -> None:
    """Remove AnyCam's tailnet handler on its HTTPS port (leaves other apps intact)."""
    from anycam.tailscale.client import TailscaleClient

    config = AppConfig.load()
    port = https_port if https_port is not None else config.tailscale.serve_port
    if TailscaleClient().serve_off(port):
        typer.echo(f"Stopped serving on tailnet port {port}.")
    else:
        typer.echo(f"Could not stop serving on port {port} (was it active?).")


@tailscale_app.command("status")
def tailscale_status() -> None:
    """Show Tailscale status."""
    from anycam.tailscale.client import TailscaleClient

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
) -> None:
    """Show the config file path and values; --port/--serve-port/--host persist changes."""
    paths.ensure_dirs()
    cfg_path = paths.config_file()
    if init and not cfg_path.exists():
        AppConfig().save()
        typer.echo(f"Wrote default config to {cfg_path}")
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
    typer.echo(f"  peers.auto_discover  = {cfg.peers.auto_discover}     # find other AnyCam nodes")
    typer.echo(f"  peers.static         = {cfg.peers.static}")


@app.command()
def version() -> None:
    """Print the AnyCam version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
