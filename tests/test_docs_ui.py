"""The in-app docs wiki is a client-side SPA route; the backend just needs to
serve the SPA shell at /docs (and not collide with FastAPI's own API docs)."""

from __future__ import annotations


def test_docs_route_serves_spa(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="root"' in resp.text  # the SPA shell, not Swagger UI


def test_docs_slug_route_serves_spa(client):
    resp = client.get("/docs/cameras")
    assert resp.status_code == 200
    assert 'id="root"' in resp.text


def test_api_docs_relocated_off_docs(client):
    # Swagger UI moved to /api-docs so /docs can host the wiki.
    swagger = client.get("/api-docs")
    assert swagger.status_code == 200
    assert "swagger" in swagger.text.lower()
    assert client.get("/openapi.json").status_code == 200
