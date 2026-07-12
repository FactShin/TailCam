# TailCam Companion — browser extension

**Your TailCam cameras, one click from the toolbar.**

TailCam Companion is the official browser extension for
[TailCam](../README.md). It is an *optional* enhancement — TailCam works
fully without it, and the extension never gets between you and the web UI.
The philosophy is **enhance, never force**: everything the extension does is
a shortcut to something the dashboard already does, plus the things only a
browser extension can do (toolbar badge, system notifications, an
always-on-top mini viewer).

One shared codebase targets Chrome, Edge, Firefox, and Safari.

## Features

- **Popup mini-dashboard** — click the toolbar icon for a node picker, your
  camera list with live snapshot thumbnails and status LEDs, per-camera
  snapshot / record / Glance actions, and the last few motion events with AI
  label chips. Opening the popup marks events as seen and clears the badge.
- **Badge** — the background worker polls each configured node's
  `/api/events` (default every 60 s, configurable) and shows a count of
  motion events you haven't seen yet.
- **Notifications** — system notifications for new motion events, filtered
  by AI label: `off`, `all events`, `labeled only`, or `person only`. Quiet
  hours (e.g. 22:00–07:00) suppress them overnight. Clicking a notification
  opens the node's events page.
- **Glance window** — a chromeless popup window with one camera's live MJPEG
  stream, snapshot and record buttons. Made for "keep an eye on it while you
  work": small, frameless, and it reconnects when the stream drops. Open it
  from the popup, the keyboard shortcut, or the icon's context menu.
- **Omnibox** — type `tc` then <kbd>Tab</kbd> in the address bar: camera
  names autocomplete and open the camera's dashboard page; `tc events` jumps
  to the events page; plain <kbd>Enter</kbd> opens the dashboard.
- **Keyboard shortcuts** — `Ctrl+Shift+9` (`Cmd+Shift+9` on macOS) opens the
  dashboard; a second, unbound-by-default command opens the pinned camera's
  Glance window.
- **Multi-node** — add every TailCam node on your tailnet in the options
  page; the popup, badge, and notifications aggregate across all of them,
  including cameras that a node reverse-proxies from its peers.

## Security & privacy

- The extension talks **only to the TailCam nodes you configure**, over your
  tailnet (or localhost). There are no third-party servers, no analytics, no
  telemetry, and no remote code — every byte of the extension ships in the
  package.
- **No host permissions by default.** The manifest declares only
  *optional* host permissions; when you add a node in the options page, the
  browser asks you to grant access to that one origin. Remove the node and
  the grant is released.
- All state (nodes, settings, seen-event markers) lives in the extension's
  local storage on your machine. Nothing syncs anywhere.
- **Mutating actions may be rejected by the node.** TailCam's server blocks
  state-changing requests (snapshot, record start/stop) whose `Origin`
  header isn't localhost, an IP literal, or `*.ts.net` — and browsers stamp
  extension requests with an extension origin. That is the node's CSRF
  defense working as designed, not a bug: a random webpage can't poke your
  cameras, and neither can an extension the node doesn't recognize. When it
  happens the extension shows *"Blocked by node security policy — use the
  dashboard for this action"* and everything read-only (streams, snapshots,
  thumbnails, events) keeps working.

## Layout

```
browser-extensions/
├── shared/            # the entire extension — one codebase for all browsers
│   ├── background.js  # service worker / event page: polling, badge, notifications
│   ├── lib/           # compat.js (browser API shim), api.js, settings.js, format.js
│   ├── popup/         # toolbar popup
│   ├── options/       # options page (nodes, polling, notifications, pinned camera)
│   ├── glance/        # chromeless mini viewer
│   └── icons/
├── chrome/manifest.json    # per-browser manifests — the ONLY per-browser files
├── edge/manifest.json
├── firefox/manifest.json
├── safari/manifest.json
│   └── (+ convert-safari.sh, see below)
└── build.py           # stdlib-only packager
```

Plain ES2022 modules, Manifest V3, zero dependencies — no npm, no bundler,
no framework. All browser API access goes through `shared/lib/compat.js`.
A build is just `shared/` + the target's `manifest.json`.

## Build

```bash
cd browser-extensions
python3 build.py                     # all four targets → ./dist/*.zip
python3 build.py --targets chrome    # just one
python3 build.py --no-zip            # assembled directories only
```

Requires Python 3.10+, nothing else. Each target gets an unpacked directory
`dist/<target>/` and (unless `--no-zip`) a store-ready
`dist/tailcam-companion-<target>-<version>.zip`.

## Install (development / unpacked)

Build first, then:

**Chrome**
1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select `dist/chrome`

**Edge**
1. Open `edge://extensions`
2. Enable **Developer mode** (left sidebar)
3. **Load unpacked** → select `dist/edge`

**Firefox**
1. Open `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…** → pick `dist/firefox/manifest.json`

   Temporary add-ons are removed when Firefox restarts. A permanent install
   requires the zip to be signed by [addons.mozilla.org](https://addons.mozilla.org)
   (self-distributed signing is fine — no store listing needed).

**Safari** (macOS only)
1. Run `safari/convert-safari.sh` — it assembles the safari package and
   wraps it in an Xcode project via Apple's `safari-web-extension-converter`
2. Open the generated project in Xcode and press **Run** once
3. Safari → **Settings → Extensions** → enable *TailCam Companion*

   During development you'll also need **Develop → Allow Unsigned
   Extensions** (enable the Develop menu in Safari Settings → Advanced;
   the setting resets when Safari quits). Distribution outside your Mac
   requires an Apple Developer account.

After installing: open the extension's **Options** page, add your node's
URL (e.g. `https://host.tailnet.ts.net:8443/` or `http://localhost:8088/`),
grant the permission prompt, and hit **Test connection**.

## Publishing (future work)

Store submission is not wired up yet. The checklist when it is:

- [ ] **Chrome Web Store** — developer account, upload `dist/tailcam-companion-chrome-<v>.zip`, privacy disclosures (no data collected)
- [ ] **Firefox Add-ons (AMO)** — submit the firefox zip for signing; listed or self-distributed
- [ ] **Edge Add-ons** — Partner Center account, upload the edge zip
- [ ] **App Store (Safari)** — Apple Developer Program, archive the converter-generated Xcode project, notarize/submit
