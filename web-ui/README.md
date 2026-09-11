# TailCam Web UI

The TailCam dashboard — a responsive React + TypeScript + Vite PWA, served by the
TailCam FastAPI backend over Tailscale.

## How it ships

`npm run build` outputs the bundle into the Python package at
**`../src/tailcam/web/spa/`**, which is committed and included in the wheel. So a
`pip install` of TailCam ships the built dashboard — **no Node needed on the host.**
FastAPI serves it at `/` (see `src/tailcam/web/app.py`); if the `spa/` dir is
absent it falls back to the legacy Jinja pages.

## Develop

Use **Node.js 22 or newer**. CI and the Docker build use Node 22; this requirement
applies to dashboard development and builds, not the Python runtime.

```bash
cd web-ui
npm ci
npm run dev        # Vite dev server on :5173, proxies /api /stream /media /proxy -> :8088
```

Run a backend alongside it (synthetic camera = no webcam needed):

```bash
TAILCAM_SYNTHETIC=1 tailcam run            # :8088
# point the dev proxy elsewhere with TAILCAM_DEV_TARGET=http://host:port
```

The in-app manual lives in `src/docs/md/`; edits to those Markdown files also
require a rebuild. Commit both the source and `src/tailcam/web/spa/`:

```bash
npm run typecheck   # required before merging; build itself does not typecheck
npm run build
npm audit          # check locked dependencies against current advisories
```

Run the production browser checks after building:

```bash
npx playwright install chromium
npm run test:e2e
```

These checks use the built dashboard, Chromium, and a read-only HTTP fixture on
loopback ports 4173 and 4174. Keep both ports free; no real cameras, peers, or AI
services are used. CI installs Chromium's Linux dependencies and runs the same
checks. Failed runs retain traces under `test-results/` for diagnosis.

## Structure

- `src/api/` — typed fetch client (`client.ts`) + React Query hooks (`hooks.ts`).
- `src/components/` — `LiveViewer` (real MJPEG `<img>` + gesture zoom/pan), `CameraTile`,
  UI primitives, toasts.
- `src/screens/` — Dashboard (grouped by host), CameraDetail, Gallery, Events, Settings.
- `src/app/AppShell.tsx` — responsive nav (sidebar + mobile tab bar).

## Multi-host

`/api/cameras` returns cameras from every node on the tailnet; each carries a
`host` and a `proxy_prefix` (`""` local, `/proxy/<key>` remote). Every stream and
control URL is prefixed with it, so the dashboard reaches remote cameras through
the node you opened. The dashboard groups tiles by `host` using `/api/hosts`.

## Dependency updates

Keep direct dependency versions pinned and commit the lockfile with each update.
Check application navigation and service-worker behavior against the production
build, including deep links, redirects, offline navigation, and fresh API/media
responses. The PWA must never satisfy API or footage requests from its cache.
Preserve the existing browser compilation target unless a browser-support change
is intentional and documented.
