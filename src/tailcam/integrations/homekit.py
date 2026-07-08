"""Apple HomeKit bridge via HAP (HomeKit Accessory Protocol).

This is the native, working path for live camera video in Apple's Home app on
iPhone/iPad/Mac. (Matter, as of this release, does not carry camera streams to
Apple Home — so HomeKit cameras pair over HAP directly, no Matter bridge.)

Each TailCam camera is exposed as a bridged HomeKit IP-camera accessory:

- **Snapshots** come from TailCam's own ``/stream/<cam>/snapshot.jpg``.
- **Live video** is transcoded by ``ffmpeg`` from TailCam's own MJPEG endpoint
  into HomeKit's H.264/SRTP. Reusing the MJPEG stream avoids fighting the
  capture worker for the camera device.

Optional dependency: the ``homekit`` extra (HAP-python). ``ffmpeg`` must be on
the host for live video; snapshots and pairing work without it.
"""

from __future__ import annotations

import secrets
import shutil
import threading
from functools import lru_cache
from re import compile as _re_compile
from typing import TYPE_CHECKING, Any

from tailcam import paths
from tailcam.integrations.base import (
    local_base_url,
    local_ip,
    mjpeg_url,
    selected_cameras,
    snapshot_url,
)
from tailcam.logging_setup import get_logger

if TYPE_CHECKING:
    from tailcam.web.context import AppContext

log = get_logger(__name__)

# HomeKit rejects these setup codes (too simple / reserved).
_PIN_BLOCKLIST = {
    "12345678", "87654321", "00000000", "11111111", "22222222", "33333333",
    "44444444", "55555555", "66666666", "77777777", "88888888", "99999999",
}

# ffmpeg template. {ffmpeg} and {source} are filled here; the remaining
# {placeholders} are filled by HAP-python per negotiated stream.
_STREAM_TEMPLATE = (
    "{ffmpeg} -f mjpeg -i {source} -an -threads 0 -vcodec libx264 -pix_fmt yuv420p "
    "-r {fps} -f rawvideo -tune zerolatency -vf scale={width}:{height} "
    "-b:v {v_max_bitrate}k -bufsize {v_max_bitrate}k -payload_type 99 -ssrc {v_ssrc} "
    "-f rtp -srtp_out_suite AES_CM_128_HMAC_SHA1_80 -srtp_out_params {v_srtp_key} "
    "srtp://{address}:{v_port}?rtcpport={v_port}&localrtcpport={v_port}&pkt_size=1378"
)


_PIN_RE = _re_compile(r"^\d{3}-\d{2}-\d{3}$")


def valid_pin(pin: str) -> bool:
    """A well-formed, non-trivial HomeKit setup code (``XXX-XX-XXX``)."""
    return bool(_PIN_RE.match(pin)) and pin.replace("-", "") not in _PIN_BLOCKLIST


def generate_pin() -> str:
    """A random valid HomeKit setup code, ``XXX-XX-XXX``."""
    while True:
        digits = "".join(secrets.choice("0123456789") for _ in range(8))
        pin = f"{digits[0:3]}-{digits[3:5]}-{digits[5:8]}"
        if valid_pin(pin):
            return pin


def build_stream_cmd(source_url: str, ffmpeg: str = "ffmpeg") -> str:
    return _STREAM_TEMPLATE.replace("{ffmpeg}", ffmpeg).replace("{source}", source_url)


@lru_cache(maxsize=4)
def qr_svg(uri: str) -> str | None:
    """Render an X-HM setup URI as an inline SVG QR (for the Integrations UI).

    Cached: the UI polls integration status every 15s and the URI only changes
    when the pin changes."""
    try:
        import io

        import pyqrcode

        buf = io.BytesIO()
        pyqrcode.create(uri).svg(buf, scale=4, quiet_zone=2)
        return buf.getvalue().decode()
    except Exception as exc:
        log.debug("QR render failed: %s", exc)
        return None


