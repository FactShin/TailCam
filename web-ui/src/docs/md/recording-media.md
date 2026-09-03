# Recording & media

TailCam captures two kinds of media: **snapshots** (single JPEG stills) and
**recordings** (video clips). Both are browsable on the **Gallery** screen.

## Snapshots

Capture a still from any camera:

- **UI** — the snapshot button on a camera's detail view.
- **API** — `POST /api/cameras/<id>/snapshot` → returns the new media id.
- **Agent** — the [MCP](mcp-overview) tool `capture_snapshot`.

Snapshots are immediate and lightweight; no confirmation needed.

## Recordings

Start and stop recording manually:

- **UI** — record controls on the camera detail view.
- **API** — `POST /api/cameras/<id>/recording/start` and `.../recording/stop`.
- **Agent** — MCP tools `start_recording` / `stop_recording`.

Recordings can also start automatically on motion — see
[Motion detection](motion-detection) and `motion.auto_record`. Recording fps
comes from `stream.default_fps`.

### Timing, disk space, and interrupted clips

- Clips are paced on the wall clock: the encoder receives exactly the camera's
  stream frame rate per second, repeating the last frame when the source is
  slower (a Pi camera negotiated down to 10 fps, a 5 fps storage-node pull). A
  clip therefore plays at real speed regardless of what the camera delivered.
- A recording won't start with less than **200 MB** free at the save location,
  and a recording whose encoder dies mid-way (disk full, drive unmounted) ends
  immediately; the camera page shows the reason. An unfinished file is removed
  rather than left as an unplayable stub.
- Stopping TailCam finalizes every active recording in parallel; the Linux
  service allows three minutes for that (`TimeoutStopSec`), so a restart never
  cuts an encoder off mid-file.

## Recording & storage settings

**Settings → Recording & storage** controls, with no config-file editing:

- **Save location** — where recordings, snapshots, and thumbnails are written.
  Leave blank for the default app data folder, or point it at an external drive
  / NAS mount (`storage.media_dir`). The path is checked for writability before
  it's accepted; existing media stays where it was, new media goes to the new
  location. Shows live disk used/free.
- **Record on motion** — turns on `motion.auto_record` so motion events save a
  clip. Motion detection must also be enabled on the camera for this to fire.
- **Keep recording after motion ends** — `motion.record_tail_seconds`.
- **Auto-cleanup** — opt-in retention (`retention.enabled`). TailCam never
  deletes media unless you turn this on; when on, the `max_gb` /
  `max_age_days` budget below is enforced.

## The gallery

The Gallery lists recordings and snapshots with camera, type, size, and trigger
(`manual`, `motion`, or `timelapse`). Filter by camera or media type. Files are
served from:

- `/media/<id>/file` — the recording or snapshot
- `/media/<id>/thumbnail` — a thumbnail

Delete media from the gallery or with `DELETE /api/media/<id>`.

## Retention

To stop media filling the disk, turn on **Auto-cleanup** (Settings → Recording &
storage). Retention is **opt-in** — TailCam never deletes media unless
`retention.enabled` is true. When enabled, the `[retention]` limits apply:

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Master switch for auto-cleanup. |
| `max_gb` | 10.0 | Total media budget in gigabytes. |
| `max_age_days` | 30 | Delete media older than this. |

When a limit is exceeded, the oldest media is pruned first (checked at startup
and every few minutes; skipped if the media drive is unmounted). Total usage is
shown on the dashboard and in `GET /api/system` (`media_bytes`).

The [MCP](mcp-overview) tool `suggest_retention_cleanup` gives a non-destructive
analysis of what's using space and what to clean.

## Where files live

Media is stored under TailCam's data directory (set with `TAILCAM_DATA_DIR`)
unless you set a custom save location. The SQLite database tracks the index;
the files themselves sit alongside it. Use `tailcam doctor` to see resolved
paths.

- **Custom folder**: type a path or click **Browse…** in Settings → Recording &
  storage to pick a folder on the device (drives and mounts are listed) — so you
  never have to remember a path from your phone. Timelapses follow the same
  location.
- **Another machine**: pick a [storage node](fleet#storage-node) to record this
  device's cameras on a peer with more disk. Its folder can be browsed from
  here too.

## Video format

Recordings and timelapses are written as **H.264 in MP4** through ffmpeg
(bundled, or the system binary), so they play inline in every browser and in the
Home app. If no ffmpeg can run, TailCam falls back to OpenCV's writer (`avc1`,
then `mp4v` — the latter may not play in Chrome/Firefox; install ffmpeg).
