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

## Linux

Opt-in at install time (most Linux nodes are headless servers):

```bash
curl -fsSL .../install-linux.sh | bash -s -- --desktop
```

That installs the GUI system libraries (GTK3, WebKit2GTK, AppIndicator),
the `tailcam[desktop]` backends, and a launcher — **TailCam appears in your
app grid**, and the tray starts at login. Everything from the macOS app is
here: tray menu with service controls, Nodes ▸ switching, update alerts,
embedded dashboard window (or your browser when WebKit2GTK isn't available).

Manual setup on an existing install:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0   gir1.2-ayatanaappindicator3-0.1 gir1.2-webkit2-4.1   # 22.04: gir1.2-webkit2gtk-4.1
pip install 'tailcam[desktop]'      # into the TailCam venv
tailcam app install --autostart     # launcher + start tray at login
```

Desktop notes:

- **GNOME** hides tray icons by default — install the *AppIndicator and
  KStatusNotifierItem Support* shell extension. KDE/XFCE/Cinnamon work out of
  the box.
- The venv must see the system `gi` bindings: the installer handles this; for
  hand-built venvs use `--system-site-packages` (or `pip install PyGObject`).
- `tailcam doctor` probes every GUI piece and prints the exact package to
  install for anything missing.
- No AppImage by design: the shell lives in the pip-managed venv so it shares
  TailCam's updater and survives reinstalls.

Smoke checklist after install: tray icon visible; Open Dashboard renders the
UI (embedded or browser); Start/Stop Service round-trips the systemd user
unit; tray reappears after logging out and back in (autostart).

## Windows

`install.ps1` sets it up automatically: after the install you'll find
**TailCam** in the Start menu, with a tray icon in the notification area. Same
features as macOS/Linux — tray menu with service controls, Nodes ▸ switching,
update alerts, and the dashboard in an embedded window.

The embedded window uses the **Microsoft Edge WebView2 Runtime** (preinstalled
on Windows 11 and current Windows 10). If it's missing, the installer points
you at the download and the app opens the dashboard in your browser instead —
everything else still works.

Manual setup on an existing install:

```powershell
& "$env:LOCALAPPDATA\TailCamenv\Scripts\python.exe" -m pip install "pywebview>=5" "pystray>=0.19" "pillow>=10"
tailcam app install --autostart    # Start-menu shortcut + tray at login
```

Notes: the shortcut and login autostart both launch `pythonw.exe -m tailcam
app` (no console window ever flashes). Updates run through the official
installer (Windows venvs aren't relocatable, so it swaps the venv safely) and
the shortcut is re-created against the new venv. `tailcam doctor` reports
whether the backends and WebView2 are available.

Smoke checklist: tray icon in the notification area; Open Dashboard renders the
UI (embedded or browser); Start/Stop from the tray round-trips the Scheduled
Task; no console window flashes at any point.
