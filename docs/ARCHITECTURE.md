# Architecture

## Runtime flow

```text
Desktop UI / Registration Portal / REST API
                    |
                    v
              Service Layer
                    |
                    v
             Repository Layer
                    |
                    v
             MySQL Database
```

The existing attendance engine remains in `backend/core/` because it contains the camera, face recognition, timetable and session orchestration logic that the desktop application depends on.

## Backend layers

### `backend/api`
FastAPI HTTP boundary. It should contain request/response handling only.

### `backend/services`
Business operations. Services coordinate repositories and domain operations.

### `backend/repositories`
Database-facing operations for API features. SQL belongs here rather than in HTTP handlers.

### `backend/database`
MySQL connection infrastructure.

### `backend/models`
Shared application entities/data structures.

### `backend/core`
The existing working attendance engine:

- face recognition
- attendance session orchestration
- timetable parsing/OCR
- desktop UI
- registration support
- MySQL-backed `DatabaseManager`

This separation keeps the proven camera/attendance code intact while providing a clean backend boundary for future development.

## Data ownership

MySQL is the source of truth.

| Data | Storage |
|---|---|
| Students/faculty | MySQL `users` |
| Face embeddings | MySQL `users` |
| Credentials | MySQL `authorized_credentials` |
| Rooms/cameras | MySQL `rooms` |
| Timetable | MySQL `timetable` |
| Sessions | MySQL `sessions` |
| Attendance | MySQL `attendance_log` |
| Overrides | MySQL `attendance_overrides` |
| Grace records | MySQL `grace_records` |
| Scan history | MySQL `scan_logs` |
| Reports | MySQL `attendance_report` |

No SQLite database or JSON timetable cache is part of the final application.

## Development ownership

A practical two-person split is:

- Developer A: `frontend/`, `backend/api/`, UI/API integration
- Developer B: `backend/core/`, `backend/services/`, `backend/repositories/`, `database/`

Coordinate shared schema changes through pull requests and update `database/schema_mysql.sql` whenever the database contract changes.

## Attendance engine (v3)

```
server.py ─ uvicorn ─ FastAPI (backend/api) ─ Store (backend/core/store.py) ─┐
                │                                                           ├─ MySQL (pooled, retrying, UTC session)
                └ SessionLogic (scheduler thread + room workers) ───────────┘
                     ├ CameraManager  → one reader thread per in-use camera (reconnect/backoff, frozen/black-frame detection)
                     ├ Recognizer     → FaceRecognizer (InsightFace) - replaceable interface
                     ├ SessionStateMachine (explicit, validated transitions, persisted in session_events)
                     └ attendance_math (pure functions: counted window, dwell intervals, camera holes, absence runs)
```

* `backend/core/attendance_math.py` – pure arithmetic, fully unit-tested.
* `backend/core/session_state.py` – states and legal transitions.
* `backend/core/session_logic.py` – scheduling, teacher authorization, scan cycle, outage handling, finalization.
* `backend/core/store.py` – every v3 table (cameras, sections, events, holes, flags, summaries, drafts) and the transactional finalization.
* `backend/database/migrations.py` – versioned, idempotent, non-destructive migrations (`schema_migrations`).
* `frontend/admin/` – dependency-free ES-module single-page app (no build step), served by the API.

Persistence rules: all new timestamps are UTC `DATETIME`; sessions, recognition events and attendance rows are idempotent (unique keys + upserts); finalization of a session is one transaction guarded by `finalized_at`.

The desktop UI, KYC registration portal and Flask portal keep using `DatabaseManager` and the same schema.
