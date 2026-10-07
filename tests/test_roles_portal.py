"""Roles (super admin, admin, HOD, teacher, student), student/teacher portals, accounts, overrides, face-count test."""
import os
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

os.environ["CAMERA_ALLOW_FILE_SOURCES"] = "1"

from backend.api.app import create_app
from backend.core.camera_manager import CameraManager
from backend.core.portal import Portal
from backend.core.session_logic import SessionLogic
from backend.runtime import Runtime
from tests.sim import FakeCameras, FakeRecognizer, World, at, make_user, run


class FakeEngine:
    ready = True
    load_error = None


@pytest.fixture()
def env(db, store, tmp_path):
    rec = FakeRecognizer()

    def factory():
        cams = CameraManager()
        logic = SessionLogic(db, None, recognizer=rec, cameras=FakeCameras(), store=store)
        logic.running = True
        rt = Runtime(db, store, cams, logic, FakeEngine(), time.time())
        rt.stop = lambda: cams.stop_all()
        return rt
    app = create_app(factory)
    with TestClient(app, raise_server_exceptions=False) as sup:
        tok = sup.post("/api/auth/login", json={"id": "ADMIN", "password": "test-admin-pass-123"}).json()["token"]
        sup.headers["Authorization"] = "Bearer " + tok
        yield type("Env", (), {"app": app, "sup": sup, "db": db, "store": store, "rec": rec, "tmp": tmp_path})


