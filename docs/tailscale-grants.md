# TailCam Tailscale Grants

TailCam uses Tailscale Serve identity headers and one application capability for
remote/fleet administration:

```text
factshin.github.io/cap/tailcam
```

When the local Tailscale CLI supports `tailscale serve --accept-app-caps`, TailCam
starts Serve with:

```bash
tailscale serve --bg --accept-app-caps=factshin.github.io/cap/tailcam --https=8443 localhost:8088
```

Tailscale announced app capabilities for the 1.92 stable release. TailCam detects
support from `tailscale serve --help`: older Tailscale clients still expose TailCam
privately over Serve, but tagged-node-to-tagged-node administration is unavailable
and appears as a node health warning until Tailscale is upgraded.

## Roles

TailCam currently recognizes these app-capability roles:

- `viewer` can read node state and camera surfaces.
- `operator` can perform day-to-day camera operations.
- `admin` can perform privileged node and fleet administration.

Personal Serve requests that include `Tailscale-User-Login` are treated as Personal
mode admin in this alpha. Tagged-node automation should use the grants below so
TailCam can authorize the request from `Tailscale-App-Capabilities`.

Application capabilities only apply after network-level access is allowed. These
examples include `ip: ["tcp:8443"]` for TailCam's default Serve port; change the
port if you use `tailscale.serve_port = 443` or `10000`.

## Personal Admin

Grant one owner full TailCam roles on tagged TailCam nodes:

```json
{
  "tagOwners": {
    "tag:tailcam": ["alice@example.com"]
  },
  "grants": [
    {
      "src": ["alice@example.com"],
      "dst": ["tag:tailcam"],
      "ip": ["tcp:8443"],
      "app": {
        "factshin.github.io/cap/tailcam": [
          { "roles": ["viewer", "operator", "admin"] }
        ]
      }
    }
  ]
}
```

## Operator

Grant an operations group view/control permissions without admin powers:

```json
{
  "groups": {
    "group:tailcam-operators": ["bob@example.com", "casey@example.com"]
  },
  "grants": [
    {
      "src": ["group:tailcam-operators"],
      "dst": ["tag:tailcam"],
      "ip": ["tcp:8443"],
      "app": {
        "factshin.github.io/cap/tailcam": [
          { "roles": ["viewer", "operator"] }
        ]
      }
    }
  ]
}
```

## Tagged-Node Admin

Grant TailCam nodes the ability to administer each other across the fleet:

```json
{
  "tagOwners": {
    "tag:tailcam": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:tailcam"],
      "dst": ["tag:tailcam"],
      "ip": ["tcp:8443"],
      "app": {
        "factshin.github.io/cap/tailcam": [
          { "roles": ["viewer", "operator", "admin"] }
        ]
      }
    }
  ]
}
```

Use this fleetwide grant only for nodes you trust to act as TailCam administrators.
It lets a TailCam desktop or node relay privileged management actions to every
other TailCam node through Tailscale, while still failing closed when the app
capability is absent.

## References

- [Tailscale application capabilities](https://tailscale.com/docs/features/access-control/grants/grants-app-capabilities)
- [Tailscale grants syntax](https://tailscale.com/docs/reference/syntax/grants)
- [Tailscale app capabilities announcement](https://tailscale.com/blog/app-capabilities)
