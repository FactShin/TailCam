"""FastAPI dependency helpers."""

from __future__ import annotations

from fastapi import Request

from anycam.web.context import AppContext


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
