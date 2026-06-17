"""Normalized error model for the TailCam MCP layer.

Every tool funnels failures through :class:`TailcamMcpError` so agents see a
stable, machine-readable ``code`` plus a human ``message`` regardless of whether
the failure came from the network, an unreachable peer, or a TailCam REST
response. Codes mirror ``docs/superpowers/specs/2026-06-17-tailcam-mcp-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Stable error codes surfaced to agents. Keep in sync with the design doc.
NOT_RUNNING = "tailcam.not_running"
UNAUTHORIZED = "tailcam.unauthorized"
ADMIN_REQUIRED = "tailcam.admin_required"
CONFIRMATION_REQUIRED = "tailcam.confirmation_required"
NODE_UNKNOWN = "tailcam.node_unknown"
CAMERA_UNKNOWN = "tailcam.camera_unknown"
CAMERA_UNAVAILABLE = "tailcam.camera_unavailable"
PEER_UNREACHABLE = "tailcam.peer_unreachable"
INVALID_REQUEST = "tailcam.invalid_request"
INVALID_RESPONSE = "tailcam.invalid_response"
TIMEOUT = "tailcam.timeout"
UNSUPPORTED_TRANSPORT = "tailcam.unsupported_transport"

_RETRYABLE = frozenset({NOT_RUNNING, PEER_UNREACHABLE, TIMEOUT})


@dataclass
class TailcamMcpError(Exception):
    """A normalized, agent-friendly TailCam error.

    ``retryable`` defaults from the code but can be overridden explicitly (e.g. a
    502 from a fleet relay is retryable even though the generic mapping wouldn't
    know the upstream was a peer).
    """

    code: str
    message: str
    status_code: int | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)
        if self.retryable is None:
            self.retryable = self.code in _RETRYABLE

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        return payload


def error_envelope(error: TailcamMcpError) -> dict[str, object]:
    """Shape a failed tool result body: ``{"ok": false, "error": {...}}``."""

    return {"ok": False, "error": error.to_payload()}
