import threading
import time

import numpy as np

from tailcam.camera.frame import FrameBuffer


def _img():
    return np.zeros((2, 2, 3), dtype=np.uint8)


def test_publish_increments_seq():
    buf = FrameBuffer()
    f0 = buf.publish(_img())
    f1 = buf.publish(_img())
    assert f1.seq == f0.seq + 1


def test_await_latest_returns_new_frame():
    buf = FrameBuffer()
    buf.publish(_img())
    frame = buf.await_latest(last_seq=-1, timeout=0.5)
    assert frame is not None
    assert frame.seq == 0


def test_await_latest_times_out_when_no_new_frame():
    buf = FrameBuffer()
    f = buf.publish(_img())
    assert buf.await_latest(last_seq=f.seq, timeout=0.1) is None


def test_latest_only_drops_intermediate_frames():
    buf = FrameBuffer()
    for _ in range(5):
        buf.publish(_img())
    latest = buf.latest()
    assert latest.seq == 4  # only the newest survives


def test_concurrent_producer_and_consumers_no_deadlock():
    buf = FrameBuffer()
    seen = []

    def consumer():
        last = -1
        for _ in range(3):
            f = buf.await_latest(last, timeout=1.0)
            if f:
                last = f.seq
                seen.append(f.seq)

    threads = [threading.Thread(target=consumer) for _ in range(3)]
    for t in threads:
        t.start()
    for _ in range(10):
        buf.publish(_img())
        time.sleep(0.01)
    for t in threads:
        t.join(timeout=2.0)
    buf.close()
    assert seen  # consumers observed frames without hanging
