from anycam.camera.enumerate import _caps_has_capture

# V4L2 capability bit-parsing. On a Raspberry Pi, only the real webcam node
# advertises VIDEO_CAPTURE; the codec/ISP nodes (video10-23) do not, and must
# be filtered out so workers don't try to open them (select() timeouts).

V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_STREAMING = 0x04000000
V4L2_CAP_VIDEO_M2M = 0x00008000
V4L2_CAP_DEVICE_CAPS = 0x80000000


def test_usb_webcam_is_capture():
    # device_caps advertises capture; capabilities has the DEVICE_CAPS bit.
    capabilities = V4L2_CAP_DEVICE_CAPS | V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING
    device_caps = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING
    assert _caps_has_capture(capabilities, device_caps) is True


def test_pi_codec_node_is_not_capture():
    # video10-style M2M codec node: device_caps lacks VIDEO_CAPTURE.
    capabilities = V4L2_CAP_DEVICE_CAPS | V4L2_CAP_VIDEO_M2M | V4L2_CAP_STREAMING
    device_caps = V4L2_CAP_VIDEO_M2M | V4L2_CAP_STREAMING
    assert _caps_has_capture(capabilities, device_caps) is False


def test_legacy_driver_without_device_caps_uses_capabilities():
    # No DEVICE_CAPS bit -> fall back to the device-wide capabilities field.
    capabilities = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING
    assert _caps_has_capture(capabilities, 0) is True


def test_non_capture_without_device_caps():
    capabilities = V4L2_CAP_STREAMING  # output/meta-only, no capture
    assert _caps_has_capture(capabilities, 0) is False
