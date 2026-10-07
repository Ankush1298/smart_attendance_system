"""Database tests against a real MySQL server."""
import pickle
import threading
from pathlib import Path

import mysql.connector
import numpy as np
import pytest

from backend.core import db as dbmod
from backend.core.db import DatabaseManager, _safe_loads
from backend.core.store import Store, StoreError

ROOT = Path(__file__).resolve().parents[1]
V3_MARK = "-- ===== Schema v3"


def table_names(db):
    with db._conn() as con:
        return {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()").fetchall()}


def test_migration_from_clean_database(db):
    names = table_names(db)
    for t in ("users", "rooms", "timetable", "sessions", "attendance_log", "cameras", "sections", "student_sections",
              "session_events", "recognition_events", "session_unmeasurable", "teacher_flags", "session_student_summary",
              "app_settings", "schema_migrations", "timetable_drafts", "timetable_archive"):
        assert t in names, t
    with db._conn() as con:
        assert [r[0] for r in con.execute("SELECT version FROM schema_migrations").fetchall()] == [3, 4, 5]
        idx = {r[0] for r in con.execute("SELECT index_name FROM information_schema.statistics WHERE table_schema=DATABASE()").fetchall()}
    assert {"uq_attendance_session_roll", "uq_sessions_timetable_date", "uq_recognition", "uq_camera_name"} <= idx


def test_startup_is_idempotent(db):
    DatabaseManager(); DatabaseManager()
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_migration_over_existing_legacy_database_preserves_everything(fresh_db_name):
    legacy_sql = (ROOT / "database" / "schema_mysql.sql").read_text().split(V3_MARK)[0]
    srv = mysql.connector.connect(host=dbmod._conn_config()["host"], port=dbmod._conn_config()["port"],
                                  user=dbmod._conn_config()["user"], password=dbmod._conn_config()["password"],
                                  database=fresh_db_name, autocommit=True)
    cur = srv.cursor()
    for st in dbmod._split_sql(legacy_sql):
        cur.execute(st)
    blob = pickle.dumps(np.arange(512, dtype=np.float32))
    cur.execute("INSERT INTO users (roll_no,name,role,embedding) VALUES ('T1','Teacher One','teacher',%s),('S1','Stud One','student',%s)", (blob, blob))
    cur.execute("INSERT INTO rooms VALUES ('R1','Room 1','0'),('R2','Room 2','rtsp://10.0.0.7/live'),('R3','Room 3','')")
    cur.execute("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id) VALUES ('monday','10:00','10:50','Math','T1','R1')")
    cur.execute("INSERT INTO sessions (session_id,timetable_id,date,status,subject) VALUES ('SES_OLD',1,'2025-12-01','completed','Math')")
    cur.execute("INSERT INTO attendance_log (session_id,roll_no,role,timestamp,status,confidence) VALUES ('SES_OLD','S1','student','2025-12-01T10:30:00','present',0.9)")
    cur.execute("INSERT INTO authorized_credentials (id_number,password,role) VALUES ('S1','plaintext-pw','student')")
    cur.close(); srv.close()

    db = DatabaseManager()                                  # migrates in place
    store = Store(db)
    assert db.get_dashboard_counts() == {"students": 1, "teachers": 1, "rooms": 3, "timetable": 1}
    assert store.get_session("SES_OLD")["state"] == "COMPLETED" and store.get_session("SES_OLD")["room_id"] == "R1"
    with db._conn() as con:
        assert con.execute("SELECT status FROM attendance_log WHERE session_id='SES_OLD'").fetchone()[0] == "present"
    cams = {c["room_id"]: c for c in store.list_cameras()}
    assert cams["R1"]["camera_type"] == "usb" and cams["R1"]["source"] == "0"          # existing camera config kept
    assert cams["R2"]["camera_type"] == "rtsp" and "R3" not in cams
    assert db.verify_credential("S1", "plaintext-pw", "student")["valid"]               # legacy plaintext password still works...
    with db._conn() as con:
        assert con.execute("SELECT password FROM authorized_credentials WHERE id_number='S1'").fetchone()[0].startswith("scrypt$")  # ...and is now hashed
    assert np.array_equal(db.get_user_by_roll("S1")["embedding"], np.arange(512, dtype=np.float32))   # face data intact


def test_unique_index_skipped_not_data_deleted_when_legacy_duplicates_exist(fresh_db_name):
    legacy_sql = (ROOT / "database" / "schema_mysql.sql").read_text().split(V3_MARK)[0]
    cfg = dbmod._conn_config()
    srv = mysql.connector.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], database=fresh_db_name, autocommit=True)
    cur = srv.cursor()
    for st in dbmod._split_sql(legacy_sql):
        cur.execute(st)
    cur.execute("INSERT INTO users (roll_no,name,role,embedding) VALUES ('X','X','student',%s)", (pickle.dumps(np.zeros(4, dtype=np.float32)),))
    cur.execute("INSERT INTO sessions (session_id,date,status) VALUES ('S','2025-01-01','active')")
    cur.execute("INSERT INTO attendance_log (session_id,roll_no,role,status) VALUES ('S','X','student','present'),('S','X','student','absent')")
    cur.close(); srv.close()
    db = DatabaseManager()
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM attendance_log").fetchone()[0] == 2           # nothing deleted
        assert not con.execute("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND index_name='uq_attendance_session_roll'").fetchone()


