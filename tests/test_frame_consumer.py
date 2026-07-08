"""FrameConsumer: the camera-following read side (fixes the restart busy-spin
+ silent-death of motion/recording/timelapse)."""

from __future__ import annotations

import numpy as np

from tailcam.camera.frame import FrameBuffer, FrameConsumer


def _publish(buf: FrameBuffer) -> None:
    buf.publish(np.zeros((4, 4, 3), dtype=np.uint8))


def test_delivers_new_frames_in_order():
    buf = FrameBuffer()
    c = FrameConsumer(buf)
    _publish(buf)
    f1 = c.next_frame(timeout=0.1)
    assert f1 is not None and f1.seq == 0
    # No new frame yet -> timeout None, not ended.
    assert c.next_frame(timeout=0.05) is None
    assert c.ended is False
    _publish(buf)
    f2 = c.next_frame(timeout=0.1)
    assert f2 is not None and f2.seq == 1


def test_closed_buffer_without_reacquire_ends_not_spins():
    buf = FrameBuffer()
    c = FrameConsumer(buf)  # no reacquire
    buf.close()
    # Returns None and marks ended immediately (loops break instead of spinning).
    assert c.next_frame(timeout=0.05) is None
    assert c.ended is True


def test_follows_camera_across_restart():
    old = FrameBuffer()
    new = FrameBuffer()
    calls = {"n": 0}

    def reacquire():
        calls["n"] += 1
        return new  # the manager hands back the fresh buffer

    c = FrameConsumer(old, reacquire)
    old.close()  # simulate a restart closing the old buffer
    # First tick after close: re-acquires the new buffer, no frame yet.
    assert c.next_frame(timeout=0.05) is None
    assert c.ended is False
    assert calls["n"] == 1 and c.buffer is new
    # Frames from the new buffer are delivered, sequence restarted at 0.
    _publish(new)
    f = c.next_frame(timeout=0.1)
    assert f is not None and f.seq == 0


def test_ends_when_reacquire_returns_none():
    buf = FrameBuffer()
    c = FrameConsumer(buf, lambda: None)  # camera removed
    buf.close()
    assert c.next_frame(timeout=0.05) is None
    assert c.ended is True


def test_ends_when_reacquire_returns_closed_buffer():
    buf = FrameBuffer()
    dead = FrameBuffer()
    dead.close()
    c = FrameConsumer(buf, lambda: dead)
    buf.close()
    assert c.next_frame(timeout=0.05) is None
    assert c.ended is True
