"""Camera access for the attendance engine, the Cameras page and the readiness check.

* One reader thread per *in-use* camera keeps the latest frame; consumers never block on I/O.
* Failures (cannot open, read errors, frozen stream, black frames, EOF) put the camera OFFLINE /
  STALE and the thread reconnects with exponential backoff. A dead camera is therefore a
  *camera* problem reported as such -- it is never turned into "teacher/student absent".
* ``test`` / ``scan`` / ``snapshot_jpeg`` never create attendance (they do not know about sessions).
* Every capture is released in a ``finally``; handles nobody uses are stopped by ``release_unused``.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from backend.core.camera_types import normalize_source, redact_source, validate_spec

log = logging.getLogger("smart_attendance.camera")

# Make FFmpeg-backed network streams fail instead of hanging forever (5 s socket timeout, TCP).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

ONLINE, OFFLINE, CONNECTING, STALE = "ONLINE", "OFFLINE", "CONNECTING", "STALE"
HealthCb = Callable[["CameraSpec", str, Optional[str], bool], None]


@dataclass(frozen=True)
class CameraSpec:
    camera_id: Optional[int]
    name: str
    camera_type: str
    source: str            # as configured by the admin
    room_id: Optional[str] = None

    @property
    def key(self) -> str:
        return f"id:{self.camera_id}" if self.camera_id is not None else f"src:{self.room_id}:{self.source}"

    @property
    def cv_source(self):
        return normalize_source(self.camera_type, self.source)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CameraSpec":
        return cls(row.get("camera_id"), row.get("name") or "camera", row.get("camera_type") or "usb",
                   str(row.get("source") or ""), row.get("room_id"))


def validate_frame(frame: Any) -> Tuple[bool, str]:
    """A usable frame is a real BGR image, big enough, and not a dead/black sensor."""
    if frame is None:
        return False, "no frame"
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        return False, "frame has an unexpected format"
    h, w = frame.shape[:2]
    if h < 48 or w < 48:
        return False, f"frame too small ({w}x{h})"
    if float(frame.mean()) < 2.0 and float(frame.std()) < 1.0:
        return False, "frame is completely black (lens covered or sensor fault)"
    return True, ""


def _open_capture(source) -> "cv2.VideoCapture":
    if isinstance(source, int) and sys.platform.startswith("win"):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(source)
    for prop, ms in (("CAP_PROP_OPEN_TIMEOUT_MSEC", 5000), ("CAP_PROP_READ_TIMEOUT_MSEC", 5000)):
        if hasattr(cv2, prop):
            try:
                cap.set(getattr(cv2, prop), ms)
            except Exception:
                pass
    return cap


def _call_with_timeout(fn: Callable[[], Any], seconds: float) -> Tuple[bool, Any]:
    """Run a possibly-blocking OpenCV call without ever blocking the caller past ``seconds``."""
    box: Dict[str, Any] = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            box["e"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return False, TimeoutError(f"timed out after {seconds:.0f}s")
    if "e" in box:
        return False, box["e"]
    return True, box.get("v")


class CameraHandle:
    """Background reader for one camera."""

    FROZEN_AFTER_SEC = 20.0
    MAX_BACKOFF = 30.0

    def __init__(self, spec: CameraSpec, on_health: Optional[HealthCb] = None, frozen_after: Optional[float] = None,
                 opener: Optional[Callable[[Any], Any]] = None):
        self.spec = spec
        self._open = opener or _open_capture
        self._on_health = on_health
        self._frozen_after = frozen_after if frozen_after is not None else self.FROZEN_AFTER_SEC
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._thread: Optional[threading.Thread] = None
        self._frame: Optional[np.ndarray] = None
        self._frame_ts = 0.0
        self._seq = 0
        self.status = CONNECTING
        self.error: Optional[str] = None
        self.last_ok: Optional[float] = None
        self.reconnects = 0
        self.keep_until = 0.0          # monotonic deadline: a preview is looking at this camera

    # -- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.spec.key}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)

    def request_reconnect(self) -> None:
        """Force the reader to drop the current capture and reopen it."""
        self._force_reconnect = True

    _force_reconnect = False

    # -- reading
    def latest(self, max_age: float = 5.0) -> Optional[np.ndarray]:
        with self._lock:
            if self._frame is None or time.monotonic() - self._frame_ts > max_age:
                return None
            return self._frame.copy()

    def get_frames(self, n: int, delay: float = 0.2, timeout: float = 8.0) -> List[np.ndarray]:
        """Up to ``n`` *distinct* fresh frames (>= ``delay`` apart). Empty list = camera unusable now."""
        frames: List[np.ndarray] = []
        deadline = time.monotonic() + timeout
        last_seq, last_t = -1, 0.0
        while len(frames) < n and time.monotonic() < deadline and not self._stop.is_set():
            with self._cond:
                if self._seq == last_seq or time.monotonic() - last_t < delay:
                    self._cond.wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
                if self._frame is not None and self._seq != last_seq and time.monotonic() - self._frame_ts < 3.0 \
                        and time.monotonic() - last_t >= delay:
                    frames.append(self._frame.copy())
                    last_seq, last_t = self._seq, time.monotonic()
        return frames

    # -- internals
    def _set_status(self, status: str, error: Optional[str] = None, reconnected: bool = False) -> None:
        changed = status != self.status or error != self.error
        if reconnected:
            self.reconnects += 1
        self.status, self.error = status, error
        if status in (OFFLINE, STALE):
            with self._cond:                 # never serve a cached frame from a dead camera
                self._frame = None
        if status == ONLINE:
            self.last_ok = time.time()
        if changed or reconnected:
            log.info("camera %s (%s) -> %s%s", self.spec.name, redact_source(self.spec.source), status, f": {error}" if error else "")
            if self._on_health:
                try:
                    self._on_health(self.spec, status, error, reconnected)
                except Exception:
                    log.exception("camera health callback failed")

    def _publish(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame, self._frame_ts = frame, time.monotonic()
            self._seq += 1
            self._cond.notify_all()

    def _run(self) -> None:
        source = self.spec.cv_source
        if source is None:
            self._set_status(OFFLINE, "camera is not configured")
            return
        is_file = self.spec.camera_type == "file"
        backoff, first, loop_restart = 1.0, True, False
        while not self._stop.is_set():
            cap = None
            try:
                if first:
                    self._set_status(CONNECTING)
                ok, cap = _call_with_timeout(lambda: self._open(source), 12.0)
                if not ok or cap is None or not cap.isOpened():
                    why = f"could not open ({cap})" if not ok else "could not open the camera (wrong index/URL, in use by another app, or no permission)"
                    self._set_status(OFFLINE, why)
                    raise _Retry()
                if isinstance(source, int):
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                fps = cap.get(cv2.CAP_PROP_FPS) or 0
                pace = 1.0 / fps if is_file and 1 <= fps <= 120 else 0.03
                fails, last_sig, same_since = 0, None, time.monotonic()
                got_frame = False
                while not self._stop.is_set():
                    if self._force_reconnect:
                        self._force_reconnect = False
                        raise _Retry("reconnect requested")
                    ret, frame = cap.read()
                    good, why = validate_frame(frame) if ret else (False, "frame read failed")
                    if not good:
                        fails += 1
                        if is_file and not ret:           # end of the file: reopen like a stream restart
                            raise _Retry("end of video")
                        if fails >= 5:
                            self._set_status(OFFLINE, why)
                            raise _Retry()
                        time.sleep(0.1)
                        continue
                    fails = 0
                    if not is_file:
                        sig = cv2.resize(frame, (16, 16)).tobytes()
                        if sig == last_sig:
                            if time.monotonic() - same_since > self._frozen_after:
                                self._set_status(STALE, "stream is frozen (identical frames)")
                                raise _Retry()
                        else:
                            last_sig, same_since = sig, time.monotonic()
                    if not got_frame:
                        got_frame = True
                        self._set_status(ONLINE, None, reconnected=not first and not loop_restart)
                        backoff = 1.0
                    elif self.status != ONLINE:
                        self._set_status(ONLINE, None)
                    self.last_ok = time.time()
                    self._publish(frame)
                    time.sleep(pace)
            except _Retry as r:
                loop_restart = str(r) == "end of video"       # a looping file is not a camera reconnect
                if loop_restart:
                    backoff = 0.2
            except Exception as exc:  # noqa: BLE001 - one bad stream must never kill the thread
                log.exception("camera %s reader error", self.spec.name)
                self._set_status(OFFLINE, f"{type(exc).__name__}: {exc}"[:200])
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        log.debug("capture release failed", exc_info=True)
            first = False
            if self._stop.wait(backoff):
                break
            backoff = min(self.MAX_BACKOFF, backoff * 2)
        log.info("camera %s reader stopped", self.spec.name)


class _Retry(Exception):
    pass


class CameraManager:
    def __init__(self, on_health: Optional[HealthCb] = None, frozen_after: Optional[float] = None,
                 opener: Optional[Callable[[Any], Any]] = None):
        self._on_health = on_health
        self._frozen_after = frozen_after
        self._opener = opener
        self._handles: Dict[str, CameraHandle] = {}
        self._lock = threading.Lock()

    def acquire(self, spec: CameraSpec) -> CameraHandle:
        with self._lock:
            h = self._handles.get(spec.key)
            if h is not None and (h.spec.source != spec.source or h.spec.camera_type != spec.camera_type):
                h.stop()                       # reconfigured: restart with the new source
                h = None
            if h is None:
                h = CameraHandle(spec, self._on_health, self._frozen_after, self._opener)
                self._handles[spec.key] = h
            h.start()
            return h

    def get(self, key: str) -> Optional[CameraHandle]:
        with self._lock:
            return self._handles.get(key)

    def release_unused(self, keep_keys: set) -> None:
        now = time.monotonic()
        with self._lock:
            drop = [k for k, h in self._handles.items() if k not in keep_keys and h.keep_until <= now]
            handles = [self._handles.pop(k) for k in drop]
        for h in handles:
            h.stop()

    def stop_all(self) -> None:
        with self._lock:
            handles, self._handles = list(self._handles.values()), {}
        for h in handles:
            h.stop()

    def statuses(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: {"status": h.status, "error": h.error, "last_ok": h.last_ok, "reconnects": h.reconnects}
                    for k, h in self._handles.items()}

    def reconnect(self, spec: CameraSpec) -> bool:
        h = self.get(spec.key)
        if h is None:
            return False
        h.request_reconnect()
        return True

    # -- one-shot operations (no attendance, no persistent state) -----------------------------
    def test(self, spec: CameraSpec, frames: int = 3, timeout: float = 12.0) -> Dict[str, Any]:
        ok_spec, err = validate_spec(spec.camera_type, spec.source)
        if not ok_spec:
            return {"ok": False, "error": err, "stage": "validation"}
        live = self.get(spec.key)
        if live is not None and live.status == ONLINE:           # already open for a session: do not open twice
            got = live.get_frames(1, timeout=3.0)
            if got:
                h, w = got[0].shape[:2]
                return {"ok": True, "width": w, "height": h, "note": "camera is currently in use by the attendance engine", "stage": "live"}
        source = spec.cv_source
        if source is None:
            return {"ok": False, "error": "Camera is not configured.", "stage": "validation"}
        t0 = time.monotonic()

        def probe():
            cap = _open_capture(source)
            try:
                if not cap.isOpened():
                    return {"ok": False, "error": "Could not open the camera. Check the index/URL, that no other application "
                                                  "is using it, and that this app has camera permission.", "stage": "open"}
                got, reason = [], "no frame"
                for _ in range(frames * 4):
                    ret, frame = cap.read()
                    good, reason = validate_frame(frame) if ret else (False, "frame read failed")
                    if good:
                        got.append(frame)
                        if len(got) >= frames:
                            break
                    else:
                        time.sleep(0.1)
                if not got:
                    return {"ok": False, "error": f"Camera opened but gave no usable frame ({reason}).", "stage": "read"}
                h, w = got[-1].shape[:2]
                return {"ok": True, "width": w, "height": h, "frames": len(got), "stage": "read"}
            finally:
                cap.release()

        ok, res = _call_with_timeout(probe, timeout)
        if not ok:
            return {"ok": False, "error": f"Camera did not respond ({res}).", "stage": "timeout"}
        res["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return res

    def scan(self, max_index: int = 6, timeout_each: float = 6.0) -> List[Dict[str, Any]]:
        """Probe local camera indexes 0..max_index-1. Indexes currently held by the engine are
        reported as in use instead of being opened a second time."""
        in_use = {h.spec.cv_source for h in list(self._handles.values()) if h.status == ONLINE}
        found = []
        for i in range(max_index):
            if i in in_use:
                found.append({"index": i, "ok": True, "in_use": True})
                continue
            r = self.test(CameraSpec(None, f"scan-{i}", "usb", str(i)), frames=1, timeout=timeout_each)
            if r.get("ok"):
                found.append({"index": i, "ok": True, "width": r["width"], "height": r["height"], "in_use": False})
        return found

    def preview_jpeg(self, spec: CameraSpec, quality: int = 75, ttl: float = 30.0, wait: float = 6.0) -> Optional[bytes]:
        """Live-preview frame. Keeps the camera open for ``ttl`` seconds so repeated polling is cheap.
        Never creates attendance (no session is involved)."""
        ok, _ = validate_spec(spec.camera_type, spec.source)
        if not ok or spec.cv_source is None:
            return None
        h = self.acquire(spec)
        h.keep_until = time.monotonic() + ttl
        frames = h.get_frames(1, timeout=wait)
        if not frames:
            return None
        return self._encode(frames[0], quality)

    def grab_frame(self, spec: CameraSpec, ttl: float = 30.0, wait: float = 6.0) -> Optional[np.ndarray]:
        """A fresh frame for the admin face-count test. Keeps the camera open for ``ttl`` s."""
        ok, _ = validate_spec(spec.camera_type, spec.source)
        if not ok or spec.cv_source is None:
            return None
        h = self.acquire(spec)
        h.keep_until = time.monotonic() + ttl
        frames = h.get_frames(1, timeout=wait)
        return frames[0] if frames else None

    @staticmethod
    def _encode(frame: np.ndarray, quality: int) -> Optional[bytes]:
        h, w = frame.shape[:2]
        if w > 960:
            frame = cv2.resize(frame, (960, int(h * 960 / w)))
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    def snapshot_jpeg(self, spec: CameraSpec, quality: int = 80) -> Optional[bytes]:
        """Latest frame as JPEG for the preview. Uses the live handle if there is one."""
        frame = None
        live = self.get(spec.key)
        if live is not None:
            frame = live.latest(max_age=5.0)
        if frame is None:
            source = spec.cv_source
            if source is None:
                return None

            def grab():
                cap = _open_capture(source)
                try:
                    for _ in range(10):
                        ret, f = cap.read()
                        if ret and validate_frame(f)[0]:
                            return f
                        time.sleep(0.1)
                    return None
                finally:
                    cap.release()
            ok, frame = _call_with_timeout(grab, 10.0)
            if not ok:
                frame = None
        return self._encode(frame, quality) if frame is not None else None
