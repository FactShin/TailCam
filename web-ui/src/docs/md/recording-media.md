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
- **Retention** — the `max_gb` / `max_age_days` budget below.

## The gallery

The Gallery lists recordings and snapshots with camera, type, size, and trigger
(`manual`, `motion`, or `timelapse`). Filter by camera or media type. Files are
served from:

- `/media/<id>/file` — the recording or snapshot
- `/media/<id>/thumbnail` — a thumbnail

Delete media from the gallery or with `DELETE /api/media/<id>`.

## Retention

To stop media filling the disk, TailCam enforces retention limits from the
`[retention]` config:

| Setting | Default | Meaning |
| --- | --- | --- |
| `max_gb` | 10.0 | Total media budget in gigabytes. |
| `max_age_days` | 30 | Delete media older than this. |

When a limit is exceeded, the oldest media is pruned first. Total usage is shown
on the dashboard and in `GET /api/system` (`media_bytes`).

The [MCP](mcp-overview) tool `suggest_retention_cleanup` gives a non-destructive
analysis of what's using space and what to clean.

## Where files live

Media is stored under TailCam's data directory (set with `TAILCAM_DATA_DIR`). The
SQLite database tracks the index; the files themselves sit alongside it. Use
`tailcam doctor` to see resolved paths.
