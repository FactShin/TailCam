"""The in-app docs wiki is a client-side SPA route; the backend just needs to
serve the SPA shell at /docs (and not collide with FastAPI's own API docs)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def spa_client(tmp_path, monkeypatch, context):
    from tailcam.web import app as web_app

    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "assets").mkdir()
    (spa / "index.html").write_text('<div id="root"></div>')
    (spa / "manifest.webmanifest").write_text('{"name":"TailCam"}')
    (tmp_path / "outside.txt").write_text("outside dashboard directory")
    monkeypatch.setattr(web_app, "_SPA_DIR", spa)
    with TestClient(web_app.create_app(context=context), base_url="http://localhost") as client:
        yield client


def test_spa_rejects_encoded_parent_traversal(spa_client):
    response = spa_client.get("/%2e%2e/outside.txt")
    assert response.status_code == 404
    assert "outside dashboard directory" not in response.text


def test_spa_rejects_symlink_escape(spa_client, tmp_path):
    try:
        (tmp_path / "spa" / "outside.txt").symlink_to(tmp_path / "outside.txt")
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this platform")
    response = spa_client.get("/outside.txt")
    assert response.status_code == 404
    assert "outside dashboard directory" not in response.text


def test_spa_still_serves_public_files_and_client_routes(spa_client):
    assert spa_client.get("/manifest.webmanifest").json() == {"name": "TailCam"}
    assert 'id="root"' in spa_client.get("/docs/installation").text


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
