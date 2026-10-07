"""Timezone-aware clock helpers.

Timetable times are local wall-clock times in ``APP_TIMEZONE`` (an IANA name such as
``Asia/Kolkata``); when unset the machine's local zone is used. Everything stored in the
new tables is UTC (``DATETIME``), converted with :func:`to_utc_naive`.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional

try:  # zoneinfo needs tzdata on Windows; fall back to the system zone if absent.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def app_tz() -> tzinfo:
    name = os.getenv("APP_TIMEZONE", "").strip()
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def tz_info() -> dict:
    """What the UI needs to show times in the server's zone (timetable times are in this zone)."""
    tz = app_tz()
    now = datetime.now(tz)
    return {"timezone": getattr(tz, "key", None), "utc_offset_minutes": int(now.utcoffset().total_seconds() // 60)}


def now_local() -> datetime:
    return datetime.now(app_tz())


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=app_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_utc_naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(app_tz())


_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?::\d{2})?\s*([AaPp][Mm])?\s*$")


def parse_hhmm(text: str) -> Optional[tuple[int, int]]:
    """Parse '9:05', '09:05', '09:05:00', '9.05', '1:30 PM'. Returns (h, m) or None."""
    m = _TIME_RE.match(str(text or ""))
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap.lower() == "pm" else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def at_time(day: datetime, hhmm: tuple[int, int]) -> datetime:
    return day.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Naive-UTC datetime (as stored) -> ISO-8601 with 'Z' for API consumers."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.replace(microsecond=0).isoformat() + "Z"


__all__ = ["tz_info", "utcnow_naive", "app_tz", "now_local", "to_utc_naive", "from_utc_naive", "parse_hhmm", "at_time", "iso_utc", "timedelta"]
