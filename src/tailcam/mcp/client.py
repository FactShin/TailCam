"""Typed async TailCam API client used by every MCP tool.

The client wraps TailCam's stable REST and v1 management APIs over httpx. Two
factories cover both transports:

- :meth:`TailcamClient.for_url` — talk to a running node over HTTP (stdio mode,
  ``TAILCAM_URL`` or ``http://127.0.0.1:8088``).
- :meth:`TailcamClient.for_app` — talk to the in-process FastAPI app over an ASGI
  transport (the mounted ``/mcp`` endpoint). No socket, fully testable.

All non-2xx responses and transport failures are normalized into
:class:`~tailcam.mcp.errors.TailcamMcpError` so tools never see raw httpx
exceptions.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from tailcam.mcp import errors
from tailcam.mcp.errors import TailcamMcpError

# Default node URL for local stdio when TAILCAM_URL is unset.
DEFAULT_URL = "http://127.0.0.1:8088"
_DEFAULT_TIMEOUT = 30.0


def _cam_path(camera_id: str) -> str:
    # Camera ids can be path-like (e.g. ``/dev/video0``); the routes use a
    # ``:path`` converter, so keep slashes literal and only escape the rest.
    #
    # Security: the in-process transport calls back into the app as trusted
    # loopback-admin, and httpx applies RFC 3986 dot-segment removal when joining
    # paths. A camera id like ``../v1/node/audit`` would collapse to an admin
    # endpoint and bypass the MCP role gate. Reject traversal segments outright;
    # no legitimate camera id contains a ``.`` or ``..`` path segment.
    if any(segment in (".", "..") for segment in camera_id.split("/")):
        raise TailcamMcpError(errors.CAMERA_UNKNOWN, "invalid camera id", status_code=404)
    return quote(camera_id, safe="/")


class TailcamClient:
    """Thin, error-normalizing wrapper over the TailCam HTTP API."""

    def __init__(self, http: httpx.AsyncClient, *, owns_client: bool = False) -> None:
        self._http = http
        self._owns_client = owns_client

    # -- construction ------------------------------------------------------
    @classmethod
    def for_url(cls, base_url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> TailcamClient:
        http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        return cls(http, owns_client=True)

    @classmethod
    def for_app(cls, app: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> TailcamClient:
        # ASGITransport's default client is ("127.0.0.1", 123): the principal
        # parser treats in-process calls as trusted loopback, so tools can
        # execute against the node while the MCP layer does its own role checks.
        transport = httpx.ASGITransport(app=app)
        http = httpx.AsyncClient(
            transport=transport, base_url="http://tailcam.local", timeout=timeout
        )
        return cls(http, owns_client=True)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # -- core request ------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method, path, params=_clean(params), json=json
            )
        except httpx.ConnectError as exc:
            raise TailcamMcpError(
                errors.NOT_RUNNING,
                "TailCam node is not reachable; is it running?",
            ) from exc
        except httpx.TimeoutException as exc:
            raise TailcamMcpError(errors.TIMEOUT, "TailCam request timed out") from exc
        except httpx.HTTPError as exc:
            raise TailcamMcpError(
                errors.INVALID_RESPONSE, f"TailCam request failed: {exc}"
            ) from exc

        if response.is_success:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise TailcamMcpError(
                    errors.INVALID_RESPONSE, "TailCam returned a non-JSON response"
                ) from exc

        raise _status_error(response, path)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self, path: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.request("POST", path, json=json, params=params)

    async def patch(self, path: str, *, json: Any | None = None) -> Any:
        return await self.request("PATCH", path, json=json)

    # -- system / fleet ----------------------------------------------------
    async def system(self) -> dict[str, Any]:
        return await self.get("/api/system")

    async def hosts(self) -> list[dict[str, Any]]:
        return await self.get("/api/hosts")

    async def update_info(self) -> dict[str, Any]:
        return await self.get("/api/update")

    async def node_health(self, node_key: str) -> dict[str, Any]:
        return await self.get(f"/api/v1/fleet/nodes/{quote(node_key, safe='')}/health")

    async def node_capabilities(self, node_key: str) -> dict[str, Any]:
        return await self.get(f"/api/v1/fleet/nodes/{quote(node_key, safe='')}/capabilities")

    async def node_audit(
        self, node_key: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self.get(
            f"/api/v1/fleet/nodes/{quote(node_key, safe='')}/audit",
            params={"limit": limit, "offset": offset},
        )

    async def reload_node(self, node_key: str) -> dict[str, Any]:
        return await self.post(
            f"/api/v1/fleet/nodes/{quote(node_key, safe='')}/actions/reload"
        )

    # -- cameras -----------------------------------------------------------
    async def cameras(self, *, scope: str = "all") -> list[dict[str, Any]]:
        return await self.get("/api/cameras", params={"scope": scope})

    async def camera(self, camera_id: str) -> dict[str, Any]:
        return await self.get(f"/api/cameras/{_cam_path(camera_id)}")

    async def update_camera(self, camera_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.patch(f"/api/cameras/{_cam_path(camera_id)}", json=body)

    async def restart_camera(self, camera_id: str) -> dict[str, Any]:
        return await self.post(f"/api/cameras/{_cam_path(camera_id)}/restart")

    async def snapshot(self, camera_id: str) -> dict[str, Any]:
        return await self.post(f"/api/cameras/{_cam_path(camera_id)}/snapshot")

    async def start_recording(self, camera_id: str) -> dict[str, Any]:
        return await self.post(f"/api/cameras/{_cam_path(camera_id)}/recording/start")

    async def stop_recording(self, camera_id: str) -> dict[str, Any]:
        return await self.post(f"/api/cameras/{_cam_path(camera_id)}/recording/stop")

    # -- events / media ----------------------------------------------------
    async def events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        camera_id: str | None = None,
        scope: str = "all",
    ) -> list[dict[str, Any]]:
        return await self.get(
            "/api/events",
            params={
                "limit": limit,
                "offset": offset,
                "camera_id": camera_id,
                "scope": scope,
            },
        )

    async def media(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        camera_id: str | None = None,
        media_type: str | None = None,
        scope: str = "all",
    ) -> list[dict[str, Any]]:
        return await self.get(
            "/api/media",
            params={
                "limit": limit,
                "offset": offset,
                "camera_id": camera_id,
                "media_type": media_type,
                "scope": scope,
            },
        )

    # -- ai / training -----------------------------------------------------
    async def ai(self) -> dict[str, Any]:
        return await self.get("/api/ai")

    async def update_ai(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/ai", json=body)

    async def training(self) -> dict[str, Any]:
        return await self.get("/api/training")

    async def update_collection(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/training/collection", json=body)

    async def datasets(self) -> list[dict[str, Any]]:
        return await self.get("/api/datasets")

    async def import_events(self, dataset_id: int) -> dict[str, Any]:
        return await self.post(f"/api/datasets/{int(dataset_id)}/import-events")


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _status_error(response: httpx.Response, path: str) -> TailcamMcpError:
    status = response.status_code
    detail = _detail(response)
    if status in (401, 403):
        code = errors.ADMIN_REQUIRED if "admin" in detail.lower() else errors.UNAUTHORIZED
        return TailcamMcpError(code, detail or "not authorized", status_code=status)
    if status == 404:
        code = errors.NODE_UNKNOWN if "/nodes/" in path else errors.CAMERA_UNKNOWN
        return TailcamMcpError(code, detail or "not found", status_code=status)
    if status in (502, 503, 504):
        return TailcamMcpError(
            errors.PEER_UNREACHABLE,
            detail or "upstream node unreachable",
            status_code=status,
            retryable=True,
        )
    return TailcamMcpError(
        errors.INVALID_RESPONSE,
        detail or f"TailCam returned HTTP {status}",
        status_code=status,
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return ""
