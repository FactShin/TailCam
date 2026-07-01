# TailCam — field test checklist

A step-by-step guide for testing TailCam in the real world. Built for **two
people**: a **Field tester** (walking around in front of cameras, plugging/
unplugging things) and a **Console watcher** (sitting at the dashboard on a
phone or laptop confirming what shows up). One person can do both — just keep
the dashboard open on your phone while you move.

For each test: do the **Action**, then check the **Expect**. Mark Pass / Fail
and jot anything weird in **Notes**.

> Conventions
> - **Dashboard** = the TailCam web app (the URL you open in a browser).
> - **Settings** = the gear/Settings screen.
> - "Within ~Ns" = give it that long before calling it a fail (video + AI add lag).

---

## 0. Setup (do this first)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|0.1| On the same network as the TailCam host, open the Dashboard URL. | Dashboard loads; you see the camera grid. | |
|0.2| Note the version: Settings → System → Version. | Shows `TailCam 0.99.x`. | |
|0.3| Confirm at least one camera tile shows live video. | Moving in front of it updates the picture. | |

---

## 1. Remote access (Tailscale)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|1.1| From a phone **on the tailnet** (Tailscale app connected), open the Dashboard. | Loads and streams just like on the LAN. | |
|1.2| Turn the phone's **Wi-Fi off** (cellular only, Tailscale still on), reload. | Still loads and streams (proves true remote access). | |
|1.3| Settings → "Access from another device". | Shows the tailnet URL / QR to reach this node. | |

---

## 2. Cameras & live view

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|2.1| Open a camera's live view. | Smooth video, low lag (a second or two is normal). | |
|2.2| **Field:** walk across the camera's view. | Console sees you move in near real time. | |
|2.3| On the camera page, change **zoom / pan**. | View zooms/pans digitally; snapshot/record use the same framing. | |
|2.4| Rotate / flip the image (camera settings). | Orientation changes and sticks after reload. | |
|2.5| Open two cameras / the grid at once. | Both stream without one freezing the other. | |

---

## 3. Snapshots (save a still)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|3.1| On a camera, tap **Snapshot**. | Toast "Snapshot saved" with a **View** link. | |
|3.2| Open **Gallery**. | The new still is at the top, tagged with the camera + time. | |
|3.3| Open the snapshot. | Full image loads; download works. | |

---

## 4. Recording — manual (save a clip)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|4.1| On a camera, tap **Record**. | Button shows recording (red) state. | |
|4.2| **Field:** move around for ~10s. | — | |
|4.3| Tap **Stop**. | Toast "Recording saved" → **View**. | |
|4.4| Gallery → open the clip. | Video plays and shows what you did. | |

---

## 5. Save location (NEW — the disk/folder for recordings)

> Settings → **Recording & storage**.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|5.1| Read **Save location**. | Shows the current folder + a **default/custom** badge and disk free/used. | |
|5.2| Type a folder in **Custom folder** (e.g. an external drive path) → **Set location**. | Toast "Save location updated"; badge flips to **custom**; path updates. | |
|5.3| Enter a path that can't be written. | Clear error ("Cannot create or write to …"); nothing changes. | |
|5.4| Take a snapshot or record a clip (tests 3–4). | The new file appears **in the custom folder on disk** (check the drive). | |
|5.5| Tap **Reset to default**. | Badge returns to **default**; future media saves to the app folder again. | |

---

## 6. Motion detection (events)

> Turn motion on per camera: open the camera → enable **Motion detection**.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|6.1| Enable Motion detection on a camera. | Indicator shows motion is armed. | |
|6.2| **Field:** walk into the camera's view, then leave. | Within ~10s an event appears on the **Events** screen with time + camera. | |
|6.3| Stand still / empty scene. | No new events (no false trigger). If it false-triggers, lower sensitivity. | |

---

## 7. Record on motion (NEW — events save a video)

> Settings → Recording & storage → turn on **"Save a clip when motion is
> detected."** Motion detection (test 6) must also be on for the camera.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|7.1| Enable **Record on motion**. | Toggle stays on. | |
|7.2| **Field:** walk through the camera view. | An event is logged **and** a clip is saved. | |
|7.3| Open that event / the Gallery. | A recording exists for the event and plays back your walk-through (plus a few seconds of tail). | |
|7.4| Confirm the clip is in the **Save location** from test 5. | File is on the expected disk/folder. | |

---

