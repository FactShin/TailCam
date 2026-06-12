import numpy as np

from anycam.motion.detector import MotionDetector


def _blank(h=240, w=320):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_first_frame_has_no_motion():
    det = MotionDetector()
    assert det.process(_blank()).motion is False


def test_identical_frames_no_motion():
    det = MotionDetector()
    img = _blank()
    det.process(img)
    result = det.process(img.copy())
    assert result.motion is False
    assert result.score == 0.0


def test_injected_rectangle_triggers_motion():
    det = MotionDetector(sensitivity=80, min_area=100)
    det.process(_blank())
    moved = _blank()
    moved[80:160, 120:220] = 255  # large bright block
    result = det.process(moved)
    assert result.motion is True
    assert result.score > 0
    assert len(result.boxes) >= 1


def test_sensitivity_changes_threshold():
    low = MotionDetector(sensitivity=1)
    high = MotionDetector(sensitivity=100)
    assert low.threshold > high.threshold