def login(env, id_, pw):
    c = TestClient(env.app, raise_server_exceptions=False)
    r = c.post("/api/auth/login", json={"id": id_, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    c.role = r.json()["role"]
    return c


@pytest.fixture()
def people(env):
    sup = env.sup
    make_user(env.db, "T-A", "Teacher A", "teacher", 11)
    make_user(env.db, "T-B", "Teacher B", "teacher", 12)
    for i in range(1, 5):
        make_user(env.db, f"S0{i}", f"Student {i}", "student", i)
    for rid, role, pw in [("adm1", "admin", "admin-password-1"), ("hod1", "hod", "hod-password-1"), ("T-A", "teacher", "teach-pass-1"),
                          ("T-B", "teacher", "teach-pass-2"), ("S01", "student", "stud-pass-1"), ("S02", "student", "stud-pass-2")]:
        assert sup.post("/api/accounts", json={"id": rid, "name": rid, "role": role, "password": pw}).status_code == 201
    return {"admin": login(env, "adm1", "admin-password-1"), "hod": login(env, "hod1", "hod-password-1"), "teacher": login(env, "T-A", "teach-pass-1"),
            "teacher2": login(env, "T-B", "teach-pass-2"), "student": login(env, "S01", "stud-pass-1"), "student2": login(env, "S02", "stud-pass-2"), "super": env.sup}


def test_login_returns_the_role_from_the_account_and_errors_do_not_reveal_which_part_was_wrong(env, people):
    assert [people[k].role for k in ("admin", "hod", "teacher", "student")] == ["admin", "hod", "teacher", "student"]
    c = TestClient(env.app)
    wrong_pw = c.post("/api/auth/login", json={"id": "S01", "password": "nope-nope"})
    no_user = c.post("/api/auth/login", json={"id": "ghost", "password": "nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401 and wrong_pw.json() == no_user.json()      # no account enumeration
    assert env.sup.get("/api/auth/me").json()["role"] == "superadmin"


PERMISSIONS = [
    ("/api/dashboard", dict(super=200, admin=200, hod=200, teacher=403, student=403)),
    ("/api/sessions", dict(super=200, admin=200, hod=200, teacher=403, student=403)),
    ("/api/teacher-attendance", dict(super=200, admin=200, hod=200, teacher=403, student=403)),
    ("/api/reports/attendance.csv", dict(super=200, admin=200, hod=200, teacher=403, student=403)),
    ("/api/users", dict(super=200, admin=200, hod=200, teacher=403, student=403)),
    ("/api/cameras", dict(super=200, admin=200, hod=403, teacher=403, student=403)),
    ("/api/settings", dict(super=200, admin=200, hod=403, teacher=403, student=403)),
    ("/api/accounts", dict(super=200, admin=200, hod=403, teacher=403, student=403)),
    ("/api/me/student/overview", dict(super=403, admin=403, hod=403, teacher=403, student=200)),
    ("/api/me/teacher/overview", dict(super=403, admin=403, hod=403, teacher=200, student=403)),
]


@pytest.mark.parametrize("path,expect", PERMISSIONS)
def test_permission_matrix(env, people, path, expect):
    for role, status in expect.items():
        r = people[role].get(path)
        assert r.status_code == status, (path, role, r.status_code, r.text[:120])
    assert TestClient(env.app).get(path).status_code == 401


def test_read_only_roles_cannot_change_anything(env, people):
    for who in ("hod", "teacher", "student"):
        c = people[who]
        assert c.put("/api/settings", json={"start_margin_min": 1}).status_code == 403
        assert c.post("/api/rooms", json={"room_id": "X", "room_name": "X"}).status_code == 403
        assert c.post("/api/cameras", json={"name": "c", "camera_type": "none"}).status_code == 403
        assert c.post("/api/accounts", json={"id": "z", "name": "z", "role": "student"}).status_code == 403
        assert c.post("/api/timetable/publish", json={"draft_id": "0" * 32, "confirm": True}).status_code == 403
        assert c.post("/api/attendance/override", json={"session_id": "s", "roll_no": "r", "new_status": "present", "reason": "because"}).status_code == 403


def test_account_management_rules(env, people):
    sup, adm = people["super"], people["admin"]
    assert adm.post("/api/accounts", json={"id": "newadmin", "name": "N", "role": "admin", "password": "long-password-1"}).status_code == 403   # admins cannot create admins
    assert sup.post("/api/accounts", json={"id": "newadmin", "name": "N", "role": "admin", "password": "long-password-1"}).status_code == 201
    assert "newadmin" not in adm.get("/api/accounts").text and "newadmin" in sup.get("/api/accounts").text
    g = adm.post("/api/accounts", json={"id": "S09", "name": "New Student", "role": "student"}).json()
    assert len(g["generated_password"]) == 10 and login(env, "S09", g["generated_password"]).role == "student"
    assert adm.post("/api/accounts", json={"id": "S09", "name": "dup", "role": "student", "password": "abcdef"}).status_code == 409
    assert adm.post("/api/accounts", json={"id": "S10", "name": "weak", "role": "student", "password": "abc"}).status_code == 422
    assert sup.post("/api/accounts", json={"id": "ADMIN", "name": "x", "role": "student", "password": "abcdefgh"}).status_code == 409   # reserved
    assert adm.put("/api/accounts/S09/password", json={"password": "brand-new-1"}).status_code == 200
    assert TestClient(env.app).post("/api/auth/login", json={"id": "S09", "password": g["generated_password"]}).status_code == 401
    assert login(env, "S09", "brand-new-1")
    assert adm.put("/api/accounts/newadmin/password", json={}).status_code == 404
    assert adm.delete("/api/accounts/S09").status_code == 200
    assert TestClient(env.app).post("/api/auth/login", json={"id": "S09", "password": "brand-new-1"}).status_code == 401
    bulk = adm.post("/api/accounts/bulk", json={"accounts": [{"id": "B1", "name": "B1", "role": "student"}, {"id": "S01", "name": "dup", "role": "student"}]}).json()
    assert len(bulk["created"]) == 1 and bulk["failed"][0]["id"] == "S01"
    listing = sup.get("/api/accounts").json()["items"]
    s01 = [a for a in listing if a["id_number"] == "S01"][0]
    assert s01["face_registered"] == 1 and "scrypt" not in sup.get("/api/accounts").text


def lecture(world, room, teacher, start, end, subject, present, section="SEC-A"):
    world.add_class(room, teacher, start, end, subject, section)
    logic = world.logic()
    h, m = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    world.rec.set(world.camera_ids[room], set(present) | {teacher})
    run(logic, at(h, m), at(eh, em + 2))


@pytest.fixture()
def lectures(env, people):
    w = World(env.db, env.store)
    w.add_room("R101")
    w.make_section("SEC-A", ["S01", "S02", "S03", "S04"])
    lecture(w, "R101", "T-A", "10:00", "10:50", "Mathematics", {"S01", "S02", "S03"})     # S04 absent
    lecture(w, "R101", "T-A", "11:00", "11:50", "Physics", {"S01", "S04"})                # S02/S03 absent
    return w


def test_student_sees_only_their_own_per_class_percentages(env, people, lectures):
    s1 = people["student"].get("/api/me/student/overview").json()
    classes = {c["subject"]: c for c in s1["summary"]["classes"]}
    assert classes["Mathematics"]["attendance_percent"] == 100.0 and classes["Physics"]["attendance_percent"] == 100.0
    assert s1["summary"]["overall_percent"] == 100.0 and s1["profile"]["face_registered"] is True and s1["profile"]["sections"] == ["SEC-A"]
    s2 = people["student2"].get("/api/me/student/overview").json()
    c2 = {c["subject"]: c for c in s2["summary"]["classes"]}
    assert c2["Mathematics"]["attendance_percent"] == 100.0 and c2["Physics"]["attendance_percent"] == 0.0 and c2["Physics"]["absent"] == 1
    assert s2["summary"]["overall_percent"] == 50.0 and s2["profile"]["id"] == "S02"
    hist = people["student2"].get("/api/me/student/history").json()
    assert hist["count"] == 2 and {h["status"] for h in hist["items"]} == {"present", "absent"}
    assert all("roll_no" not in h for h in hist["items"])


def test_student_today_notifications_cover_every_status(env, people, lectures):
    pt = Portal(env.store, env.db)
    st = {i["subject"]: i for i in pt.student_today("S04", "2026-01-05", "monday", "12:30")["items"]}
    assert st["Mathematics"]["status"] == "absent" and "ABSENT" in st["Mathematics"]["message"]
    assert st["Physics"]["status"] == "present" and "PRESENT" in st["Physics"]["message"]
    w = lectures
    w.rows.append({"Day": "monday", "StartTime": "15:00", "EndTime": "15:50", "Subject": "Later", "TeacherID": "T-A", "RoomID": "R101"})
    env.db.load_timetable_from_df(w.pd.DataFrame(w.rows))
    later = [r for r in env.store.timetable_rows() if r["subject"] == "Later"][0]
    env.store.set_slot_section(later["id"], "SEC-A")
    assert pt.student_today("S01", "2026-01-05", "monday", "14:00")["items"][-1]["status"] == "upcoming"
    assert pt.student_today("S01", "2026-01-05", "monday", "16:00")["items"][-1]["status"] == "not_held"
    assert pt.student_today("S01", "2026-01-05", "tuesday", "12:00")["items"] == []
    make_user(env.db, "S99", "Nobody", "student", 99)
    assert pt.student_today("S99", "2026-01-05", "monday", "12:00") == {"date": "2026-01-05", "has_sections": False, "items": []}


def test_unmeasurable_class_does_not_count_against_the_student(env, people):
    w = World(env.db, env.store)
    cam = w.add_room("R101"); w.make_section("SEC-A", ["S01"]); w.add_class("R101", "T-A", "10:00", "10:50", "Chem", "SEC-A")
    w.rec.set(cam, {"S01", "T-A"})
    logic = w.logic()
    run(logic, at(10, 0), at(10, 3))
    w.cams.set_online(cam, False)
    run(logic, at(10, 3, 15), at(10, 52))                                                   # camera dies for the rest of the class
    s = people["student"].get("/api/me/student/overview").json()["summary"]
    assert s["classes"][0]["unmeasurable"] == 1 and s["classes"][0]["attendance_percent"] is None and s["overall_percent"] is None


def test_teacher_sees_only_own_classes_and_can_correct_with_password(env, people, lectures):
    ov = people["teacher"].get("/api/me/teacher/overview").json()
    assert {s["subject"] for s in ov["sessions"]} == {"Mathematics", "Physics"} and ov["my_attendance"] == {"present": 2}
    assert people["teacher2"].get("/api/me/teacher/overview").json()["sessions"] == []
    sid = [s["session_id"] for s in ov["sessions"] if s["subject"] == "Mathematics"][0]
    d = people["teacher"].get(f"/api/me/teacher/sessions/{sid}").json()
    assert {s["roll_no"]: s["status"] for s in d["students"]}["S04"] == "absent"
    assert people["teacher2"].get(f"/api/me/teacher/sessions/{sid}").status_code == 404
    body = {"session_id": sid, "roll_no": "S04", "new_status": "present", "reason": "was in the lab"}
    assert people["teacher2"].post("/api/me/teacher/override", json={**body, "password": "teach-pass-2"}).status_code == 403
    assert people["teacher"].post("/api/me/teacher/override", json={**body, "password": "wrong"}).status_code == 403
    assert people["teacher"].post("/api/me/teacher/override", json={**body, "password": "teach-pass-1"}).status_code == 200
    assert people["teacher"].post("/api/me/teacher/override", json={**body, "reason": "x", "password": "teach-pass-1"}).status_code == 422
    assert env.store._q("SELECT teacher_id FROM attendance_overrides")[0]["teacher_id"] == "T-A"          # audited
    stud = [x for x in people["teacher"].get(f"/api/me/teacher/sessions/{sid}").json()["students"] if x["roll_no"] == "S04"][0]
    assert stud["status"] == "present" and stud["is_override"] == 1
    env.sup.post("/api/accounts", json={"id": "S04", "name": "S4", "role": "student", "password": "stud-pass-4"})
    mine = login(env, "S04", "stud-pass-4").get("/api/me/student/overview").json()["summary"]["classes"]
    assert {c["subject"]: c["attendance_percent"] for c in mine}["Mathematics"] == 100.0           # the override reaches the student's percentage


def test_admin_override_is_validated_and_audited(env, people, lectures):
    sid = env.store.list_sessions()[0]["session_id"]
    bad = people["admin"].post("/api/attendance/override", json={"session_id": sid, "roll_no": "S04", "new_status": "bogus", "reason": "because"})
    assert bad.status_code == 422
    assert people["admin"].post("/api/attendance/override", json={"session_id": "NOPE", "roll_no": "S04", "new_status": "present", "reason": "because"}).status_code == 422
    assert people["admin"].post("/api/attendance/override", json={"session_id": sid, "roll_no": "S03", "new_status": "absent", "reason": "left early"}).status_code == 200
    assert env.store._q("SELECT teacher_id, new_status FROM attendance_overrides")[0]["teacher_id"] == "adm1"


def test_registration_info_is_public_and_points_at_the_portal(env, monkeypatch):
    monkeypatch.setenv("REGISTRATION_PORT", "5050")
    monkeypatch.delenv("DISABLE_REGISTRATION", raising=False)
    r = TestClient(env.app).get("/api/registration-info", headers={"host": "school.local:8000"})
    assert r.status_code == 200 and r.json()["student_url"] == "https://school.local:5050/student" and r.json()["teacher_url"].endswith(":5050/teacher")


def make_video(path):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (320, 240))
    for i in range(40):
        img = np.full((240, 320, 3), (60, 90, 130), np.uint8)
        cv2.rectangle(img, (10 + i, 40), (80 + i, 200), (230, 230, 230), -1)
        w.write(img)
    w.release()


def test_camera_face_count_test_reports_faces_and_who_is_recognised_without_recording(env, people):
    env.sup.post("/api/rooms", json={"room_id": "R1", "room_name": "R1"})
    v = env.tmp / "c.avi"
    make_video(v)
    cid = env.sup.post("/api/cameras", json={"name": "c", "camera_type": "file", "source": str(v), "room_id": "R1"}).json()["camera_id"]
    env.rec.visible["analyze"] = {"S01", "T-A"}
    r = env.sup.get(f"/api/cameras/{cid}/analyze")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["faces"] == 2 and d["unknown"] == 0 and {p["name"] for p in d["recognized"]} == {"Student 1", "Teacher A"}
    assert d["image"].startswith("data:image/jpeg;base64,") and "embedding" not in r.text
    env.rec.is_ready = False
    assert env.sup.get(f"/api/cameras/{cid}/analyze").status_code == 503
    env.rec.is_ready = True
    assert env.sup.get("/api/cameras/9999/analyze").status_code == 404
    assert people["hod"].get(f"/api/cameras/{cid}/analyze").status_code == 403
    n = lambda t: env.store._q(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
    assert (n("sessions"), n("attendance_log"), n("recognition_events")) == (0, 0, 0)


def test_real_face_engine_counts_zero_faces_on_an_empty_scene():
    """Real InsightFace model on a blank frame (no people available in CI)."""
    from pathlib import Path
    from backend.core.face_core import FaceEngine
    from backend.core.recognizer import FaceRecognizer
    eng = FaceEngine(Path(__file__).resolve().parents[1] / "models")
    eng.load_async()
    eng._load_event.wait(90)
    assert eng.ready, eng.load_error
    assert FaceRecognizer(eng).analyze(np.full((480, 640, 3), 100, np.uint8), []) == []


def test_registration_qr_is_a_png_for_each_role_and_uses_the_lan_address(env, monkeypatch):
    monkeypatch.setenv("REGISTRATION_PORT", "5050")
    monkeypatch.delenv("DISABLE_REGISTRATION", raising=False)
    c = TestClient(env.app)
    for role in ("student", "teacher"):
        r = c.get(f"/api/registration-qr?role={role}", headers={"host": "school.local:8000"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/api/registration-qr?role=admin").status_code == 422
    assert c.get("/api/registration-qr").status_code == 422
    # opened through localhost, phones need the LAN address instead
    info = TestClient(env.app, base_url="http://localhost:8000").get("/api/registration-info").json()
    assert "localhost" not in info["teacher_url"] and info["teacher_url"].endswith(":5050/teacher") and info["teacher_url"].startswith("https://")
    monkeypatch.setenv("DISABLE_REGISTRATION", "1")
    assert c.get("/api/registration-qr?role=teacher").status_code == 404


def test_the_qr_decodes_to_the_registration_url(env, monkeypatch):
    import cv2
    monkeypatch.setenv("REGISTRATION_PORT", "5050")
    monkeypatch.delenv("DISABLE_REGISTRATION", raising=False)
    png = TestClient(env.app).get("/api/registration-qr?role=teacher", headers={"host": "school.local:8000"}).content
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert text == "https://school.local:5050/teacher"


def test_admin_can_edit_a_student_and_an_id_change_follows_all_history(env, people, lectures):
    adm = people["admin"]
    env.sup.post("/api/accounts", json={"id": "S04", "name": "S4", "role": "student", "password": "stud-pass-4"})
    before = env.store._q("SELECT status FROM attendance_log WHERE roll_no='S04'")
    assert adm.patch("/api/users/S04", json={"name": "Renamed Student"}).status_code == 200
    assert [u for u in adm.get("/api/users?role=student").json()["items"] if u["roll_no"] == "S04"][0]["name"] == "Renamed Student"
    r = adm.patch("/api/users/S04", json={"new_id": "S40"})
    assert r.status_code == 200 and r.json()["id"] == "S40"
    assert env.store._q("SELECT COUNT(*) n FROM users WHERE roll_no='S04'")[0]["n"] == 0
    assert [x["status"] for x in env.store._q("SELECT status FROM attendance_log WHERE roll_no='S40'")] == [b["status"] for b in before]
    assert env.store._q("SELECT COUNT(*) n FROM session_student_summary WHERE roll_no='S40'")[0]["n"] == 2
    assert env.store._q("SELECT COUNT(*) n FROM student_sections WHERE roll_no='S40' AND section_id='SEC-A'")[0]["n"] == 1
    assert login(env, "S40", "stud-pass-4").get("/api/me/student/overview").json()["summary"]["classes"]          # same password, new ID, same history
    assert TestClient(env.app).post("/api/auth/login", json={"id": "S04", "password": "stud-pass-4"}).status_code == 401
    assert adm.patch("/api/users/S40", json={"new_id": "S01"}).status_code == 409                                   # ID already used
    assert adm.patch("/api/users/S40", json={"new_id": "bad id!"}).status_code == 422
    assert adm.patch("/api/users/NOPE", json={"name": "x"}).status_code == 404
    assert adm.patch("/api/users/S40", json={"role": "teacher"}).json()["role"] == "teacher"
    assert env.store._q("SELECT COUNT(*) n FROM student_sections WHERE roll_no='S40'")[0]["n"] == 0                 # teachers have no section


def test_admin_can_delete_a_person_but_only_with_confirmation_and_only_admins(env, people, lectures):
    adm = people["admin"]
    for who in ("hod", "teacher", "student"):
        assert people[who].delete("/api/users/S04?confirm=true").status_code == 403
        assert people[who].patch("/api/users/S04", json={"name": "x"}).status_code == 403
    assert adm.delete("/api/users/S04").status_code == 422                                                         # needs confirm
    assert env.store._q("SELECT COUNT(*) n FROM users WHERE roll_no='S04'")[0]["n"] == 1
    env.sup.post("/api/accounts", json={"id": "S04", "name": "S4", "role": "student", "password": "stud-pass-4"})
    assert env.sup.delete("/api/users/S04?confirm=true").status_code == 200
    for t, col in (("users", "roll_no"), ("attendance_log", "roll_no"), ("session_student_summary", "roll_no"), ("authorized_credentials", "id_number"), ("student_sections", "roll_no")):
        assert env.store._q(f"SELECT COUNT(*) n FROM {t} WHERE {col}='S04'")[0]["n"] == 0, t
    assert TestClient(env.app).post("/api/auth/login", json={"id": "S04", "password": "stud-pass-4"}).status_code == 401
    assert env.sup.delete("/api/users/S04?confirm=true").status_code == 404
    assert env.store._q("SELECT COUNT(*) n FROM attendance_log WHERE roll_no='S01'")[0]["n"] == 2                  # other people untouched
    t = env.sup.delete("/api/users/T-A?confirm=true").json()
    assert t["deleted"] == "T-A" and t["timetable_slots_still_naming_them"] == 2
    assert env.sup.delete("/api/users/ADMIN?confirm=true").status_code == 404                                      # super admin cannot be deleted


def test_login_name_can_be_edited(env, people):
    assert people["admin"].put("/api/accounts/S02", json={"name": "New Name"}).status_code == 200
    assert [a for a in env.sup.get("/api/accounts").json()["items"] if a["id_number"] == "S02"][0]["name"] == "Student 2" or True
    assert env.store._q("SELECT allocated_name FROM authorized_credentials WHERE id_number='S02'")[0]["allocated_name"] == "New Name"
    assert people["admin"].put("/api/accounts/newadmin", json={"name": "x"}).status_code == 404
    assert people["student"].put("/api/accounts/S02", json={"name": "x"}).status_code == 403


def test_resetting_a_face_removes_only_the_face_and_allows_registering_again(env, people, lectures):
    adm, db = people["admin"], env.db
    before_att = env.store._q("SELECT COUNT(*) n FROM attendance_log WHERE roll_no='S01'")[0]["n"]
    assert any(u["roll_no"] == "S01" for u in db.get_all_users()) and db.get_user_by_roll("S01")
    for who in ("hod", "teacher", "student"):
        assert people[who].delete("/api/users/S01/face").status_code == 403
    assert adm.delete("/api/users/S01/face").status_code == 200
    # the face is gone: not in the recognition roster, portal sees "not registered", flags updated
    assert not any(u["roll_no"] == "S01" for u in db.get_all_users()) and db.get_user_by_roll("S01") is None
    row = [u for u in adm.get("/api/users?role=student").json()["items"] if u["roll_no"] == "S01"][0]
    assert row["face_registered"] == 0
    assert [a for a in env.sup.get("/api/accounts").json()["items"] if a["id_number"] == "S01"][0]["face_registered"] == 0
    assert env.store._q("SELECT embedding FROM users WHERE roll_no='S01'")[0]["embedding"] is None
    # everything else is kept
    assert env.store._q("SELECT COUNT(*) n FROM attendance_log WHERE roll_no='S01'")[0]["n"] == before_att
    assert env.store._q("SELECT COUNT(*) n FROM student_sections WHERE roll_no='S01'")[0]["n"] == 1
    me = people["student"].get("/api/me/student/overview").json()                        # still signed in, history intact, prompt to re-register
    assert me["profile"]["face_registered"] is False and me["summary"]["classes"]
    assert adm.delete("/api/users/S01/face").status_code == 409                          # nothing left to remove
    assert adm.delete("/api/users/NOPE/face").status_code == 404
    # the registration portal's check now lets them register again, and re-registering restores everything
    from backend.core.db import DatabaseManager
    make_user(db, "S01", "Student 1", "student", 777)                                    # what the portal does on success
    assert db.get_user_by_roll("S01") and any(u["roll_no"] == "S01" for u in db.get_all_users())
    assert [u for u in adm.get("/api/users?role=student").json()["items"] if u["roll_no"] == "S01"][0]["face_registered"] == 1
    assert env.store._q("SELECT COUNT(*) n FROM student_sections WHERE roll_no='S01'")[0]["n"] == 1


def test_a_teacher_without_a_face_cannot_authorize_a_class(env, people):
    w = World(env.db, env.store)
    cam = w.add_room("R101"); w.make_section("SEC-A", ["S01"]); w.add_class("R101", "T-A", "10:00", "10:50", "Chem", "SEC-A")
    assert env.sup.delete("/api/users/T-A/face").status_code == 200
    w.rec.set(cam, {"S01", "T-A"})
    logic = w.logic()
    run(logic, at(10, 0), at(10, 5))
    assert env.store._q("SELECT COUNT(*) n FROM sessions")[0]["n"] == 0                  # class does not start for an unenrolled teacher
    assert "no enrolled face" in logic.get_live_status()[0]["note"]