## 8. AI motion labels (optional — needs Ollama)

> Settings → AI motion analysis → point at an Ollama host + model.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|8.1| Enable AI and confirm "reachable / model present". | Green/ready status. | |
|8.2| **Field:** walk past (person), then have a car pass if possible. | Events get labels like `person` / `vehicle` with a confidence. | |

---

## 9. Notifications

> Settings → Notifications → set up Discord, Telegram, and/or the generic
> webhook → **Save**.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|9.1| Tap **Send test**. | A test message arrives in the configured channel(s). | |
|9.2| **Field:** trigger motion (test 6). | A motion alert arrives (with the AI label if AI is on). | |
|9.3| **Field:** unplug a camera (or block it). | Within ~30s an "offline" alert arrives. | |
|9.4| Reconnect the camera. | A "back online" alert arrives. | |

---

## 10. Timelapse

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|10.1| Start a timelapse on a camera. | It begins capturing frames (count climbs). | |
|10.2| Let it run, then **Encode**. | Produces an MP4 you can play; it's listed with size + duration. | |

---

## 11. Apple HomeKit (needs `tailcam[homekit]` + ffmpeg on the host)

> Settings → Integrations → **Apple HomeKit** → Enable. Phone on the **same
> Wi-Fi** as the host for pairing.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|11.1| Enable HomeKit; confirm **running**. | A QR + setup code appear; no "ffmpeg missing" warning (if live video wanted). | |
|11.2| iPhone **Home app → + → Add Accessory → More options** → scan the QR. | TailCam pairs; cameras appear as Home accessories. | |
|11.3| Open a camera in the Home app. | Snapshot shows; **live video plays** (requires ffmpeg). | |
|11.4| Leave Wi-Fi (cellular) with a Home Hub set up. | Camera still viewable remotely via the hub. | |

---

## 12. Home Assistant

> Settings → Integrations → **Home Assistant** → Enable.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|12.1| Copy a camera's **stream**/**snapshot** URL (or **Copy configuration.yaml**). | URLs copy to clipboard. | |
|12.2| Add it in HA as an **MJPEG IP Camera**. | The TailCam camera shows live in Home Assistant. | |
|12.3| *(MQTT, optional)* Set the broker host → Save MQTT; check HA. | A **TailCam** device with motion + online sensors appears. | |
|12.4| **Field:** trigger motion. | The HA **motion** sensor flips on, then clears after the reset window. | |

---

## 13. Retention / auto-cleanup (opt-in)

> Auto-cleanup is **off by default** — TailCam never deletes media unless you
> enable it.

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|13.1| With Auto-cleanup **off**, record clips and wait. | Nothing is ever deleted. | |
|13.2| Settings → Recording & storage → turn on **Auto-cleanup**, set **Storage budget** low (e.g. 0.1 GB), **Save**. | Saves without error; budget fields appear when the toggle is on. | |
|13.3| Record several clips to exceed the budget, wait a few minutes (or restart). | Oldest media is automatically deleted to stay under budget. | |
|13.4| Restore a sane budget (or turn Auto-cleanup back off). | — | |

---

## 14. Multi-node fleet (only if you run more than one TailCam)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|14.1| Open the Dashboard on the main node. | Cameras from peer nodes also appear (tagged by host). | |
|14.2| View a remote node's camera. | It streams through the main node. | |
|14.3| Take down a peer node. | It shows offline; an alert fires (if enabled). | |

---

## 15. Install as an app (PWA)

| # | Action | Expect | P/F |
|---|--------|--------|-----|
|15.1| Add the Dashboard to your phone's home screen. | Installs with the TailCam lens icon. | |
|15.2| Launch it. | Opens full-screen; the lens "boot" animation plays once. | |

---

## Quick triage if something fails

- **No video / camera offline** → on the camera page hit **Restart**; check it's plugged in; check it isn't claimed by another app.
- **Events log but no clip saved** → confirm **both** "Record on motion" (Settings) **and** Motion detection (camera page) are on.
- **Nothing saves to my drive** → re-check the **Save location** path is exactly right and shows **writable**; the badge should read **custom**.
- **HomeKit live video black** → install **ffmpeg** on the host (snapshots/pairing work without it).
- **Can't reach remotely** → confirm Tailscale is connected on both devices; try the tailnet URL from Settings → Access.
- **Notifications silent** → use **Send test** first; if that fails the channel config is wrong, not the trigger.
