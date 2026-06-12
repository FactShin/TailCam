# TailCam Web-UI — Porting the Design Prototype into a Functional Dashboard

> **Status: NOT STARTED.** This is the saved plan for "Stage 2". Stage 1 (backend
> multi-host aggregation) is **done and merged** (PR #4). Resume here.

## Context

Claude Design produced a near-complete **interactive prototype** for the TailCam dashboard.
It nails the visual design and all interaction logic, but it is **not** a wirable codebase:
- Plain JS via in-browser Babel + `window.*` globals (no TypeScript, no module imports).
- Driven by a **mock API**; the live "video" is a `<canvas>` drawing a fake scene via
  `renderFeed()`, not a real MJPEG stream.
- Wrapped in a design-tool harness (Tweaks panel + device frame) that must be removed.
- Missing `index.html` (cut off), though `styles.css` (33 KB, complete) made it.

**The prototype source is saved in this repo at `docs/design-prototype/`** (so resuming needs
nothing external):
- `js/{app,icons,mock,ui,viewer,screens1,screens2}.jsx`, `styles.css`, `tweaks-panel.jsx`
- `DESIGN-README.md` (handoff notes), `design-chat.md` (the full design conversation + the
  original spec/prompt that drove it).

Goal: port this into a real **React + TS + Vite + PWA** app under `web-ui/`, wired to TailCam's
FastAPI API, with FastAPI serving the built bundle at the same Tailscale URL. ~70% of the work
(design, CSS, interaction logic) is already in the prototype; the rest is a mechanical port plus
one real rewrite (the viewer).

## Recommended decisions (confirmed with user)
- **Keep the prototype's custom CSS** (`docs/design-prototype/styles.css`) almost verbatim — it's
  a complete, token-based dark-control-room system. Do **not** rebuild in Tailwind.
- Use **React Query** for data (replacing the prototype's custom `useStore` pub/sub).
- Keep the prototype's component boundaries and class names so the CSS drops in unchanged.

## IMPORTANT: the API changed since the prototype (multi-host shipped in Stage 1)

The prototype's mock predates multi-host aggregation. The real API now returns extra fields and a
new endpoint. The port MUST account for these:

- **`CameraInfo` now includes `host: str` and `proxy_prefix: str`.** `proxy_prefix` is `""` for a
  local camera and `"/proxy/<key>"` for a camera owned by another tailnet node.
- **All stream/control URLs for a camera must be prefixed with its `proxy_prefix`.** e.g. the MJPEG
  src is `` `${cam.proxy_prefix}/stream/${cam.id}.mjpg?...` ``; snapshot POST is
  `` `${cam.proxy_prefix}/api/cameras/${cam.id}/snapshot` ``; PATCH is
  `` `${cam.proxy_prefix}/api/cameras/${cam.id}` ``. For local cameras the prefix is empty, so the
  same code path works unchanged.
- **New `GET /api/hosts`** → `HostInfo[]` (`host, kind: "local"|"peer", online, version,
  camera_count, proxy_prefix`). Use it to **group the dashboard grid by host** (a section header
  per device) so the user sees which machine each camera lives on.
- **`SystemInfo` gained `host`** (this node's identity) — show it in Settings.
- `GET /api/cameras` already returns local + all peers aggregated (no extra work client-side beyond
  honoring `host`/`proxy_prefix`). `?scope=local` exists but the UI should NOT use it.

## Target structure

```
web-ui/
├── package.json            # pinned: react, react-dom, react-router-dom, @tanstack/react-query,
│                           #         vite, typescript, vite-plugin-pwa
├── vite.config.ts          # base:'/', dev proxy /api /stream /media /proxy -> localhost:8088, PWA
├── tsconfig.json
├── index.html              # recreate (was missing from the bundle)
├── public/                 # PWA icons (from the inline Logo SVG), manifest
└── src/
    ├── main.tsx            # React root + QueryClientProvider + BrowserRouter
    ├── styles.css          # ported verbatim from docs/design-prototype/styles.css
    ├── types.ts            # CameraInfo (+host,+proxy_prefix), HostInfo, MediaInfo, etc.
    ├── api/{client.ts,hooks.ts}
    ├── icons.tsx
    ├── components/         # StatusPill, ControlSlider, Toggle, Segmented, BottomSheet,
    │                       #   Toasts, ConfirmDialog, ScopeBadge, LiveViewer, CameraTile
    ├── screens/            # Dashboard, CameraDetail, Gallery, Events, Settings
    └── app/                # AppShell (sidebar + bottom tab bar), router
```

## Work breakdown

1. **Scaffold `web-ui/`** — Vite + React 18 + TS + `vite-plugin-pwa`; pinned `package.json`,
   `vite.config.ts` (base `/`, dev proxy `/api`,`/stream`,`/media`,**`/proxy`** → `:8088`),
   `tsconfig.json`, recreate `index.html`, PWA manifest + icons from the prototype's inline `Logo`. *(small)*
2. **Port `styles.css` verbatim** from `docs/design-prototype/`. *(trivial)*
3. **Real API client** (`src/api/client.ts`) — fetch wrappers for `/api/*`. Reuse the prototype's
   `streamUrl/snapshotUrl/mediaFileUrl/mediaThumbUrl/cacheBust` helpers **but make each accept a
   `proxy_prefix`** and prepend it. Camera ids may contain slashes — do NOT encode them. *(small/medium)*
4. **React Query hooks** (`src/api/hooks.ts`) — `useCameras` (~2.5s), `useCamera`, **`useHosts`**,
   `useSystem` (~15s), `useEvents`/`useMedia` (~5s), mutations `usePatchCamera`/`useSnapshot`/
   `useRecording`/`useDeleteMedia` with optimistic update+revert (prototype's `onPatch` maps
   directly). Pause polling on hidden tab (port `usePageVisible`). Mutations must target
   `cam.proxy_prefix`. *(medium)*
5. **LiveViewer rewrite — the one real change** (`src/components/LiveViewer.tsx`). Replace the
   `<canvas>`+`renderFeed()` sim with a real **MJPEG `<img>`** whose `src` =
   `` `${cam.proxy_prefix}${streamUrl(cam.id, debouncedView)}` ``. KEEP all logic from
   `docs/design-prototype/js/viewer.jsx`: gesture pinch/drag zoom-pan, wheel zoom, debounce
   (260ms), IntersectionObserver offscreen pause (clear `src`), `usePageVisible` pause,
   offline/reconnect backoff + cache-bust, OSD, LIVE/REC chips. Zoom/pan = query params (server
   crops), not CSS transform. Brightness/contrast/rotation = server-side PATCH (no instant preview). *(medium — core)*
6. **Port components 1:1** from `ui.jsx`, `viewer.jsx` (CameraTile), keeping class names. Typed props. *(medium, rote)*
7. **Port the 5 screens** from `screens1.jsx`/`screens2.jsx`. Pick default tile layout
   (`cinematic`) + detail layout (`auto`: side ≥1000px else bottom sheet). **Dashboard: group tiles
   by `host` using `/api/hosts`** (section per device, show online/offline + camera count). *(medium)*
8. **Port AppShell + router** from `app.jsx`, switch hash router → **React Router `BrowserRouter`**.
   **Remove** the Tweaks panel, device-frame stage, and `tweaks-panel.jsx`. *(small/medium)*
9. **FastAPI glue (backend)** — serve the SPA. In `src/tailcam/web/app.py`, mount `web-ui/dist` at
   `/` with `StaticFiles(html=True)` + a catch-all returning `index.html` for non-API paths
   (exclude `/api`, `/stream`, `/media`, `/proxy`, `/static`). Likely retire the Jinja pages
   (`routes_pages.py` + `templates/`). Bundle/ship `dist/` with the package. *(small)*
10. **Build + end-to-end verification** (below). *(medium)*

## Critical files
- Port FROM (in-repo reference): `docs/design-prototype/js/{viewer,ui,screens1,screens2,app,mock,icons}.jsx`, `docs/design-prototype/styles.css`
- Create: everything under `web-ui/`
- Modify (backend): `src/tailcam/web/app.py` (serve dist + SPA catch-all); likely retire
  `src/tailcam/web/routes_pages.py` + `templates/`. Reuse `routes_api.py`, `routes_stream.py`,
  `routes_proxy.py` unchanged.

## Effort summary
Not from scratch. One focused pass → a working, wired-in `web-ui/` (steps 1–9); a second pass for
polish from real-device testing. Biggest risks: (a) canvas→MJPEG viewer swap, (b) many live MJPEG
`<img>`s on the dashboard — mitigated by low-bandwidth grid params (`fps=8&w=480`) + offscreen
`src` clearing. New for multi-host: remote tiles stream **through the proxy**, so a busy aggregator
node carries that bandwidth — keep grid tiles low-fps.

## Verification (end-to-end)
1. `cd web-ui && npm install && npm run build` — clean, no runtime CDN deps.
2. Dev: `npm run dev` (Vite proxy) + `TAILCAM_SYNTHETIC=1 tailcam run` on :8088 — synthetic camera
   exercises the whole UI headless.
3. **Multi-host check:** run a 2nd instance (`TAILCAM_HOST=pi TAILCAM_DATA_DIR=/tmp/nodeB tailcam run
   --port 9090 --no-tailscale`) and point node A at it (`TAILCAM_PEERS=http://127.0.0.1:9090`).
   Confirm the dashboard groups cameras under `mac` and `pi`, and the remote tile streams + its
   controls work (all via `/proxy/pi/...`).
4. Confirm: status pills reflect online/degraded/offline; detail streams; per-tab fps/zoom/pan/
   quality change the `<img>` src; camera settings PATCH + persist; snapshot/record create media;
   gallery + events; offline tile shows reconnect overlay.
5. Prod: `tailcam run`, open `http://localhost:8088/` — SPA served by FastAPI, deep links work, PWA
   installable. Then over Tailscale at `https://<host>.ts.net:8443/`.
6. Backend regression: existing `pytest` green; `ruff` + `mypy` clean.
