# Home automation

TailCam plugs into the two big home-automation ecosystems: **Apple HomeKit**
(the Home app on iPhone / iPad / Mac) and **Home Assistant**. Set both up under
**Settings → Integrations**.

## Apple HomeKit

Your cameras appear in Apple's **Home** app as HomeKit cameras — live view,
snapshots, and remote access through a Home Hub (HomePod / Apple TV).

> **Why HAP, not Matter?** Apple Home consumes live camera video over **HAP**
> (the HomeKit Accessory Protocol). Matter, as of this release, does **not**
> carry camera streams to Apple Home — so TailCam pairs directly over HAP. No
> Matter bridge or hub-side bridge software is required.

### Requirements
- Install the extra: `pip install 'tailcam[homekit]'`
- **ffmpeg** on the TailCam host (for live video). Without it, snapshots and
  pairing still work, but live view won't. Install via your package manager
  (`apt install ffmpeg`, `brew install ffmpeg`, …).
- Your iPhone/iPad and the TailCam host on the **same local network** for
  pairing (HomeKit discovers accessories over Bonjour/mDNS). Once paired, a Home
  Hub gives you remote access from anywhere.

### Pair
1. **Settings → Integrations → Apple HomeKit → Enable.**
2. Pick which cameras to expose (all by default).
3. In the **Home** app: **+ → Add Accessory → More options…**, choose your
   TailCam bridge, and **scan the QR** shown in Settings — or tap *Enter Code
   Manually* and type the **setup code**.
4. The cameras show up as a bridge of camera accessories.

Use **New setup code** to rotate the code, or **Reset pairing** to forget all
controllers and start over. The HomeKit bridge listens on port **51826** by
default — allow it through any host firewall.

## Home Assistant

Two complementary paths — cameras for viewing, MQTT for automations.

### Cameras (no extra dependency)
TailCam already serves stream + snapshot URLs, so add each camera with HA's
built-in **MJPEG IP Camera** integration:

1. **Settings → Integrations → Home Assistant → Enable.**
2. Copy the per-camera **stream** / **snapshot** URLs (or **Copy
   configuration.yaml** for all of them) and add to HA, e.g.:

```yaml
camera:
  - platform: mjpeg
    name: "Front Door (TailCam)"
    mjpeg_url: http://<tailscale-host>:8088/stream/front.mjpg
    still_image_url: http://<tailscale-host>:8088/stream/front/snapshot.jpg
```

The URLs use your Tailscale host so Home Assistant can reach TailCam from
anywhere on your tailnet.

### Automations via MQTT (optional)
Publish each camera's **motion** and **connectivity** to HA as auto-discovered
`binary_sensor` entities, so automations can react to TailCam events.

1. Install the extra: `pip install 'tailcam[mqtt]'`
2. Under **Home Assistant → MQTT discovery**, set your broker **host/port** (the
   same broker HA uses) and credentials, then **Save MQTT**.
3. HA auto-creates, per camera:
   - **`<name> Motion`** — a `motion` binary sensor (on when TailCam detects
     motion; carries the AI label/confidence as attributes; auto-clears after a
     short window).
   - **`<name> Online`** — a `connectivity` binary sensor (camera up/down).

All entities are grouped under one **TailCam** device in HA.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HomeKit toggle disabled | `pip install 'tailcam[homekit]'`, then restart. |
| "ffmpeg missing — snapshots only" | Install `ffmpeg` on the TailCam host. |
| Accessory not found in Home app | Pair from the **same Wi-Fi/LAN**; check the host firewall allows port 51826 + mDNS. |
| HA can't load the camera | Confirm the host can reach the stream URL (Tailscale up); try the snapshot URL in a browser. |
| MQTT sensors don't appear | Install `tailcam[mqtt]`; verify broker host/credentials and that HA's MQTT integration uses the same broker. |

Check readiness any time from the CLI: `tailcam homekit`.
