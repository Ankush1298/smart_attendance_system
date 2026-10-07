# Smart Attendance

Classroom attendance that runs by itself from the timetable: at the scheduled time the room's camera looks for the **scheduled teacher**; once the teacher is recognised the class is authorized and students of that class's **section** are recognised through the lecture. Results land in MySQL and in an admin web UI.

```
TIMETABLE → class session → room → camera → teacher verification → authorization
          → student recognition → attendance engine → MySQL → reports
```

* Fully local: no cloud services. Face recognition is InsightFace `buffalo_sc` on the CPU.
* One process: `python server.py` serves the web UI, the REST API and the attendance scheduler.
* The original CustomTkinter desktop app and the face-registration portal still work (see [Desktop app](#desktop-app-and-registration-portal)).

## Who sees what (roles)
Everyone signs in at the same address with **their own ID and password**; the account decides the view.

| Role | How the account is created | Sees / can do |
|---|---|---|
| **Super admin** | `ADMIN_ID` / `ADMIN_PASSWORD` in `.env` | Everything, incl. creating admin and HOD logins. |
| **Admin** | Created by the super admin (Accounts & Registration) | Everything except creating admin accounts: timetable, **edit/delete students and teachers**, rooms, cameras (test, preview, face count), sections, logins, settings, corrections. |
| **HOD / Management** | Created by an admin | Read-only: dashboard, live sessions, attendance, teacher attendance and absence flags, reports/CSV, timetable, readiness. Nothing can be changed. |
| **Teacher** | Created by an admin (same ID is used to register their face) | *My Classes*: today's classes, live status of their own class, results of their own classes, their own attendance and flags, **correct** a student's result (password re-asked, audited). |
| **Student** | Created by an admin (same ID is used to register their face) | *My Attendance*: today's classes with a plain-language **present / absent notice** (also shown as a small notification), **attendance percentage per class** and overall, and their recent history. A student sees only their own data. |

## Face registration (students and teachers)
`python server.py` also starts the **registration portal** (HTTPS, port `REGISTRATION_PORT`, default 5050). The sign-in page links to it with a QR code for phones; teachers and students also have a **Register My Face** page in their menu, and admins see both QR codes on *Accounts & Registration*. When the admin browses via `localhost`, the QR/link use the server's LAN address so phones can reach it. A person opens `https://<server>:5050/student` (or `/teacher`) on their phone or laptop, signs in with the ID/password the admin gave them and follows the guided head-movement capture. Only numeric embeddings are stored. *Accounts & Registration* shows who has registered and who still has to, and lists registered people who have no login yet. Self-signed certificate: accept the browser warning once. Set `DISABLE_REGISTRATION=1` to turn the portal off.

## Testing a camera: how many faces does it see?
*Cameras → Preview → Count faces* shows the live frame with a box around every face (green = recognised, with name and confidence; orange = unknown) and the counts, e.g. “3 faces detected · 2 recognised · 1 unknown”. Nothing is recorded. Use it to position cameras before the first lecture.

## Contents
[Requirements](#requirements) · [Quick start](#quick-start) · [Environment variables](#environment-variables) · [MySQL setup](#mysql-setup) · [Face model setup](#face-model-setup) · [Registering people](#teacher-and-student-registration) · [Rooms and cameras](#rooms-and-camera-setup) · [Timetable and sections](#timetable-and-sections) · [How attendance works](#how-attendance-works) · [Running](#running-the-application) · [Testing](#testing) · [Troubleshooting](#troubleshooting) · [Production checklist](#production-checklist) · [Known limitations](#known-limitations)

## Requirements
| | |
|---|---|
| OS | Windows 10+, macOS, Linux |
| Python | 3.10 – 3.14 |
| MySQL | 8.0+ (tested on MySQL 26.7 / 8.x series) |
| Camera | USB/built-in webcam, DroidCam phone, IP/RTSP camera (any OpenCV-readable stream) |
| Browser | Any current Chrome, Edge, Firefox or Safari |
| CPU | A modern laptop CPU is enough for a few rooms; recognition is a short burst once a minute per room |

## Quick start
```bash
# macOS / Linux
./setup.sh                      # venv, dependencies, .env (secret generated), doctor
nano .env                       # set MYSQL_PASSWORD, ADMIN_PASSWORD, APP_TIMEZONE
python doctor.py --migrate      # creates/updates the database schema
source .venv/bin/activate && python server.py
```
```powershell
# Windows (PowerShell)
Set-ExecutionPolicy -Scope Process Bypass; .\setup.ps1
notepad .env
.\.venv\Scripts\python.exe doctor.py --migrate
.\.venv\Scripts\python.exe server.py
```
Open **http://127.0.0.1:8000**, sign in with `ADMIN_ID` / `ADMIN_PASSWORD`, then work down **System Readiness**: it lists everything that is missing and how to fix it. Automatic attendance only starts when the required items are not in error.

`python doctor.py` checks Python, packages, configuration, MySQL, schema, face model, OpenCV, filesystem (add `--cameras` to probe local cameras; that can trigger an OS camera-permission prompt).

## Environment variables
Set in `.env` (copied from `.env.example`); never commit it.

| Variable | Default | Meaning |
|---|---|---|
| `MYSQL_HOST/PORT/DATABASE/USER/PASSWORD` | 127.0.0.1 / 3306 / smart_attendance / root / – | Database connection. Use a dedicated MySQL user, not `root`. |
| `MYSQL_POOL_SIZE` | 20 | Connection pool size (max 32). |
| `ADMIN_ID`, `ADMIN_PASSWORD`, `ADMIN_NAME` | ADMIN / **(none)** / Administrator | Web-UI login. **Login is refused while the password is empty or a placeholder such as `change_me`.** |
| `APP_SECRET_KEY` | random per start | Signs login tokens (8 h). Set a fixed 64-hex value so restarts do not sign users out. |
| `APP_TIMEZONE` | server zone | IANA zone (e.g. `Asia/Kolkata`) in which timetable times are interpreted. Windows also needs `pip install tzdata` (setup.ps1 does it). |
| `API_HOST`, `API_PORT` | 127.0.0.1 / 8000 | Bind address. Use a reverse proxy with HTTPS to expose beyond localhost. |
| `CORS_ORIGINS` | empty | Allowed cross-site origins (empty = same-origin only). |
| `REGISTRATION_PORT`, `REGISTRATION_TLS`, `DISABLE_REGISTRATION` | 5050 / 1 / 0 | Face-registration portal started by `server.py` (HTTPS is needed for phone cameras). |
| `ALLOW_OPEN_REGISTRATION` | 0 | 1 lets anyone register while no ID/password exists for a role. Keep 0. |
| `ALLOW_UNSECTIONED_CLASSES` | 0 | 0 = slots without a section record **no** student attendance. 1 = legacy: every registered student counts. |
| `FACE_DEMO_MODE` | 0 | 1 replaces recognition with random numbers. Never in production (Readiness reports ERROR). |
| `CAMERA_ALLOW_FILE_SOURCES` | 0 | Allow a video file as a "camera" (testing only). |
| `ATTENDANCE_<SETTING>` | – | Override a policy default, e.g. `ATTENDANCE_START_MARGIN_MIN=5`. The Settings page values win. |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_DIR` | INFO / text / ./logs | Logging (`json` format available). Camera URL credentials and passwords are redacted. |

## MySQL setup
```sql
CREATE DATABASE smart_attendance CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'attendance'@'localhost' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON smart_attendance.* TO 'attendance'@'localhost';
```
Put the user/password in `.env`, then `python doctor.py --migrate`. The schema is created and upgraded automatically at start-up (versioned migrations, tracked in `schema_migrations`). Upgrades **never delete data**: new columns/tables are added, existing rooms with a camera become camera rows, and unique indexes are only added when existing rows do not violate them.

Back up with `mysqldump smart_attendance > backup.sql` (the dump contains face embeddings: treat it as sensitive).

## Face model setup
The model files live in `models/models/buffalo_sc/` (`det_500m.onnx`, `w600k_mbf.onnx`) and ship with the project. Readiness shows their short hashes. Users enrolled with a different model are flagged because their embeddings will not match reliably.

## Teacher and student registration
1. As administrator create an ID and password for each person (*Accounts & Registration → New login* or *Add many*, which generates the passwords; the registration portal's own admin page is `https://<server>:5050/admin`, super-admin credentials).
2. Each person opens `https://<server>:5050/student` or `/teacher` on the school network, signs in with their ID/password and completes the guided face capture (self-signed certificate: accept the browser warning once).
3. Only numeric embeddings are stored. They are never shown by the API or UI and never logged.

The portal is started by the desktop app (`python main.py`) or on its own: `python register.py`.

## Rooms and camera setup
**Cameras** page → *Add camera*.

| Camera type | Source | Notes |
|---|---|---|
| Webcam / USB camera, Built-in laptop camera | device index `0`, `1`, `2`… | Use **Scan available cameras** to find the index. Nothing assumes index 0. |
| DroidCam / phone | `192.168.1.20:4747` | Phone and computer on the same Wi-Fi; `/video` is added automatically. |
| IP camera (HTTP/MJPEG) | `http://192.168.1.20:8080/video` | |
| Network camera (RTSP) | `rtsp://user:pass@192.168.1.30:554/stream1` | Credentials are hidden in lists and logs. |
| Disabled / not configured | – | Kept for reference, never opened. |

Pick the **room**, press **Test camera** (opens it briefly and reports resolution/latency or the reason it failed), then Save. **Preview** shows a live frame. Testing and previewing never create attendance. A class only uses cameras assigned to its own room; several cameras per room are combined.

If a camera drops during a lecture the system records the time as **unmeasurable**, retries with back-off, and resumes by itself. It is never counted as absence.

macOS/Windows need camera permission for the terminal/Python process (System Settings → Privacy & Security → Camera).

## Timetable and sections
* **Timetable → Upload**: CSV, Excel, Word weekly grid, PDF (text layer or scanned/OCR) or image. The upload is parsed into a **preview** with per-row errors/warnings (bad day/time, unknown teacher, overlapping slots in a room). Nothing changes until you press *Review and publish* and confirm. Publishing replaces the timetable in one transaction after **archiving the old one**; section assignments of unchanged slots are kept.
* **Students → Sections**: create sections (e.g. `CSE-A`), tick students, *Assign*. Then pick the section of each slot on the Timetable page. A student receives attendance only for classes of a section they belong to.
* CSV columns: `Day,StartTime,EndTime,Subject,TeacherID,RoomID` (`TeacherID` = the teacher's registered ID or exact name).

## How attendance works
1. **Scheduling.** Every few seconds the scheduler reads today's timetable. At the start time, for each slot with an enabled camera in its room it creates exactly one session (idempotent: repeated cycles, restarts or two processes never duplicate it) and enters `WAITING_TEACHER`. A class that already ended never gets a session; a room without an enabled camera never starts.
2. **Teacher authorization.** Only the **scheduled** teacher, recognised on **that room's** camera, authorizes the session (`ACTIVE`). Another teacher, a student or an unrecognised person never does (it is logged). Authorization needs the face visible once; afterwards the teacher may move anywhere in the room. If the teacher is not verified within 20 minutes (and the camera was working) the session is `SUSPENDED`.
3. **Counted window.** For a 50-minute lecture 10:00–10:50 only 10:05–10:45 counts, for students and teachers. Margins are configurable (Settings; default 5/5). Detections outside the window are stored but never counted.
4. **Student presence.** About every 60 s a burst of frames is recognised. Each detection counts ±30 s; overlapping intervals are merged and clamped to the window (dwell time, not “scans × minutes”). Percentage = present ÷ measurable minutes; ≥ 80 % present, ≥ 60 % warning, else absent (existing rules, incl. transition grace). Recognition events are kept separately from final records and inserting them twice changes nothing.
5. **Teacher presence.** Presence is the whole classroom (no zone). A missed recognition is bridged for up to 3 minutes; a continuous unrecognised stretch > 20 minutes raises an HOD/admin **flag** (status “present, long absence”) – it never marks the class absent.
6. **Camera / engine / database trouble** = *unmeasurable* time (`CAMERA_LOST`, `DB_ERROR`), subtracted from both numerator and denominator. If too little of a lecture was measurable the result is `unmeasurable`, not `absent`. DB writes are buffered in memory during a MySQL outage and flushed on recovery; after a crash/restart open sessions are resumed or finalized and the downtime is recorded as unmeasurable.
7. **State machine.** `SCHEDULED → WAITING_TEACHER → ACTIVE ⇄ TEACHER_ABSENT/RESUMED`, `CAMERA_LOST/DB_ERROR → RECOVERED → previous state`, `SUSPENDED`, `QUARANTINED` (bad timetable row, overlapping slots), `COMPLETED`. Illegal transitions raise; every transition is logged and stored in `session_events` (visible on Attendance → session).

## Running the application
```bash
python server.py            # web UI + API + scheduler            http://127.0.0.1:8000
python doctor.py            # health report
python main.py              # optional desktop app (also runs a scheduler - do not run both)
```
Run **one** scheduler per database (either `server.py` or `main.py`). The web UI works on phones; use the **System Readiness** page as the daily check.

### Desktop app and registration portal
`python main.py` starts the original desktop app, its scheduler and the registration portal on port 5050. The desktop app’s *teacher zone* setting is no longer used (teacher presence is whole-room). The desktop live-view tab opens its own camera handle; do not use it on a camera that has a class running.

## Testing
```bash
pip install -r requirements-dev.txt
(cd frontend/tests && npm install)            # jsdom + axe-core for the UI tests
export TEST_MYSQL_HOST=127.0.0.1 TEST_MYSQL_PORT=3306 TEST_MYSQL_USER=root TEST_MYSQL_PASSWORD=
python -m pytest                              # backend, database, API, camera, scheduler, E2E, load, real-Chrome UI
(cd frontend/tests && node --test)            # frontend unit tests
ruff check --select F,E9,E722,B006 --ignore F401,F841,F811 backend tests *.py
```
Database tests need a **real MySQL server** (they create and drop `sa_test_*` databases; use a throw-away instance). Browser tests use the installed Google Chrome through Playwright and are skipped if Chrome/MySQL are missing. Physical-camera and real-face tests cannot run in CI; manual procedures are in [Camera setup](#rooms-and-camera-setup) (use *Test camera*, *Preview* and watch **Live Sessions** during a real lecture).

## Troubleshooting
| Symptom | Check |
|---|---|
| Cannot sign in | `ADMIN_PASSWORD` set in `.env` and not a placeholder; 5 wrong attempts lock the account for 5 min. |
| Class never starts | System Readiness; room has an **enabled** camera; teacher is registered (name or ID matches the timetable); slot is not `QUARANTINED` (Live Sessions). |
| Stuck in “Waiting for teacher” | Camera Preview shows the teacher’s face clearly; the teacher is enrolled with the current model. |
| Students get no attendance | Slot has a section and the students are in it (Students page). |
| Camera “Offline” | Press Test; close other apps using it; check OS camera permission; RTSP URL/credentials; for DroidCam same Wi-Fi. |
| Times are off | Set `APP_TIMEZONE`; the UI shows times in the server’s zone. |
| “Database is not reachable” | MySQL running? `python doctor.py`. The app reconnects by itself. |
| Logs | `logs/attendance.log` (rotating); structured key=value with `session_id`, `room_id`, `teacher_id`. |

## Production checklist
- [ ] Dedicated MySQL user; MySQL not exposed publicly; backups scheduled.
- [ ] `ADMIN_PASSWORD` strong, `APP_SECRET_KEY` fixed, `ALLOW_OPEN_REGISTRATION=0`, `FACE_DEMO_MODE=0`.
- [ ] `APP_TIMEZONE` set. HTTPS reverse proxy in front of the web UI if reachable beyond localhost.
- [ ] Every timetabled room has an enabled, tested camera; every slot has a section; teachers registered.
- [ ] `database/initial_data.sql` (contains face embeddings) removed from the deployment.
- [ ] System Readiness is green; a dry-run lecture was observed on Live Sessions.
- [ ] Logs monitored; disk space for `logs/`.

## Known limitations
* Teacher **re-identification / body tracking is not implemented**: presence is credited whenever the teacher’s face is recognised anywhere in the room (any camera of the room), and gaps up to 3 minutes are bridged. A teacher who never faces a camera for long stretches can be reported absent.
* Real-world recognition accuracy, physical cameras and scanned-timetable OCR accuracy are not covered by the automated tests (no hardware / sample files in the repo).
* Single scheduler process per database; the API runs in the same process.
* The desktop app’s live tab and `server.py`/`main.py` schedulers must not run together.
