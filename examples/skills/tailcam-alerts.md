# Skill: TailCam alerts (Hermes / OpenClaw)

A reference "skill" for routing TailCam notifications through a personal agent
like Hermes or OpenClaw. There are two patterns — use whichever fits your bot.
Both end with the bot deciding what's worth your attention and forwarding it
(DM, channel, phone push, etc.).

TailCam already does the simple fan-out (Discord / Telegram / webhook) on its own
— reach for an agent skill when you want **smart filtering, summarization, or to
take an action** in response (e.g. "if a person is seen at the front door after
11pm, capture a snapshot and DM me").

---

## Pattern A — Push (receive the webhook)

Point **Settings → Notifications → Generic webhook** at an endpoint your bot
exposes. TailCam POSTs a flat JSON event (see
`examples/notifications/sample-payloads.json`). Your skill handles it:

```python
# pseudo-code for a webhook handler in your bot
def on_tailcam_event(event: dict):
    if event["kind"] == "motion":
        # Smart filter: only escalate people/vehicles at night, above 0.8
        if event.get("label") in {"person", "vehicle"} and event.get("confidence", 0) >= 0.8:
            if is_quiet_hours():
                notify_me(f"🚨 {event['title']} — {event['body']}")
    elif event["kind"] in {"camera", "node"} and event["severity"] == "warning":
        notify_me(f"⚠️ {event['title']}")
    elif event["kind"] == "training" and event["status"] == "complete":
        notify_me(f"✅ {event['title']} — {event['body']}")
```

This works even when the bot isn't in an active session — it's just an HTTP
endpoint.

---

## Pattern B — Pull (poll via MCP)

Connect your bot to TailCam's MCP server (`tailcam mcp stdio`, or the remote
`/mcp` over Tailscale — see `docs/mcp.md`) and poll on a schedule. No webhook
needed, and the bot can **act** in the same turn.

Loop (every ~30–60s):

1. Call `list_recent_events` (it returns recent motion events with labels,
   confidence, and thumbnail URLs).
2. Track the highest `event id` you've already seen; only act on newer ones.
3. Apply your own rules, then forward — or call another tool, e.g.
   `capture_snapshot`, `get_node_health`, or `summarize_fleet_health`.

Example agent prompt for a recurring skill:

> Every minute, call `list_recent_events` with limit 10. For any event newer than
> the last id you reported, if the label is `person` or `vehicle` with confidence
> ≥ 0.8, send me a one-line alert with the camera and label. Once an hour, call
> `summarize_fleet_health` and only message me if there are errors.

MCP config (stdio) for the bot:

```json
{
  "mcpServers": {
    "tailcam": {
      "command": "tailcam",
      "args": ["mcp", "stdio"],
      "env": { "TAILCAM_URL": "http://127.0.0.1:8088" }
    }
  }
}
```

---

## Which to use?

- **Just want pings forwarded/filtered?** Pattern A (webhook) is simplest.
- **Want the bot to investigate or act (snapshot, reload, summarize)?** Pattern B
  (MCP) — it's a two-way door.
- **Both?** Use the webhook to wake the bot, then let it pull more context via MCP.
