# Desktop app

TailCam has a native desktop presence: a **menu-bar app on macOS** (tray on
Linux/Windows — coming next) with an embedded dashboard window, service
controls, fleet-node switching, and update alerts. It's a thin shell over the
same local server and REST API the browser uses — nothing new to configure.

## macOS

The macOS installer sets everything up: after
`curl -fsSL .../install-macos.sh | bash` you'll find **TailCam** in
Spotlight/Launchpad (`~/Applications/TailCam.app`). Launch it and the camera
icon appears in the menu bar:

- **Open Dashboard** — the full dashboard in its own window (no browser tabs).
- **Nodes ▸** — every TailCam machine on your tailnet; click one to open its
  dashboard. Peers need Tailscale Serve enabled to be reachable; entries
  without it are shown with a hint instead of a dead link.
- **Start / Stop / Restart Service** — drives the launchd agent. On a machine
  without the service, it becomes **Install & Start Service**.
- **Update available — install** — appears when a newer TailCam exists;
  one click upgrades the node and restarts it.

If the service is stopped, the window shows a friendly "start the service"
page instead of a connection error. If the embedded window backend isn't
available, the dashboard opens in your default browser — every menu action
still works.

Manual setup (already done by the installer):

```bash
pip install 'tailcam[desktop]'   # into the TailCam venv
tailcam app install               # creates ~/Applications/TailCam.app
tailcam app                       # or run it directly from a terminal
```

Notes for the curious: the app is generated locally and ad-hoc signed; it
never opens cameras itself (capture stays in the background service, so
macOS camera permissions are untouched by reinstalls); re-running
`tailcam app install` after an upgrade re-points it at the current venv.

## Client mode (view a remote node)

On a laptop with **no local TailCam server**, the same shell can front any
node on your tailnet over its Tailscale Serve HTTPS URL:

```bash
pipx install 'tailcam[desktop]'          # or pip, no server setup needed
tailcam app --url https://mac-mini.your-tailnet.ts.net:8443
```

Service controls are hidden in client mode (you can't launchctl someone
else's machine); dashboard, nodes, and viewing all work.

## CLI reference

```bash
tailcam app                # run the menu-bar/tray app (opens the window)
tailcam app --no-window    # tray only
tailcam app --url <URL>    # client mode against a remote node
tailcam app --check        # verify GUI backends; exit 0/1
tailcam app --smoke        # headless self-test (used by CI)
tailcam app install        # macOS: create ~/Applications/TailCam.app
tailcam app uninstall      # remove it
```

`tailcam doctor` also reports whether the desktop backends are available.

## Linux & Windows

Tracked as follow-ups on the same issue (#38): Linux gets the tray + a
`.desktop` entry (needs WebKit2GTK + an AppIndicator-capable desktop), Windows
gets the tray + a Start-menu shortcut (embedded window uses WebView2). The
entire core — menu model, service control, node switching, updates — is
shared; only the thin packaging layer differs per OS.
