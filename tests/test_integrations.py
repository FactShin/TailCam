from __future__ import annotations

from tailcam.config import AppConfig
from tailcam.integrations import homeassistant as ha
from tailcam.integrations import homekit as hk
from tailcam.integrations.base import CameraRef, slugify


# -- config ----------------------------------------------------------------
def test_config_roundtrip_integrations():
    cfg = AppConfig()
    data = cfg.to_dict()
    assert "homekit" in data and "homeassistant" in data
    restored = AppConfig.from_dict(data)
    assert restored.homekit.port == 51826
    assert restored.homekit.bridge_name == "TailCam"
    assert restored.homeassistant.discovery_prefix == "homeassistant"
    assert restored.homeassistant.node_id == "tailcam"


# -- HomeKit pure ----------------------------------------------------------
def test_pin_validation_and_generation():
    assert hk.valid_pin("031-45-154")
    assert not hk.valid_pin("123-45-678")  # blocklisted
    assert not hk.valid_pin("11111111")  # wrong format + blocklisted
    assert not hk.valid_pin("031-451-54")  # wrong group sizes
    for _ in range(50):
        assert hk.valid_pin(hk.generate_pin())


def test_build_stream_cmd_keeps_pyhap_placeholders():
    cmd = hk.build_stream_cmd("http://127.0.0.1:8088/stream/cam0.mjpg?fps=15", "/usr/bin/ffmpeg")
    assert cmd.startswith("/usr/bin/ffmpeg -f mjpeg -i http://127.0.0.1:8088/stream/cam0.mjpg?fps=15")
    assert "{ffmpeg}" not in cmd and "{source}" not in cmd
    for ph in ("{fps}", "{width}", "{height}", "{v_srtp_key}", "{address}", "{v_port}"):
        assert ph in cmd  # filled by HAP-python per negotiated stream


def test_camera_options_shape():
    opts = hk.camera_options("http://h/stream/c.mjpg", "192.168.1.9", "ffmpeg")
    assert opts["srtp"] is True
    assert opts["address"] == "192.168.1.9"
    assert opts["video"]["resolutions"]
    assert "start_stream_cmd" in opts


def test_qr_svg_renders():
    svg = hk.qr_svg("X-HM://0024RCIP3WBMT")
    assert svg is not None and svg.lstrip().startswith("<?xml")
    assert "<svg" in svg


def test_homekit_available():
    assert hk.HomeKitBridge.available() is True  # HAP-python installed in dev


# -- HomeKit accessory construction (real driver, never started) -----------
def test_homekit_accessory_builds(tmp_path):
    from pyhap.accessory import Bridge
    from pyhap.accessory_driver import AccessoryDriver

    pin = hk.generate_pin()
    driver = AccessoryDriver(
        port=51999, persist_file=str(tmp_path / "hk.state"),
        pincode=pin.encode(), address="127.0.0.1",
    )
    try:
        bridge = Bridge(driver, "TailCam")
        cam = hk._make_camera(
            driver, "Front Door", "http://127.0.0.1:8088/stream/c.mjpg?fps=15",
            "http://127.0.0.1:8088/stream/c/snapshot.jpg", "ffmpeg",
        )
        bridge.add_accessory(cam)
        driver.add_accessory(bridge)
        assert bridge.xhm_uri().startswith("X-HM://")
        assert cam.services  # camera + stream-management services present
    finally:
        driver.stop()


# -- Home Assistant pure ---------------------------------------------------
def test_slugify():
    assert slugify("video0") == "video0"
    assert slugify("/dev/video0") == "dev_video0"
    assert slugify("Cam #1!") == "cam_1"


def test_discovery_messages():
    refs = [CameraRef("cam0", "Front Door", "cam0"), CameraRef("cam1", "Garage", "cam1")]
    msgs = ha.discovery_messages(
        node_id="tailcam", prefix="homeassistant", cameras=refs,
        device=ha._device_info("tailcam", "100.64.0.1"),
        availability_topic="tailcam/availability", publish_motion=True, publish_status=True,
    )
    topics = {t for t, _ in msgs}
    assert "homeassistant/binary_sensor/tailcam/cam0_motion/config" in topics
    assert "homeassistant/binary_sensor/tailcam/cam1_status/config" in topics
    payloads = dict(msgs)
    motion = payloads["homeassistant/binary_sensor/tailcam/cam0_motion/config"]
    assert motion["device_class"] == "motion"
    assert motion["state_topic"] == "tailcam/cam0/motion"
    assert motion["availability_topic"] == "tailcam/availability"
    status = payloads["homeassistant/binary_sensor/tailcam/cam0_status/config"]
    assert status["device_class"] == "connectivity"


def test_discovery_respects_toggles():
    refs = [CameraRef("c", "C", "c")]
    only_status = ha.discovery_messages(
        node_id="t", prefix="p", cameras=refs, device={}, availability_topic="t/a",
        publish_motion=False, publish_status=True,
    )
    assert all("_status/" in t for t, _ in only_status)


def test_mqtt_available():
    assert ha.MqttPublisher.available() is True  # paho-mqtt installed in dev


# -- REST ------------------------------------------------------------------
def test_integrations_endpoint(client):
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["homekit"]["available"] is True
    assert body["homekit"]["cameras"]  # synthetic cameras present
    ha_body = body["homeassistant"]
    assert ha_body["cameras"]
    assert "platform: mjpeg" in ha_body["yaml"]
    cam = ha_body["cameras"][0]
    assert cam["mjpeg_url"].endswith(".mjpg")
    assert cam["still_image_url"].endswith("/snapshot.jpg")


def test_homeassistant_enable_without_broker(client):
    resp = client.post("/api/integrations/homeassistant", json={"enabled": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["mqtt_configured"] is False  # no broker host -> nothing connects
    assert body["mqtt_connected"] is False


def test_homekit_update_persists_without_start(client):
    # enabled stays False so no HAP driver / mDNS is started during the test.
    resp = client.post(
        "/api/integrations/homekit",
        json={"bridge_name": "My House", "regenerate_pin": True, "enabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bridge_name"] == "My House"
    assert body["running"] is False
    assert hk.valid_pin(body["pin"])
