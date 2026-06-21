# Notifications

TailCam can push an alert when something happens — motion with an AI label, a
camera or fleet node going offline, or a training run finishing. Set it all up in
**Settings → Notifications**, and hit **Send test** to confirm it works.

Three channels, all independent — use any combination:

- **Discord** — easiest. Paste an incoming webhook URL (no bot, no tokens).
- **Telegram** — a bot token + chat id from @BotFather.
- **Generic webhook** — TailCam POSTs a JSON event to a URL *you* control. This is
  the route for a **personal bot like Hermes/OpenClaw** (see below).

## Set up a channel

### Discord
In your Discord server: **Server Settings → Integrations → Webhooks → New
Webhook**, pick a channel, **Copy Webhook URL**, and paste it into the Discord
field in Settings. Alerts arrive as rich embeds with the event thumbnail.

### Telegram
1. Message **@BotFather**, send `/newbot`, and copy the **bot token**.
2. Start a chat with your new bot (send it any message).
3. Get your **chat id** — e.g. open `https://api.telegram.org/bot<token>/getUpdates`
   and read `chat.id`.
4. Paste the token and chat id into Settings.

### Generic webhook
Put any URL that accepts a POST. TailCam sends a JSON body (below). Great for a
personal bot, n8n, Home Assistant, or a serverless function.

## Triggers & filters

Pick what you want to hear about:

- **Motion + AI label** — e.g. "Person · front-door".
- **Camera / node offline** — a camera goes offline/degraded (and when it
  recovers), or a fleet node drops.
- **Training updates** — a run completes or errors.

To avoid noise on motion:

- **Min AI confidence** — skip labels below a threshold.
- **Only these labels** — an allowlist (e.g. `person, vehicle`); blank = all.
- **Cooldown per camera** — a quiet period between motion alerts from the same
  camera (default 60s).

## The webhook payload

Every generic-webhook event is a flat JSON object:

```json
{
  "source": "tailcam",
  "kind": "motion",
  "title": "Person · front-door",
  "body": "person (94%)",
  "severity": "warning",
  "ts": 1718900000.0,
  "camera_id": "front-door",
  "label": "person",
  "confidence": 0.94,
  "event_id": 123
}
```

`kind` is one of `motion`, `camera`, `node`, `training`, or `test`. `severity` is
`info` / `warning` / `success`. Extra fields depend on the kind (e.g. `status`
for camera/node, `run_id` + `metrics` for training).

## Routing through a personal bot (Hermes / OpenClaw)

You have two ways to wire TailCam into a personal agent:

1. **Push (webhook).** Point the generic webhook at an endpoint your bot exposes.
   TailCam POSTs the event; your bot filters, enriches, and forwards it wherever
   you like (a DM, a channel, your phone). This is the simplest path and works
   even when the bot isn't actively running a session.
2. **Pull (MCP).** Your bot connects to TailCam's [MCP](mcp-overview) server and
   polls `list_recent_events` (and `summarize_fleet_health`, `find_offline_cameras`)
   on a schedule, deciding what's worth a ping. No webhook needed — and the bot
   can also *act* (capture a snapshot, reload a node) in the same breath.

An example skill that does both ships in the repo at
`examples/skills/tailcam-alerts.md`, with sample payloads in
`examples/notifications/`.

> Note: external services (Discord, Telegram) can't reach your private tailnet, so
> TailCam **uploads the event thumbnail directly** to them — the image shows up in
> the alert without exposing your node to the internet.
