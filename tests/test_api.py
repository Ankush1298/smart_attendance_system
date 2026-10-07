"""API tests: auth, validation, status codes, error shape, secrets, cameras, timetable draft flow."""
import io
import os
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

os.environ["CAMERA_ALLOW_FILE_SOURCES"] = "1"

from backend.api import auth
from backend.api.app import create_app
from backend.core.camera_manager import CameraManager
from backend.runtime import Runtime
from backend.core.session_logic import SessionLogic
from tests.sim import FakeCameras, FakeRecognizer, make_user


class FakeEngine:
    ready = True
    load_error = None


@pytest.fixture()
def client(db, store, tmp_path):
    def factory():
        cams = CameraManager()
        logic = SessionLogic(db, None, recognizer=FakeRecognizer(), cameras=FakeCameras(), store=store)
        logic.running = True
        rt = Runtime(db, store, cams, logic, FakeEngine(), time.time())
        rt.stop = lambda: cams.stop_all()
        return rt
    app = create_app(factory)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"id": "ADMIN", "password": "test-admin-pass-123"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = "Bearer " + r.json()["token"]
        c.store, c.db, c.tmp = store, db, tmp_path
        yield c


def anon(client):
    return TestClient(client.app, raise_server_exceptions=False)


def video(tmp_path):
    p = tmp_path / "cam.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 30, (160, 120))
    for i in range(60):
        img = np.full((120, 160, 3), (40, 90, 160), np.uint8)
        cv2.rectangle(img, (5 + i, 30), (45 + i, 90), (255, 255, 255), -1)
        w.write(img)
    w.release()
    return p


def test_health_is_public_and_minimal():
    from backend.api.app import create_app as mk
    r = TestClient(mk(lambda: None)).get("/api/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "service", "version", "time"}


def test_every_data_endpoint_requires_auth(client):
    c = anon(client)
    for method, path in [("get", "/api/dashboard"), ("get", "/api/users"), ("get", "/api/attendance"), ("get", "/api/cameras"),
                         ("get", "/api/readiness"), ("get", "/api/timetable"), ("get", "/api/settings"), ("post", "/api/cameras/test"),
                         ("get", "/api/reports/attendance.csv"), ("get", "/api/cameras/1/snapshot"), ("put", "/api/settings")]:
        r = getattr(c, method)(path)
        assert r.status_code == 401, (path, r.status_code)
        assert r.json()["error"]["code"] == "unauthorized"
    assert c.get("/api/dashboard", headers={"Authorization": "Bearer garbage"}).status_code == 401
    expired = auth.issue("ADMIN", "x", "superadmin", ttl=-5)["token"]
    assert c.get("/api/dashboard", headers={"Authorization": "Bearer " + expired}).status_code == 401
    tampered = client.headers["Authorization"][:-3] + "AAA"
    assert c.get("/api/dashboard", headers={"Authorization": tampered}).status_code == 401


def test_login_wrong_password_and_throttle(client):
    c = anon(client)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"id": "ADMIN", "password": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"id": "ADMIN", "password": "test-admin-pass-123"})
    assert r.status_code == 429 and "Try again" in r.json()["error"]["message"]      # locked out even with the right password
    assert anon(client).post("/api/auth/login", json={"id": "", "password": ""}).status_code == 422


def test_placeholder_admin_password_disables_login(db, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "change_me")
    assert db.verify_credential("ADMIN", "change_me", "admin")["valid"] is False


