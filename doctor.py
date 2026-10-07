#!/usr/bin/env python3
"""Smart Attendance doctor: checks the machine, configuration and database before you start.

    python doctor.py              # all checks (never opens a camera)
    python doctor.py --migrate    # also create/upgrade the database schema
    python doctor.py --cameras    # also probe local camera indexes 0-4 (may trigger an OS camera-permission prompt)
    python doctor.py --json       # machine-readable output

Exit status: 0 = no errors, 1 = at least one ERROR.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OK, WARN, ERR = "OK", "WARN", "ERROR"
results = []


def add(section: str, status: str, message: str, fix: str = "") -> None:
    results.append({"section": section, "status": status, "message": message, "fix": fix})


def check_python():
    v = sys.version_info
    if v < (3, 10):
        add("Python", ERR, f"Python {platform.python_version()} is too old.", "Install Python 3.10 or newer.")
    else:
        add("Python", OK, f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")
    if sys.prefix == sys.base_prefix:
        add("Python", WARN, "Not running inside a virtual environment.", "python -m venv .venv && source .venv/bin/activate (Windows: .venv\\Scripts\\activate)")


def check_dependencies():
    required = {"fastapi": "fastapi", "uvicorn": "uvicorn", "multipart": "python-multipart", "mysql.connector": "mysql-connector-python",
                "dotenv": "python-dotenv", "numpy": "numpy", "cv2": "opencv-python", "pandas": "pandas", "onnxruntime": "onnxruntime",
                "insightface": "insightface", "PIL": "pillow", "fitz": "pymupdf", "flask": "flask", "openpyxl": "openpyxl", "docx": "python-docx"}
    optional = {"customtkinter": "customtkinter (desktop app)", "rapidocr_onnxruntime": "rapidocr-onnxruntime (scanned PDF timetables)",
                "pytesseract": "pytesseract (OCR fallback)", "cryptography": "cryptography (HTTPS registration)"}
    missing = []
    for mod, pkg in required.items():
        try:
            importlib.import_module(mod)
        except Exception as exc:
            missing.append(pkg)
    add("Dependencies", ERR if missing else OK, f"Missing: {', '.join(missing)}" if missing else "All required packages import.",
        "pip install -r requirements.txt" if missing else "")
    for mod, pkg in optional.items():
        try:
            importlib.import_module(mod)
        except Exception:
            add("Dependencies", WARN, f"Optional package not available: {pkg}", "pip install -r requirements.txt")


def check_config():
    env_file = ROOT / ".env"
    if not env_file.exists():
        add("Configuration", ERR, ".env file not found.", "cp .env.example .env   then edit it (see README > Environment variables)")
    from dotenv import load_dotenv
    load_dotenv(env_file)
    from backend.core.db import INSECURE_PASSWORDS
    if os.getenv("ADMIN_PASSWORD", "") in INSECURE_PASSWORDS:
        add("Configuration", ERR, "ADMIN_PASSWORD is empty or a placeholder, so nobody can sign in to the web UI.", "Set a strong ADMIN_PASSWORD in .env")
    elif len(os.getenv("ADMIN_PASSWORD", "")) < 10:
        add("Configuration", WARN, "ADMIN_PASSWORD is shorter than 10 characters.", "Use a longer password.")
    else:
        add("Configuration", OK, "Administrator password is set.")
    if not os.getenv("APP_SECRET_KEY"):
        add("Configuration", WARN, "APP_SECRET_KEY is not set: everyone is signed out on every restart.", 'python -c "import secrets;print(secrets.token_hex(32))" and put it in .env')
    if not os.getenv("APP_TIMEZONE"):
        add("Configuration", WARN, "APP_TIMEZONE is not set; the server computer's time zone will be used for timetable times.", "Set APP_TIMEZONE=Region/City in .env")
    else:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(os.environ["APP_TIMEZONE"])
            add("Configuration", OK, f"Time zone {os.environ['APP_TIMEZONE']}")
        except Exception:
            add("Configuration", ERR, f"APP_TIMEZONE '{os.environ['APP_TIMEZONE']}' is not a valid IANA zone.", "e.g. Asia/Kolkata (on Windows also: pip install tzdata)")
    if os.getenv("FACE_DEMO_MODE", "0") == "1":
        add("Configuration", ERR, "FACE_DEMO_MODE=1: recognition is fake.", "Set FACE_DEMO_MODE=0")
    if os.getenv("ALLOW_OPEN_REGISTRATION", "0") == "1":
        add("Configuration", WARN, "ALLOW_OPEN_REGISTRATION=1: anyone can register while no credentials exist.", "Set it to 0 once IDs/passwords are created.")


def check_mysql(migrate: bool):
    try:
        import mysql.connector
        from backend.core import db as dbmod
        cfg = dbmod._conn_config()
        try:
            con = mysql.connector.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], connection_timeout=5)
        except mysql.connector.Error as exc:
            add("MySQL", ERR, f"Cannot connect to {cfg['host']}:{cfg['port']} as {cfg['user']}: {exc}",
                "Start MySQL and check MYSQL_HOST/PORT/USER/PASSWORD in .env (README > MySQL setup)")
            return
        cur = con.cursor()
        cur.execute("SELECT VERSION()")
        ver = cur.fetchone()[0]
        add("MySQL", OK, f"Connected to {cfg['host']}:{cfg['port']} (MySQL {ver})")
        cur.execute("SHOW DATABASES LIKE %s", (cfg["database"],))
        exists = cur.fetchone() is not None
        con.close()
        if not exists and not migrate:
            add("Schema", WARN, f"Database '{cfg['database']}' does not exist yet.", "python doctor.py --migrate   (or just start the server)")
            return
        if migrate:
            dbmod.DatabaseManager()
            add("Schema", OK, "Schema created / migrated to the latest version.")
        c = dbmod._mysql_connection_from_env()
        try:
            versions = [r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()]
        except Exception:
            versions = []
        finally:
            c.close()
        if dbmod.SCHEMA_VERSION in versions:
            add("Schema", OK, f"Schema version {dbmod.SCHEMA_VERSION} applied.")
            from backend.core.store import Store
            counts = Store(dbmod.DatabaseManager()).counts()
            add("Schema", OK, "Data: " + ", ".join(f"{k}={v}" for k, v in counts.items() if k in ("students", "teachers", "rooms", "timetable", "cameras", "sections")))
        else:
            add("Schema", WARN, "Schema is not at the latest version.", "python doctor.py --migrate")
        dbmod.reset_pools()
    except Exception as exc:
        add("MySQL", ERR, f"Unexpected error: {type(exc).__name__}: {exc}")


def check_face_model():
    try:
        from backend.config.settings import settings
        from backend.core.face_core import model_info
        mi = model_info(settings.models_dir)
        if mi["missing"]:
            add("Face model", ERR, f"Missing {', '.join(mi['missing'])} in {mi['dir']}", "Place the InsightFace buffalo_sc files there (README > Face model setup).")
            return
        add("Face model", OK, f"{mi['name']}: " + ", ".join(f"{k} ({v})" for k, v in mi["files"].items()))
        try:
            import numpy as np
            from backend.core.face_core import FaceEngine
            eng = FaceEngine(settings.models_dir)
            eng.load_async()
            eng._load_event.wait(60)
            if eng.ready:
                faces = eng.get_all_faces(np.full((480, 640, 3), 90, np.uint8))
                add("Face engine", OK, f"Model loaded; blank frame -> {len(faces)} faces (expected 0).")
            else:
                add("Face engine", ERR, f"Model failed to load: {eng.load_error}", "pip install -r requirements.txt; re-copy the model files.")
        except Exception as exc:
            add("Face engine", ERR, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        add("Face model", ERR, f"{type(exc).__name__}: {exc}")


def check_opencv(probe_cameras: bool):
    try:
        import cv2
        add("OpenCV", OK, f"OpenCV {cv2.__version__}")
    except Exception as exc:
        add("OpenCV", ERR, f"OpenCV unavailable: {exc}", "pip install opencv-python")
        return
    if not probe_cameras:
        add("Camera", OK, "Camera probing skipped (it can trigger an OS permission prompt). Run with --cameras to probe indexes 0-4.")
        return
    from backend.core.camera_manager import CameraManager
    found = CameraManager().scan(max_index=5)
    if found:
        add("Camera", OK, "Found: " + ", ".join(f"index {c['index']} ({c.get('width', '?')}x{c.get('height', '?')})" for c in found))
    else:
        add("Camera", WARN, "No local camera found.", "Plug one in, close other apps using it, and allow camera access for the terminal/Python in the OS privacy settings. Network cameras are added in the UI.")


def check_filesystem():
    for name in ("logs",):
        d = ROOT / name
        try:
            d.mkdir(exist_ok=True)
            t = d / ".write_test"
            t.write_text("x"); t.unlink()
            add("Filesystem", OK, f"{name}/ is writable")
        except OSError as exc:
            add("Filesystem", ERR, f"{name}/ is not writable: {exc}", "Fix the directory permissions.")
    ui = ROOT / "frontend" / "admin" / "index.html"
    add("Filesystem", OK if ui.exists() else ERR, "Web UI files present" if ui.exists() else "frontend/admin/index.html is missing", "" if ui.exists() else "Restore the frontend/ folder.")
    if (ROOT / "database" / "initial_data.sql").exists():
        add("Permissions", WARN, "database/initial_data.sql contains biometric data (face embeddings). Do not publish or share this repository.", "Remove the file once the data is imported into MySQL.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--migrate", action="store_true"); ap.add_argument("--cameras", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    check_python(); check_dependencies(); check_config(); check_mysql(a.migrate); check_face_model(); check_opencv(a.cameras); check_filesystem()
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        icon = {OK: "[ OK ]", WARN: "[WARN]", ERR: "[FAIL]"}
        for r in results:
            print(f"{icon[r['status']]} {r['section']:<13} {r['message']}")
            if r["fix"] and r["status"] != OK:
                print(f"       -> {r['fix']}")
        n_err = sum(r["status"] == ERR for r in results); n_warn = sum(r["status"] == WARN for r in results)
        print(f"\n{n_err} error(s), {n_warn} warning(s). " + ("Ready to start: python server.py" if not n_err else "Fix the errors above, then re-run python doctor.py"))
    return 1 if any(r["status"] == ERR for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
