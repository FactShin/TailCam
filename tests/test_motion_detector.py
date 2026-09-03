import numpy as np

from tailcam.motion.detector import MotionDetector


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


def test_motion_worker_downscales_and_rescales_boxes():
    import numpy as np

    from tailcam.config import MotionConfig
    from tailcam.motion.worker import ANALYSIS_WIDTH, MotionWorker

    class _Log:
        def open_event(self, *a):
            return 1

        def close_event(self, *a):
            pass

        def set_thumb(self, *a):
            pass

    worker = MotionWorker("cam", None, MotionConfig(min_area=800), _Log())
    big = np.zeros((720, 1280, 3), np.uint8)
    small = worker._downscale(big)
    assert small.shape[1] == ANALYSIS_WIDTH and small.shape[0] == 180
    # min_area scales with the area ratio (1/16 here).
    assert worker._detector.min_area == 50
    assert worker._upscale_boxes([(10, 20, 30, 40)]) == [(40, 80, 120, 160)]
    # Frames already small are analyzed as-is.
    tiny = np.zeros((120, 160, 3), np.uint8)
    assert worker._downscale(tiny) is tiny
    assert worker._upscale_boxes([(1, 2, 3, 4)]) == [(1, 2, 3, 4)]