def test_error_shape_and_status_codes(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/api/rooms", json={"room_id": "", "room_name": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.post("/api/rooms", json={"room_id": "R1", "room_name": "Room 1"}).status_code == 201
    assert client.post("/api/rooms", json={"room_id": "R1", "room_name": "dup"}).status_code == 409
    assert client.get("/api/sessions/NOPE").status_code == 404
    assert client.get("/api/attendance?limit=999999").status_code == 422
    assert client.get("/api/attendance?date=not-a-date").status_code == 422
    assert client.delete("/api/rooms/NOPE").status_code == 200                         # idempotent delete


def test_users_never_expose_biometrics_or_passwords(client):
    make_user(client.db, "S1", "Stud", "student", 3)
    client.db.set_authorized_credential("S1", "secret-pw-1", "student", "Stud")
    r = client.get("/api/users")
    body = r.text.lower()
    assert r.status_code == 200 and r.json()["count"] == 1
    assert "embedding" not in body.replace("embedding_model", "") and "secret-pw-1" not in body and "scrypt" not in body
    assert client.patch("/api/users/S1", json={"name": "New Name"}).status_code == 200
    assert client.patch("/api/users/NOPE", json={"name": "x"}).status_code == 404


def test_cameras_crud_validation_test_preview_never_create_attendance(client):
    client.post("/api/rooms", json={"room_id": "R101", "room_name": "Room 101"})
    v = video(client.tmp)
    assert client.post("/api/cameras", json={"name": "bad", "camera_type": "rtsp", "source": "ftp://x", "room_id": "R101"}).status_code == 422
    assert client.post("/api/cameras", json={"name": "bad", "camera_type": "laser", "source": "1"}).status_code == 422
    assert client.post("/api/cameras", json={"name": "bad", "camera_type": "usb", "source": "abc"}).status_code == 422
    r = client.post("/api/cameras", json={"name": "Cam R101", "camera_type": "file", "source": str(v), "room_id": "R101"})
    assert r.status_code == 201
    cid = r.json()["camera_id"]
    assert client.post("/api/cameras", json={"name": "Cam R101", "camera_type": "usb", "source": "1"}).status_code == 409
    lst = client.get("/api/cameras").json()
    assert lst["count"] == 1 and "file" in lst["types"] and lst["items"][0]["room_id"] == "R101"

    t = client.post("/api/cameras/test", json={"camera_id": cid}).json()
    assert t["ok"] and t["width"] == 160
    assert client.get("/api/cameras").json()["items"][0]["status"] == "ONLINE"          # test updated health
    bad = client.post("/api/cameras/test", json={"camera_type": "rtsp", "source": "rtsp://127.0.0.1:1/x"}).json()
    assert bad["ok"] is False and bad["error"]
    assert client.post("/api/cameras/test", json={"camera_type": "usb", "source": "zzz"}).json()["stage"] == "validation"
    assert client.post("/api/cameras/test", json={}).status_code == 422
    assert client.post("/api/cameras/test", json={"camera_id": 9999}).status_code == 404

    snap = client.get(f"/api/cameras/{cid}/snapshot")
    assert snap.status_code == 200 and snap.headers["content-type"] == "image/jpeg" and snap.content[:2] == b"\xff\xd8"
    assert client.get("/api/cameras/9999/snapshot").status_code == 404
    rc = client.post(f"/api/cameras/{cid}/reconnect")
    assert rc.status_code == 200

    # none of the above created a session or any attendance
    n = lambda t: client.store._q(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
    assert (n("sessions"), n("attendance_log"), n("recognition_events"), n("session_events")) == (0, 0, 0, 0)

    assert client.put(f"/api/cameras/{cid}", json={"enabled": False}).json()["enabled"] == 0
    assert client.put(f"/api/cameras/{cid}", json={"camera_type": "none"}).json()["enabled"] == 0
    assert client.delete(f"/api/cameras/{cid}").status_code == 200
    assert client.delete(f"/api/cameras/{cid}").status_code == 404
    assert client.get("/api/camera-scan?max_index=0").status_code == 422                  # bounds enforced
    client.app.state  # noqa


def test_camera_credentials_are_hidden_in_list_but_available_for_editing(client):
    client.post("/api/rooms", json={"room_id": "R1", "room_name": "R1"})
    r = client.post("/api/cameras", json={"name": "net", "camera_type": "rtsp", "source": "rtsp://admin:hunter2@10.0.0.2/s", "room_id": "R1"})
    cid = r.json()["camera_id"]
    assert "hunter2" not in client.get("/api/cameras").text
    assert "hunter2" in client.get(f"/api/cameras/{cid}").text


def test_readiness_reports_what_is_wrong_and_blocks_automatic_attendance(client):
    rd = client.get("/api/readiness").json()
    by = {c["id"]: c for c in rd["checks"]}
    assert by["mysql"]["status"] == "READY"
    assert by["camera"]["status"] == "ERROR" and by["camera"]["fix"]
    assert by["timetable"]["status"] == "ERROR" and by["teachers"]["status"] == "ERROR"
    assert rd["automatic_attendance_allowed"] is False and {"camera", "timetable", "teachers"} <= set(rd["blocking"])
    assert rd["overall"] == "ERROR"
    assert by["face_engine"]["status"] == "READY"


def test_dashboard_uses_real_data(client):
    d = client.get("/api/dashboard").json()
    assert d["counts"] == {"classes_today": 0, "active_sessions": 0, "upcoming": 0}
    assert d["cameras"] == {"enabled": 0, "online": 0, "offline": 0, "unknown": 0}
    assert d["warnings"] and d["system"]["automatic_attendance_allowed"] is False


def test_settings_validation(client):
    s = client.get("/api/settings").json()["settings"]
    assert s["start_margin_min"] == 5.0 and s["end_margin_min"] == 5.0
    assert client.put("/api/settings", json={"start_margin_min": 2}).json()["settings"]["start_margin_min"] == 2.0
    assert client.put("/api/settings", json={"heartbeat_sec": 1}).status_code == 422
    assert client.put("/api/settings", json={"bogus": 1}).status_code == 422
    assert client.put("/api/settings", json={"start_margin_min": "abc"}).status_code == 422


def test_sections_and_timetable_draft_publish_flow(client):
    make_user(client.db, "T1", "Teacher One", "teacher", 5)
    for i in (1, 2):
        make_user(client.db, f"S{i}", f"S{i}", "student", i)
    assert client.post("/api/sections", json={"section_id": "A", "section_name": "Section A"}).status_code == 201
    r = client.put("/api/sections/A/students", json={"roll_nos": ["S1", "S2", "GHOST"]}).json()
    assert r["assigned"] == 2 and r["unknown"] == ["GHOST"]
    assert client.get("/api/sections").json()["items"][0]["students"] == 2

    csv_bytes = b"Day,StartTime,EndTime,Subject,TeacherID,RoomID\nmonday,10:00,10:50,Maths,T1,R101\nmonday,11:00,11:50,Physics,T1,R101\n"
    r = client.post("/api/timetable/preview", files={"file": ("tt.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["errors"] == 0 and len(d["rows"]) == 2
    assert client.get("/api/timetable").json()["count"] == 0                                   # preview published nothing
    assert client.post("/api/timetable/publish", json={"draft_id": d["draft_id"], "confirm": False}).status_code == 422
    assert client.get("/api/timetable").json()["count"] == 0
    pub = client.post("/api/timetable/publish", json={"draft_id": d["draft_id"], "confirm": True})
    assert pub.status_code == 200 and pub.json()["published"] == 2
    slot = client.get("/api/timetable").json()["items"][0]
    assert client.put(f"/api/timetable/{slot['id']}/section", json={"section_id": "A"}).status_code == 200
    assert client.put(f"/api/timetable/{slot['id']}/section", json={"section_id": "NOPE"}).status_code == 422
    assert client.delete("/api/sections/A").status_code == 409                                  # in use by a slot
    assert client.post("/api/timetable/publish", json={"draft_id": d["draft_id"], "confirm": True}).status_code == 409   # only once


def test_timetable_upload_hardening(client):
    f = lambda name, data: client.post("/api/timetable/preview", files={"file": (name, io.BytesIO(data), "application/octet-stream")})
    assert f("evil.exe", b"MZ").status_code == 422
    assert f("../../etc/passwd.csv", b"Day,StartTime,EndTime,Subject,TeacherID,RoomID\nmonday,10:00,10:50,M,T,R\n").status_code == 201   # name is never used as a path
    assert f("empty.csv", b"").status_code == 422
    assert f("big.csv", b"a" * (16 * 1024 * 1024)).status_code == 413
    assert f("garbage.csv", b"\x00\x01\x02not a csv at all").status_code in (201, 422)
    assert f("noheader.csv", b"hello,world\n1,2\n").status_code == 422


def test_csv_export_neutralises_formula_injection(client):
    make_user(client.db, "=cmd|' /C calc'!A0", "=HYPERLINK(\"http://evil\")", "student", 9)
    with client.db._conn() as con:
        con.execute("INSERT INTO sessions (session_id,date) VALUES ('S1','2026-01-05')")
    client.db.log_attendance("S1", "=cmd|' /C calc'!A0", "student", "present", 0.9)
    r = client.get("/api/reports/attendance.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert "'=cmd" in lines[1] and ",=cmd" not in lines[1] and "'=HYPERLINK" in lines[1]


def test_security_headers_and_frontend_served(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    idx = client.get("/")
    assert idx.status_code == 200 and "Smart Attendance" in idx.text


def test_database_outage_is_a_503_with_a_human_message(client, monkeypatch):
    import mysql.connector
    monkeypatch.setattr(client.store, "list_sessions", lambda *a, **k: (_ for _ in ()).throw(mysql.connector.errors.InterfaceError("Can not reconnect to MySQL")))
    r = client.get("/api/sessions")
    assert r.status_code == 503 and r.json()["error"]["code"] == "db_unavailable" and "reconnects automatically" in r.json()["error"]["message"]
    assert "InterfaceError" not in r.text and "reconnect to MySQL" not in r.text


def test_unhandled_error_does_not_leak_internals(client, monkeypatch):
    monkeypatch.setattr(client.store, "counts", lambda: (_ for _ in ()).throw(RuntimeError("secret internal path /etc/x")))
    r = client.get("/api/readiness")
    assert r.status_code == 200 and "secret internal path" in r.text        # readiness reports DB trouble to the admin by design
    monkeypatch.setattr(client.store, "list_sessions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internal path /etc/x")))
    r = client.get("/api/sessions")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error" and "secret" not in r.text