def test_create_session_is_idempotent_and_never_resets_state(db):
    with db._conn() as con:
        con.execute("INSERT INTO rooms VALUES ('R1','R1','')")
        con.execute("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id) VALUES ('monday','10:00','10:50','M','T','R1')")
    assert db.create_session("SES1", 1, "2026-01-05", "M", state="WAITING_TEACHER") is True
    db.update_session_status("SES1", "completed")
    assert db.create_session("SES1", 1, "2026-01-05", "M") is False
    assert db.create_session("SES_OTHER_ID", 1, "2026-01-05", "M") is False                   # (timetable_id, date) is unique too
    with db._conn() as con:
        assert con.execute("SELECT status FROM sessions WHERE session_id='SES1'").fetchone()[0] == "completed"
        assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_log_attendance_is_atomic_upsert_and_respects_override(db):
    from tests.sim import make_user
    make_user(db, "S1", "S1", "student", 1); make_user(db, "T1", "T1", "teacher", 2)
    with db._conn() as con:
        con.execute("INSERT INTO sessions (session_id,date) VALUES ('X','2026-01-05')")

    def worker():
        for _ in range(20):
            db.log_attendance("X", "S1", "student", "present", 0.9)
    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM attendance_log WHERE session_id='X'").fetchone()[0] == 1
    assert db.apply_manual_override("X", "S1", "absent", "T1", "medical", is_admin=True)["success"]
    db.log_attendance("X", "S1", "student", "present", 1.0)
    with db._conn() as con:
        row = con.execute("SELECT status,is_override FROM attendance_log WHERE session_id='X' AND roll_no='S1'").fetchone()
    assert tuple(row) == ("absent", 1)


def test_recognition_events_idempotent(db, store):
    with db._conn() as con:
        con.execute("INSERT INTO sessions (session_id,date) VALUES ('X','2026-01-05')")
    from datetime import datetime
    ev = [("S1", "student", None, datetime(2026, 1, 5, 10, 5), 0.5)]
    store.add_recognitions("X", ev); store.add_recognitions("X", ev)
    store.add_recognitions("X", [("S1", "student", None, datetime(2026, 1, 5, 10, 5), 0.9)])
    rows = store.load_recognitions("X")
    assert len(rows) == 1 and rows[0]["confidence"] == 0.9                                   # repeated insert only raises confidence


