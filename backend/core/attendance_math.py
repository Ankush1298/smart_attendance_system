"""Pure attendance arithmetic (no I/O): counted window, dwell time, teacher absence runs.

All datetimes must be comparable with each other (all aware or all naive).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

Interval = Tuple[datetime, datetime]


def counted_window(start: datetime, end: datetime, start_margin_min: float, end_margin_min: float) -> Interval:
    """[start+start_margin, end-end_margin]; collapses to an empty window if margins overlap."""
    cs = start + timedelta(minutes=start_margin_min)
    ce = end - timedelta(minutes=end_margin_min)
    if ce < cs:
        mid = start + (end - start) / 2
        return mid, mid
    return cs, ce


def merge(intervals: Iterable[Interval]) -> List[Interval]:
    out: List[List[datetime]] = []
    for a, b in sorted((i for i in intervals if i[1] > i[0])):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def clamp(intervals: Iterable[Interval], window: Interval) -> List[Interval]:
    lo, hi = window
    return [(max(a, lo), min(b, hi)) for a, b in intervals if min(b, hi) > max(a, lo)]


def subtract(intervals: Sequence[Interval], holes: Sequence[Interval]) -> List[Interval]:
    out = list(merge(intervals))
    for ha, hb in merge(holes):
        nxt: List[Interval] = []
        for a, b in out:
            if hb <= a or ha >= b:
                nxt.append((a, b))
                continue
            if ha > a:
                nxt.append((a, ha))
            if hb < b:
                nxt.append((hb, b))
        out = nxt
    return out


def minutes(intervals: Iterable[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in intervals) / 60.0


def detection_intervals(points: Iterable[datetime], half_window: timedelta,
                        bridge: Optional[timedelta] = None,
                        holes: Sequence[Interval] = ()) -> List[Interval]:
    """Turn detection timestamps into presence intervals.

    Each detection covers +/- half_window around itself (so detections one heartbeat
    apart join up). Two consecutive detections further apart than that are *not* joined
    unless ``bridge`` is given and the *measurable* time between them (gap minus camera
    holes) is <= bridge -- used for the teacher, where one or two missed recognitions must
    not mean "left the room".
    """
    pts = sorted(set(points))
    ivs: List[Interval] = [(p - half_window, p + half_window) for p in pts]
    if bridge is not None:
        for p, q in zip(pts, pts[1:]):
            gap = (p + half_window, q - half_window)
            if gap[1] <= gap[0]:
                continue
            measurable = minutes(subtract([gap], holes))
            if measurable * 60.0 <= bridge.total_seconds():
                ivs.append(gap)
    return merge(ivs)


@dataclass
class Presence:
    window: Interval
    window_minutes: float
    measurable_minutes: float
    present_minutes: float
    percentage: Optional[float]            # None when nothing was measurable
    present_intervals: List[Interval] = field(default_factory=list)
    absence_runs: List[Tuple[datetime, datetime, float]] = field(default_factory=list)  # (start, end, measurable minutes)
    longest_absence_minutes: float = 0.0
    unmeasurable_minutes: float = 0.0


def compute_presence(points: Iterable[datetime], window: Interval, holes: Sequence[Interval] = (),
                     half_window_sec: float = 30.0, bridge_min: Optional[float] = None) -> Presence:
    """Dwell-time presence inside ``window`` with camera-loss ``holes`` carved out.

    present  = union(detection intervals) clamped to window, minus holes
    measurable = window minus holes
    Absence runs are stretches with no presence; a hole neither adds to nor breaks a run
    (the camera was blind, so the person's whereabouts are unknown, not absent).
    """
    lo, hi = window
    win_min = max(0.0, (hi - lo).total_seconds() / 60.0)
    holes_in = merge(clamp(holes, window))
    measurable = subtract([(lo, hi)], holes_in) if hi > lo else []
    bridge = timedelta(minutes=bridge_min) if bridge_min else None
    ivs = detection_intervals(points, timedelta(seconds=half_window_sec), bridge, holes_in)
    present = subtract(clamp(ivs, window), holes_in)
    present_min, meas_min = minutes(present), minutes(measurable)

    # Build the timeline of ABSENT stretches (measurable & not present) to find runs.
    absent = subtract(measurable, present)
    runs: List[Tuple[datetime, datetime, float]] = []
    cur: Optional[List] = None  # [start, end, accumulated minutes]
    events = sorted([(a, b, "A") for a, b in absent] + [(a, b, "P") for a, b in present])
    for a, b, kind in events:
        if kind == "P":
            if cur:
                runs.append((cur[0], cur[1], cur[2]))
                cur = None
        else:
            if cur is None:
                cur = [a, b, (b - a).total_seconds() / 60.0]
            else:
                cur[1] = b
                cur[2] += (b - a).total_seconds() / 60.0
    if cur:
        runs.append((cur[0], cur[1], cur[2]))
    longest = max((r[2] for r in runs), default=0.0)
    pct = round(min(100.0, present_min / meas_min * 100.0), 1) if meas_min > 0 else None
    return Presence(window=window, window_minutes=win_min, measurable_minutes=meas_min,
                    present_minutes=present_min, percentage=pct, present_intervals=present,
                    absence_runs=runs, longest_absence_minutes=longest,
                    unmeasurable_minutes=max(0.0, win_min - meas_min))
