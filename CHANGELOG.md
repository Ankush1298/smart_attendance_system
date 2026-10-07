# Changelog

## 3.1.0 – roles, portals and registration in the web app
- **Roles and sign-in for everyone:** super admin, admin, HOD/management, teacher, student, one login page, server-enforced permissions (HODs read-only, teachers/students only their own data); `authorized_credentials.role` widened (migration 4); identical error for unknown ID and wrong password.
- **Student portal:** today's classes with a plain-language present/absent notice (also a small notification), per-class and overall attendance percentage, history; manual corrections reach the percentage.
- **Teacher portal:** own classes, live status, results, own attendance and flags, audited corrections with password re-confirmation.
- **Accounts & Registration page:** create logins (single or bulk, generated passwords shown once), reset/delete logins, see who has registered a face.
- **Face registration is part of `server.py`** again (HTTPS portal started in-process, linked from the sign-in page, readiness check).
- **Camera test counts faces:** Preview → Count faces draws boxes, names recognised people and reports recognised/unknown counts; nothing is recorded.
- **QR face registration for teachers and students:** `/api/registration-qr`, a *Register My Face* page, QR on the sign-in page and on Accounts & Registration (LAN address substituted for localhost).
- **Edit and delete people:** admins/super admin can edit a student's or teacher's name, ID and role and delete them (Students, Teachers, Accounts pages). An ID change is carried through login, attendance history, sections and timetable in one transaction; delete is permanent and asks for confirmation; HOD/teacher/student cannot do either.
- **Reset a registered face:** *Reset face* button on Students/Teachers (admins). Deletes only the stored face so the person can register again; login, section and history stay. A "Face" column shows who is registered. A person without a face is not in the recognition roster and a teacher without one cannot authorize a class. (Migration 5: face data is optional.)
- Fixed: republishing the timetable renumbered slot ids and detached today's finished sessions (and could open a second session for a running class); stray text in action-less dialogs; server 403 messages were hidden.

## 3.0.0 – production upgrade

### Added
- **Automatic, idempotent attendance engine** (`backend/core/session_logic.py`): timetable → session → room → camera → teacher verification → authorization → student recognition → MySQL. One session per class/day regardless of repeated cycles, restarts or parallel schedulers; no session for ended classes, rooms without an enabled camera, or other rooms' cameras.
- **Explicit session state machine** (`session_state.py`): SCHEDULED, WAITING_TEACHER, ACTIVE, TEACHER_ABSENT, RESUMED, CAMERA_LOST, RECOVERED, DB_ERROR, QUARANTINED, SUSPENDED, COMPLETED. Illegal transitions raise; each transition is logged and stored in `session_events`.
- **Attendance arithmetic** (`attendance_math.py`): configurable counted window (default 5/5 min), dwell-time interval union, camera-outage "holes", teacher absence runs and >20-minute flags.
- **Camera subsystem** (`camera_manager.py`, `camera_types.py`): per-camera reader thread, exponential-backoff reconnect, frozen/black/invalid-frame detection, timeouts, test, scan, live preview, cross-platform (DirectShow on Windows), credential redaction.
- **Camera table** with types (USB, built-in, DroidCam, IP, RTSP, file, disabled), room assignment, health, last-seen, reconnect count. Existing `rooms.camera_source` values are migrated into it and kept in sync with the desktop room editor.
- **Sections / enrollment**: students receive attendance only for classes of their section (`sections`, `student_sections`, `timetable.section_id`).
- **Tables** `session_events`, `recognition_events`, `session_unmeasurable`, `teacher_flags`, `session_student_summary`, `app_settings`, `timetable_drafts`, `timetable_archive`, `schema_migrations`; versioned non-destructive migrations.
- **REST API + web UI**: authenticated API (signed bearer tokens, login throttling), consistent errors, readiness, dashboard, live sessions, sessions/attendance/teacher reports (CSV, formula-injection safe), cameras, rooms, timetable preview/publish, sections, settings. New single-page admin UI (dark/light, responsive, accessible).
- `server.py`, `doctor.py`, `setup.sh`, `setup.ps1`, `requirements-dev.txt`, `pytest.ini`, structured credential-redacting logging.
- Test suite: 100+ backend tests against real MySQL, 23 frontend unit tests, real-Chrome UI tests with axe accessibility audit, load tests.

### Changed
- Teacher presence is the whole classroom (teacher *zone* removed from the logic; the old column is ignored).
- Students' counted window now excludes the start/end margins (previously only teachers).
- Dwell-time union no longer bridges long gaps between two detections (a student seen at minute 5 and 45 used to count as present throughout).
- Camera loss, face-engine failure, DB outage and process downtime are unmeasurable time, not absence.
- Student results are written once at class end (recognition events are stored separately); in-flight "present" rows are no longer written per scan.
- `create_session` is a true idempotent create (the old `INSERT OR REPLACE` reset finished sessions to "active"); `log_attendance` is an atomic upsert that never overwrites a manual override.
- MySQL access uses a connection pool with retry on transient errors, UTC session time zone, utf8mb4.
- Timetable import is preview → validate → explicit confirm → archive-and-replace.
- Default `ALLOW_OPEN_REGISTRATION` is now `0`; admin login is refused with an empty/placeholder password.
- `requirements.txt` replaced: it was a whole-environment freeze (selenium, frida, aiogram, camoufox …); now only what the project imports.
- Old duplicate admin pages (`static/`, `frontend/admin/index.html` calling non-existent endpoints) replaced by the new UI.

### Fixed
- `INSERT OR REPLACE INTO sessions` wiping session state; duplicate attendance rows from select-then-insert races.
- Embeddings were loaded with `pickle.loads` (code execution if the DB is tampered): now a numpy-only restricted unpickler.
- `normalize_camera_source`/GUI fallback silently using camera `0`; room editor now warns when no camera is set.
- Camera URL validation rejected `rtsp://user:pass@host/…`.
- Cached frame served after a camera disconnected.
- Missing imports in `gui_app.py` (`np`, `FaceEngine`, `Dict`, `Any`, `Optional`) – crash at import on Python 3.10–3.13 and at runtime in registration; unreachable orphan code (undefined `vals`) in `timetable_ocr.py` removed.
- `python-docx` was imported but not declared.
- Import-time DB connection in the old API (`AttendanceService()` as a default argument).
- Debug `print`s in runtime modules replaced by logging.
- Schema file path fix and `ON UPDATE CASCADE` duplicate-clause syntax error (already in the working tree) retained.

### Security
- Authenticated API with signed tokens, per-client login throttling, strict CSP and security headers, no CORS by default, upload allow-list/size limit/server-chosen temp names, CSV formula-injection neutralised, credentials redacted from logs and lists, no biometric data in any API response.

### Known limitations
See README → Known limitations (no teacher re-identification/tracking; physical camera and real-face behaviour not covered by automation).
