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

import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from tailcam.mcp import errors
from tailcam.mcp.errors import TailcamMcpError
from tailcam.security.principal import RequestPrincipal

# Default node URL for local stdio when TAILCAM_URL is unset.
DEFAULT_URL = "http://127.0.0.1:8088"
_DEFAULT_TIMEOUT = 30.0


def _cam_path(camera_id: str) -> str:
    # Camera ids can be path-like (e.g. ``/dev/video0``); the routes use a
    # ``:path`` converter, so keep slashes literal and only escape the rest.
    #
    # Security: httpx applies RFC 3986 dot-segment removal when joining paths.
    # A camera id like ``../v1/node/audit`` would select a different endpoint.
    # The in-process transport preserves the caller's principal, and traversal
    # rejection also keeps every tool within its intended endpoint. Reject these segments;
    # no legitimate camera id contains a ``.`` or ``..`` path segment.
    if any(segment in (".", "..") for segment in camera_id.split("/")):
        raise TailcamMcpError(errors.CAMERA_UNKNOWN, "invalid camera id", status_code=404)
    return quote(camera_id, safe="/")


_PROXY_RE = re.compile(r"^/proxy/[A-Za-z0-9._~:-]+$")


def _proxy(prefix: str) -> str:
    """Validate a camera's ``proxy_prefix`` before splicing it into a path.

    The prefix comes from API responses; only the exact ``/proxy/{key}`` shape
    the node emits is accepted so a malformed value can't rewrite the request
    path (same reasoning as the traversal guard above).
    """
    if not prefix:
        return ""
    if not _PROXY_RE.match(prefix):
        raise TailcamMcpError(errors.INVALID_RESPONSE, f"invalid proxy prefix {prefix!r}")
    return prefix


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
    def for_app(
        cls,
        app: Any,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        principal: RequestPrincipal | None = None,
    ) -> TailcamClient:
        async def scoped_app(scope, receive, send):
            if principal is not None:
                scope = {**scope, "tailcam.internal_principal": principal}
            await app(scope, receive, send)

        transport = httpx.ASGITransport(app=scoped_app)
        # base_url host must be loopback: it becomes the Host header, and
        # SecurityMiddleware's anti-DNS-rebinding guard only allows localhost /
        # IP-literal / *.ts.net hosts on mutating requests (write tools like
        # snapshot/record would otherwise be blocked with 403).
        http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=timeout)
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
            response = await self._http.request(method, path, params=_clean(params), json=json)
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

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)

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
        return await self.post(f"/api/v1/fleet/nodes/{quote(node_key, safe='')}/actions/reload")

    # -- cameras -----------------------------------------------------------
    async def cameras(self, *, scope: str = "all") -> list[dict[str, Any]]:
        return await self.get("/api/cameras", params={"scope": scope})

    async def camera(self, camera_id: str, *, prefix: str = "") -> dict[str, Any]:
        return await self.get(f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}")

    async def update_camera(
        self, camera_id: str, body: dict[str, Any], *, prefix: str = ""
    ) -> dict[str, Any]:
        return await self.patch(f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}", json=body)

    async def restart_camera(self, camera_id: str, *, prefix: str = "") -> dict[str, Any]:
        return await self.post(f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}/restart")

    async def snapshot(self, camera_id: str, *, prefix: str = "") -> dict[str, Any]:
        return await self.post(f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}/snapshot")

    async def start_recording(self, camera_id: str, *, prefix: str = "") -> dict[str, Any]:
        return await self.post(
            f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}/recording/start"
        )

    async def stop_recording(self, camera_id: str, *, prefix: str = "") -> dict[str, Any]:
        return await self.post(
            f"{_proxy(prefix)}/api/cameras/{_cam_path(camera_id)}/recording/stop"
        )

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

    async def ollama_models(self) -> dict[str, Any]:
        return await self.get("/api/ai/models")

    async def pull_ollama_model(self, model: str) -> dict[str, Any]:
        return await self.post("/api/ai/pull", json={"model": model})

    async def load_ollama_model(self, model: str) -> dict[str, Any]:
        return await self.post("/api/ai/load", json={"model": model})

    # -- datasets / samples ------------------------------------------------
    async def datasets(self) -> list[dict[str, Any]]:
        return await self.get("/api/datasets")

    async def dataset(self, dataset_id: int) -> dict[str, Any]:
        return await self.get(f"/api/datasets/{int(dataset_id)}")

    async def create_dataset(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/datasets", json=body)

    async def delete_dataset(self, dataset_id: int) -> dict[str, Any]:
        return await self.delete(f"/api/datasets/{int(dataset_id)}")

    async def import_events(self, dataset_id: int) -> dict[str, Any]:
        return await self.post(f"/api/datasets/{int(dataset_id)}/import-events")

    async def dataset_samples(
        self, dataset_id: int, *, label: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self.get(
            f"/api/datasets/{int(dataset_id)}/samples",
            params={"label": label, "limit": limit, "offset": offset},
        )

    async def relabel_sample(self, sample_id: int, label: str | None) -> dict[str, Any]:
        return await self.patch(f"/api/samples/{int(sample_id)}", json={"label": label})

    async def delete_sample(self, sample_id: int) -> dict[str, Any]:
        return await self.delete(f"/api/samples/{int(sample_id)}")

    # -- models ------------------------------------------------------------
    async def models(self) -> list[dict[str, Any]]:
        return await self.get("/api/models")

    async def register_model(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/models", json=body)

    async def activate_model(self, model_id: int) -> dict[str, Any]:
        return await self.post(f"/api/models/{int(model_id)}/activate")

    async def deactivate_model(self) -> dict[str, Any]:
        return await self.post("/api/models/deactivate")

    async def delete_model(self, model_id: int) -> dict[str, Any]:
        return await self.delete(f"/api/models/{int(model_id)}")

    # -- training runs -----------------------------------------------------
    async def start_run(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/training/runs", json=body)

    async def runs(self) -> list[dict[str, Any]]:
        return await self.get("/api/training/runs")

    async def run(self, run_id: int) -> dict[str, Any]:
        return await self.get(f"/api/training/runs/{int(run_id)}")

    async def stop_run(self, run_id: int) -> dict[str, Any]:
        return await self.post(f"/api/training/runs/{int(run_id)}/stop")

    # -- durable training supervision -------------------------------------
    async def dataset_revision(self, dataset_id: int) -> dict[str, Any]:
        return await self.get(f"/api/v1/training/datasets/{int(dataset_id)}/revision")

    async def supervisions(self, *, cursor: int = 0, limit: int = 50) -> dict[str, Any]:
        return await self.get(
            "/api/v1/training/supervisions", params={"cursor": cursor, "limit": limit}
        )

    async def approve_supervision(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.post("/api/v1/training/supervisions", json=body)

    async def supervision(self, supervision_id: str) -> dict[str, Any]:
        return await self.get(f"/api/v1/training/supervisions/{UUID(supervision_id)}")

    async def supervision_experiment(
        self, supervision_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.post(
            f"/api/v1/training/supervisions/{UUID(supervision_id)}/experiments", json=body
        )

    async def supervision_heartbeat(
        self, supervision_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.post(
            f"/api/v1/training/supervisions/{UUID(supervision_id)}/heartbeat", json=body
        )

    async def stop_supervision(self, supervision_id: str) -> dict[str, Any]:
        return await self.post(f"/api/v1/training/supervisions/{UUID(supervision_id)}/stop")

    async def finish_supervision(self, supervision_id: str, reason: str) -> dict[str, Any]:
        return await self.post(
            f"/api/v1/training/supervisions/{UUID(supervision_id)}/finish", json={"reason": reason}
        )

    async def supervision_report(self, supervision_id: str) -> dict[str, Any]:
        return await self.get(f"/api/v1/training/supervisions/{UUID(supervision_id)}/report")

    async def supervision_events(self, supervision_id: str, after: int = 0) -> dict[str, Any]:
        return await self.get(
            f"/api/v1/training/supervisions/{UUID(supervision_id)}/events", params={"after": after}
        )


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
        if "/nodes/" in path:
            code = errors.NODE_UNKNOWN
        elif "/cameras/" in path:
            code = errors.CAMERA_UNKNOWN
        else:
            code = errors.INVALID_REQUEST
        return TailcamMcpError(code, detail or "not found", status_code=status)
    if status in (502, 504):
        return TailcamMcpError(
            errors.PEER_UNREACHABLE,
            detail or "upstream unreachable",
            status_code=status,
            retryable=True,
        )
    if status == 503:
        return TailcamMcpError(
            errors.INVALID_RESPONSE,
            detail or "service unavailable",
            status_code=status,
            retryable=False,
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
