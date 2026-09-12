# Installation

TailCam runs on Linux, macOS, and Windows — including **Windows on ARM**
(Surface / Snapdragon X), where the installer uses x64 Python under Windows 11
emulation because OpenCV has no native ARM64 wheels yet. It needs Python 3.10+.
Only capture nodes need a camera; hubs, storage nodes, and compute nodes can run
without one. A built-in synthetic camera is available for testing.

> Prefer containers? TailCam has a dedicated Docker image that bundles Tailscale
> and all media libraries — see [Running in Docker](docker).

## Install the published PyPI release

TailCam is published on [PyPI](https://pypi.org/project/tailcam/). The wheel
includes the dashboard and this manual; no Node.js or Git checkout is needed.
Python 3.10+ and working camera/system libraries are still required.

With [pipx](https://pipx.pypa.io/) installed:

```bash
pipx install tailcam
tailcam version
tailcam run
```

Or create an isolated virtual environment on Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install tailcam
tailcam version
tailcam run
```

On Windows PowerShell, explicit paths avoid activation-policy changes:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install tailcam
.\.venv\Scripts\tailcam.exe version
.\.venv\Scripts\tailcam.exe run
```

Use x64 Python on Windows ARM. Open `http://localhost:8088/` once the server
starts. Manual pip/pipx installs do not install Tailscale or register services.

## Choose this node's purpose

Before the first `tailcam run`, select a preset:

```bash
tailcam setup --preset hub --node-name "Workshop hub"
tailcam run
```

Presets are `all-in-one`, `camera`, `hub`, `storage`, and `compute`. The camera
preset includes local storage and skips AI/training. Use `--roles capture` for
a streaming camera with recordings routed elsewhere. Existing valid configs
retain all roles until you change them. See [Node configuration](configuration).

You can change roles later in **Settings → Node purpose**; restart the server
to apply them. A hub skips camera discovery, local model initialization, and
training. These controls do not remove base dependencies or configure an
external storage/AI destination for you.

The installers configure the chosen preset before starting a service. For a hub,
pass `--preset hub` (Windows `-Preset hub`) or set `TAILCAM_PRESET=hub`. Omitted
answers preserve existing settings; a new node without a choice uses all-in-one.
Use `tailcam setup --interactive` for guided selection or `--roles capture` for a
custom combination. `--dry-run --json` previews configuration without saving or
moving legacy AnyCam data;
`--quiet` prints only errors. Setup performs no camera discovery or model downloads.

## OS installers (GitHub main)

For automatic system dependencies, Tailscale setup, and a background user
service, use the dedicated installer:

```bash
# Linux (Debian/Ubuntu/Raspberry Pi OS)
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-linux.sh | bash
# macOS
curl -fsSL https://raw.githubusercontent.com/factshin/tailcam/main/install-macos.sh | bash
```

```powershell
# Windows
irm https://raw.githubusercontent.com/factshin/tailcam/main/install.ps1 | iex
```

These scripts install the pinned **PyPI 1.9.2** package. Download the script and
pass `--version X.Y.Z` (Windows `-Version`) for another setup-capable release, or
`--ref REF` (`-Ref`) to explicitly install source. `--node-name` (`-NodeName`)
sets the label. `--non-interactive` (`-NonInteractive`) skips role/login prompts;
`--no-color` (`-NoColor`, or `NO_COLOR`) uses plain installer messages. The desktop
shell is included on macOS/Windows; Linux opts in with `--desktop`.

Reruns preserve roles and the server port unless explicitly changed. Media and
node identities stay in place. Logs are stored beside the virtualenv on POSIX
and under `%LOCALAPPDATA%\TailCam` on Windows. Setup, service registration, or
startup verification failure restores the previous installation and leaves it
stopped: inspect the log and repair its config before starting it. Startup
verification uses the local API and checks the installed version, node identity,
and active roles; it does not test cameras or models. With `--no-service`, the
installer retains the backup and leaves startup to you.

## Optional features: use TailCam's environment

Install extras into the environment running TailCam, then restart the server.
A bare `pip` outside that environment will not add features to an existing
pipx or installer-managed server.

For **pipx**, install extras initially with `pipx install 'tailcam[homekit,mqtt]'`,
or add them to an existing install:

```bash
pipx inject tailcam 'tailcam[homekit,mqtt]'
```

For an **activated virtual environment**, use its Python:

```bash
python -m pip install 'tailcam[homekit,mqtt]'  # Apple HomeKit + MQTT discovery
python -m pip install 'tailcam[desktop]'      # desktop shell; OS GUI libraries also needed
python -m pip install 'tailcam[training]'     # Ultralytics/PyTorch
python -m pip install 'tailcam[activelearning]' # Label Studio SDK
python -m pip install 'tailcam[florence2]'     # Florence-2 labeling/fine-tune
python -m pip install 'tailcam[qwen-vl]'       # Qwen2.5-VL labeling
```

Without activation, use `.venv/bin/python` on Linux/macOS or
`.\.venv\Scripts\python.exe` on Windows in place of `python`.

For an **OS installer-managed server**, use its interpreter:

```bash
# Linux / macOS
~/.local/share/tailcam/venv/bin/python -m pip install 'tailcam[homekit,mqtt]'
~/.local/share/tailcam/venv/bin/tailcam restart
```

```powershell
# Windows
& "$env:LOCALAPPDATA\TailCam\venv\Scripts\python.exe" -m pip install 'tailcam[homekit,mqtt]'
& "$env:LOCALAPPDATA\TailCam\venv\Scripts\tailcam.exe" restart
```

- **HomeKit live video** needs system `ffmpeg`; pairing/snapshots work without it.
  See [Home automation](home-automation).
- **Ollama analysis** needs a separate running [Ollama](ai-analysis) instance.
  Built-in object detection does not require Ollama.
- **Training / VLM extras** can download large, platform-specific ML dependencies.
- **Active learning** also needs a separate [Label Studio](active-learning)
  server. Keep the server in its own environment.
- **Timelapse smoothing** uses bundled or system `ffmpeg`, optionally
  `rife-ncnn-vulkan`. See [Timelapse](timelapse).
- The `webrtc` and `turbojpeg` dependency extras are reserved for future backend
  work. Installing them does not enable a WebRTC stream or TurboJPEG encoder;
  the current server streams MJPEG using OpenCV JPEG encoding.

## Updating

For published **PyPI releases**:

```bash
pipx upgrade tailcam
# Or, in the activated TailCam virtual environment:
python -m pip install --upgrade tailcam
```

Include the same extras (for example `'tailcam[homekit,mqtt]'`) when you want
pip to upgrade their dependencies too. Restart the server afterward; use
`tailcam restart` if it is registered as a service, then `tailcam version`.

**`tailcam update` and the dashboard/desktop updater use GitHub main**, even
when TailCam was installed from PyPI. `tailcam update --check` checks that
channel without installing. Use pip/pipx to stay on published PyPI versions.
Installer-based updates recreate the environment, so re-check optional extras
and reinstall any missing ones using the environment paths above.

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

## Tailscale is installed and signed in for you

The OS installer scripts attempt to prepare Tailscale before finishing.
This does not happen during a pip/pipx install:

1. **Missing?** It is installed — the official script on Linux
   (`curl -fsSL https://tailscale.com/install.sh | sh`), `brew install tailscale`
   on macOS (or the App Store app if present), `winget install Tailscale.Tailscale`
   on Windows.
2. **Not signed in?** The installer runs `tailscale up`, which prints a login
   link. Open it on any device — your phone is fine — and approve the machine.
   The installer waits up to `TAILCAM_TAILSCALE_LOGIN_TIMEOUT` seconds
   (default 600) for the node to join the tailnet.
3. **Connected?** `tailscale serve` is enabled and the HTTPS tailnet URL is
   printed.

Nothing here can fail the install: if sudo can't prompt (piped install on a
headless box) or the login times out, the exact commands to finish later are
printed and TailCam keeps working on `http://localhost:8088/`.

Flags: `--no-tailscale-install` / `-NoTailscaleInstall` skips only the automatic
install (serve still happens if Tailscale is present); `--no-tailscale` /
`-NoTailscale` skips everything Tailscale-related. `-NonInteractive` on Windows
(and the detached self-updater) never waits for a login.
