# FAQ

## Does TailCam send anything to the cloud?

No. Capture, streaming, recording, and AI analysis all run on machines you
control. [Tailscale](tailscale) provides the private network and identity; there's
no TailCam cloud service in the loop.

## Do I need Tailscale?

Only for remote access. TailCam works fully on `localhost` without it. Add
Tailscale when you want to view cameras from other devices. See
[Tailscale setup](tailscale).

## Is the AI required?

No. [AI analysis](ai-analysis) is optional and off by default. Cameras,
streaming, recording, motion detection, and timelapse all work without it.

## What's the difference between the two ports?

`server.port` (default 8088) is the local HTTP port. `tailscale.serve_port`
(default 8443) is the tailnet HTTPS port, and Tailscale only allows 443, 8443, or
10000 there. See [Tailscale setup](tailscale).

## Can I change the port?

Yes — `tailcam config --port <n>` for the local port, `--serve-port <n>` (443 /
8443 / 10000) for the tailnet port. See the [CLI reference](cli).

## How do I see cameras from all my machines in one place?

Install TailCam on each machine on the same tailnet. They auto-discover each other
and aggregate cameras into one grid. See [Fleet](fleet).

## Where are my recordings and config stored?

Under TailCam's data and config directories (`TAILCAM_DATA_DIR` /
`TAILCAM_CONFIG_DIR`). Run `tailcam doctor` to see the resolved paths.

## How do I control storage usage?

Set `[retention]` `max_gb` and `max_age_days`. The oldest media is pruned first.
See [Recording & media](recording-media). The MCP `suggest_retention_cleanup`
tool analyzes usage non-destructively.

## Can an AI agent control TailCam?

Yes — that's the built-in [MCP](mcp-overview) server. Codex, Claude, Hermes, and
OpenClaw can read status and drive cameras, AI, and training. See
[Connecting agents](mcp-connect).

## Is it safe to let an agent in?

Local stdio agents act as the local admin (personal-computer model). Remote agents
are gated by your Tailscale role, can't touch admin tools without the `admin`
grant, must supply confirmation strings for destructive/fleet actions, and every
write is audited. See [MCP security](mcp-security).

## Can I train a model on my own footage?

Yes. Build a dataset (import events or collect frames), then run a classification
or detection training run, and activate the result. See [Training](training).

## How do I update TailCam?

`tailcam update` (or `--check` to look without installing). The dashboard shows a
banner when an update is available. Across a [fleet](fleet), `check_fleet_version_drift`
flags laggards.

## I used to run AnyCam — will my data carry over?

Yes. TailCam migrates your old config, media, and database automatically on first
run, or run `tailcam migrate` manually.

## Where do I report bugs or request features?

This wiki covers usage. For project-level issues, see the TailCam project
repository.
