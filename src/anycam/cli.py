"""AnyCam command-line interface."""

from __future__ import annotations

import typer

from anycam import __version__, paths
from anycam.config import AppConfig
from anycam.logging_setup import setup_logging

app = typer.Typer(
    help="AnyCam — view any webcam from anywhere over Tailscale.", no_args_is_help=True
)
tailscale_app = typer.Typer(help="Tailscale integration commands.")
app.add_typer(tailscale_app, name="tailscale")


@app.command()
def run(
    host: str | None = typer.Option(None, help="Bind address (default from config)."),
    port: int | None = typer.Option(None, help="Bind port (default from config)."),
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
    if port:
        config.server.port = port
    if no_tailscale:
        config.tailscale.auto_serve = False

    from anycam.web.app import create_app

    application = create_app(config)
    uvicorn.run(application, host=config.server.host, port=config.server.port, log_level="info")


@app.command()
def status() -> None:
    """Show camera and Tailscale status plus the access URL."""
    setup_logging("WARNING")
    paths.ensure_dirs()
    config = AppConfig.load()

    from anycam.camera import enumerate as cam_enumerate
    from anycam.tailscale.client import TailscaleClient

    typer.echo(f"AnyCam {__version__}")
    typer.echo("\nCameras:")
    for desc in cam_enumerate.discover():
        typer.echo(f"  • {desc.name}  [{desc.backend}]  id={desc.id}")

    ts = TailscaleClient()
    st = ts.status()
    typer.echo("\nTailscale:")
    typer.echo(f"  installed: {st.installed}  running: {st.running}")
    if st.ipv4:
        typer.echo(f"  ip: {st.ipv4}  magicdns: {st.magic_dns}")
    serve_port = config.tailscale.serve_port
    typer.echo(f"\nLocal URL:  http://localhost:{config.server.port}/")
    typer.echo(f"Access URL: {ts.access_url(config.server.port, st.running, serve_port)}")
    if st.running:
        typer.echo(f"  (Tailscale serve port: {serve_port})")


@app.command()
def cameras() -> None:
    """List detected cameras."""
    from anycam.camera import enumerate as cam_enumerate

    for desc in cam_enumerate.discover():
        typer.echo(f"{desc.id}\t{desc.backend}\t{desc.name}")


@app.command(name="install-service")
def install_service() -> None:
    """Install and start the AnyCam background service."""
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
    won't take over the root URL another app may be using. Pass --https-port to
    change it; the choice is persisted for the background service too.
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
) -> None:
    """Show the config file path and current values (creates it with --init)."""
    paths.ensure_dirs()
    cfg_path = paths.config_file()
    if init and not cfg_path.exists():
        AppConfig().save()
        typer.echo(f"Wrote default config to {cfg_path}")
    cfg = AppConfig.load()
    typer.echo(f"Config file: {cfg_path}  (exists={cfg_path.exists()})")
    typer.echo(f"  server.host        = {cfg.server.host}")
    typer.echo(f"  server.port        = {cfg.server.port}        # local web UI port")
    typer.echo(f"  tailscale.auto_serve = {cfg.tailscale.auto_serve}")
    typer.echo(f"  tailscale.serve_port = {cfg.tailscale.serve_port}     # tailnet HTTPS port")


@app.command()
def version() -> None:
    """Print the AnyCam version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
