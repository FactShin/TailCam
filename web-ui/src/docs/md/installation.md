# Installation

TailCam runs on Linux, macOS, and Windows. It needs Python 3.10+ and a webcam
(or the built-in synthetic camera for testing).

> Prefer containers? TailCam has a dedicated Docker image that bundles Tailscale
> and all media libraries — see [Running in Docker](docker).

## Install

TailCam is distributed as a Python package. Install it with `pip` (a virtual
environment is recommended):

```bash
python -m pip install tailcam
```

This installs the `tailcam` command-line tool. Verify it:

```bash
tailcam version
```

## Optional features

Some capabilities require extra packages, installed as "extras":

```bash
# WebRTC low-latency streaming
python -m pip install "tailcam[webrtc]"

# Faster JPEG encoding (TurboJPEG)
python -m pip install "tailcam[turbojpeg]"
```

- **AI analysis** needs a running [Ollama](ai-analysis) instance (separate
  install). TailCam talks to it over HTTP.
- **Model training** needs the Ultralytics/PyTorch engine. TailCam auto-detects
  it; install it to enable the [Training](training) page.
- **Timelapse smoothing** uses `ffmpeg` (bundled or system) and optionally
  `rife-ncnn-vulkan` for GPU frame interpolation. See [Timelapse](timelapse).

## Install Tailscale

Remote access uses Tailscale. Install it from
[tailscale.com/download](https://tailscale.com/download) and sign in:

```bash
tailscale up
```

TailCam works locally without Tailscale, but to reach cameras from other devices
you'll want it. See [Tailscale setup](tailscale).

## Run as a background service

To keep TailCam running across reboots, install it as a service:

```bash
tailcam install-service
tailcam start
```

See the [CLI reference](cli) for `start`, `stop`, `restart`, and
`uninstall-service`.

## First run

Start the server in the foreground:

```bash
tailcam run
```

Then open `http://localhost:8088/`. Continue with the [Quick start](quickstart).

## Upgrading from AnyCam

TailCam was previously named AnyCam. On first run it automatically migrates your
old config, media, and database. You can also run it manually:

```bash
tailcam migrate
```
