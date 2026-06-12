import numpy as np

from anycam.camera.transforms import (
    CameraTransform,
    StreamTransform,
    crop_zoom_pan,
    flip,
    resize_max_width,
    rotate,
)


def _img(h=4, w=6):
    return np.arange(h * w * 3, dtype=np.uint8).reshape(h, w, 3)


def test_rotate_90_swaps_dimensions():
    img = _img(4, 6)
    out = rotate(img, 90)
    assert out.shape[:2] == (6, 4)


def test_rotate_zero_is_noop():
    img = _img()
    assert np.array_equal(rotate(img, 0), img)


def test_rotate_invalid_raises():
    import pytest

    with pytest.raises(ValueError):
        rotate(_img(), 45)


def test_flip_horizontal():
    img = _img(2, 2)
    out = flip(img, horizontal=True)
    assert np.array_equal(out[:, 0], img[:, -1])


def test_crop_zoom_pan_keeps_dimensions():
    img = _img(20, 20)
    out = crop_zoom_pan(img, zoom=2.0)
    assert out.shape == img.shape


def test_crop_zoom_pan_noop_below_one():
    img = _img(10, 10)
    assert np.array_equal(crop_zoom_pan(img, 1.0), img)


def test_resize_max_width():
    img = _img(40, 80)
    out = resize_max_width(img, 40)
    assert out.shape[1] == 40
    assert out.shape[0] == 20  # aspect preserved


def test_camera_transform_identity():
    assert CameraTransform().is_identity()
    assert not CameraTransform(rotation=90).is_identity()


def test_stream_transform_equality_for_default():
    assert StreamTransform() == StreamTransform()
