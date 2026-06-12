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

```bash
cd web-ui
npm install
npm run dev        # Vite dev server on :5173, proxies /api /stream /media /proxy -> :8088
```

Run a backend alongside it (synthetic camera = no webcam needed):

```bash
TAILCAM_SYNTHETIC=1 tailcam run            # :8088
# point the dev proxy elsewhere with TAILCAM_DEV_TARGET=http://host:port
```

Then rebuild into the package and commit both the source and `src/tailcam/web/spa/`:

```bash
npm run build
npm run typecheck   # optional; CI-style type gate (build itself uses esbuild)
```

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
