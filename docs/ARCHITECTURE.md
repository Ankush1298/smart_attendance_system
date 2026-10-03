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

## Dwell-time attendance and teacher presence

Active classroom sessions use a 60-second recognition heartbeat. Student attendance is no longer calculated as `number_of_samples * 5 minutes`. Each successful student detection contributes a short presence interval around its timestamp, and the final attendance duration is the union of those intervals. This prevents a 1-minute late arrival or 1-minute early departure from losing an entire five-minute block.

Teacher attendance is a separate rule: for a 50-minute lecture, only the middle 40 minutes (`start + 5 min` through `end - 5 min`) count. The scheduled teacher must be detected in the room's configured normalized front/whiteboard zone. The teacher can authorize the session before the five-minute counted window; continuous recognition is not required for authorization, but the 60-second heartbeat tracks later presence. A continuous absence gap greater than 20 minutes is flagged for HOD/management reporting.

Each room can configure the teacher zone as normalized `x1,y1,x2,y2` values in the Cameras & Rooms screen. The safe default is the full frame so existing room/camera configurations keep working until a front/whiteboard zone is configured.
