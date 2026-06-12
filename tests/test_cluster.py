import httpx

from anycam.cluster.service import ClusterService, _key_for
from anycam.config import PeersConfig

PEER_URL = "https://anycam-pi.tailnet.ts.net:8443"
PEER_HOST = "anycam-pi.tailnet.ts.net"
LOCAL_HOST = "mymac.tailnet.ts.net"


def _camera(cid: str) -> dict:
    return {
        "id": cid,
        "name": "Front Door",
        "backend": "v4l2",
        "status": "online",
        "fps": 15.0,
        "width": 1280,
        "height": 720,
        "recording": False,
        "motion_enabled": True,
        "properties": {"width": 1280, "height": 720, "fps": 15},
        "transform": {"rotation": 0, "flip_h": False, "flip_v": False},
        "host": PEER_HOST,
        "proxy_prefix": "",
    }


class FakeTailscale:
    def is_installed(self) -> bool:
        return False

    def peers(self):
        return []


def _service(handler, static=None) -> ClusterService:
    svc = ClusterService(
        PeersConfig(auto_discover=False, static=static if static is not None else [PEER_URL]),
        FakeTailscale(),
        local_host=LOCAL_HOST,
    )
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
    return svc


def _peer_handler(host: str = PEER_HOST, cameras=None):
    cams = cameras if cameras is not None else [_camera("synthetic-0")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/system":
            return httpx.Response(200, json={"version": "0.1.0", "host": host})
        if request.url.path == "/api/cameras":
            return httpx.Response(200, json=cams)
        return httpx.Response(404)

    return handler


def test_key_for_sanitizes_hostname():
    assert _key_for("anycam-pi.tailnet.ts.net") == "anycam-pi"
    assert _key_for("My_Box.local") == "my-box"


async def test_discovers_peer_and_assigns_key():
    svc = _service(_peer_handler())
    peers = await svc.peers()
    assert len(peers) == 1
    assert peers[0].host == PEER_HOST
    assert peers[0].key == "anycam-pi"
    assert peers[0].version == "0.1.0"
    assert svc.peer_base("anycam-pi") == PEER_URL
    await svc.aclose()


async def test_skips_self():
    # Peer reports our own host -> must not be treated as a peer.
    svc = _service(_peer_handler(host=LOCAL_HOST))
    peers = await svc.peers()
    assert peers == []
    await svc.aclose()


async def test_remote_cameras_tagged_with_proxy_prefix():
    svc = _service(_peer_handler(cameras=[_camera("synthetic-0"), _camera("/dev/video0")]))
    cams = await svc.remote_cameras()
    assert len(cams) == 2
    for c in cams:
        assert c["host"] == PEER_HOST
        assert c["proxy_prefix"] == "/proxy/anycam-pi"
    await svc.aclose()


async def test_unreachable_peer_is_tolerated():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    svc = _service(handler)
    assert await svc.peers() == []
    assert await svc.remote_cameras() == []
    await svc.aclose()


async def test_no_peers_when_static_empty_and_no_discovery():
    svc = _service(_peer_handler(), static=[])
    assert await svc.peers() == []
    await svc.aclose()


def test_peer_base_unknown_key():
    svc = _service(_peer_handler())
    assert svc.peer_base("nope") is None
