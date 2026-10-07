"""System readiness: what is READY / WARNING / ERROR, why, and how to fix it."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from backend.config.settings import settings
from backend.core.camera_types import validate_spec

READY, WARNING, ERROR = "READY", "WARNING", "ERROR"
MANDATORY = {"mysql", "face_model", "face_engine", "camera", "timetable", "teachers", "engine"}


def _c(cid: str, label: str, status: str, detail: str, fix: str = "") -> Dict[str, Any]:
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix, "mandatory": cid in MANDATORY}


def compute(rt) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}

    # MYSQL
    try:
        counts = rt.store.counts()
        ver = rt.store._q("SELECT MAX(version) v FROM schema_migrations")[0]["v"]
        from backend.core.db import SCHEMA_VERSION
        if ver != SCHEMA_VERSION:
            checks.append(_c("mysql", "MySQL", ERROR, f"Connected, but schema version is {ver} (expected {SCHEMA_VERSION}).",
                             "Restart the application so migrations run, or run: python doctor.py --migrate"))
        else:
            checks.append(_c("mysql", "MySQL", READY, f"Connected to {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}; schema v{ver}."))
    except Exception as exc:  # noqa: BLE001
        checks.append(_c("mysql", "MySQL", ERROR, f"Cannot use the database: {type(exc).__name__}: {exc}",
                         "Check MYSQL_* values in .env and that the MySQL server is running (see README > MySQL setup)."))
        return _finish(checks, rt)

    # FACE MODEL
    from backend.core.face_core import model_info
    mi = model_info(settings.models_dir)
    if mi["missing"]:
        checks.append(_c("face_model", "Face model", ERROR, f"Missing model file(s) in {mi['dir']}: {', '.join(mi['missing'])}.",
                         "Copy the InsightFace 'buffalo_sc' model files into models/models/buffalo_sc/ (see README > Face model setup)."))
    else:
        stale = rt.store._q("SELECT COUNT(*) n FROM users WHERE embedding_model IS NOT NULL AND embedding_model<>%s", (mi["name"],))[0]["n"]
        detail = f"{mi['name']} ({', '.join(f'{k} {v}' for k, v in mi['files'].items())})."
        if stale:
            checks.append(_c("face_model", "Face model", WARNING, detail + f" {stale} user(s) were enrolled with a different model and will not match reliably.",
                             "Re-register those users with the current model."))
        else:
            checks.append(_c("face_model", "Face model", READY, detail))

    # FACE ENGINE
    eng = rt.engine
    if os.getenv("FACE_DEMO_MODE", "0") == "1":
        checks.append(_c("face_engine", "Face engine", ERROR, "FACE_DEMO_MODE=1: embeddings are random numbers, nobody can be recognised.", "Set FACE_DEMO_MODE=0 in .env and restart."))
    elif eng is None:
        checks.append(_c("face_engine", "Face engine", ERROR, "No face engine in this process.", "Start the application with python server.py"))
    elif getattr(eng, "load_error", None):
        checks.append(_c("face_engine", "Face engine", ERROR, f"Model failed to load: {eng.load_error}",
                         "Check onnxruntime/insightface are installed (pip install -r requirements.txt) and the model files are intact."))
    elif not getattr(eng, "ready", False):
        checks.append(_c("face_engine", "Face engine", WARNING, "Model is still loading.", "Wait a few seconds and refresh."))
    else:
        checks.append(_c("face_engine", "Face engine", READY, "Model loaded; CPU inference."))

    # CAMERA
    cams = rt.store.list_cameras()
    enabled = [c for c in cams if c["enabled"] and c["camera_type"] != "none"]
    if not enabled:
        checks.append(_c("camera", "Cameras", ERROR, "No enabled camera is configured.", "Open Cameras, add a camera, assign it to a room and press Test."))
    else:
        online = [c for c in enabled if c["status"] == "ONLINE"]
        bad = [c["name"] for c in enabled if validate_spec(c["camera_type"], c["source"])[0] is False]
        if bad:
            checks.append(_c("camera", "Cameras", ERROR, f"Invalid camera configuration: {', '.join(bad)}.", "Edit the camera and fix its source."))
        elif len(online) == len(enabled):
            checks.append(_c("camera", "Cameras", READY, f"{len(online)} of {len(enabled)} enabled camera(s) online."))
        else:
            checks.append(_c("camera", "Cameras", WARNING, f"{len(online)} of {len(enabled)} enabled camera(s) reported online "
                             "(cameras are opened only while a class is running, so 'unknown' before the first lecture is normal).",
                             "Use Test on each camera to verify it before the first lecture."))

    # ROOMS
    rooms = rt.store.rooms_overview()
    if not rooms:
        checks.append(_c("rooms", "Rooms", ERROR, "No rooms exist.", "Import a timetable or add rooms on the Rooms page."))
    elif counts["slots_without_camera"]:
        checks.append(_c("rooms", "Rooms", WARNING, f"{counts['slots_without_camera']} timetable slot(s) are in rooms without an enabled camera; those classes will not start.",
                         "Assign an enabled camera to each room used by the timetable."))
    else:
        checks.append(_c("rooms", "Rooms", READY, f"{len(rooms)} room(s); every timetabled room has a camera."))

    # TIMETABLE
    if counts["timetable"] == 0:
        checks.append(_c("timetable", "Timetable", ERROR, "The timetable is empty.", "Upload and publish a timetable on the Timetable page."))
    elif counts["slots_without_section"]:
        checks.append(_c("timetable", "Timetable", WARNING, f"{counts['timetable']} slot(s); {counts['slots_without_section']} have no section, so student attendance is disabled for them.",
                         "Assign a section to each slot (Timetable page) and enrol students in sections (Students page)."))
    else:
        checks.append(_c("timetable", "Timetable", READY, f"{counts['timetable']} slot(s), all assigned to a section."))

    # TEACHERS / STUDENTS
    if counts["teachers"] == 0:
        checks.append(_c("teachers", "Teacher registration", ERROR, "No teacher has registered a face.", "Have each teacher register through the registration portal (README > Teacher registration)."))
    elif counts["slots_teacher_unregistered"]:
        checks.append(_c("teachers", "Teacher registration", WARNING, f"{counts['slots_teacher_unregistered']} timetable slot(s) name a teacher who has not registered; those classes cannot be authorized.",
                         "Register the missing teachers or correct the timetable."))
    else:
        checks.append(_c("teachers", "Teacher registration", READY, f"{counts['teachers']} teacher(s) registered; all timetabled teachers found."))
    if counts["students"] == 0:
        checks.append(_c("students", "Student enrollment", WARNING, "No student has registered a face.", "Students register through the registration portal."))
    else:
        unassigned = rt.store._q("""SELECT COUNT(*) n FROM users u WHERE u.role='student'
                                    AND NOT EXISTS (SELECT 1 FROM student_sections s WHERE s.roll_no=u.roll_no)""")[0]["n"]
        if unassigned:
            checks.append(_c("students", "Student enrollment", WARNING, f"{counts['students']} student(s) registered; {unassigned} are not in any section and will never receive attendance.",
                             "Assign them to a section on the Students page."))
        else:
            checks.append(_c("students", "Student enrollment", READY, f"{counts['students']} student(s) registered and assigned to sections."))

    if os.getenv("DISABLE_REGISTRATION", "0") != "1":
        import socket
        port = int(os.getenv("REGISTRATION_PORT", "5050"))
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            checks.append(_c("registration", "Face registration portal", READY, f"Running on port {port} (HTTPS). Students and teachers register there."))
        except OSError:
            checks.append(_c("registration", "Face registration portal", WARNING, f"Not reachable on port {port}: nobody can register a face.",
                             "Check logs/attendance.log; make sure the port is free and `cryptography` is installed."))
    return _finish(checks, rt)


def _finish(checks: List[Dict[str, Any]], rt) -> Dict[str, Any]:
    lg = rt.logic
    age = (time.time() - lg.last_tick_at.timestamp()) if getattr(lg, "last_tick_at", None) else None
    if not lg.running:
        checks.append(_c("engine", "Attendance engine", ERROR, "The scheduler is not running.", "Start the application with python server.py"))
    elif lg.last_tick_error:
        checks.append(_c("engine", "Attendance engine", ERROR, f"Last scheduler cycle failed: {lg.last_tick_error}", "Check logs/attendance.log."))
    elif age is None or age > 60:
        checks.append(_c("engine", "Attendance engine", WARNING, "Scheduler is starting (no cycle completed yet)." if age is None else f"No scheduler cycle for {int(age)} s.", "Check logs/attendance.log if this persists."))
    else:
        checks.append(_c("engine", "Attendance engine", READY, f"Scheduler running; last cycle {int(age)} s ago."))
    errors = [c for c in checks if c["status"] == ERROR]
    warns = [c for c in checks if c["status"] == WARNING]
    blocking = [c for c in errors if c["mandatory"]]
    return {"overall": ERROR if errors else WARNING if warns else READY, "automatic_attendance_allowed": not blocking,
            "blocking": [c["id"] for c in blocking], "checks": checks}