def camera_options(source_url: str, address: str, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """HAP-python ``Camera`` options for a TailCam MJPEG source (pure; testable)."""
    from pyhap import camera as cam

    return {
        "video": {
            "codec": {
                "profiles": [
                    cam.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["BASELINE"],
                    cam.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["MAIN"],
                    cam.VIDEO_CODEC_PARAM_PROFILE_ID_TYPES["HIGH"],
                ],
                "levels": [
                    cam.VIDEO_CODEC_PARAM_LEVEL_TYPES["TYPE3_1"],
                    cam.VIDEO_CODEC_PARAM_LEVEL_TYPES["TYPE3_2"],
                    cam.VIDEO_CODEC_PARAM_LEVEL_TYPES["TYPE4_0"],
                ],
            },
            "resolutions": [
                [1920, 1080, 30], [1280, 960, 30], [1280, 720, 30], [1024, 768, 30],
                [640, 480, 30], [640, 360, 30], [480, 360, 30], [480, 270, 30],
                [320, 240, 30], [320, 180, 30],
            ],
        },
        "audio": {"codecs": [{"type": "OPUS", "samplerate": 24}]},
        "srtp": True,
        "address": address,
        "start_stream_cmd": build_stream_cmd(source_url, ffmpeg),
    }


@lru_cache(maxsize=1)
def _snapshot_client() -> Any:
    """One keep-alive HTTP client for all HomeKit snapshot fetches — controllers
    poll every camera's tile every few seconds while the Home app is open."""
    import httpx

    return httpx.Client(timeout=4.0)


def _make_camera(driver: Any, name: str, source_url: str, snap_url: str, ffmpeg: str) -> Any:
    from pyhap.camera import Camera

    class TailcamCamera(Camera):
        def get_snapshot(self, image_size: Any) -> bytes:  # noqa: ARG002
            try:
                resp = _snapshot_client().get(snap_url)
                if resp.status_code == 200 and resp.content:
                    return resp.content
            except Exception as exc:  # fall back to HAP-python's placeholder
                log.debug("HomeKit snapshot fetch failed for %s: %s", name, exc)
            return super().get_snapshot(image_size)

    options = camera_options(source_url, local_ip(), ffmpeg)
    return TailcamCamera(options, driver, name)


class HomeKitBridge:
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._driver: Any = None
        self._bridge: Any = None
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import pyhap  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def _cfg(self):
        return self._ctx.config.homekit

    def ffmpeg_present(self) -> bool:
        return shutil.which(self._cfg.ffmpeg) is not None

    @property
    def running(self) -> bool:
        return self._driver is not None and self._thread is not None and self._thread.is_alive()

    @property
    def paired(self) -> bool:
        try:
            return bool(self._driver and self._driver.state.paired)
        except Exception:
            return False

    def ensure_pin(self) -> str:
        if not valid_pin(self._cfg.pin):
            self._cfg.pin = generate_pin()
            self._ctx.config.save()
        return self._cfg.pin

    def setup_uri(self) -> str | None:
        try:
            if self._bridge is not None:
                return self._bridge.xhm_uri()
        except Exception as exc:
            log.debug("xhm_uri failed: %s", exc)
        return None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.running or not self._cfg.enabled or not self.available():
                return
            try:
                self._start_locked()
                self._last_error = None
            except Exception as exc:
                # Surface the reason (e.g. "Address already in use") instead of
                # a bare running:false, so the UI can tell the user what to fix.
                self._last_error = str(exc)
                log.warning("HomeKit bridge failed to start: %s", exc)
                self._driver = self._bridge = self._thread = None

    def _start_locked(self) -> None:
        from pyhap.accessory import Bridge
        from pyhap.accessory_driver import AccessoryDriver

        pin = self.ensure_pin()
        persist = paths.config_dir() / "homekit.state"
        persist.parent.mkdir(parents=True, exist_ok=True)
        driver = AccessoryDriver(
            port=self._cfg.port,
            persist_file=str(persist),
            pincode=pin.encode(),
            address=local_ip(),
        )
        bridge = Bridge(driver, self._cfg.bridge_name)
        base = local_base_url(self._ctx)
        cams = selected_cameras(self._ctx, self._cfg.cameras)
        for cam in cams:
            source = mjpeg_url(base, cam.id) + "?fps=15"
            snap = snapshot_url(base, cam.id)
            bridge.add_accessory(_make_camera(driver, cam.name, source, snap, self._cfg.ffmpeg))
        driver.add_accessory(bridge)
        self._driver = driver
        self._bridge = bridge
        self._thread = threading.Thread(target=driver.start, name="homekit", daemon=True)
        self._thread.start()
        log.info("HomeKit bridge '%s' started with %d camera(s)", self._cfg.bridge_name, len(cams))

    def stop(self) -> None:
        with self._lock:
            driver = self._driver
            thread = self._thread
            self._driver = self._bridge = self._thread = None
        if driver is not None:
            try:
                driver.stop()
            except Exception as exc:
                log.debug("HomeKit driver stop: %s", exc)
        if thread is not None:
            thread.join(timeout=5.0)

    def restart(self) -> None:
        self.stop()
        self.start()

    def reset_pairing(self) -> None:
        """Forget all paired controllers (re-pair from scratch)."""
        self.stop()
        try:
            (paths.config_dir() / "homekit.state").unlink(missing_ok=True)
        except OSError as exc:
            log.debug("HomeKit state unlink: %s", exc)
        if self._cfg.enabled:
            self.start()

    # -- status ------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        uri = self.setup_uri()
        return {
            "enabled": self._cfg.enabled,
            "available": self.available(),
            "ffmpeg_present": self.ffmpeg_present(),
            "running": self.running,
            "paired": self.paired,
            "pin": self._cfg.pin if valid_pin(self._cfg.pin) else "",
            "setup_uri": uri,
            "setup_qr": qr_svg(uri) if uri else None,
            "bridge_name": self._cfg.bridge_name,
            "port": self._cfg.port,
            "error": self._last_error or "",
            "selected": list(self._cfg.cameras),
            "cameras": [
                {"id": c.id, "name": c.name} for c in selected_cameras(self._ctx, [])
            ],
        }
