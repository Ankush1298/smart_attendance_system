from datetime import datetime, timedelta

from backend.core.attendance_math import compute_presence, counted_window, detection_intervals, merge, subtract, minutes

T0 = datetime(2026, 1, 5, 10, 0)


def t(m, s=0):
    return T0 + timedelta(minutes=m, seconds=s)


def test_counted_window_default_margins():
    cs, ce = counted_window(T0, t(50), 5, 5)
    assert (cs, ce) == (t(5), t(45))


def test_counted_window_configurable_and_degenerate():
    assert counted_window(T0, t(50), 2, 3) == (t(2), t(47))
    cs, ce = counted_window(T0, t(8), 5, 5)  # margins swallow the whole lecture
    assert cs == ce


def test_merge_subtract():
    assert merge([(t(0), t(2)), (t(1), t(3)), (t(5), t(6))]) == [(t(0), t(3)), (t(5), t(6))]
    assert subtract([(t(0), t(10))], [(t(2), t(4)), (t(8), t(12))]) == [(t(0), t(2)), (t(4), t(8))]


def test_heartbeat_detections_join_up_but_far_ones_do_not():
    # 60 s heartbeat, +/-30 s each -> contiguous
    pts = [t(m) for m in range(10, 15)]
    assert minutes(detection_intervals(pts, timedelta(seconds=30))) == 5.0
    # detections at minute 10 and minute 40 must NOT be joined (old code bridged them)
    far = detection_intervals([t(10), t(40)], timedelta(seconds=30))
    assert minutes(far) == 2.0


def test_no_detection_outside_counted_window_counts():
    win = counted_window(T0, t(50), 5, 5)
    p = compute_presence([t(1), t(2), t(3), t(48), t(49)], win)  # all in the excluded margins
    assert p.present_minutes == 0.0
    # a detection at the very edge only counts its in-window half
    p = compute_presence([t(5)], win)
    assert p.present_minutes == 0.5


def test_full_presence_is_100_percent():
    win = counted_window(T0, t(50), 5, 5)
    p = compute_presence([t(m) for m in range(5, 46)], win)
    assert p.measurable_minutes == 40.0
    assert p.present_minutes == 40.0 and p.percentage == 100.0


def test_late_arrival_and_early_departure_dwell():
    win = counted_window(T0, t(50), 5, 5)
    # 1-minute late (arrives 06:00 of a 05:00 start), leaves 1 minute early
    p = compute_presence([t(m) for m in range(6, 45)], win)
    assert p.present_minutes == 39.0  # covered 5:30-44:30; no whole 5-minute block lost


def test_camera_hole_is_not_absence():
    win = counted_window(T0, t(50), 5, 5)
    pts = [t(m) for m in range(5, 20)] + [t(m) for m in range(30, 46)]
    hole = [(t(19, 30), t(29, 30))]  # camera blind for 10 minutes (between the two coverages)
    with_hole = compute_presence(pts, win, holes=hole)
    without = compute_presence(pts, win)
    assert with_hole.unmeasurable_minutes == 10.0
    assert with_hole.measurable_minutes == 30.0
    assert with_hole.percentage == 100.0           # student not penalised
    assert without.percentage < 80.0               # same data without the hole would be


def test_teacher_bridge_and_absence_run_with_flag_threshold():
    win = counted_window(T0, t(50), 5, 5)
    # present 5-15, gone 15-37 (22 min), back 37-45
    pts = [t(m) for m in range(5, 15)] + [t(m) for m in range(37, 46)]
    p = compute_presence(pts, win, bridge_min=3.0)
    assert 20.0 < p.longest_absence_minutes < 23.0
    # a single missed minute is bridged, not an absence
    p2 = compute_presence([t(m) for m in range(5, 46) if m != 20], win, bridge_min=3.0)
    assert p2.longest_absence_minutes == 0.0 and p2.percentage == 100.0


def test_camera_hole_inside_absence_run_is_neutral():
    win = counted_window(T0, t(50), 5, 5)
    pts = [t(m) for m in range(5, 10)]
    p = compute_presence(pts, win, holes=[(t(20), t(30))], bridge_min=3.0)
    # unrecognised time 9.5..45 minus 10 blind minutes = 25.5 measurable minutes, one run
    assert len(p.absence_runs) == 1
    assert abs(p.longest_absence_minutes - 25.5) < 0.01


def test_nothing_measurable_gives_none_percentage():
    win = counted_window(T0, t(50), 5, 5)
    p = compute_presence([], win, holes=[(t(0), t(50))])
    assert p.measurable_minutes == 0.0 and p.percentage is None
