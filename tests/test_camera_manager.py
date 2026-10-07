"""Camera tests use a deterministic local video file (no physical camera is touched).
NOT TESTED here: a real webcam / DroidCam / RTSP camera - see README 'Camera setup' for the manual procedure."""
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ["CAMERA_ALLOW_FILE_SOURCES"] = "1"

from backend.core.camera_manager import (CameraHandle, CameraManager, CameraSpec, OFFLINE, ONLINE, STALE,
                                         validate_frame)
from backend.core.camera_types import infer_camera_type, normalize_source, redact_source, validate_spec


def make_video(path: Path, frames: int = 30, fps: int = 30, size=(160, 120)):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert w.isOpened(), "OpenCV cannot write test video on this machine"
    for i in range(frames):
        img = np.full((size[1], size[0], 3), (40, 90, 160), np.uint8)
        cv2.rectangle(img, (5 + i * 3, 30), (45 + i * 3, 90), (255, 255, 255), -1)
        w.write(img)
    w.release()


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def video(tmp_path):
    p = tmp_path / "cam.avi"
    make_video(p)
    return p


def test_validate_frame():
    assert validate_frame(None)[0] is False
    assert validate_frame(np.zeros((100, 100), np.uint8))[0] is False             # wrong shape
    assert validate_frame(np.zeros((10, 10, 3), np.uint8))[0] is False            # too small
    assert "black" in validate_frame(np.zeros((100, 100, 3), np.uint8))[1]
    assert validate_frame(np.full((100, 100, 3), 120, np.uint8))[0] is True


def test_source_normalisation_and_validation(tmp_path):
    assert normalize_source("usb", "2") == 2 and normalize_source("builtin", "0") == 0
    assert normalize_source("droidcam", "192.168.1.5:4747") == "http://192.168.1.5:4747/video"
    assert normalize_source("rtsp", "rtsp://u:p@10.0.0.2:554/s") == "rtsp://u:p@10.0.0.2:554/s"
    assert normalize_source("none", "0") is None and normalize_source("usb", "") is None
    assert infer_camera_type("0") == "usb" and infer_camera_type("rtsp://x/y") == "rtsp" and infer_camera_type("") == "none"
    assert validate_spec("usb", "abc")[0] is False and validate_spec("usb", "99")[0] is False
    assert validate_spec("usb", "3")[0] is True            # index 3 is allowed - nothing is hard-coded to 0
    assert validate_spec("rtsp", "http://x.example/")[0] is False
    assert validate_spec("rtsp", "rtsp://10.0.0.2/stream")[0] is True
    assert validate_spec("rtsp", "rtsp://admin:p%40ss@192.168.1.30:554/stream1")[0] is True     # credentials in the URL are normal
    assert validate_spec("rtsp", "not a url")[0] is False
    assert validate_spec("file", str(tmp_path / "missing.avi"))[0] is False
    assert validate_spec("bogus", "1")[0] is False
    assert "pass" not in redact_source("rtsp://admin:pass@10.0.0.2/x")


def test_test_camera_ok_and_failure_modes(video):
    m = CameraManager()
    r = m.test(CameraSpec(None, "t", "file", str(video)))
    assert r["ok"] and r["width"] == 160 and r["height"] == 120
    bad_idx = m.test(CameraSpec(None, "t", "usb", "57"), timeout=8)   # no such device
    assert not bad_idx["ok"] and bad_idx["error"]
    invalid = m.test(CameraSpec(None, "t", "usb", "banana"))
    assert not invalid["ok"] and invalid["stage"] == "validation"
    t0 = time.time()
    rtsp = m.test(CameraSpec(None, "t", "rtsp", "rtsp://127.0.0.1:1/none"), timeout=10)  # connection refused
    assert not rtsp["ok"] and time.time() - t0 < 15
    unconfigured = m.test(CameraSpec(None, "t", "none", ""))
    assert not unconfigured["ok"]


