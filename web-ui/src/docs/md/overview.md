# Welcome to TailCam

TailCam turns any computer with a webcam into a private, self-hosted camera node
you can view from anywhere on your [Tailscale](tailscale) network — no cloud, no
accounts, no port forwarding. Run it on a laptop, a Raspberry Pi, a mini PC, or a
whole fleet of them, and every camera shows up in one dashboard.

This documentation lives **inside** TailCam. Everything you need is here — you
never have to leave the app.

## What TailCam does

- **View any webcam from anywhere.** Live MJPEG streams over your tailnet, behind
  Tailscale's encryption and identity — only your devices can connect.
- **Motion detection.** Cheap pixel-based motion gating with optional automatic
  recording. See [Motion detection](motion-detection).
- **Recording & snapshots.** Capture stills and clips on demand or on motion,
  browse them in the gallery, with size/age retention. See [Recording & media](recording-media).
- **Timelapse.** Long-duration capture with frame interpolation and deflicker —
  built for 3D-print and project timelapses. See [Timelapse](timelapse).
- **AI analysis.** Local vision-model labeling of motion events via Ollama —
  person / animal / vehicle / package / plant. See [AI analysis](ai-analysis).
- **On-device training.** Build datasets from your own footage and fine-tune
  classification or object-detection models. See [Training](training).
- **Fleet.** Auto-discover other TailCam nodes on your tailnet and see every
  camera across every device in one grid. See [Fleet](fleet).
- **Agent control (MCP).** Drive TailCam from Codex, Claude, Hermes, or
  OpenClaw through the built-in Model Context Protocol server. See [MCP overview](mcp-overview).

## How it's built

TailCam is a single Python application that serves a FastAPI backend and this
React dashboard. Cameras are captured locally; streams and media never leave your
devices. Tailscale provides the network layer and the identity used for access
control. There is no external service in the loop.

## Where to go next

- New here? Start with [Installation](installation) and the [Quick start](quickstart).
- Setting up remote access? See [Tailscale setup](tailscale).
- Running more than one node? See [Fleet](fleet).
- Connecting an AI agent? See [MCP overview](mcp-overview) and [Connecting agents](mcp-connect).
- Looking for a specific setting or command? See the [Configuration reference](configuration),
  [CLI reference](cli), and [API reference](api).
- Something not working? See [Troubleshooting](troubleshooting) and the [FAQ](faq).
