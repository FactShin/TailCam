"""Regression: event ids and media ids are local to their owning catalogs."""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from tailcam.cluster.service import ClusterService, Peer, _key_for
from tailcam.config import MCPConfig, MotionConfig, PeersConfig
from tailcam.mcp.client import TailcamClient
from tailcam.mcp.errors import TailcamMcpError
from tailcam.mcp.tools import (
    _event_recording_url,
    _investigate_motion_event,
    _list_recent_events,
)
from tailcam.media.capture_router import RemoteMedia
from tailcam.media.gallery import MediaGallery
from tailcam.motion.events import EventLog
from tailcam.motion.worker import MotionWorker
from tailcam.persistence.models import MediaRecord, MotionEventRecord
from tailcam.persistence.store import Store
from tailcam.web import routes_api, routes_proxy, routes_stream

SOURCE = "camera.tailnet.ts.net"
STORAGE = "storage.tailnet.ts.net"
OBSERVER = "viewer.tailnet.ts.net"


def test_recording_owner_migration_preserves_legacy_events(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE motion_events (id INTEGER PRIMARY KEY, camera_id TEXT, "
            "start_ts REAL, end_ts REAL, peak_score REAL, recording_id INTEGER, "
            "label TEXT, description TEXT, confidence REAL, thumb_path TEXT)"
        )
        conn.execute("INSERT INTO motion_events VALUES (9, 'cam', 1, 2, .8, 1, NULL, "
                     "NULL, NULL, NULL)")
    store = Store(db)
    store.migrate()  # idempotent on the already-upgraded database
    legacy = store.get_motion_event(9)
    assert legacy.recording_id == 1
    assert legacy.recording_host == ""
    log = EventLog(store)
    event_id = log.open_event("cam", 3, .6)
    log.close_event(event_id, 4, .7, 1, STORAGE)
    reopened = Store(db)
    assert reopened.get_motion_event(event_id).recording_host == STORAGE
    log.close_event(9, 2, .8, 1)  # old callers still describe a local recording
    assert reopened.get_motion_event(9).recording_host == ""


@pytest.mark.parametrize("shutdown", [False, True], ids=["cooldown", "shutdown"])
@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_motion_worker_retains_recording_owner(store, monkeypatch, shutdown, remote):
    import tailcam.motion.worker as worker_module

    image = np.zeros((10, 10, 3), dtype=np.uint8)
    motions = iter([True] if shutdown else [True, False])

    class Consumer:
        ended = False

        def next_frame(self, **kwargs):
            if self.ended:
                return None
            return SimpleNamespace(image=image)

    consumer = Consumer()

    def detect(_image):
        motion = next(motions)
        # The next iteration represents camera removal/app shutdown, or follows
        # the normal cooldown close. No camera, encoder, or clock waiting needed.
        if shutdown or not motion:
            consumer.ended = True
        return SimpleNamespace(motion=motion, score=.7, boxes=[])

    record = RemoteMedia(1, STORAGE) if remote else SimpleNamespace(id=1)
    recorder = SimpleNamespace(start=lambda *a, **kw: True, stop=lambda *a: record)
    worker = MotionWorker(
        "cam", None, MotionConfig(cooldown_seconds=-1, record_tail_seconds=0),
        EventLog(store), recorder=recorder,
    )
    monkeypatch.setattr(worker_module, "FrameConsumer", lambda *a: consumer)
    monkeypatch.setattr(worker_module.time, "sleep", lambda *a: None)
    monkeypatch.setattr(worker, "_enrich_event", lambda *a: None)
    monkeypatch.setattr(worker._detector, "process", detect)
    worker._run()
    event = store.list_motion_events()[0]
    assert event.end_ts is not None
    assert event.recording_id == 1
    assert event.recording_host == (STORAGE if remote else "")


@pytest.fixture
def event_fleet(tmp_path):
    """Three in-memory HTTP apps with independent SQLite files and media roots."""
    nodes = {}
    apps = {}
    for host in (SOURCE, STORAGE, OBSERVER):
        root = tmp_path / host
        root.mkdir()
        store = Store(root / "catalog.db")
        clip = root / "clip.mp4"
        clip.write_bytes(f"recording owned by {host}".encode())
        assert store.add_media(MediaRecord(
            None, "cam", "recording", str(clip), None, 1, "motion", clip.stat().st_size,
        )) == 1
        cluster = ClusterService(PeersConfig(auto_discover=False), None, host)
        node = SimpleNamespace(
            local_host=host, store=store, gallery=MediaGallery(store),
            event_log=EventLog(store), cluster=cluster,
            manager=SimpleNamespace(get=lambda _: None),
        )
        app = FastAPI()
        app.state.ctx = node
        app.include_router(routes_api.router)
        app.include_router(routes_proxy.router)
        app.include_router(routes_stream.router)
        nodes[host] = node
        apps[host] = app

    async def dispatch(request):
        transport = httpx.ASGITransport(app=apps[request.url.host])
        return await transport.handle_async_request(request)

    for host, node in nodes.items():
        cluster = node.cluster
        cluster._peers = [
            Peer(_key_for(h), h, f"http://{h}", online=True) for h in apps if h != host
        ]
        cluster._by_key = {p.key: p for p in cluster._peers}
        cluster._fetched_at = time.monotonic()
        cluster._client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    nodes[SOURCE].store.add_motion_event(MotionEventRecord(
        None, "cam", 10, 11, .7, 1, recording_host=STORAGE,
    ))
    return nodes, apps


@pytest.mark.parametrize("viewer", [SOURCE, STORAGE, OBSERVER])
async def test_event_rest_and_mcp_follow_storage_owner_with_colliding_ids(event_fleet, viewer):
    nodes, apps = event_fleet
    client = TailcamClient.for_app(apps[viewer])
    ctx = SimpleNamespace(client=client, config=MCPConfig())
    expected_prefix = "" if viewer == STORAGE else "/proxy/storage"
    try:
        events = await client.events(scope="all")
        event = events[0]
        assert event["host"] == SOURCE
        assert event["recording_host"] == STORAGE
        assert event["recording_proxy_prefix"] == expected_prefix
        listed = await _list_recent_events(ctx, {})
        investigated = await _investigate_motion_event(ctx, {"event_id": 1})
        expected_url = f"{expected_prefix}/media/1/file"
        assert listed.data["events"][0]["recording_url"] == expected_url
        assert investigated.data["links"]["recording_url"] == expected_url
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[viewer]), base_url="http://localhost",
        ) as http:
            clip = await http.get(expected_url)
            assert clip.status_code == 200
            assert clip.content == f"recording owned by {STORAGE}".encode()
            # Gallery opens this exact entry even when it is outside recent pages.
            metadata = await http.get(f"{expected_prefix}/api/media/1")
            assert metadata.status_code == 200
            assert metadata.json()["host"] == STORAGE
            assert (await http.get(f"{expected_prefix}/api/media/99")).status_code == 404
    finally:
        await client.aclose()
        for node in nodes.values():
            await node.cluster.aclose()


