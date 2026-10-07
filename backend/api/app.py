"""Smart Attendance REST API + admin web UI.

* All /api routes except /api/health and /api/auth/login need ``Authorization: Bearer <token>``.
* Errors are always ``{"error": {"code": str, "message": str}}``; lists are ``{"items": [...], "count": n}``.
* Importing this module has no side effects (no DB connection); the runtime is built on startup.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import secrets
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import mysql.connector
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import auth, readiness
from backend.config.settings import settings
from backend.core.camera_manager import CameraSpec
from backend.core.camera_types import CAMERA_TYPES, redact_source, validate_spec
from backend.core.clock import iso_utc, now_local, tz_info, utcnow_naive
from backend.core.policy import POLICY_KEYS
from backend.core.portal import Portal
from backend.core.store import StoreError

log = logging.getLogger("smart_attendance.api")
VERSION = "3.0.0"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_UPLOADS = {".pdf", ".xlsx", ".xls", ".csv", ".docx", ".png", ".jpg", ".jpeg"}
STORE_ERROR_STATUS = {"forbidden_role": 403, "not_found": 404, "duplicate": 409, "in_use": 409, "conflict": 409, "has_errors": 409}


# --------------------------------------------------------------------------- helpers
def jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, datetime):
        return iso_utc(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None                     # never serialise binary (face data)
    return v


def ok(data: Any = None, status: int = 200, **extra) -> JSONResponse:
    body = data if isinstance(data, dict) else {"data": data}
    return JSONResponse(jsonable({**body, **extra}), status_code=status)


def items(rows: List[Any], **extra) -> JSONResponse:
    return JSONResponse(jsonable({"items": rows, "count": len(rows), **extra}))


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def csv_safe(v: Any) -> Any:
    """Neutralise spreadsheet formula injection in exported cells."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class LoginBody(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class RoomBody(BaseModel):
    room_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w .\-/]+$")
    room_name: str = Field(min_length=1, max_length=255)


class RoomUpdate(BaseModel):
    room_name: str = Field(min_length=1, max_length=255)


class CameraBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    camera_type: str
    source: str = Field(default="", max_length=1000)
    room_id: Optional[str] = Field(default=None, max_length=100)
    enabled: bool = True

    @field_validator("camera_type")
    @classmethod
    def _t(cls, v: str) -> str:
        if v not in CAMERA_TYPES:
            raise ValueError(f"camera_type must be one of {', '.join(CAMERA_TYPES)}")
        return v


class CameraPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    camera_type: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=1000)
    room_id: Optional[str] = Field(default=None, max_length=100)
    enabled: Optional[bool] = None

    @field_validator("camera_type")
    @classmethod
    def _t(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CAMERA_TYPES:
            raise ValueError(f"camera_type must be one of {', '.join(CAMERA_TYPES)}")
        return v


class CameraTest(BaseModel):
    camera_id: Optional[int] = None
    camera_type: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=1000)


class SectionBody(BaseModel):
    section_id: str = Field(min_length=1, max_length=100, pattern=r"^[\w .\-]+$")
    section_name: str = Field(min_length=1, max_length=255)


class SectionStudents(BaseModel):
    roll_nos: List[str] = Field(max_length=2000)


class SlotSection(BaseModel):
    section_id: Optional[str] = Field(default=None, max_length=100)


class PublishBody(BaseModel):
    draft_id: str = Field(min_length=32, max_length=32)
    confirm: bool = False


class AccountBody(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w.@\-]+$")
    name: str = Field(min_length=1, max_length=255)
    role: str
    password: Optional[str] = Field(default=None, max_length=200)

    @field_validator("role")
    @classmethod
    def _r(cls, v: str) -> str:
        if v not in ("admin", "hod", "teacher", "student"):
            raise ValueError("role must be admin, hod, teacher or student")
        return v


class BulkAccounts(BaseModel):
    accounts: List[AccountBody] = Field(max_length=500)


class PasswordBody(BaseModel):
    password: Optional[str] = Field(default=None, max_length=200)


class OverrideBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=150)
    roll_no: str = Field(min_length=1, max_length=100)
    new_status: str
    reason: str = Field(min_length=3, max_length=500)
    password: Optional[str] = Field(default=None, max_length=200)

    @field_validator("new_status")
    @classmethod
    def _s(cls, v: str) -> str:
        if v not in ("present", "warning", "absent"):
            raise ValueError("new_status must be present, warning or absent")
        return v


class UserPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    role: Optional[str] = Field(default=None, pattern="^(student|teacher)$")
    new_id: Optional[str] = Field(default=None, min_length=1, max_length=100, pattern=r"^[\w.@\-]+$")


class AccountPatch(BaseModel):
    name: str = Field(min_length=1, max_length=255)


# --------------------------------------------------------------------------- app factory
def create_app(runtime_factory: Optional[Callable[[], Any]] = None) -> FastAPI:
    """``runtime_factory`` returns a backend.runtime.Runtime (tests inject one with fakes)."""
    throttle = auth.LoginThrottle()
    rt_roles = {"superadmin", "admin", "hod", "teacher", "student"}
    state: Dict[str, Any] = {"rt": None}

    def _build():
        if runtime_factory is not None:
            return runtime_factory()
        from backend.core.logging_setup import setup_logging
        setup_logging()
        from backend.runtime import build_runtime
        return build_runtime(start_scheduler=os.getenv("DISABLE_SCHEDULER", "0") != "1")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["rt"] = _build()
        try:
            yield
        finally:
            if state["rt"] is not None:
                state["rt"].stop()

    app = FastAPI(title="Smart Attendance", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def rt():
        if state["rt"] is None:
            raise HTTPException(503, "starting")
        return state["rt"]

    # ---- cross-cutting
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        try:
            resp = await call_next(request)
        except mysql.connector.Error as exc:       # database unreachable: say so, never crash or reset the connection
            log.error("database error on %s %s: %s", request.method, request.url.path, exc)
            resp = err(503, "db_unavailable", "The database is not reachable right now. The system reconnects automatically as soon as MySQL is back.")
        except Exception:  # noqa: BLE001
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            resp = err(500, "internal_error", "Something went wrong on the server. The details are in the server log.")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    if settings.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(StoreError)
    async def _store_err(_: Request, exc: StoreError):
        return err(STORE_ERROR_STATUS.get(exc.code, 422), exc.code, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_err(_: Request, exc: StarletteHTTPException):
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 409: "conflict", 429: "too_many_requests", 503: "unavailable"}
        return err(exc.status_code, codes.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_err(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return err(422, "validation_error", f"{loc + ': ' if loc else ''}{first.get('msg', 'invalid request')}")

    def current_user(request: Request) -> Dict[str, Any]:
        header = request.headers.get("Authorization", "")
        payload = auth.verify(header[7:]) if header.startswith("Bearer ") else None
        if not payload or payload.get("role") not in rt_roles:
            raise HTTPException(401, "Please sign in again.")
        return payload

    def require(*roles: str):
        def dep(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
            if user["role"] not in roles:
                raise HTTPException(403, "Your account is not allowed to do this.")
            return user
        return dep

    Anyone = Depends(current_user)
    Staff = Depends(require("superadmin", "admin"))                  # configure + change data
    Viewer = Depends(require("superadmin", "admin", "hod"))          # read reports / dashboards
    SuperOnly = Depends(require("superadmin"))
    StudentOnly = Depends(require("student"))
    TeacherOnly = Depends(require("teacher"))
    Admin = Staff

    # ---- public
    @app.get("/api/health")
    def health():
        return ok({"status": "ok", "service": "smart-attendance", "version": VERSION, "time": utcnow_naive().isoformat() + "Z"})

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request):
        key = f"{request.client.host if request.client else '?'}|{body.id.strip().lower()}"
        wait = throttle.blocked(key)
        if wait:
            raise HTTPException(429, f"Too many failed attempts. Try again in {wait} seconds.")
        res = rt().db.authenticate(body.id, body.password)
        if not res.get("valid"):
            throttle.fail(key)
            log.warning("login failed for id=%s", body.id.strip())
            raise HTTPException(401, res.get("error", "Invalid ID or password."))
        throttle.reset(key)
        tok = auth.issue(res["id"], res["name"], res["role"])
        log.info("login id=%s role=%s", res["id"], res["role"])
        return ok({**tok, "id": res["id"], "name": res["name"], "role": res["role"], **tz_info()})

    def _lan_ip() -> str:
        import socket
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sk.connect(("10.255.255.255", 1))
            return sk.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sk.close()

    def _registration_urls(request: Request) -> Dict[str, Any]:
        """Phones cannot open 'localhost': when the admin browses via localhost we hand out the LAN address."""
        port = os.getenv("REGISTRATION_PORT", "5050")
        enabled = os.getenv("DISABLE_REGISTRATION", "0") != "1"
        host = request.url.hostname or "localhost"
        if host in ("localhost", "127.0.0.1", "::1"):
            host = _lan_ip()
        base = f"https://{host}:{port}"
        return {"enabled": enabled, "host": host, "student_url": base + "/student" if enabled else None, "teacher_url": base + "/teacher" if enabled else None}

    @app.get("/api/registration-info")
    def registration_info(request: Request):
        """Public: where people register their face (the registration portal runs on its own HTTPS port)."""
        return ok(_registration_urls(request))

    @app.get("/api/registration-qr")
    def registration_qr(request: Request, role: str = Query(..., pattern="^(student|teacher)$")):
        """Public: QR code (PNG) that opens the student or teacher face-registration page on a phone."""
        info = _registration_urls(request)
        if not info["enabled"]:
            raise HTTPException(404, "Face registration is turned off on this server.")
        try:
            import qrcode
        except ImportError:
            raise HTTPException(503, "The 'qrcode' package is not installed (pip install qrcode).")
        buf = io.BytesIO()
        qr = qrcode.QRCode(version=None, box_size=8, border=2)
        qr.add_data(info[role + "_url"])
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/auth/me")
    def me(a=Anyone):
        return ok({"id": a["sub"], "name": a["name"], "role": a["role"], "expires_at": a["exp"], **tz_info()})

    # ---- readiness / dashboard / live
    @app.get("/api/readiness")
    def get_readiness(a=Viewer):
        return ok(readiness.compute(rt()))

    @app.get("/api/live-sessions")
    def live(a=Viewer):
        r = rt()
        cams = {c["room_id"]: c for c in r.store.list_cameras()}
        rows = r.logic.get_live_status()
        for row in rows:
            c = cams.get(row["room_id"])
            row["camera_status"] = c["status"] if c else "UNKNOWN"
        return items(rows)

    @app.get("/api/dashboard")
    def dashboard(a=Viewer):
        r = rt()
        now = now_local()
        today, day = now.strftime("%Y-%m-%d"), now.strftime("%A").lower()
        live_rows = {x["session_id"]: x for x in r.logic.get_live_status()}
        slots = [s for s in r.store.timetable_rows() if s["day_of_week"] == day]
        now_hm = now.strftime("%H:%M")
        classes = []
        for s in slots:
            db_s = r.store.find_session_for_slot(today, s)
            sid = db_s["session_id"] if db_s else f"SES_{s['room_id']}_{s['id']}_{today}"
            lv = live_rows.get(sid)
            st = (lv or {}).get("state") or (db_s or {}).get("state")
            phase = "completed" if st in ("COMPLETED", "SUSPENDED") else "live" if st else ("upcoming" if now_hm < s["start_time"] else "missed" if now_hm >= s["end_time"] else "starting")
            classes.append({**s, "session_id": sid, "state": st, "phase": phase, "note": (lv or {}).get("note", "")})
        cams = r.store.list_cameras()
        enabled = [c for c in cams if c["enabled"] and c["camera_type"] != "none"]
        rooms = r.store.rooms_overview()
        room_online = sum(1 for x in rooms if x["cameras_online"] > 0)
        t_rows = r.store._q("""SELECT ta.status, COUNT(*) n FROM teacher_attendance ta WHERE ta.date=%s GROUP BY ta.status""", (today,))
        s_rows = r.store._q("""SELECT a.status, COUNT(*) n FROM attendance_log a JOIN sessions s ON s.session_id=a.session_id
                               WHERE s.date=%s AND a.role='student' GROUP BY a.status""", (today,))
        flags = [f for f in r.store.teacher_flags(20) if f.get("date") == today]
        rd = readiness.compute(r)
        warnings = [{"level": c["status"], "title": c["label"], "message": c["detail"], "fix": c["fix"]} for c in rd["checks"] if c["status"] != "READY"]
        warnings += [{"level": "WARNING", "title": "Teacher absence flag", "message": f"{f['teacher_name']} was unrecognised for {f['minutes']:.0f} min in {f['subject']} ({f['room_id']}).", "fix": ""} for f in flags]
        return ok({
            "date": today, "classes": classes,
            "counts": {"classes_today": len(classes), "active_sessions": sum(1 for x in live_rows.values() if not x["finished"] and x["state"] not in ("QUARANTINED",)),
                       "upcoming": sum(1 for c in classes if c["phase"] == "upcoming")},
            "teacher_attendance": {x["status"]: x["n"] for x in t_rows}, "student_attendance": {x["status"]: x["n"] for x in s_rows},
            "cameras": {"enabled": len(enabled), "online": sum(1 for c in enabled if c["status"] == "ONLINE"),
                        "offline": sum(1 for c in enabled if c["status"] in ("OFFLINE", "STALE")), "unknown": sum(1 for c in enabled if c["status"] == "UNKNOWN")},
            "rooms": {"total": len(rooms), "online": room_online, "offline": len(rooms) - room_online},
            "system": {"overall": rd["overall"], "automatic_attendance_allowed": rd["automatic_attendance_allowed"]},
            "recent_events": r.store.recent_events(12), "warnings": warnings,
        })

    # ---- sessions / attendance / reports
    @app.get("/api/sessions")
    def sessions(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), limit: int = Query(100, ge=1, le=500), a=Viewer):
        return items(rt().store.list_sessions(date, limit))

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: str, a=Viewer):
        r = rt()
        s = r.store.get_session(session_id)
        if not s:
            raise HTTPException(404, "Session not found.")
        return ok({"session": s, "students": r.store.session_students(session_id), "teacher": r.store.teacher_summary(session_id),
                   "events": r.store.list_events(session_id), "unmeasurable": r.store.load_holes(session_id)})

    @app.get("/api/attendance")
    def attendance(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), session_id: Optional[str] = Query(None, max_length=150),
                   limit: int = Query(200, ge=1, le=2000), a=Viewer):
        return items(rt().store.attendance_rows(date, session_id, limit))

    @app.get("/api/teacher-attendance")
    def teacher_attendance(limit: int = Query(200, ge=1, le=2000), a=Viewer):
        return items(rt().store.teacher_report(limit))

    @app.get("/api/teacher-flags")
    def teacher_flags(a=Viewer):
        return items(rt().store.teacher_flags(200))

    def _csv(rows: List[Dict[str, Any]], name: str) -> Response:
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for row in jsonable(rows):
                w.writerow({k: csv_safe(v) for k, v in row.items()})
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/reports/attendance.csv")
    def report_attendance(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), a=Viewer):
        return _csv(rt().store.attendance_rows(date, None, 50000), f"attendance{'-' + date if date else ''}.csv")

    @app.get("/api/reports/teacher.csv")
    def report_teacher(a=Viewer):
        return _csv(rt().store.teacher_report(50000), "teacher-attendance.csv")

    # ---- people
    @app.get("/api/users")
    def users(role: Optional[str] = Query(None, pattern="^(student|teacher|admin)$"), a=Viewer):
        return items(rt().store.users_meta(role))        # never includes embeddings

    @app.patch("/api/users/{roll_no}")
    def patch_user(roll_no: str, body: UserPatch, a=Staff):
        res = Portal(rt().store, rt().db).update_user(roll_no, body.name, body.role, body.new_id)
        rt().logic.invalidate_timetable_cache()
        log.info("user edited id=%s -> %s by=%s", roll_no, res, a["sub"])
        return ok(res)

    @app.delete("/api/users/{roll_no}")
    def delete_user(roll_no: str, confirm: bool = Query(False), a=Staff):
        if not confirm:
            raise HTTPException(422, "Deleting a person is permanent. Send confirm=true to proceed.")
        res = Portal(rt().store, rt().db).delete_user(roll_no)
        rt().logic.invalidate_timetable_cache()
        log.warning("user deleted id=%s by=%s", roll_no, a["sub"])
        return ok(res)

    @app.delete("/api/users/{roll_no}/face")
    def reset_face(roll_no: str, a=Staff):
        """Delete only the stored face so the person can register again (login and history are kept)."""
        res = Portal(rt().store, rt().db).reset_face(roll_no)
        rt().logic.invalidate_timetable_cache()
        log.warning("face reset id=%s by=%s", roll_no, a["sub"])
        return ok(res)

    @app.put("/api/accounts/{account_id}")
    def edit_account(account_id: str, body: AccountPatch, a=Staff):
        Portal(rt().store, rt().db).rename_account(a["role"], account_id, body.name)
        return ok({"id": account_id, "name": body.name.strip()})

    # ---- rooms
    @app.get("/api/rooms")
    def rooms(a=Viewer):
        return items(rt().store.rooms_overview())

    @app.post("/api/rooms")
    def add_room(body: RoomBody, a=Staff):
        r = rt()
        if r.store._q("SELECT 1 x FROM rooms WHERE room_id=%s", (body.room_id,)):
            raise HTTPException(409, f"Room '{body.room_id}' already exists.")
        r.store.save_room(body.room_id, body.room_name)
        r.logic.invalidate_timetable_cache()
        return ok({"room_id": body.room_id}, 201)

    @app.put("/api/rooms/{room_id}")
    def edit_room(room_id: str, body: RoomUpdate, a=Staff):
        r = rt()
        if not r.store._q("SELECT 1 x FROM rooms WHERE room_id=%s", (room_id,)):
            raise HTTPException(404, "Room not found.")
        r.store.save_room(room_id, body.room_name)
        return ok({"room_id": room_id})

    @app.delete("/api/rooms/{room_id}")
    def del_room(room_id: str, a=Staff):
        rt().store.delete_room(room_id)
        rt().logic.invalidate_timetable_cache()
        return ok({"deleted": room_id})

    # ---- cameras
    def _spec(row: Dict[str, Any]) -> CameraSpec:
        return CameraSpec.from_row(row)

    @app.get("/api/cameras")
    def cameras(a=Staff):
        r = rt()
        live_status = r.cameras.statuses()
        rows = []
        for c in r.store.list_cameras():
            live_s = live_status.get(_spec(c).key)
            rows.append({**c, "source": redact_source(c["source"]), "live": live_s["status"] if live_s else None,
                         "live_error": live_s["error"] if live_s else None})   # credentials only via GET /api/cameras/{id}
        return items(rows, types=CAMERA_TYPES)

    @app.get("/api/cameras/{camera_id}")
    def camera(camera_id: int, a=Staff):
        c = rt().store.get_camera(camera_id)
        if not c:
            raise HTTPException(404, "Camera not found.")
        return ok(c)

    @app.post("/api/cameras")
    def add_camera(body: CameraBody, a=Staff):
        r = rt()
        cid = r.store.create_camera(body.name, body.camera_type, body.source, body.room_id, body.enabled)
        r.logic.invalidate_timetable_cache()
        log.info("camera created camera_id=%s room_id=%s type=%s", cid, body.room_id, body.camera_type)
        return ok(r.store.get_camera(cid), 201)

    @app.put("/api/cameras/{camera_id}")
    def edit_camera(camera_id: int, body: CameraPatch, a=Staff):
        r = rt()
        cam = r.store.update_camera(camera_id, **body.model_dump(exclude_unset=True))
        r.logic.invalidate_timetable_cache()
        log.info("camera updated camera_id=%s", camera_id)
        return ok(cam)

    @app.delete("/api/cameras/{camera_id}")
    def del_camera(camera_id: int, a=Staff):
        r = rt()
        if not r.store.delete_camera(camera_id):
            raise HTTPException(404, "Camera not found.")
        r.logic.invalidate_timetable_cache()
        return ok({"deleted": camera_id})

    @app.post("/api/cameras/test")
    def test_camera(body: CameraTest, a=Staff):
        """Opens the camera briefly. Never creates a session or any attendance."""
        r = rt()
        if body.camera_id is not None:
            row = r.store.get_camera(body.camera_id)
            if not row:
                raise HTTPException(404, "Camera not found.")
            spec = _spec({**row, **({"camera_type": body.camera_type} if body.camera_type else {}), **({"source": body.source} if body.source is not None else {})})
        else:
            if not body.camera_type:
                raise HTTPException(422, "camera_type is required")
            spec = CameraSpec(None, "test", body.camera_type, body.source or "")
        res = r.cameras.test(spec)
        if body.camera_id is not None and not body.source and not body.camera_type:
            r.store.set_camera_health(body.camera_id, "ONLINE" if res["ok"] else "OFFLINE", None if res["ok"] else res.get("error"),
                                      ok_at=utcnow_naive() if res["ok"] else None)
        return ok(res)

    @app.get("/api/camera-scan")
    def scan_cameras(max_index: int = Query(5, ge=1, le=16), a=Staff):
        return items(rt().cameras.scan(max_index=max_index), note="Only cameras attached to the machine running the server are found; network cameras must be added manually.")

    @app.get("/api/cameras/{camera_id}/snapshot")
    def snapshot(camera_id: int, a=Staff):
        """Live preview frame (JPEG). Preview never creates attendance."""
        r = rt()
        row = r.store.get_camera(camera_id)
        if not row:
            raise HTTPException(404, "Camera not found.")
        jpg = r.cameras.preview_jpeg(_spec(row))
        if not jpg:
            raise HTTPException(503, "The camera did not deliver a frame. Press Test for the reason.")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/cameras/{camera_id}/reconnect")
    def reconnect(camera_id: int, a=Staff):
        r = rt()
        row = r.store.get_camera(camera_id)
        if not row:
            raise HTTPException(404, "Camera not found.")
        spec = _spec(row)
        if r.cameras.reconnect(spec):
            return ok({"reconnecting": True, "message": "Reconnect requested; the camera will be re-opened now."})
        res = r.cameras.test(spec)
        r.store.set_camera_health(camera_id, "ONLINE" if res["ok"] else "OFFLINE", None if res["ok"] else res.get("error"),
                                  ok_at=utcnow_naive() if res["ok"] else None, reconnected=res["ok"])
        return ok({"reconnecting": False, "test": res, "message": "Camera is not in use right now; it was tested instead."})

    # ---- timetable
    @app.get("/api/timetable")
    def timetable(day: Optional[str] = Query(None, max_length=20), a=Viewer):
        rows = rt().store.timetable_rows()
        if day:
            rows = [x for x in rows if x["day_of_week"] == day.strip().lower()]
        return items(rows)

    @app.put("/api/timetable/{slot_id}/section")
    def slot_section(slot_id: int, body: SlotSection, a=Staff):
        rt().store.set_slot_section(slot_id, body.section_id or None)
        rt().logic.invalidate_timetable_cache()
        return ok({"id": slot_id, "section_id": body.section_id})

    @app.post("/api/timetable/preview")
    def timetable_preview(file: UploadFile = File(...), a=Staff):
        """Parse a timetable file into a DRAFT. Nothing is published or changed."""
        r = rt()
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_UPLOADS:
            raise HTTPException(422, f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_UPLOADS))}")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File is larger than 15 MB.")
        if not data:
            raise HTTPException(422, "The uploaded file is empty.")
        with tempfile.TemporaryDirectory(prefix="tt_") as tmp:
            path = Path(tmp) / f"upload{ext}"                 # server-chosen name: no path traversal via filename
            path.write_bytes(data)
            try:
                import backend.core.timetable_parser as tp
                df = tp.extract_timetable_data(str(path))
            except Exception as exc:  # noqa: BLE001
                log.exception("timetable parse failed")
                raise HTTPException(422, f"The file could not be read as a timetable ({type(exc).__name__}). Try a clearer scan or a spreadsheet.")
        if df is None or len(df) == 0:
            raise HTTPException(422, "No timetable rows were found in the file.")
        rows = [{"day": str(x.get("Day", "")), "start": str(x.get("StartTime", "")), "end": str(x.get("EndTime", "")),
                 "subject": str(x.get("Subject", "")), "teacher": str(x.get("TeacherID", "")), "room": str(x.get("RoomID", ""))}
                for x in df.to_dict("records")]
        return ok(r.store.create_draft(Path(file.filename or "upload").name[:200], rows), 201)

    @app.get("/api/timetable/drafts/{draft_id}")
    def get_draft(draft_id: str, a=Staff):
        d = rt().store.get_draft(draft_id)
        if not d:
            raise HTTPException(404, "Draft not found.")
        return ok(d)

    @app.post("/api/timetable/publish")
    def publish(body: PublishBody, a=Staff):
        if not body.confirm:
            raise HTTPException(422, "Publishing replaces the current timetable. Send confirm=true to proceed.")
        r = rt()
        res = r.store.publish_draft(body.draft_id)
        r.logic.invalidate_timetable_cache()
        log.info("timetable published draft=%s rows=%s", body.draft_id, res["published"])
        return ok(res)

    @app.delete("/api/timetable/drafts/{draft_id}")
    def discard(draft_id: str, a=Staff):
        rt().store.discard_draft(draft_id)
        return ok({"discarded": draft_id})

    # ---- sections
    @app.get("/api/sections")
    def sections(a=Viewer):
        return items(rt().store.list_sections())

    @app.post("/api/sections")
    def add_section(body: SectionBody, a=Staff):
        rt().store.upsert_section(body.section_id, body.section_name)
        return ok({"section_id": body.section_id}, 201)

    @app.delete("/api/sections/{section_id}")
    def del_section(section_id: str, a=Staff):
        rt().store.delete_section(section_id)
        return ok({"deleted": section_id})

    @app.get("/api/sections/{section_id}/students")
    def section_students(section_id: str, a=Viewer):
        return items(rt().store.section_members(section_id))

    @app.put("/api/sections/{section_id}/students")
    def set_students(section_id: str, body: SectionStudents, a=Staff):
        res = rt().store.set_section_students(section_id, body.roll_nos)
        rt().logic.invalidate_timetable_cache()
        return ok(res)

    # ---- settings
    @app.get("/api/settings")
    def get_settings(a=Staff):
        p = rt().store.get_policy()
        from backend.core.clock import app_tz
        return ok({"settings": {k: getattr(p, k) for k in POLICY_KEYS}, "timezone": str(app_tz()), "tz_configured": bool(os.getenv("APP_TIMEZONE")), "server_time": now_local().isoformat(timespec="seconds"),
                   "version": VERSION})

    @app.put("/api/settings")
    def put_settings(body: Dict[str, float], a=Staff):
        p = rt().store.set_policy_values(body)
        rt().logic.invalidate_timetable_cache()
        return ok({"settings": {k: getattr(p, k) for k in POLICY_KEYS}})

    # ---- accounts (logins) and face-registration status
    def portal() -> Portal:
        return Portal(rt().store, rt().db)

    @app.get("/api/accounts")
    def accounts(a=Staff):
        return items(portal().list_accounts(a["role"]))

    @app.post("/api/accounts")
    def add_account(body: AccountBody, a=Staff):
        res = portal().create_account(a["role"], body.id, body.role, body.name, body.password)
        log.info("account created id=%s role=%s by=%s", body.id, body.role, a["sub"])
        return ok(res, 201)

    @app.post("/api/accounts/bulk")
    def add_accounts_bulk(body: BulkAccounts, a=Staff):
        created, failed = [], []
        for acc in body.accounts:
            try:
                created.append(portal().create_account(a["role"], acc.id, acc.role, acc.name, acc.password))
            except StoreError as exc:
                failed.append({"id": acc.id, "error": str(exc)})
        return ok({"created": created, "failed": failed}, 201)

    @app.put("/api/accounts/{account_id}/password")
    def reset_password(account_id: str, body: PasswordBody, a=Staff):
        res = portal().reset_password(a["role"], account_id, body.password)
        log.info("password reset id=%s by=%s", account_id, a["sub"])
        return ok(res)

    @app.delete("/api/accounts/{account_id}")
    def delete_account(account_id: str, a=Staff):
        portal().delete_account(a["role"], account_id)
        return ok({"deleted": account_id})

    # ---- manual correction (admin) - audited in attendance_overrides
    @app.post("/api/attendance/override")
    def admin_override(body: OverrideBody, a=Staff):
        r = rt().db.apply_manual_override(body.session_id, body.roll_no, body.new_status, a["sub"], body.reason, is_admin=True)
        if not r.get("success"):
            raise HTTPException(422, r.get("error", "Could not apply the override."))
        return ok({"message": r["message"]})

    # ---- student portal: a student only ever sees their own data
    @app.get("/api/me/student/overview")
    def student_overview(a=StudentOnly):
        r, pt = rt(), portal()
        now = now_local()
        today = pt.student_today(a["sub"], now.strftime("%Y-%m-%d"), now.strftime("%A").lower(), now.strftime("%H:%M"))
        return ok({"profile": {"id": a["sub"], "name": a["name"], "sections": pt.student_sections(a["sub"]), "face_registered": pt.has_face(a["sub"])},
                   "summary": pt.student_summary(a["sub"]), "today": today})

    @app.get("/api/me/student/history")
    def student_history(limit: int = Query(60, ge=1, le=300), a=StudentOnly):
        return items(portal().student_history(a["sub"], limit))

    # ---- teacher portal
    @app.get("/api/me/teacher/overview")
    def teacher_overview(a=TeacherOnly):
        pt = portal()
        now = now_local()
        day = now.strftime("%A").lower()
        slots = pt.teacher_slots(a["sub"])
        live_rows = [x for x in rt().logic.get_live_status() if pt.owns_session(a["sub"], x["session_id"]) or str(x.get("teacher_id", "")).casefold() in pt.teacher_keys(a["sub"])]
        return ok({"profile": {"id": a["sub"], "name": a["name"], "face_registered": pt.has_face(a["sub"])},
                   "today": [s for s in slots if s["day_of_week"] == day], "week": slots, "live": live_rows,
                   "sessions": pt.teacher_sessions(a["sub"]), "my_attendance": pt.teacher_stats(a["sub"]),
                   "flags": [f for f in rt().store.teacher_flags(100) if f["teacher_id"] == a["sub"]]})

    @app.get("/api/me/teacher/sessions/{session_id}")
    def teacher_session(session_id: str, a=TeacherOnly):
        r = rt()
        if not portal().owns_session(a["sub"], session_id):
            raise HTTPException(404, "Session not found.")
        return ok({"session": r.store.get_session(session_id), "students": r.store.session_students(session_id), "teacher": r.store.teacher_summary(session_id),
                   "events": r.store.list_events(session_id), "unmeasurable": r.store.load_holes(session_id)})

    @app.post("/api/me/teacher/override")
    def teacher_override(body: OverrideBody, a=TeacherOnly):
        """A teacher may correct attendance in their own sessions; their password is asked again and every change is audited."""
        r = rt()
        if not portal().owns_session(a["sub"], body.session_id):
            raise HTTPException(403, "You can only correct attendance in your own classes.")
        if not body.password or not r.db.authenticate(a["sub"], body.password).get("valid"):
            raise HTTPException(403, "Password confirmation failed.")
        res = r.db.apply_manual_override(body.session_id, body.roll_no, body.new_status, a["sub"], body.reason, is_admin=True)
        if not res.get("success"):
            raise HTTPException(422, res.get("error", "Could not apply the override."))
        return ok({"message": res["message"]})

    # ---- camera face-count test (admin): how many faces are visible and who is recognised. Records nothing.
    @app.get("/api/cameras/{camera_id}/analyze")
    def analyze_camera(camera_id: int, a=Staff):
        import base64
        import cv2
        r = rt()
        row = r.store.get_camera(camera_id)
        if not row:
            raise HTTPException(404, "Camera not found.")
        rec = r.logic.recognizer
        if rec is None or not rec.ready():
            raise HTTPException(503, "The face engine is not ready yet (it may still be loading).")
        frame = r.cameras.grab_frame(_spec(row))
        if frame is None:
            raise HTTPException(503, "The camera did not deliver a frame. Press Test for the reason.")
        roster = r.logic._users or [u for u in r.db.get_all_users() if u.get("role") in ("student", "teacher")]
        found = rec.analyze(frame, roster)
        img = frame.copy()
        people, unknown = [], 0
        for f in found:
            x1, y1, x2, y2 = f["bbox"]
            m = f["match"]
            colour = (60, 190, 90) if m else (40, 150, 240)
            cv2.rectangle(img, (x1, y1), (x2, y2), colour, 2)
            label = (f"{m['name']} ({m['role']})" if m else "Unknown").encode("ascii", "replace").decode()
            cv2.putText(img, label, (x1, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)
            if m:
                people.append({"roll_no": m["roll_no"], "name": m["name"], "role": m["role"], "confidence": m["confidence"]})
            else:
                unknown += 1
        h, w = img.shape[:2]
        if w > 960:
            img = cv2.resize(img, (960, int(h * 960 / w)))
        okj, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        return ok({"faces": len(found), "recognized": people, "unknown": unknown, "frame": [w, h],
                   "image": "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode() if okj else None})

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_404(rest: str):
        raise HTTPException(404, "No such API endpoint.")

    # ---- frontend (static, same origin)
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="frontend")
    return app


app = create_app()
