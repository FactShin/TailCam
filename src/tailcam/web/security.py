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


def _origin_allowed(origin: str, host_header: str) -> bool:
    """Allow same-host, localhost, and tailnet (*.ts.net) origins."""
    host = urlsplit(origin).hostname or ""
    host = host.lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host.endswith(".ts.net"):
        return True
    # Same host as the request (ignoring port differences from a proxy).
    req_host = (host_header.split(":", 1)[0] or "").lower()
    return bool(req_host) and host == req_host


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _MUTATING:
            origin = request.headers.get("origin")
            if origin and not _origin_allowed(origin, request.headers.get("host", "")):
                return JSONResponse(
                    {"detail": "cross-origin request blocked"}, status_code=403
                )
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        return response
