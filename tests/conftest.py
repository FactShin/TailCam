import os

import pytest

# Force the synthetic camera source for the whole suite (no hardware needed).
os.environ["TAILCAM_SYNTHETIC"] = "1"


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
    with TestClient(app) as c:
        yield c
