"""Scheduler behaviour: idempotency, eligibility, isolation, failure handling, recovery."""
import threading
from datetime import timedelta

import mysql.connector
import pytest

from tests.sim import World, at, run

STUDENTS = [f"S{i:02d}" for i in range(1, 9)]


@pytest.fixture()
def w(db, store):
    world = World(db, store)
    world.add_people([("T-A", "Teacher A"), ("T-B", "Teacher B")], STUDENTS + ["X01"])
    world.make_section("SEC-A", STUDENTS)
    return world


def sid_of(w, room="R101"):
    return [s["session_id"] for s in w.sessions() if s["room_id"] == room][0]


def test_repeated_ticks_create_exactly_one_session(w):
    cam = w.add_room("R101")
    w.add_class("R101", "T-A")
    logic = w.logic()
    for _ in range(40):
        logic.tick(now=at(10, 0, 5))
    assert len(w.sessions()) == 1
    assert len(w.store._q("SELECT 1 x FROM session_events WHERE event_type='session_created'")) == 1
    assert w.states(sid_of(w)) == ["WAITING_TEACHER"]


def test_two_scheduler_instances_do_not_duplicate_sessions(w):
    cam = w.add_room("R101")
    w.add_class("R101", "T-A")
    a, b = w.logic(), w.logic()
    errors = []

    def spin(lg):
        try:
            for i in range(15):
                lg.tick(now=at(10, 0, i))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    ts = [threading.Thread(target=spin, args=(lg,)) for lg in (a, b, a, b)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    assert len(w.sessions()) == 1


def test_no_session_after_class_ended(w):
    cam = w.add_room("R101")
    w.add_class("R101", "T-A")
    logic = w.logic()
    logic.tick(now=at(10, 50, 0))                  # exactly at the end
    logic.tick(now=at(11, 30))
    assert w.sessions() == []


def test_no_session_before_class_starts(w):
    w.add_room("R101"); w.add_class("R101", "T-A")
    w.logic().tick(now=at(9, 59, 59))
    assert w.sessions() == []


def test_room_without_enabled_camera_never_starts_then_starts_when_configured(w):
    w.add_room("R101", camera=False)
    w.add_class("R101", "T-A")
    logic = w.logic()
    run(logic, at(10, 0), at(10, 3))
    assert w.sessions() == [] and w.cams.acquired == []
    assert logic.get_live_status()[0]["note"] == "room has no enabled camera"
    # a disabled camera does not count either
    cid = w.store.create_camera("Cam off", "rtsp", "rtsp://10.9.9.9/x", "R101", enabled=False)
    logic.invalidate_timetable_cache(); logic.tick(now=at(10, 4))
    assert w.sessions() == []
    w.store.update_camera(cid, enabled=True)
    logic.invalidate_timetable_cache(); logic.tick(now=at(10, 5))
    assert len(w.sessions()) == 1


def test_wrong_room_camera_and_wrong_teacher_isolation(w):
    cam1, cam2 = w.add_room("R101"), w.add_room("R102")
    w.add_class("R101", "T-A", section="SEC-A")
    w.add_class("R102", "T-B", section=None)
    logic = w.logic()
    # R101's camera sees T-B (scheduled in R102) ; R102's camera sees T-B and an R101 student
    w.rec.set(cam1, {"T-B", "S01"})
    w.rec.set(cam2, {"T-B", "S01"})
    run(logic, at(10, 0), at(10, 10))
    s1, s2 = (w.store.get_session(sid_of(w, r)) for r in ("R101", "R102"))
    assert s1["state"] == "WAITING_TEACHER"                       # T-B cannot authorize T-A's class
    assert s2["state"] == "ACTIVE"
    assert [e for e in w.store.list_events(s1["session_id"]) if e["event_type"] == "unauthorized_teacher_seen"]
    # R102 has no section: its student attendance is disabled, S01 (enrolled elsewhere) gets nothing there
    run(logic, at(10, 10, 15), at(10, 52))
    assert "S01" not in w.att(s2["session_id"])
    assert "S01" not in w.att(s1["session_id"]) or w.att(s1["session_id"])["S01"]["role"] == "student"
    assert w.store.get_session(s2["session_id"])["state"] == "COMPLETED"


def test_student_cannot_authorize_and_unrecognised_does_not(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, {"S01", "S02", "X01"})                        # only students / unknown id
    run(logic, at(10, 0), at(10, 15))
    assert w.store.get_session(sid_of(w))["state"] == "WAITING_TEACHER"
    assert w.store.load_recognitions(sid_of(w)) == []


def test_unsectioned_class_records_no_student_attendance_by_default(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A", section=None)
    logic = w.logic()
    w.rec.set(cam, {"T-A", "S01", "S02"})
    run(logic, at(10, 0), at(10, 52))
    sid = sid_of(w)
    assert w.store.get_session(sid)["state"] == "COMPLETED"
    assert set(w.att(sid)) == {"T-A"}                             # teacher yes, no student rows


def test_unsectioned_legacy_mode_is_explicit_opt_in(w, monkeypatch):
    monkeypatch.setenv("ALLOW_UNSECTIONED_CLASSES", "1")
    cam = w.add_room("R101"); w.add_class("R101", "T-A", section=None)
    logic = w.logic()
    w.rec.set(cam, {"T-A", "S01"})
    run(logic, at(10, 0), at(10, 52))
    att = w.att(sid_of(w))
    assert att["S01"]["status"] == "present" and att["S02"]["status"] == "absent"


def test_teacher_absent_over_20_minutes_raises_flag_but_not_class_absent(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()

    def scene(t):
        m = t.minute + t.second / 60
        w.rec.set(cam, set(STUDENTS) | ({"T-A"} if (m < 12 or m >= 40) else set()))   # gone 10:12 - 10:40 (28 min)
    run(logic, at(10, 0), at(10, 52), hook=scene)
    sid = sid_of(w)
    t = w.store.teacher_summary(sid)
    assert t["status"] == "partial_absent" and t["absence_over_20m"] == 1
    assert 27.0 <= t["longest_absence_minutes"] <= 28.5
    flags = w.store.teacher_flags()
    assert len(flags) == 1 and flags[0]["flag_type"] == "TEACHER_ABSENCE_ALERT" and flags[0]["minutes"] > 20
    assert [e for e in w.store.list_events(sid) if e["event_type"] == "teacher_absence_alert"]
    # the students are NOT marked absent because the teacher left
    assert all(w.att(sid)[r]["status"] == "present" for r in STUDENTS)
    assert "TEACHER_ABSENT" in w.states(sid) and w.states(sid)[-1] == "COMPLETED"


def test_a_single_missed_recognition_is_not_a_departure(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()

    def scene(t):
        m = t.minute
        w.rec.set(cam, set(STUDENTS) | ({"T-A"} if m not in (20, 21) else set()))   # 2 missed heartbeats
    run(logic, at(10, 0), at(10, 52), hook=scene)
    sid = sid_of(w)
    t = w.store.teacher_summary(sid)
    assert t["status"] == "present" and t["longest_absence_minutes"] == 0.0 and t["present_minutes"] == 40.0
    assert "TEACHER_ABSENT" not in w.states(sid)


def test_teacher_never_arrives_session_is_suspended_after_wait_limit(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS))
    run(logic, at(10, 0), at(10, 40))
    sid = sid_of(w)
    s = w.store.get_session(sid)
    assert (s["state"], s["status"]) == ("SUSPENDED", "suspended")
    assert w.att(sid)["T-A"]["status"] == "absent"
    assert [k for k in w.att(sid) if k != "T-A"] == []            # students were never counted
    assert w.states(sid) == ["WAITING_TEACHER", "SUSPENDED"]


def test_camera_dead_the_whole_time_is_unmeasurable_not_absent(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.cams.set_online(cam, False)
    run(logic, at(10, 0), at(10, 52))
    sid = sid_of(w)
    s = w.store.get_session(sid)
    assert s["state"] == "COMPLETED"                              # never wrongly SUSPENDED for an absent teacher
    t = w.store.teacher_summary(sid)
    assert t["status"] == "unmeasurable" and t["present_minutes"] == 0.0 and t["absent_minutes"] == 0.0
    assert t["absence_over_20m"] == 0 and w.store.teacher_flags() == []
    assert len(w.store.load_holes(sid)) == 1 and w.store.load_holes(sid)[0]["ended_at"] is not None


def test_camera_dies_after_authorization_students_unmeasurable(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS) | {"T-A"})

    def scene(t):
        w.cams.set_online(cam, not (t.minute >= 4))               # dies right after authorization
    run(logic, at(10, 0), at(10, 52), hook=scene)
    sid = sid_of(w)
    assert {r["status"] for r in w.summary(sid).values()} == {"unmeasurable"}
    assert w.store.teacher_summary(sid)["status"] == "unmeasurable"
    assert "absent" not in {w.att(sid)[r]["status"] for r in STUDENTS}


def test_face_engine_not_ready_blocks_start_then_crash_is_unmeasurable_and_survivable(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    w.rec.is_ready = False
    run(logic, at(10, 0), at(10, 3))
    assert w.sessions() == []                                      # automatic attendance does not start without the engine
    assert "face engine is not ready" in logic.get_live_status()[0]["note"]
    w.rec.is_ready = True
    run(logic, at(10, 3, 15), at(10, 4))
    assert w.store.get_session(sid_of(w))["state"] == "ACTIVE"
    w.rec.fail = True                                              # model starts raising mid-class
    run(logic, at(10, 4, 15), at(10, 8))
    assert w.rec.calls > 0 and w.store.get_session(sid_of(w))["state"] == "CAMERA_LOST"   # exception contained
    w.rec.fail = False
    run(logic, at(10, 8, 15), at(10, 52))
    sid = sid_of(w)
    assert w.store.get_session(sid)["state"] == "COMPLETED"
    assert all(w.att(sid)[r]["status"] == "present" for r in STUDENTS)       # blind minutes were not penalised
    holes = w.store.load_holes(sid)
    assert (holes[0]["started_at"].minute, holes[0]["started_at"].second) == (0, 0)   # blind from the class start (10:00)
    assert "late" in holes[0]["reason"] and len(holes) == 2                    # + the later crash interval


def test_bad_timetable_rows_are_quarantined_not_fatal(w, db):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    with db._conn() as con:
        con.execute("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id) VALUES ('monday','25:99','10:50','Bad','T-A','R101')")
        con.execute("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id) VALUES ('monday','11:00','10:00','Inverted','T-A','R101')")
    logic = w.logic()
    w.rec.set(cam, {"T-A"})
    run(logic, at(10, 0), at(10, 5))
    states = {r["subject"]: r["state"] for r in logic.get_live_status()}
    assert states["Bad"] == "QUARANTINED" and states["Inverted"] == "QUARANTINED" and states["Maths"] == "ACTIVE"


def test_overlapping_slots_in_one_room_second_is_quarantined(w):
    cam = w.add_room("R101")
    w.add_class("R101", "T-A", "10:00", "10:50", "First")
    w.add_class("R101", "T-A", "10:30", "11:20", "Second")
    logic = w.logic()
    w.rec.set(cam, {"T-A"})
    run(logic, at(10, 31), at(10, 33))
    st = {r["subject"]: r for r in logic.get_live_status()}
    assert st["Second"]["state"] == "QUARANTINED" and "overlaps" in st["Second"]["note"]


def test_teacher_without_enrolled_face_cannot_start_and_class_is_not_quarantined_forever(w):
    cam = w.add_room("R101"); w.add_class("R101", "Ghost Teacher")
    logic = w.logic()
    run(logic, at(10, 0), at(10, 2))
    assert w.sessions() == []
    assert "no enrolled face" in logic.get_live_status()[0]["note"]
    from tests.sim import make_user
    make_user(w.db, "GT", "Ghost Teacher", "teacher", 77)
    logic.invalidate_timetable_cache(); w.rec.set(cam, {"GT"})
    run(logic, at(10, 3), at(10, 6))
    assert w.store.get_session(sid_of(w))["state"] == "ACTIVE"


def test_database_outage_buffers_then_recovers_without_data_loss(w, monkeypatch):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    real = w.store.add_recognitions
    down = {"on": False}

    def flaky(sid, events):
        if down["on"]:
            raise mysql.connector.errors.OperationalError(errno=2013, msg="Lost connection to MySQL server")
        return real(sid, events)
    monkeypatch.setattr(w.store, "add_recognitions", flaky)

    def scene(t):
        down["on"] = 20 <= t.minute < 30                          # database unavailable 10:20 - 10:30
    run(logic, at(10, 0), at(10, 52), hook=scene)
    sid = sid_of(w)
    assert "DB_ERROR" in w.states(sid) and w.states(sid)[-1] == "COMPLETED"
    assert w.states(sid).count("RECOVERED") >= 1
    sm = w.summary(sid)
    assert all(sm[r]["present_minutes"] == 40.0 for r in STUDENTS)            # nothing lost during the outage
    assert len(w.store.load_recognitions(sid)) == len({(r["roll_no"], r["detected_at"]) for r in w.store.load_recognitions(sid)})


def test_restart_mid_class_rehydrates_and_records_downtime_as_unmeasurable(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    first = w.logic()
    run(first, at(10, 0), at(10, 20))
    sid = sid_of(w)
    # process "crashes" (no stop); a new process starts at 10:30
    second = w.logic()
    run(second, at(10, 30), at(10, 52))
    s = w.store.get_session(sid)
    assert s["state"] == "COMPLETED" and len(w.sessions()) == 1
    holes = w.store.load_holes(sid)
    assert len(holes) == 1 and "not running" in holes[0]["reason"]
    sm = w.summary(sid)
    assert all(sm[r]["measurable_minutes"] < 40.0 and sm[r]["percentage"] == 100.0 for r in STUDENTS)   # downtime not an absence
    assert w.store.teacher_summary(sid)["status"] == "present"


def test_crash_after_class_end_is_finalized_on_next_start(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    run(w.logic(), at(10, 0), at(10, 30))                          # crashes at 10:30 and nobody restarts until 12:00
    sid = sid_of(w)
    assert w.store.get_session(sid)["finalized_at"] is None
    w.logic().tick(now=at(12, 0))
    s = w.store.get_session(sid)
    assert s["state"] == "COMPLETED" and s["finalized_at"] is not None
    assert w.att(sid)["T-A"]["status"] == "present"
    assert len(w.sessions()) == 1


def test_finalize_is_idempotent_and_never_overwrites_manual_override(w, db):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS[:4]) | {"T-A"})
    run(logic, at(10, 0), at(10, 49))
    sid = sid_of(w)
    r = db.apply_manual_override(sid, "S08", "present", "T-A", "was in the lab", is_admin=True)
    assert r["success"]
    run(logic, at(10, 49, 15), at(10, 52))
    assert w.att(sid)["S08"]["status"] == "present" and w.att(sid)["S08"]["is_override"] == 1     # S08 was never seen -> auto 'absent'
    assert w.store.finalize_session(sid, teacher={"teacher_id": "T-A", "date": "2026-01-05", "counted_start": None, "counted_end": None,
                                                  "first_seen": None, "last_seen": None, "present_minutes": 0, "absent_minutes": 0,
                                                  "longest_absence_minutes": 0, "status": "absent", "absence_over_20m": False},
                                    students=[], flags=[]) is False           # second finalize is a no-op
    assert w.store.teacher_summary(sid)["status"] == "present"


def test_configurable_margins(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    w.store.set_policy_values({"start_margin_min": 2, "end_margin_min": 3})
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    run(logic, at(10, 0), at(10, 52))
    sid = sid_of(w)
    assert w.summary(sid)["S01"]["measurable_minutes"] == 45.0               # 10:02 - 10:47
    assert w.store.teacher_summary(sid)["counted_start"] == "2026-01-05T10:02:00"


def test_multiple_rooms_run_in_parallel_without_cross_talk(db, store):
    w = World(db, store)
    rooms = [f"R{200 + i}" for i in range(6)]
    teachers = [(f"T{i}", f"Teacher {i}") for i in range(6)]
    students = {r: [f"{r}-S{j}" for j in range(5)] for r in rooms}
    w.add_people(teachers, [s for ss in students.values() for s in ss])
    cams = {}
    for i, r in enumerate(rooms):
        cams[r] = w.add_room(r)
        w.make_section(f"SEC-{r}", students[r])
        w.add_class(r, f"T{i}", section=f"SEC-{r}")
    logic = w.logic()

    def scene(t):
        for i, r in enumerate(rooms):
            neighbour_student = students[rooms[(i + 1) % 6]][0]            # belongs to another room's section
            w.rec.set(cams[r], set(students[r]) | {f"T{i}", neighbour_student})
    run(logic, at(10, 0), at(10, 52), hook=scene)
    assert len(w.sessions()) == 6
    for i, r in enumerate(rooms):
        sid = sid_of(w, r)
        att = w.att(sid)
        assert set(att) == set(students[r]) | {f"T{i}"}                    # neighbour's student seen on my camera: ignored
        assert all(att[s]["status"] == "present" for s in students[r])
        assert w.store.get_session(sid)["state"] == "COMPLETED"


def test_concurrent_ticks_from_many_threads_are_safe(db, store):
    w = World(db, store)
    w.add_people([("T-A", "Teacher A")], STUDENTS)
    cam = w.add_room("R101"); w.make_section("SEC-A", STUDENTS); w.add_class("R101", "T-A")
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    logic = w.logic()
    errors = []

    def spin(k):
        try:
            for i in range(30):
                logic.tick(now=at(10, k % 3, i * 2), wait=True)
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))
    ts = [threading.Thread(target=spin, args=(k,)) for k in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    assert len(w.sessions()) == 1
    rec = w.store.load_recognitions(sid_of(w))
    assert len(rec) == len({(r["roll_no"], r["detected_at"]) for r in rec})  # no duplicate events


def test_republishing_the_timetable_mid_class_does_not_create_a_second_session(w):
    cam = w.add_room("R101"); w.add_class("R101", "T-A")
    logic = w.logic()
    w.rec.set(cam, set(STUDENTS) | {"T-A"})
    run(logic, at(10, 0), at(10, 20))
    first = sid_of(w)
    w.add_class("R101", "T-A")                                    # same slot republished: ids are renumbered
    logic.invalidate_timetable_cache()
    run(logic, at(10, 20, 15), at(10, 52))
    assert len(w.sessions()) == 1 and w.sessions()[0]["session_id"] == first
    assert w.store.get_session(first)["state"] == "COMPLETED"
    # a *new* process after the class also reuses the finished session
    w.add_class("R101", "T-A"); logic2 = w.logic()
    run(logic2, at(10, 30), at(10, 40))
    assert len(w.sessions()) == 1
