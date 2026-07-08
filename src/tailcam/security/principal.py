"""Request principal parsing for TailCam management APIs."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from email.header import decode_header, make_header
from enum import Enum
from typing import Literal

from starlette.requests import Request

from tailcam.tailscale.client import TAILCAM_APP_CAPABILITY


class TailCamRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


@dataclass(frozen=True)
class RequestPrincipal:
    actor: str
    display_name: str | None
    source: Literal["local", "tailscale-user", "tailscale-node", "unverified"]
    verified: bool
    roles: frozenset[TailCamRole]


_ADMIN_ROLES = frozenset({TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN})
_NO_ROLES: frozenset[TailCamRole] = frozenset()


def principal_from_request(request: Request) -> RequestPrincipal:
    """Return the verified TailCam principal represented by a request."""

    client_host = request.client.host if request.client else ""
    if not _is_loopback(client_host):
        return _unverified()

    caps_header = request.headers.get("tailscale-app-capabilities")
    cap_roles = _roles_from_app_capabilities(caps_header)

    user_login = (request.headers.get("tailscale-user-login") or "").strip()
    if user_login:
        # Honor an explicit TailCam grant if the admin configured one for this
        # identity — including a restricted (viewer/operator-only) grant, and
        # including one that omits admin. Only when NO tailcam capability is
        # present at all do we treat a verified user as the node's admin
        # ("personal mode": one person, no grants set up, it just works).
        # Previously this branch unconditionally granted admin, silently
        # nullifying every restricted grant (privilege escalation).
        roles = cap_roles if _capability_present(caps_header) else _ADMIN_ROLES
        return RequestPrincipal(
            actor=user_login,
            display_name=_decode_header(request.headers.get("tailscale-user-name")),
            source="tailscale-user",
            verified=True,
            roles=roles,
        )

    if cap_roles:
        return RequestPrincipal(
            actor="tailscale-node",
            display_name=None,
            source="tailscale-node",
            verified=True,
            roles=cap_roles,
        )

    host = _request_host(request.headers.get("host", ""))
    if _is_local_host(host):
        return RequestPrincipal(
            actor="local",
            display_name="Local TailCam",
            source="local",
            verified=True,
            roles=_ADMIN_ROLES,
        )

    return _unverified()


def _unverified() -> RequestPrincipal:
    return RequestPrincipal(
        actor="unverified",
        display_name=None,
        source="unverified",
        verified=False,
        roles=_NO_ROLES,
    )


def _decode_header(value: str | None) -> str | None:
    if not value:
        return None
    try:
        decoded = str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return value
    return decoded or None


def _capability_present(raw: str | None) -> bool:
    """True if this identity carries a TailCam capability grant at all (even an
    empty/restricted one). Distinguishes 'admin configured a grant' from
    'personal mode, no grant' so a restricted grant is never upgraded to admin."""
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and TAILCAM_APP_CAPABILITY in data


def _roles_from_app_capabilities(raw: str | None) -> frozenset[TailCamRole]:
    if not raw:
        return _NO_ROLES
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return _NO_ROLES
    entries = data.get(TAILCAM_APP_CAPABILITY) if isinstance(data, dict) else None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return _NO_ROLES

    roles: set[TailCamRole] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_roles = entry.get("roles", [])
        if not isinstance(raw_roles, list):
            continue
        for raw_role in raw_roles:
            try:
                roles.add(TailCamRole(str(raw_role)))
            except ValueError:
                continue
    return frozenset(roles)


def _request_host(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host.strip("[]")
    return host.split(":", 1)[0]


def _is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"} or _is_loopback(host)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