async def test_legacy_peer_event_and_offline_storage_never_use_wrong_catalog(event_fleet):
    nodes, _ = event_fleet
    source = nodes[SOURCE]
    source.store.update_motion_event(1, 11, .7, 1)  # legacy event-local recording
    try:
        events = await nodes[STORAGE].cluster.remote_events({})
        assert events[0]["recording_host"] == SOURCE
        assert events[0]["recording_proxy_prefix"] == "/proxy/camera"
        # Owner aliases resolve consistently; absent owners stay unresolved.
        assert source.cluster.media_owner_reference("storage") == (STORAGE, "/proxy/storage")
        source.cluster._peers = []
        assert source.cluster.media_owner_reference(STORAGE) == (STORAGE, None)
        source.cluster._peers = [Peer("storage", "storage.other.ts.net", "http://other")]
        assert source.cluster.media_owner_reference(STORAGE) == (STORAGE, None)
    finally:
        for node in nodes.values():
            await node.cluster.aclose()


def test_mcp_legacy_clip_links_and_unresolved_owners():
    assert _event_recording_url({
        "recording_id": 1, "proxy_prefix": "/proxy/camera",
    }) == "/proxy/camera/media/1/file"
    assert _event_recording_url({
        "recording_id": 1, "proxy_prefix": "/proxy/camera",
        "recording_host": STORAGE, "recording_proxy_prefix": None,
    }) is None


async def test_aggregation_upgrades_event_payloads_from_old_peers():
    cluster = ClusterService(PeersConfig(auto_discover=False), None, STORAGE)
    peer = Peer("camera", SOURCE, f"http://{SOURCE}", online=True)
    cluster._peers = [peer]
    cluster._by_key = {peer.key: peer}
    cluster._fetched_at = time.monotonic()
    cluster._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{
            "id": 1, "recording_id": 1, "host": SOURCE, "proxy_prefix": "",
        }])
    ))
    try:
        event = (await cluster.remote_events({}))[0]
        assert event["recording_host"] == SOURCE
        assert event["recording_proxy_prefix"] == "/proxy/camera"
    finally:
        await cluster.aclose()


async def test_investigation_requires_owner_for_duplicate_event_ids(event_fleet):
    nodes, apps = event_fleet
    nodes[STORAGE].store.add_motion_event(MotionEventRecord(None, "cam", 20, 21, .9, 1))
    client = TailcamClient.for_app(apps[OBSERVER])
    ctx = SimpleNamespace(client=client, config=MCPConfig())
    try:
        with pytest.raises(TailcamMcpError, match="multiple nodes"):
            await _investigate_motion_event(ctx, {"event_id": 1})
        result = await _investigate_motion_event(ctx, {"event_id": 1, "node_key": "camera"})
        assert result.data["event"]["host"] == SOURCE
        assert result.data["nearby_events"] == []  # same camera id on storage is unrelated
    finally:
        await client.aclose()
        for node in nodes.values():
            await node.cluster.aclose()
