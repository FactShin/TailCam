# Motion detection

TailCam detects motion with a cheap, fast pixel-difference algorithm that runs on
a downsampled copy of the feed. It's designed to gate more expensive work — like
[AI analysis](ai-analysis) — so the vision model is only consulted when something
actually moves.

## How it works

1. A lightweight motion worker samples the feed at `motion.sample_fps` (default 5
   fps) — not the full stream rate.
2. Consecutive frames are differenced; regions of change larger than
   `motion.min_area` pixels count as motion.
3. `motion.sensitivity` (1–100, higher = more sensitive) tunes the threshold.
4. When motion crosses the threshold a **motion event** is recorded, with a
   thumbnail and timestamp. Events appear on the **Events** screen.
5. A `motion.cooldown_seconds` window (default 5s) prevents one continuous event
   from spamming many records.

## Enabling it

Motion is **off** by default. Enable it:

- **Per camera** — toggle Motion detection in the camera's settings.
- **Globally** — set `motion.enabled = true` in [Configuration](configuration).

## Automatic recording

Set `motion.auto_record = true` to start a recording when motion begins. TailCam
keeps recording until motion stops, plus a tail of `motion.record_tail_seconds`
(default 5s) so you don't lose the end of the action. Clips land in the
[gallery](recording-media) and are linked from the triggering event.

## Tuning

| Setting | Default | Effect |
| --- | --- | --- |
| `sensitivity` | 50 | Higher catches subtler motion (and more false positives). |
| `min_area` | 800 | Minimum changed-pixel area to count. Raise to ignore small movement. |
| `sample_fps` | 5 | How often motion is sampled. Higher = more responsive, more CPU. |
| `cooldown_seconds` | 5.0 | Gap before a new event can start. |
| `auto_record` | false | Record automatically on motion. |
| `record_tail_seconds` | 5.0 | Extra recording after motion ends. |

**Too many false events?** Lower `sensitivity` or raise `min_area`. Outdoor
cameras with foliage and changing light usually need this.

**Missing real motion?** Raise `sensitivity`, lower `min_area`, or raise
`sample_fps`.

## Events and AI labels

Each motion event stores a peak motion score and a thumbnail. If [AI
analysis](ai-analysis) is enabled, the event is also labeled (person, animal,
vehicle, package, plant, nothing) with a confidence and short description. Browse
and filter events on the **Events** screen, or via `GET /api/events`.

For agent-driven investigation, the [MCP](mcp-overview) tool
`investigate_motion_event` pulls an event's detail, nearby events, camera state,
and media links together.