def test_foreign_keys_and_cascades(db, store):
    store.save_room("R1", "R1")
    cid = store.create_camera("c1", "rtsp", "rtsp://10.0.0.1/a", "R1")
    store.delete_room("R1")                                                                    # camera survives, room link cleared
    assert store.get_camera(cid)["room_id"] is None
    with db._conn() as con:
        con.execute("INSERT INTO sessions (session_id,date) VALUES ('Z','2026-01-05')")
        con.execute("INSERT INTO session_events (session_id,event_type,occurred_at) VALUES ('Z','x',NOW())")
        with pytest.raises(mysql.connector.IntegrityError):
            con.execute("INSERT INTO session_events (session_id,event_type,occurred_at) VALUES ('NOPE','x',NOW())")


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError):
        with db._conn() as con:
            con.execute("INSERT INTO rooms VALUES ('RB','RB','')")
            raise RuntimeError("boom")
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM rooms WHERE room_id='RB'").fetchone()[0] == 0


def test_finalize_transaction_is_all_or_nothing(db, store):
    from tests.sim import make_user
    for i, r in enumerate(("S1", "S2", "T")):
        make_user(db, r, r, "teacher" if r == "T" else "student", i)
    with db._conn() as con:
        con.execute("INSERT INTO sessions (session_id,date,state) VALUES ('F','2026-01-05','ACTIVE')")
    teacher = {"teacher_id": "T", "date": "2026-01-05", "counted_start": None, "counted_end": None, "first_seen": None, "last_seen": None,
               "present_minutes": 1, "absent_minutes": 1, "longest_absence_minutes": 0, "status": "present", "absence_over_20m": False}
    bad = [{"roll_no": "S1", "present_minutes": 1, "measurable_minutes": 1, "percentage": 100, "status": "present"},
           {"roll_no": "S2", "present_minutes": 1}]                                            # missing keys -> KeyError mid-transaction
    with pytest.raises(KeyError):
        store.finalize_session("F", teacher=teacher, students=bad, flags=[])
    assert store.get_session("F")["finalized_at"] is None and store.get_session("F")["state"] == "ACTIVE"
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM session_student_summary").fetchone()[0] == 0   # first student rolled back too


def test_embedding_decoding_blocks_code_execution(db):
    class Evil:
        def __reduce__(self):
            return (__import__("os").system, ("echo pwned",))
    with pytest.raises(pickle.UnpicklingError):
        _safe_loads(pickle.dumps(Evil()))
    good = np.random.rand(512).astype(np.float32)
    assert np.array_equal(_safe_loads(pickle.dumps(good)), good)
    assert [np.array_equal(a, b) for a, b in zip(_safe_loads(pickle.dumps([good, good])), [good, good])] == [True, True]


def test_pool_survives_many_threads_and_connection_loss(db):
    errors = []

    def hit():
        try:
            for _ in range(25):
                with db._conn() as con:
                    con.execute("SELECT 1").fetchone()
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))
    ts = [threading.Thread(target=hit) for _ in range(20)]                                    # more threads than pool size
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors
    # kill every pooled connection server-side; the next use must transparently reconnect via the retry path
    cfg = dbmod._conn_config()
    admin = mysql.connector.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], autocommit=True)
    cur = admin.cursor()
    cur.execute("SELECT id FROM information_schema.processlist WHERE db=%s AND id<>CONNECTION_ID()", (cfg["database"],))
    for (pid,) in cur.fetchall():
        cur.execute(f"KILL {int(pid)}")
    cur.close(); admin.close()
    store = Store(db)
    assert store.counts()["rooms"] == 0                                                       # query still works after server dropped the sessions


def test_policy_validation_and_persistence(store):
    p = store.set_policy_values({"start_margin_min": 3, "end_margin_min": 4})
    assert (p.start_margin_min, p.end_margin_min) == (3.0, 4.0) and store.get_policy().end_margin_min == 4.0
    with pytest.raises(StoreError):
        store.set_policy_values({"heartbeat_sec": 1})
    with pytest.raises(StoreError):
        store.set_policy_values({"nope": 1})
    with pytest.raises(StoreError):
        store.set_policy_values({"start_margin_min": -5})


