import os

import pytest

# Force the synthetic camera source for the whole suite (no hardware needed).
os.environ["TAILCAM_SYNTHETIC"] = "1"


@pytest.fixture(autouse=True)
def _reset_media_override():
    """paths._media_override is process-global; never leak it across tests."""
    from tailcam import paths

    yield
    paths.set_media_override(None)


@pytest.fixture(autouse=True)
def _no_detector_downloads(monkeypatch):
    """The built-in detector self-provisions (a real ~23 MB model download) on
    first use — never from the test suite. Tests that exercise the detector
    stub its state directly (see test_builtin_detection.py)."""
    from tailcam.ai.detector import BuiltinDetector

    monkeypatch.setattr(BuiltinDetector, "ensure_ready", lambda self: None)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    cfg = tmp_path / "config"
    monkeypatch.setenv("TAILCAM_DATA_DIR", str(data))
    monkeypatch.setenv("TAILCAM_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("TAILCAM_CONFIG", raising=False)
    from tailcam import paths

    paths.ensure_dirs()
    return tmp_path


@pytest.fixture
def store(isolated_env):
    from tailcam.persistence.store import Store

    return Store()


@pytest.fixture
def context(isolated_env, store):
    from tailcam.config import AppConfig
    from tailcam.web.context import AppContext

    config = AppConfig()
    config.tailscale.auto_serve = False
    ctx = AppContext(config, store=store)
    ctx.manager.discover()
    yield ctx
    ctx.shutdown()


@pytest.fixture
def client(context):
    from fastapi.testclient import TestClient

    from tailcam.web.app import create_app

    app = create_app(context.config, context=context)
    # base_url gives a Host header of "localhost" — a real browser value that
    # SecurityMiddleware's Host allowlist accepts. TestClient's "testserver"
    # default is a hostname the anti-DNS-rebinding guard (correctly) rejects
    # for mutating requests.
    with TestClient(app, base_url="http://localhost:8088") as c:
        yield c