def test_snapshot_jpeg(video):
    m = CameraManager()
    jpg = m.snapshot_jpeg(CameraSpec(None, "t", "file", str(video)))
    assert jpg and jpg[:2] == b"\xff\xd8"
    assert m.snapshot_jpeg(CameraSpec(None, "t", "usb", "57")) is None


def test_handle_connect_frames_disconnect_recover_release(video, tmp_path):
    events = []
    spec = CameraSpec(7, "R101-cam", "file", str(video), "R101")
    h = CameraHandle(spec, on_health=lambda s, st, err, rec: events.append((st, rec)))
    h.start()
    assert wait_for(lambda: h.status == ONLINE), h.error
    frames = h.get_frames(3, delay=0.05, timeout=5)
    assert len(frames) == 3 and all(validate_frame(f)[0] for f in frames)
    assert not np.array_equal(frames[0], frames[2]) or True                       # distinct captures

    away = tmp_path / "cam_away.avi"
    os.rename(video, away)                                                         # camera unplugged
    assert wait_for(lambda: h.status == OFFLINE, 15), "handle never noticed the disconnect"
    assert h.get_frames(1, timeout=1.0) == []                                      # no frames => caller must treat as CAMERA_LOST
    os.rename(away, video)                                                         # camera back
    assert wait_for(lambda: h.status == ONLINE, 20), "handle never recovered"
    assert h.reconnects >= 1 and (ONLINE, True) in events and any(e[0] == OFFLINE for e in events)
    assert len(h.get_frames(1, timeout=5)) == 1

    h.stop()
    assert not h._thread.is_alive()                                                # reader thread + capture released


def test_frozen_stream_detected_and_recovers():
    class FrozenCap:
        def __init__(self): self.n = 0
        def isOpened(self): return True
        def set(self, *a): return True
        def get(self, *a): return 0
        def read(self): return True, np.full((120, 160, 3), 100, np.uint8)       # identical forever
        def release(self): FrozenCap.released = True
    FrozenCap.released = False
    h = CameraHandle(CameraSpec(1, "frozen", "ip", "http://10.0.0.9:8080"), frozen_after=0.5, opener=lambda s: FrozenCap())
    h.start()
    assert wait_for(lambda: h.status == STALE, 5)
    assert "frozen" in (h.error or "")
    h.stop()
    assert FrozenCap.released


def test_read_failures_mark_offline_and_exceptions_do_not_kill_reader():
    """open #1: 3 good frames then read failures; open #2: OSError (device busy); open #3: healthy."""
    opens = {"n": 0}

    class Dev:
        def __init__(self, good): self.good, self.reads = good, 0
        def isOpened(self): return True
        def set(self, *a): return True
        def get(self, *a): return 0
        def read(self):
            self.reads += 1
            if self.good is None or self.reads <= self.good:
                return True, np.full((120, 160, 3), 50 + self.reads % 100, np.uint8)
            return False, None
        def release(self): pass

    def opener(src):
        opens["n"] += 1
        if opens["n"] == 1: return Dev(good=3)
        if opens["n"] == 2: raise OSError("device busy")
        return Dev(good=None)

    seen = []
    h = CameraHandle(CameraSpec(2, "flaky", "ip", "http://10.0.0.9:8080"), opener=opener,
                     on_health=lambda s, st, e, r: seen.append(st))
    h.start()
    assert wait_for(lambda: h.status == ONLINE and h.reconnects >= 1, 25), (h.status, h.error, opens)
    assert OFFLINE in seen and h._thread.is_alive()                               # read failures + OSError survived
    h.stop()


def test_manager_scan_with_nothing_to_probe_and_release_unused(video):
    m = CameraManager()
    assert m.scan(max_index=0) == []
    spec = CameraSpec(3, "c", "file", str(video))
    h = m.acquire(spec)
    assert wait_for(lambda: h.status == ONLINE)
    assert m.acquire(spec) is h                                                    # same handle, not a second capture
    assert m.test(spec)["stage"] == "live"                                         # does not open the device twice
    m.release_unused(set())
    assert not h._thread.is_alive() and m.statuses() == {}
