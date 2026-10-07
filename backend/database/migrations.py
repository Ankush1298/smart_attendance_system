"""Versioned, idempotent, backward-compatible schema migrations.

``database/schema_mysql.sql`` creates missing tables (CREATE IF NOT EXISTS); this module
adds columns/indexes to tables that already exist in older databases, backfills data
and records each applied step in ``schema_migrations``. Nothing here deletes data.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Tuple

log = logging.getLogger("smart_attendance.migrations")


def _has_column(con, table: str, column: str) -> bool:
    return con.execute("""SELECT 1 FROM information_schema.COLUMNS
                          WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""",
                       (table, column)).fetchone() is not None


def _has_index(con, table: str, index: str) -> bool:
    return con.execute("""SELECT 1 FROM information_schema.STATISTICS
                          WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                       (table, index)).fetchone() is not None


def _add_column(con, table: str, column: str, ddl: str) -> None:
    if not _has_column(con, table, column):
        con.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")
        log.info("added column %s.%s", table, column)


def _add_index(con, table: str, index: str, ddl: str) -> None:
    if not _has_index(con, table, index):
        con.execute(f"ALTER TABLE `{table}` ADD {ddl}")
        log.info("added index %s.%s", table, index)


def _add_unique_if_clean(con, table: str, index: str, cols: str, dup_sql: str) -> None:
    """Add a UNIQUE key only when existing rows do not already violate it (never delete data)."""
    if _has_index(con, table, index):
        return
    if con.execute(dup_sql).fetchone():
        log.warning("existing duplicate rows in %s; unique index %s NOT created (resolve duplicates, restart)", table, index)
        return
    con.execute(f"ALTER TABLE `{table}` ADD UNIQUE KEY `{index}` ({cols})")
    log.info("added unique index %s.%s", table, index)


def _m3_sections_cameras_events(con) -> None:
    from backend.core.camera_types import infer_camera_type

    for col, ddl in [("room_id", "VARCHAR(100) NULL"), ("teacher_id", "VARCHAR(100) NULL"),
                     ("section_id", "VARCHAR(100) NULL"), ("state", "VARCHAR(30) NULL"),
                     ("counted_start", "DATETIME NULL"), ("counted_end", "DATETIME NULL"),
                     ("scheduled_start", "DATETIME NULL"), ("scheduled_end", "DATETIME NULL"),
                     ("finalized_at", "DATETIME NULL"),
                     ("created_at", "TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP")]:
        _add_column(con, "sessions", col, ddl)
    _add_index(con, "sessions", "idx_sessions_room_date", "INDEX idx_sessions_room_date (room_id, date)")
    _add_column(con, "timetable", "section_id", "VARCHAR(100) NULL")
    _add_index(con, "timetable", "idx_timetable_section", "INDEX idx_timetable_section (section_id)")
    _add_column(con, "users", "embedding_model", "VARCHAR(50) NULL")

    _add_unique_if_clean(con, "attendance_log", "uq_attendance_session_roll", "session_id, roll_no",
                         "SELECT 1 FROM attendance_log WHERE session_id IS NOT NULL AND roll_no IS NOT NULL "
                         "GROUP BY session_id, roll_no HAVING COUNT(*)>1 LIMIT 1")
    _add_unique_if_clean(con, "sessions", "uq_sessions_timetable_date", "timetable_id, date",
                         "SELECT 1 FROM sessions WHERE timetable_id IS NOT NULL GROUP BY timetable_id, date "
                         "HAVING COUNT(*)>1 LIMIT 1")

    # Backfill denormalised session info and state from existing data.
    con.execute("""UPDATE sessions s JOIN timetable t ON s.timetable_id=t.id
                   SET s.room_id=COALESCE(s.room_id,t.room_id), s.teacher_id=COALESCE(s.teacher_id,t.teacher_id),
                       s.section_id=COALESCE(s.section_id,t.section_id)""")
    con.execute("""UPDATE sessions SET state=CASE status WHEN 'completed' THEN 'COMPLETED'
                   WHEN 'suspended' THEN 'SUSPENDED' ELSE 'ACTIVE' END WHERE state IS NULL""")

    # Cameras: one row per room that already has a camera source in the legacy rooms table.
    rooms = con.execute("""SELECT room_id, camera_source FROM rooms
                           WHERE camera_source IS NOT NULL AND TRIM(camera_source)<>''""").fetchall()
    for room_id, source in [(r[0], r[1]) for r in rooms]:
        if con.execute("SELECT 1 FROM cameras WHERE room_id=%s LIMIT 1", (room_id,)).fetchone():
            continue
        name = f"{room_id} camera"
        con.execute("INSERT INTO cameras (name, camera_type, source, room_id, enabled) VALUES (%s,%s,%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE camera_id=camera_id", (name, infer_camera_type(source), source.strip(), room_id))


def _m4_account_roles(con) -> None:
    """Credentials can now belong to hod / admin accounts as well (was ENUM student|teacher|admin)."""
    row = con.execute("""SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                         AND TABLE_NAME='authorized_credentials' AND COLUMN_NAME='role'""").fetchone()
    if row and str(row[0]).lower() == "enum":
        con.execute("ALTER TABLE authorized_credentials MODIFY role VARCHAR(20) NOT NULL")


def _m5_optional_face(con) -> None:
    """A person can exist without a face (after an admin resets it, or before they register)."""
    row = con.execute("""SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                         AND TABLE_NAME='users' AND COLUMN_NAME='embedding'""").fetchone()
    if row and str(row[0]).upper() == "NO":
        con.execute("ALTER TABLE users MODIFY embedding LONGBLOB NULL")


MIGRATIONS: List[Tuple[int, str, Callable]] = [
    (3, "sections_cameras_events", _m3_sections_cameras_events),
    (4, "account_roles", _m4_account_roles),
    (5, "optional_face", _m5_optional_face),
]


def run_migrations(connect: Callable) -> List[int]:
    """Apply pending migrations in order. Each runs in its own transaction-ish step
    (DDL auto-commits in MySQL, so every step is written to be re-runnable)."""
    applied_now: List[int] = []
    con = connect()
    try:
        done = {r[0] for r in con.execute("SELECT version FROM schema_migrations").fetchall()}
        for version, name, fn in MIGRATIONS:
            if version in done:
                continue
            log.info("applying migration %d (%s)", version, name)
            fn(con)
            con.execute("INSERT INTO schema_migrations (version, name) VALUES (%s,%s)", (version, name))
            con.commit()
            applied_now.append(version)
    finally:
        con.close()
    return applied_now
