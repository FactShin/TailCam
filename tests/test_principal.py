from __future__ import annotations

import json
from email.header import Header

from starlette.requests import Request

from tailcam.security.principal import RequestPrincipal, TailCamRole, principal_from_request

APP_CAPABILITY = "factshin.github.io/cap/tailcam"


def _request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "127.0.0.1",
    host: str = "localhost:8088",
) -> Request:
    all_headers = {"host": host, **(headers or {})}
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/node/health",
            "scheme": "http",
            "server": ("127.0.0.1", 8088),
            "client": (client_host, 54321),
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in all_headers.items()
            ],
        }
    )


def _roles(principal: RequestPrincipal) -> set[TailCamRole]:
    return set(principal.roles)


def test_loopback_is_local_admin() -> None:
    principal = principal_from_request(_request())

    assert principal.actor == "local"
    assert principal.display_name == "Local TailCam"
    assert principal.source == "local"
    assert principal.verified is True
    assert _roles(principal) == {TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN}


def test_serve_user_is_verified_personal_admin() -> None:
    encoded_name = Header("José Camera", "utf-8").encode()
    principal = principal_from_request(
        _request(
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-User-Login": "alice@example.com",
                "Tailscale-User-Name": encoded_name,
            },
        )
    )

    assert principal.actor == "alice@example.com"
    assert principal.display_name == "José Camera"
    assert principal.source == "tailscale-user"
    assert principal.verified is True
    assert TailCamRole.ADMIN in principal.roles


def test_app_capability_roles_are_parsed() -> None:
    principal = principal_from_request(
        _request(
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-App-Capabilities": json.dumps(
                    {APP_CAPABILITY: [{"roles": ["viewer", "operator", "admin"]}]}
                )
            },
        )
    )

    assert principal.actor == "tailscale-node"
    assert principal.display_name is None
    assert principal.source == "tailscale-node"
    assert principal.verified is True
    assert _roles(principal) == {TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN}


def test_tagged_node_without_capability_is_not_admin() -> None:
    principal = principal_from_request(_request(host="tailcam.example.ts.net:8443"))

    assert principal.actor == "unverified"
    assert principal.source == "unverified"
    assert principal.verified is False
    assert principal.roles == frozenset()


def test_spoofed_headers_on_non_loopback_request_are_rejected() -> None:
    principal = principal_from_request(
        _request(
            client_host="100.64.0.22",
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-User-Login": "mallory@example.com",
                "Tailscale-App-Capabilities": json.dumps(
                    {APP_CAPABILITY: [{"roles": ["admin"]}]}
                ),
            },
        )
    )

    assert principal.actor == "unverified"
    assert principal.display_name is None
    assert principal.source == "unverified"
    assert principal.verified is False
    assert principal.roles == frozenset()


def test_user_login_with_restricted_grant_is_not_upgraded_to_admin() -> None:
    # A user carrying an explicit operator grant must get exactly that — not
    # the personal-mode admin default (the privilege-escalation regression).
    principal = principal_from_request(
        _request(
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-User-Login": "bob@example.com",
                "Tailscale-App-Capabilities": json.dumps(
                    {APP_CAPABILITY: [{"roles": ["viewer", "operator"]}]}
                ),
            },
        )
    )
    assert principal.actor == "bob@example.com"
    assert principal.source == "tailscale-user"
    assert principal.verified is True
    assert _roles(principal) == {TailCamRole.VIEWER, TailCamRole.OPERATOR}
    assert TailCamRole.ADMIN not in principal.roles


def test_user_login_with_admin_grant_keeps_admin() -> None:
    principal = principal_from_request(
        _request(
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-User-Login": "carol@example.com",
                "Tailscale-App-Capabilities": json.dumps(
                    {APP_CAPABILITY: [{"roles": ["viewer", "operator", "admin"]}]}
                ),
            },
        )
    )
    assert _roles(principal) == {TailCamRole.VIEWER, TailCamRole.OPERATOR, TailCamRole.ADMIN}


def test_user_login_with_empty_grant_is_locked_down() -> None:
    # An explicit grant that lists no valid roles = no access (fail closed),
    # NOT the admin default.
    principal = principal_from_request(
        _request(
            host="tailcam.example.ts.net:8443",
            headers={
                "Tailscale-User-Login": "dave@example.com",
                "Tailscale-App-Capabilities": json.dumps({APP_CAPABILITY: [{"roles": []}]}),
            },
        )
    )
    assert principal.roles == frozenset()
