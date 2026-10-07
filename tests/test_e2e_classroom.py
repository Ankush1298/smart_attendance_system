"""End-to-end simulated classroom (real MySQL, virtual clock, scripted cameras/people)."""
from datetime import timedelta

import pytest

from tests.sim import World, at, run

ELIGIBLE = [f"S{i:02d}" for i in range(1, 11)]          # S01..S10 enrolled in SEC-A
EXTRA = ["S11", "S12"]                                  # S11 -> other section, S12 -> no section


@pytest.fixture()
def classroom(db, store):
    w = World(db, store)
    w.add_people([("T-A", "Teacher A"), ("T-B", "Teacher B")], ELIGIBLE + EXTRA)
    cam = w.add_room("R101")
    w.make_section("SEC-A", ELIGIBLE)
    w.make_section("SEC-B", ["S11"])
    w.add_class("R101", "T-A")
    return w, cam


def test_full_lecture_10_00_to_10_50(classroom):
    w, cam = classroom
    logic = w.logic()
    sid = "SES_R101_%d_2026-01-05" % w.store.timetable_rows()[0]["id"]

    def scene(t):
        m = t.hour * 60 + t.minute + t.second / 60 - 600           # minutes after 10:00
        seen = set()
        if m >= 1:                                                    # most students arrive at 10:01
            seen |= {f"S{i:02d}" for i in (1, 2, 3, 4, 5, 10)}
        if m >= 20: seen.add("S06")                                   # late arrival
        if 1 <= m < 25: seen.add("S07")                               # leaves at 10:25
        if 5 <= m < 12: seen.add("S08")                               # only 7 minutes inside the window
        seen |= {"S11", "S12"} if m >= 1 else set()                   # strangers to this class
        w.rec.set(cam, seen | ({"T-B"} if 1.5 <= m < 4 else set()) |       # another teacher walks in first
                  ({"T-A"} if (2 <= m < 28 or m >= 35) else set()))   # teacher leaves 10:28-10:35
        w.cams.set_online(cam, not (15 <= m < 22))                    # camera fails 10:15-10:22

    run(logic, at(10, 0), at(10, 52), hook=scene)

    sessions = w.sessions()
    assert len(sessions) == 1 and sessions[0]["session_id"] == sid
    s = sessions[0]
    assert (s["room_id"], s["teacher_id"], s["section_id"], s["state"], s["status"]) == ("R101", "T-A", "SEC-A", "COMPLETED", "completed")
    assert s["finalized_at"] is not None

    # state machine: only legal transitions, in this exact order, all logged
    assert w.states(sid) == ["WAITING_TEACHER", "ACTIVE", "CAMERA_LOST", "RECOVERED", "ACTIVE",
                             "TEACHER_ABSENT", "RESUMED", "ACTIVE", "COMPLETED"]

    # teacher authorization: T-B never authorizes, T-A does at 10:02
    ev = w.store.list_events(sid)
    assert [e for e in ev if e["event_type"] == "unauthorized_teacher_seen" and "T-B" in e["detail"]]
    auth = [e for e in ev if e["event_type"] == "teacher_authorized"]
    assert len(auth) == 1 and auth[0]["occurred_at"].minute == 2 and "T-A" in auth[0]["detail"]

    # camera loss recorded as unmeasurable, exactly 10:14:30 -> 10:21:30
    holes = w.store.load_holes(sid)
    assert len(holes) == 1
    assert (holes[0]["started_at"].hour, holes[0]["started_at"].minute, holes[0]["started_at"].second) == (10, 14, 30)
    assert holes[0]["ended_at"] - holes[0]["started_at"] == timedelta(minutes=7)

    # student eligibility: 10 enrolled + 1 teacher row; S11/S12/T-B have nothing
    att = w.att(sid)
    assert set(att) == set(ELIGIBLE) | {"T-A"}
    assert not ({"S11", "S12", "T-B"} & set(att))
    assert not w.store._q("SELECT 1 x FROM recognition_events WHERE session_id=%s AND roll_no IN ('S11','S12','T-B')", (sid,))
    first_s01 = w.store._q("SELECT MIN(detected_at) t FROM recognition_events WHERE session_id=%s AND roll_no='S01'", (sid,))[0]["t"]
    assert (first_s01.minute, first_s01.second) == (2, 0)                  # nothing recorded before authorization

    # counted minutes / percentages (measurable = 40 min window - 7 min camera loss = 33)
    sm = w.summary(sid)
    for roll in ("S01", "S02", "S03", "S04", "S05", "S10"):
        assert sm[roll]["measurable_minutes"] == 33.0 and sm[roll]["present_minutes"] == 33.0
        assert att[roll]["status"] == "present" and sm[roll]["percentage"] == 100.0
    assert sm["S06"]["present_minutes"] == 23.5 and att["S06"]["status"] == "warning"       # 71.2 %
    assert sm["S07"]["present_minutes"] == 12.5 and att["S07"]["status"] == "absent"
    assert sm["S08"]["present_minutes"] == 6.5 and att["S08"]["status"] == "absent"
    assert sm["S09"]["present_minutes"] == 0.0 and att["S09"]["status"] == "absent"
    assert all(sm[r]["measurable_minutes"] == 33.0 for r in ELIGIBLE)       # camera loss never penalised anyone

    # teacher: counted only 10:05-10:45, 7-minute absence is below the 20-minute flag
    t = w.store.teacher_summary(sid)
    assert t["present_minutes"] == 26.0 and t["absent_minutes"] == 7.0 and t["longest_absence_minutes"] == 7.0
    assert t["status"] == "present" and t["absence_over_20m"] == 0
    assert (t["counted_start"], t["counted_end"]) == ("2026-01-05T10:05:00", "2026-01-05T10:45:00")
    assert w.store.teacher_flags() == []
    assert att["T-A"]["status"] == "present"

    # idempotency after completion: more cycles change nothing
    before = (w.store._q("SELECT COUNT(*) n FROM attendance_log")[0]["n"], len(w.store.list_events(sid)))
    run(logic, at(10, 52), at(11, 30), step_s=60)
    assert before == (w.store._q("SELECT COUNT(*) n FROM attendance_log")[0]["n"], len(w.store.list_events(sid)))
    assert len(w.sessions()) == 1


def test_nothing_is_counted_in_the_excluded_margins(classroom):
    """Everyone stays all lecture; presence = exactly the middle 40 minutes, never more."""
    w, cam = classroom
    logic = w.logic()
    w.rec.set(cam, set(ELIGIBLE) | {"T-A"})
    run(logic, at(10, 0), at(10, 52))
    sid = w.sessions()[0]["session_id"]
    sm = w.summary(sid)
    assert all(sm[r]["present_minutes"] == 40.0 and sm[r]["measurable_minutes"] == 40.0 for r in ELIGIBLE)
    t = w.store.teacher_summary(sid)
    assert t["present_minutes"] == 40.0 and t["absent_minutes"] == 0.0
    rec = w.store.load_recognitions(sid)
    assert min(r["detected_at"] for r in rec).minute == 0                    # events are stored (even in margins)...
    assert max(sm[r]["present_minutes"] for r in ELIGIBLE) <= 40.0           # ...but never counted beyond the window
