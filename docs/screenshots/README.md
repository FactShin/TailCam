# README screenshots

These are real captures of the running app (dark theme, 1440×900 @2x, downscaled),
taken with headless Chromium/Playwright against a synthetic-camera fleet — no
webcam or tailnet needed.

To regenerate:

1. Start three peer nodes (isolated config/data dirs), each with a synthetic camera:

   ```bash
   TAILCAM_SYNTHETIC=1 TAILCAM_PORT=8090 TAILCAM_HOST=garage-pi \
     TAILCAM_CONFIG_DIR=/tmp/tc0/config TAILCAM_DATA_DIR=/tmp/tc0/data tailcam run &
   # repeat for printer-pi:8091 and mac-mini:8092
   ```

2. Start the main node pointing at them:

   ```bash
   TAILCAM_SYNTHETIC=1 TAILCAM_HOST=workshop-pi \
     TAILCAM_PEERS=http://localhost:8090,http://localhost:8091,http://localhost:8092 tailcam run &
   ```

3. Rename cameras and enable motion via the API (`PATCH /api/cameras/synthetic-0`
   with `{"name": …, "motion_enabled": true}` on each port), take a snapshot or two
   (`POST /api/cameras/synthetic-0/snapshot`), start a recording and a timelapse, and
   give motion events a minute to accumulate.

4. Screenshot each route (`/`, `/camera/workshop-pi/synthetic-0`, `/gallery`,
   `/events`, `/ai`, `/timelapse`, `/agents`, `/plugins`, `/settings`, `/docs`) with
   Playwright at a 1440×900 viewport, `deviceScaleFactor: 2`, dark color scheme.
   MJPEG streams never go network-idle — wait on a fixed delay (~4 s), not
   `networkidle`.

   For the video wall, open a stream-free page first (e.g. `/settings`) and press
   `w` — opening it over the dashboard leaves the grid's MJPEG connections running
   underneath, and the browser's per-origin connection limit starves some wall tiles.

   The command palette is `Ctrl/Cmd+K` on any page.
