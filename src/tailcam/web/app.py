"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tailcam import paths
from tailcam.config import AppConfig
from tailcam.web.context import AppContext

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SPA_DIR = Path(__file__).parent / "spa"  # built React dashboard (web-ui/dist)

# Prefixes that must never be served by the SPA fallback.
_API_PREFIXES = ("/api", "/stream", "/media", "/proxy", "/static", "/assets", "/mcp")


def create_app(config: AppConfig | None = None, context: AppContext | None = None) -> FastAPI:
    config = config or AppConfig.load()
    paths.ensure_dirs()
    ctx = context or AppContext(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.startup()
        yield
        ctx.shutdown()
        await ctx.aclose()

    # Relocate FastAPI's interactive API docs off /docs so the in-app wiki can
    # own that path (the "Docs" nav item). Swagger UI stays available at
    # /api-docs, ReDoc at /api-redoc, and the schema at /openapi.json.
    app = FastAPI(
        title="TailCam",
        lifespan=lifespan,
        docs_url="/api-docs",
        redoc_url="/api-redoc",
    )
    app.state.ctx = ctx
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    from tailcam.web.security import SecurityMiddleware

    app.add_middleware(SecurityMiddleware)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Import here to avoid circular imports at module load.
    from tailcam.web import routes_api, routes_fleet_v1, routes_node_v1, routes_proxy, routes_stream

    app.include_router(routes_stream.router)
    app.include_router(routes_node_v1.router)
    app.include_router(routes_fleet_v1.router)
    app.include_router(routes_proxy.router)
    app.include_router(routes_api.router)

    # Streamable HTTP MCP endpoint. Always mounted (before the SPA catch-all so
    # /mcp is never swallowed by client-side routing) but fail-closed: the
    # handler checks ``[mcp] enabled + http_enabled`` per request, so the MCP
    # page's toggle takes effect immediately — no restart.
    from tailcam.mcp import transport_http

    app.include_router(transport_http.router)

    spa_index = _SPA_DIR / "index.html"
    if spa_index.exists():
        # Serve the built React dashboard. Hashed assets live under /assets;
        # everything else falls back to index.html for client-side routing.
        app.mount("/assets", StaticFiles(directory=str(_SPA_DIR / "assets")), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> FileResponse:
            if any(("/" + full_path).startswith(p) for p in _API_PREFIXES):
                raise HTTPException(status_code=404, detail="not found")
            candidate = _SPA_DIR / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)  # manifest, icons, sw.js, favicon
            return FileResponse(spa_index)
    else:
        # No built SPA (dev without `npm run build`): fall back to Jinja pages.
        from tailcam.web import routes_pages

        app.include_router(routes_pages.router)

    return app
