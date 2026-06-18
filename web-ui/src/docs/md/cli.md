# CLI reference

Everything TailCam does from the terminal goes through the `tailcam` command. Run
`tailcam --help` or `tailcam <command> --help` for the latest details.

## Server

| Command | What it does |
| --- | --- |
| `tailcam run` | Start the web server in the foreground. |
| `tailcam run --host <addr>` | Override the bind address. |
| `tailcam run --port <n>` | Override the bind port (also `$TAILCAM_PORT`). |
| `tailcam run --no-tailscale` | Don't run `tailscale serve` (local only). |

## Background service

| Command | What it does |
| --- | --- |
| `tailcam install-service` | Install TailCam as a background service. |
| `tailcam uninstall-service` | Remove the service. |
| `tailcam start` | Start the background service. |
| `tailcam stop` | Stop it. |
| `tailcam restart` | Restart it (e.g. after editing config). |

## Status & diagnostics

| Command | What it does |
| --- | --- |
| `tailcam status` | Cameras, tailnet nodes, and access URLs. |
| `tailcam doctor` | Run diagnostic checks (paths, devices, Tailscale). |
| `tailcam cameras` | List detected cameras. |
| `tailcam version` | Print the TailCam version. |

## Configuration

| Command | What it does |
| --- | --- |
| `tailcam config` | Show the current config. |
| `tailcam config --init` | Write a default config file if none exists. |
| `tailcam config --reset` | Reset to defaults. |
| `tailcam config --edit` | Open the config in your editor. |
| `tailcam config --port <n>` | Set the local bind port. |
| `tailcam config --serve-port <n>` | Set the Tailscale HTTPS port (443/8443/10000). |
| `tailcam config --host <addr>` | Set the bind address. |

See the [Configuration reference](configuration) for every setting.

## Tailscale

| Command | What it does |
| --- | --- |
| `tailcam tailscale serve` | Enable Tailscale Serve. `--https-port` sets the port. |
| `tailcam tailscale serve-off` | Disable Tailscale Serve. |
| `tailcam tailscale status` | Show Tailscale state. |

See [Tailscale setup](tailscale).

## MCP (agents)

| Command | What it does |
| --- | --- |
| `tailcam mcp stdio` | Run the MCP server over stdio for an agent. |

See [Connecting agents](mcp-connect).

## Updates & migration

| Command | What it does |
| --- | --- |
| `tailcam update` | Install the latest TailCam release. |
| `tailcam update --check` | Check for updates without installing. |
| `tailcam migrate` | Migrate data from a pre-rename AnyCam install. |

## Environment variables

| Variable | Effect |
| --- | --- |
| `TAILCAM_PORT` | Default local bind port (overridden by `--port`). |
| `TAILCAM_URL` | Node URL the MCP stdio server connects to. |
| `TAILCAM_DATA_DIR` | Where media and the database live. |
| `TAILCAM_CONFIG_DIR` | Where the config file lives. |
| `TAILCAM_SYNTHETIC` | `1` to use a built-in synthetic camera (no hardware). |
| `TAILCAM_HOST` | Override this node's identity/hostname. |
| `TAILCAM_PEERS` | Extra peer base URLs for [fleet](fleet) discovery. |
