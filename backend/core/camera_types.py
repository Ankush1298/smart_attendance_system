"""Camera type/source normalisation and validation (pure, no OpenCV import)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple, Union

CAMERA_TYPES = {
    "usb": "Webcam / USB camera",
    "builtin": "Built-in laptop camera",
    "droidcam": "DroidCam / phone webcam",
    "ip": "IP camera (HTTP/MJPEG)",
    "rtsp": "Network camera (RTSP)",
    "file": "Video file (testing only)",
    "none": "Disabled / not configured",
}
_INDEX_TYPES = {"usb", "builtin"}
_URL_RE = re.compile(r"^(https?|rtsp|rtmp)://([^\s/@]+@)?[^\s/:@]+(:\d{1,5})?(/\S*)?$", re.I)
Source = Union[int, str, None]


def infer_camera_type(source: Any) -> str:
    s = str(source or "").strip()
    if not s:
        return "none"
    if s.isdigit():
        return "usb"
    low = s.lower()
    if low.startswith(("rtsp://", "rtmp://")):
        return "rtsp"
    if ":4747" in low:
        return "droidcam"
    if low.startswith(("http://", "https://")) or re.match(r"^[\w.-]+:\d+", s):
        return "ip"
    return "file"


def normalize_source(camera_type: str, source: Any) -> Source:
    """OpenCV-ready source: int index, URL string or file path; None when not configured."""
    s = str(source if source is not None else "").strip()
    if not s or camera_type == "none":
        return None
    if camera_type in _INDEX_TYPES or (camera_type in ("", None) and s.isdigit()):
        return int(s) if s.isdigit() else None
    if s.isdigit():          # legacy rooms.camera_source holds bare indexes
        return int(s)
    if camera_type == "file":
        return s
    s = s.rstrip("/")
    if not re.match(r"^[a-z]+://", s, re.I):
        s = "http://" + s
    if camera_type == "droidcam" and not s.endswith(("/video", "/mjpegfeed")):
        s += "/video"
    elif camera_type == "ip" and ":8080" in s and not s.endswith(("/video", "/video.mjpg", "/mjpegfeed")):
        s += "/video"          # IP Webcam app default
    return s


def validate_spec(camera_type: str, source: Any) -> Tuple[bool, str]:
    """Static validation of what an admin typed (does not touch the device)."""
    if camera_type not in CAMERA_TYPES:
        return False, f"Unknown camera type '{camera_type}'."
    if camera_type == "none":
        return True, ""
    s = str(source if source is not None else "").strip()
    if not s:
        return False, "Camera source is required (an index such as 0, or a URL)."
    if camera_type in _INDEX_TYPES:
        if not s.isdigit() or int(s) > 63:
            return False, "USB / built-in camera source must be a device index between 0 and 63."
        return True, ""
    if camera_type == "file":
        if os.getenv("CAMERA_ALLOW_FILE_SOURCES", "0") != "1":
            return False, "Video-file sources are disabled. Set CAMERA_ALLOW_FILE_SOURCES=1 to allow them (testing only)."
        if not Path(s).is_file():
            return False, f"Video file not found: {s}"
        return True, ""
    norm = normalize_source(camera_type, s)
    if not isinstance(norm, str) or not _URL_RE.match(norm):
        return False, "Enter a valid URL such as rtsp://user:pass@192.168.1.20:554/stream or http://192.168.1.5:4747."
    if camera_type == "rtsp" and not norm.lower().startswith(("rtsp://", "rtmp://")):
        return False, "RTSP cameras need a URL starting with rtsp://"
    return True, ""


def redact_source(source: Any) -> str:
    """Hide credentials embedded in a URL before logging / returning it."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", str(source or ""))
