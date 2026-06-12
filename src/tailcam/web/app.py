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
_API_PREFIXES = ("/api", "/stream", "/media", "/proxy", "/static", "/assets")


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

    app = FastAPI(title="TailCam", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    from tailcam.web.security import SecurityMiddleware

    app.add_middleware(SecurityMiddleware)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Import here to avoid circular imports at module load.
    from tailcam.web import routes_api, routes_proxy, routes_stream

    app.include_router(routes_stream.router)
    app.include_router(routes_proxy.router)
    app.include_router(routes_api.router)

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
