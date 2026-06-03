"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from anycam import paths
from anycam.config import AppConfig
from anycam.web.context import AppContext

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"


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

    app = FastAPI(title="AnyCam", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Import here to avoid circular imports at module load.
    from anycam.web import routes_api, routes_pages, routes_proxy, routes_stream

    app.include_router(routes_stream.router)
    app.include_router(routes_proxy.router)
    app.include_router(routes_api.router)
    app.include_router(routes_pages.router)
    return app