def test_camera_crud_validation_and_room_sync(store, db):
    store.save_room("R9", "Room 9")
    cid = store.create_camera("front", "usb", "2", "R9")
    assert store.get_camera(cid)["source"] == "2"
    with db._conn() as con:
        assert con.execute("SELECT camera_source FROM rooms WHERE room_id='R9'").fetchone()[0] == "2"    # legacy column mirrors it
    with pytest.raises(StoreError):
        store.create_camera("front", "usb", "3", "R9")                       # duplicate name
    with pytest.raises(StoreError):
        store.create_camera("bad", "rtsp", "http://nope", "R9")              # invalid rtsp url
    with pytest.raises(StoreError):
        store.create_camera("ghost", "usb", "0", "NO_ROOM")
    store.update_camera(cid, enabled=False)
    assert store.room_cameras("R9") == []                                     # disabled => not configured (no legacy fallback)
    store.update_camera(cid, enabled=True, camera_type="rtsp", source="rtsp://10.1.1.1/s")
    assert store.room_cameras("R9")[0]["source"] == "rtsp://10.1.1.1/s"
    db.upsert_room("R9", "Room 9", "1")                                       # desktop room editor changes the camera
    assert [c["source"] for c in store.room_cameras("R9")] in (["rtsp://10.1.1.1/s"], ["1"])


def test_timetable_validate_draft_publish_archives_and_never_silently_overwrites(db, store):
    from tests.sim import make_user
    make_user(db, "T1", "Teacher One", "teacher", 5)
    store.save_room("R1", "R1")
    db.load_timetable_from_df(__import__("pandas").DataFrame([{"Day": "monday", "StartTime": "09:00", "EndTime": "09:50", "Subject": "Old", "TeacherID": "T1", "RoomID": "R1"}]))
    store.upsert_section("A", "A")
    store.set_slot_section(store.timetable_rows()[0]["id"], "A")
    rows = [{"day": "Mon", "start": "9:00", "end": "09:50", "subject": "Old", "teacher": "T1", "room": "R1"},
            {"day": "tue", "start": "10:00", "end": "10:50", "subject": "New", "teacher": "Teacher One", "room": "R-NEW"},
            {"day": "tue", "start": "10:30", "end": "11:00", "subject": "Clash", "teacher": "T1", "room": "R-NEW"},
            {"day": "funday", "start": "10:00", "end": "10:50", "subject": "X", "teacher": "T1", "room": "R1"},
            {"day": "wed", "start": "11:00", "end": "10:00", "subject": "Y", "teacher": "T1", "room": "R1"},
            {"day": "wed", "start": "12:00", "end": "12:50", "subject": "Z", "teacher": "Nobody", "room": "R1"}]
    draft = store.create_draft("tt.pdf", rows)
    assert draft["errors"] >= 3 and any("overlaps" in i["message"] for i in draft["issues"])
    assert len(store.timetable_rows()) == 1 and store.timetable_rows()[0]["subject"] == "Old"      # preview changed nothing
    with pytest.raises(StoreError):
        store.publish_draft(draft["draft_id"])                                 # errors block publishing
    good = store.create_draft("tt.pdf", rows[:2] + [rows[5]])
    assert good["errors"] == 0 and good["warnings"] >= 1
    res = store.publish_draft(good["draft_id"])
    assert res["published"] == 3 and res["archived_previous"] == 1
    t = {r["subject"]: r for r in store.timetable_rows()}
    assert t["Old"]["section_id"] == "A"                                      # unchanged slot keeps its section
    with db._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM timetable_archive").fetchone()[0] == 1
    with pytest.raises(StoreError):
        store.publish_draft(good["draft_id"])                                  # a draft publishes only once
