"""Security middleware for TailCam.

The trust model is "the network is the boundary" — TailCam binds to localhost
and is reached over Tailscale, with no per-request auth. This middleware adds
defense-in-depth for that model:

1. Security response headers (nosniff, clickjacking, referrer, permissions, CSP).
2. A cross-origin guard on state-changing requests so a malicious web page the
   user happens to visit can't drive their local/tailnet TailCam (CSRF / drive-by
   / DNS-rebinding). Requests from localhost and the tailnet (*.ts.net) are
   allowed; requests with a foreign Origin are rejected. Tools without an Origin
   header (curl, the CLI) are unaffected.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "  # React sets inline style attributes
    "script-src 'self'; "
    "connect-src 'self'; "
    "worker-src 'self'; "
    "manifest-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'"
)

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), payment=(), usb=()",
    "Content-Security-Policy": _CSP,
}


def _hostname(value: str) -> str:
    """The bare host from a Host header or origin authority (drops the port,
    unwraps a bracketed IPv6 literal)."""
    host = (value or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host.strip("[]")
    return host.split(":", 1)[0]


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_allowed(value: str) -> bool:
    """Whether a Host/Origin host is legitimate for a mutating request.

    A DNS-rebinding attack needs a *hostname* (e.g. evil.com) that the attacker
    rebinds to 127.0.0.1, so we reject any hostname that isn't localhost or the
    tailnet (*.ts.net). Bare IP literals can't be rebinding targets (the user
    typed/knows the address), so every IP-based access — loopback, tailnet
    100.64/10, or a LAN IP — is allowed, preserving TailCam's supported
    local/LAN/tailnet reach.
    """
    host = _hostname(value)
    if not host:
        return False
    if host in ("localhost",):
        return True
    if host.endswith(".ts.net"):
        return True
    return _is_ip_literal(host)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _MUTATING:
            # Reject on the Host header first: this catches DNS rebinding even
            # when the page sends no Origin (a rebound hostname reaches us as
            # the Host). Then reject a foreign Origin. Tools with an IP Host and
            # no Origin (curl, the CLI) pass, matching the prior behavior.
            if not _host_allowed(request.headers.get("host", "")):
                return JSONResponse(
                    {"detail": "cross-origin request blocked"}, status_code=403
                )
            origin = request.headers.get("origin")
            if origin and not _host_allowed(urlsplit(origin).netloc):
                return JSONResponse(
                    {"detail": "cross-origin request blocked"}, status_code=403
                )
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        return response
