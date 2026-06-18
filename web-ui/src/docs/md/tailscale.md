# Tailscale setup

Tailscale is TailCam's network and identity backbone. It gives every device a
stable, private address on your **tailnet**, encrypts all traffic, and tells
TailCam *who* is connecting — without any port forwarding or public exposure.

## Install and sign in

Install Tailscale from [tailscale.com/download](https://tailscale.com/download) on
the TailCam machine and on any device you'll view from, then:

```bash
tailscale up
```

## Serving TailCam over the tailnet

By default (`tailscale.auto_serve = true`) TailCam runs `tailscale serve` for you
on startup, publishing the local server over HTTPS on your tailnet. Your access
URL looks like:

```
https://<host>.<your-tailnet>.ts.net:8443/
```

Find the exact URL on the **Settings** screen or with `tailcam status`.

### The two ports

TailCam has **two** distinct ports — don't confuse them:

| Port | Config | Default | What it is |
| --- | --- | --- | --- |
| Local bind | `server.port` | 8088 | The HTTP port TailCam listens on locally. |
| Tailnet HTTPS | `tailscale.serve_port` | 8443 | The public-on-your-tailnet HTTPS port. |

> Tailscale only allows **443**, **8443**, or **10000** for HTTPS serve/funnel.
> The tailnet port must be one of those three; the local bind port can be anything.

8443 is the default because it keeps TailCam off the root URL (443), so it won't
clobber another app you already serve there.

### Managing serve from the CLI

```bash
tailcam tailscale serve --https-port 8443   # enable (443 | 8443 | 10000)
tailcam tailscale serve-off                 # disable
tailcam tailscale status                    # show tailscale state
```

To run without serving (local only): `tailcam run --no-tailscale`.

## Access URL behavior

The access URL TailCam reports adapts to your setup:

- **Served on 443** → `https://<host>.ts.net/` (clean, no port).
- **Served on 8443/10000** → `https://<host>.ts.net:<port>/`.
- **Not served** → `http://<tailscale-ip>:<local-port>/` (direct over the tailnet),
  or `http://localhost:<local-port>/` locally.

## Identity and app capabilities

Tailscale Serve forwards identity headers to TailCam over loopback, which TailCam
uses to know the caller's user and roles. TailCam enables a Tailscale **app
capability** (`--accept-app-caps`) when your Tailscale version supports it, so
tailnet ACL grants can assign TailCam roles (viewer / operator / admin) to users
and nodes. See [Security & access](security) for the role model, and the ACL
grant examples for fleet relay.

## Funnel (public internet)

TailCam is built for your **tailnet**, not the public internet. It does not enable
Tailscale Funnel, and the [MCP](mcp-security) HTTP endpoint is restricted to
Tailscale-served access. Keep it that way unless you fully understand the
exposure.

## Troubleshooting

- **No access URL?** Check `tailcam tailscale status` and that `tailscale up` has
  run. See [Troubleshooting](troubleshooting).
- **URL uses an unexpected port?** That's `tailscale.serve_port`. Change it with
  `tailcam tailscale serve --https-port ...`.
