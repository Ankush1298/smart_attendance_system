"""Persistence for the v3 tables: cameras, sections, session events, recognition events,
camera-loss intervals, teacher flags, summaries, settings, timetable drafts.

All datetimes passed in/out are *naive UTC* (see backend.core.clock). Every write here is
idempotent so a repeated scheduler cycle or a retried request cannot corrupt attendance.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import mysql.connector

from backend.core.camera_types import infer_camera_type, normalize_source, validate_spec
from backend.core.db import DAY_MAP, DatabaseManager, TRANSIENT_ERRNOS, normalize_day
from backend.core.clock import parse_hhmm, utcnow_naive
from backend.core.policy import POLICY_KEYS, Policy, build_policy

log = logging.getLogger("smart_attendance.store")


class StoreError(Exception):
    """A request that is invalid for the current data (maps to HTTP 409/422 in the API)."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


class Store:
    def __init__(self, db: DatabaseManager):
        self.db = db

    # -- helpers ---------------------------------------------------------------------------
    def _retry(self, fn: Callable[[], Any], attempts: int = 3) -> Any:
        """Run a DB operation, retrying transient MySQL errors with backoff."""
        delay = 0.3
        for i in range(1, attempts + 1):
            try:
                return fn()
            except mysql.connector.Error as exc:
                if getattr(exc, "errno", None) not in TRANSIENT_ERRNOS or i == attempts:
                    raise
                log.warning("transient MySQL error (%s), retry %d/%d", exc, i, attempts)
                time.sleep(delay)
                delay *= 2

    def _q(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        def run():
            with self.db._conn() as con:
                return _rows(con.execute(sql, params))
        return self._retry(run)

    def _x(self, sql: str, params: Sequence[Any] = ()) -> int:
        def run():
            with self.db._conn() as con:
                return con.execute(sql, params).rowcount
        return self._retry(run)

    # -- settings --------------------------------------------------------------------------
    def get_policy(self) -> Policy:
        rows = self._q("SELECT setting_key, setting_value FROM app_settings")
        return build_policy({r["setting_key"]: r["setting_value"] for r in rows})

    def set_policy_values(self, values: Dict[str, Any]) -> Policy:
        unknown = set(values) - set(POLICY_KEYS)
        if unknown:
            raise StoreError(f"Unknown setting(s): {', '.join(sorted(unknown))}", "unknown_setting")
        current = {r["setting_key"]: r["setting_value"] for r in self._q("SELECT setting_key, setting_value FROM app_settings")}
        try:
            policy = build_policy({**current, **{k: v for k, v in values.items()}})   # validates
        except (ValueError, TypeError) as exc:
            raise StoreError(str(exc), "invalid_setting")
        with self.db._conn() as con:
            for k, v in values.items():
                con.execute("INSERT INTO app_settings (setting_key, setting_value) VALUES (%s,%s) "
                            "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)", (k, str(v)))
        return policy

    # -- cameras ---------------------------------------------------------------------------
    CAMERA_COLS = "camera_id, name, camera_type, source, room_id, enabled, status, last_ok_at, last_error, reconnect_count"

    def list_cameras(self, room_id: Optional[str] = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql, params = f"SELECT {self.CAMERA_COLS} FROM cameras WHERE 1=1", []
        if room_id is not None:
            sql += " AND room_id=%s"; params.append(room_id)
        if enabled_only:
            sql += " AND enabled=1 AND camera_type<>'none'"
        return self._q(sql + " ORDER BY room_id, camera_id", params)

    def get_camera(self, camera_id: int) -> Optional[Dict[str, Any]]:
        rows = self._q(f"SELECT {self.CAMERA_COLS} FROM cameras WHERE camera_id=%s", (camera_id,))
        return rows[0] if rows else None

    def _check_camera_fields(self, name: str, camera_type: str, source: str, room_id: Optional[str]) -> None:
        if not str(name or "").strip():
            raise StoreError("Camera name is required.", "invalid_camera")
        ok, err = validate_spec(camera_type, source)
        if not ok:
            raise StoreError(err, "invalid_camera")
        if room_id:
            if not self._q("SELECT 1 x FROM rooms WHERE room_id=%s", (room_id,)):
                raise StoreError(f"Room '{room_id}' does not exist.", "unknown_room")

    def create_camera(self, name: str, camera_type: str, source: str, room_id: Optional[str], enabled: bool = True) -> int:
        self._check_camera_fields(name, camera_type, source, room_id)
        try:
            def run():
                with self.db._conn() as con:
                    return con.execute("INSERT INTO cameras (name, camera_type, source, room_id, enabled) VALUES (%s,%s,%s,%s,%s)",
                                       (name.strip(), camera_type, str(source).strip(), room_id or None,
                                        1 if enabled and camera_type != "none" else 0)).lastrowid
            cid = self._retry(run)
        except mysql.connector.IntegrityError as exc:
            if exc.errno == 1062:
                raise StoreError(f"A camera named '{name}' already exists.", "duplicate")
            raise
        self._sync_room_source(room_id)
        return cid

    def update_camera(self, camera_id: int, **fields: Any) -> Dict[str, Any]:
        cur = self.get_camera(camera_id)
        if not cur:
            raise StoreError(f"Camera {camera_id} not found.", "not_found")
        new = {**cur, **{k: v for k, v in fields.items() if k in ("name", "camera_type", "source", "room_id", "enabled")}}
        self._check_camera_fields(new["name"], new["camera_type"], new["source"], new["room_id"])
        try:
            self._x("UPDATE cameras SET name=%s, camera_type=%s, source=%s, room_id=%s, enabled=%s WHERE camera_id=%s",
                    (new["name"].strip(), new["camera_type"], str(new["source"]).strip(), new["room_id"] or None,
                     1 if new["enabled"] and new["camera_type"] != "none" else 0, camera_id))
        except mysql.connector.IntegrityError as exc:
            if exc.errno == 1062:
                raise StoreError(f"A camera named '{new['name']}' already exists.", "duplicate")
            raise
        for rid in {cur["room_id"], new["room_id"]}:
            self._sync_room_source(rid)
        return self.get_camera(camera_id)  # type: ignore[return-value]

    def delete_camera(self, camera_id: int) -> bool:
        cur = self.get_camera(camera_id)
        if not cur:
            return False
        self._x("DELETE FROM cameras WHERE camera_id=%s", (camera_id,))
        self._sync_room_source(cur["room_id"])
        return True

    def _sync_room_source(self, room_id: Optional[str]) -> None:
        """Keep the legacy rooms.camera_source (used by the desktop UI) in step with cameras."""
        if not room_id:
            return
        cams = self.list_cameras(room_id, enabled_only=True)
        self._x("UPDATE rooms SET camera_source=%s WHERE room_id=%s", (cams[0]["source"] if cams else "", room_id))

    def room_cameras(self, room_id: str) -> List[Dict[str, Any]]:
        """Enabled cameras of a room. Falls back to the legacy rooms.camera_source when no
        camera rows exist (databases written by older versions)."""
        cams = self.list_cameras(room_id, enabled_only=True)
        if cams:
            return cams
        if self._q("SELECT 1 x FROM cameras WHERE room_id=%s", (room_id,)):
            return []                     # cameras exist but all are disabled -> not configured
        legacy = self._q("SELECT camera_source FROM rooms WHERE room_id=%s", (room_id,))
        src = (legacy[0]["camera_source"] if legacy else "") or ""
        if str(src).strip():
            return [{"camera_id": None, "name": f"{room_id} (legacy)", "camera_type": infer_camera_type(src),
                     "source": str(src).strip(), "room_id": room_id, "enabled": 1, "status": "UNKNOWN",
                     "last_ok_at": None, "last_error": None, "reconnect_count": 0}]
        return []

    def set_camera_health(self, camera_id: Optional[int], status: str, error: Optional[str] = None,
                          ok_at: Optional[datetime] = None, reconnected: bool = False) -> None:
        if camera_id is None:
            return
        self._x("""UPDATE cameras SET status=%s, last_error=%s,
                   last_ok_at=COALESCE(%s,last_ok_at), reconnect_count=reconnect_count+%s WHERE camera_id=%s""",
                (status, (error or None) and error[:480], ok_at, 1 if reconnected else 0, camera_id))

    # -- sections / eligibility ----------------------------------------------------------------
    def list_sections(self) -> List[Dict[str, Any]]:
        return self._q("""SELECT s.section_id, s.section_name, COUNT(ss.roll_no) AS students,
                          (SELECT COUNT(*) FROM timetable t WHERE t.section_id=s.section_id) AS timetable_slots
                          FROM sections s LEFT JOIN student_sections ss ON ss.section_id=s.section_id
                          GROUP BY s.section_id, s.section_name ORDER BY s.section_id""")

    def upsert_section(self, section_id: str, name: str) -> None:
        sid = section_id.strip()
        if not sid or not name.strip():
            raise StoreError("Section id and name are required.", "invalid_section")
        self._x("INSERT INTO sections (section_id, section_name) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE section_name=VALUES(section_name)", (sid, name.strip()))

    def delete_section(self, section_id: str) -> None:
        if self._q("SELECT 1 x FROM timetable WHERE section_id=%s LIMIT 1", (section_id,)):
            raise StoreError("Section is used by timetable slots; reassign them first.", "in_use")
        self._x("DELETE FROM sections WHERE section_id=%s", (section_id,))

    def set_section_students(self, section_id: str, roll_nos: Iterable[str]) -> Dict[str, Any]:
        if not self._q("SELECT 1 x FROM sections WHERE section_id=%s", (section_id,)):
            raise StoreError(f"Section '{section_id}' does not exist.", "not_found")
        wanted = {str(r).strip() for r in roll_nos if str(r).strip()}
        valid = {r["roll_no"] for r in self._q("SELECT roll_no FROM users WHERE role='student'")}
        unknown = sorted(wanted - valid)
        with self.db._conn() as con:
            con.execute("DELETE FROM student_sections WHERE section_id=%s", (section_id,))
            for r in sorted(wanted & valid):
                con.execute("INSERT INTO student_sections (section_id, roll_no) VALUES (%s,%s)", (section_id, r))
        return {"assigned": len(wanted & valid), "unknown": unknown}

    def section_members(self, section_id: str) -> List[str]:
        return [r["roll_no"] for r in self._q("SELECT roll_no FROM student_sections WHERE section_id=%s ORDER BY roll_no", (section_id,))]

    def eligible_students(self, section_id: Optional[str]) -> Optional[set]:
        """Roll numbers allowed to receive attendance for a class.
        None => the class has no section (caller decides: skip or legacy 'everyone')."""
        if not section_id:
            return None
        return set(self.section_members(section_id))

    # -- sessions ---------------------------------------------------------------------------
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM sessions WHERE session_id=%s", (session_id,))
        return rows[0] if rows else None

    def find_session_for_slot(self, date: str, slot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The session held for a timetable slot today. Republishing the timetable renumbers slot ids,
        so after the exact id lookup we also match on date + room + subject."""
        s = self.get_session(f"SES_{slot['room_id']}_{slot['id']}_{date}")
        if s:
            return s
        rows = self._q("""SELECT * FROM sessions WHERE date=%s AND room_id=%s AND subject=%s AND (timetable_id IS NULL OR timetable_id=%s)
                          ORDER BY created_at LIMIT 1""", (date, slot["room_id"], slot["subject"], slot["id"]))
        return rows[0] if rows else None

    def find_session_by_schedule(self, date: str, room_id: str, subject: str, scheduled_start: datetime) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM sessions WHERE date=%s AND room_id=%s AND subject=%s AND scheduled_start=%s LIMIT 1",
                       (date, room_id, subject, scheduled_start))
        return rows[0] if rows else None

    def set_session_state(self, session_id: str, state: str, status: Optional[str] = None) -> None:
        self._x("UPDATE sessions SET state=%s, status=COALESCE(%s,status) WHERE session_id=%s", (state, status, session_id))

    def add_event(self, session_id: str, event_type: str, from_state: Optional[str] = None,
                  to_state: Optional[str] = None, detail: str = "", at: Optional[datetime] = None) -> None:
        self._x("INSERT INTO session_events (session_id,event_type,from_state,to_state,detail,occurred_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (session_id, event_type, from_state, to_state, (detail or "")[:480], at or utcnow_naive()))

    def list_events(self, session_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        return self._q("SELECT * FROM session_events WHERE session_id=%s ORDER BY occurred_at, event_id LIMIT %s", (session_id, limit))

    def add_recognitions(self, session_id: str, events: Iterable[Tuple[str, str, Optional[int], datetime, float]]) -> int:
        """Idempotent: (session, roll_no, detected_at) is unique; repeats only raise confidence."""
        evs = list(events)
        if not evs:
            return 0
        def run():
            with self.db._conn() as con:
                for roll_no, role, camera_id, at, conf in evs:
                    con.execute("""INSERT INTO recognition_events (session_id,roll_no,role,camera_id,detected_at,confidence)
                                   VALUES (%s,%s,%s,%s,%s,%s)
                                   ON DUPLICATE KEY UPDATE confidence=GREATEST(COALESCE(confidence,0),VALUES(confidence))""",
                                (session_id, roll_no, role, camera_id, at, float(conf)))
            return len(evs)
        return self._retry(run)

    def load_recognitions(self, session_id: str) -> List[Dict[str, Any]]:
        return self._q("SELECT roll_no, role, camera_id, detected_at, confidence FROM recognition_events WHERE session_id=%s ORDER BY detected_at", (session_id,))

    def open_hole(self, session_id: str, started_at: datetime, reason: str) -> None:
        self._x("INSERT INTO session_unmeasurable (session_id,started_at,reason) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE reason=VALUES(reason)", (session_id, started_at, reason[:250]))

    def close_hole(self, session_id: str, started_at: datetime, ended_at: datetime) -> None:
        self._x("UPDATE session_unmeasurable SET ended_at=%s WHERE session_id=%s AND started_at=%s",
                (ended_at, session_id, started_at))

    def load_holes(self, session_id: str) -> List[Dict[str, Any]]:
        return self._q("SELECT started_at, ended_at, reason FROM session_unmeasurable WHERE session_id=%s ORDER BY started_at", (session_id,))

    def open_sessions(self) -> List[Dict[str, Any]]:
        """Sessions a crash left unfinished (resumed or finalized at startup). Legacy rows
        without scheduled_end are ignored."""
        return self._q("""SELECT * FROM sessions WHERE finalized_at IS NULL AND scheduled_end IS NOT NULL
                          AND state IS NOT NULL AND state NOT IN ('COMPLETED','SUSPENDED','QUARANTINED')""")

    def finalize_session(self, session_id: str, *, teacher: Dict[str, Any], students: List[Dict[str, Any]],
                         flags: List[Dict[str, Any]], final_state: str = "COMPLETED") -> bool:
        """Write every final result in ONE transaction. Idempotent: a session whose
        ``finalized_at`` is set is left untouched (returns False)."""
        def run():
            with self.db._conn() as con:
                row = con.execute("SELECT finalized_at FROM sessions WHERE session_id=%s FOR UPDATE", (session_id,)).fetchone()
                if row is None:
                    raise StoreError(f"Session {session_id} not found", "not_found")
                if row[0] is not None:
                    return False
                now_iso = datetime.now().isoformat(timespec="seconds")
                for s in students:
                    con.execute("""INSERT INTO session_student_summary (session_id,roll_no,present_minutes,measurable_minutes,percentage,status)
                                   VALUES (%s,%s,%s,%s,%s,%s)
                                   ON DUPLICATE KEY UPDATE present_minutes=VALUES(present_minutes),
                                   measurable_minutes=VALUES(measurable_minutes), percentage=VALUES(percentage), status=VALUES(status)""",
                                (session_id, s["roll_no"], s["present_minutes"], s["measurable_minutes"], s["percentage"], s["status"]))
                    con.execute("""INSERT INTO attendance_log (session_id,roll_no,role,timestamp,status,confidence,original_auto_status)
                                   VALUES (%s,%s,'student',%s,%s,%s,%s)
                                   ON DUPLICATE KEY UPDATE
                                     status=IF(is_override=1,status,VALUES(status)),
                                     timestamp=IF(is_override=1,timestamp,VALUES(timestamp)),
                                     confidence=IF(is_override=1,confidence,VALUES(confidence)),
                                     original_auto_status=IF(is_override=1,original_auto_status,VALUES(original_auto_status))""",
                                (session_id, s["roll_no"], now_iso, s["status"], s.get("confidence", 0.0), s["status"]))
                t = teacher
                con.execute("""INSERT INTO teacher_attendance
                    (session_id,teacher_id,date,counted_start,counted_end,first_seen,last_seen,
                     present_minutes,absent_minutes,longest_absence_minutes,status,absence_over_20m)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE counted_start=VALUES(counted_start), counted_end=VALUES(counted_end),
                      first_seen=VALUES(first_seen), last_seen=VALUES(last_seen), present_minutes=VALUES(present_minutes),
                      absent_minutes=VALUES(absent_minutes), longest_absence_minutes=VALUES(longest_absence_minutes),
                      status=VALUES(status), absence_over_20m=VALUES(absence_over_20m)""",
                            (session_id, t["teacher_id"], t["date"], t["counted_start"], t["counted_end"], t["first_seen"],
                             t["last_seen"], t["present_minutes"], t["absent_minutes"], t["longest_absence_minutes"],
                             t["status"], 1 if t["absence_over_20m"] else 0))
                legacy = {"present": "present", "partial_absent": "present"}.get(t["status"], "absent" if t["status"] == "absent" else t["status"])
                con.execute("""INSERT INTO attendance_log (session_id,roll_no,role,timestamp,status,confidence,original_auto_status)
                               VALUES (%s,%s,'teacher',%s,%s,1.0,%s)
                               ON DUPLICATE KEY UPDATE status=IF(is_override=1,status,VALUES(status)),
                                 original_auto_status=IF(is_override=1,original_auto_status,VALUES(original_auto_status))""",
                            (session_id, t["teacher_id"], now_iso, legacy, legacy))
                for f in flags:
                    con.execute("""INSERT INTO teacher_flags (session_id,teacher_id,flag_type,started_at,ended_at,minutes)
                                   VALUES (%s,%s,%s,%s,%s,%s)
                                   ON DUPLICATE KEY UPDATE ended_at=VALUES(ended_at), minutes=VALUES(minutes)""",
                                (session_id, f["teacher_id"], f["flag_type"], f["started_at"], f["ended_at"], f["minutes"]))
                status = {"COMPLETED": "completed", "SUSPENDED": "suspended"}.get(final_state, "completed")
                con.execute("UPDATE sessions SET state=%s, status=%s, finalized_at=UTC_TIMESTAMP() WHERE session_id=%s",
                            (final_state, status, session_id))
            return True
        return self._retry(run)

    def finalize_without_attendance(self, session_id: str, final_state: str, teacher_id: str, date: str,
                                    status: str = "absent") -> None:
        """Suspended / quarantined sessions: record the outcome only (no student rows)."""
        def run():
            with self.db._conn() as con:
                con.execute("UPDATE sessions SET state=%s, status=%s, finalized_at=COALESCE(finalized_at,UTC_TIMESTAMP()) WHERE session_id=%s",
                            (final_state, {"SUSPENDED": "suspended"}.get(final_state, "completed"), session_id))
                if teacher_id:
                    con.execute("""INSERT INTO attendance_log (session_id,roll_no,role,timestamp,status,confidence,original_auto_status)
                                   VALUES (%s,%s,'teacher',%s,%s,1.0,%s)
                                   ON DUPLICATE KEY UPDATE status=IF(is_override=1,status,VALUES(status))""",
                                (session_id, teacher_id, datetime.now().isoformat(timespec="seconds"), status, status))
        self._retry(run)

    # -- queries for the API ----------------------------------------------------------------------
    def list_sessions(self, date: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql = """SELECT s.session_id, s.date, s.subject, s.room_id, s.teacher_id, s.section_id, s.state, s.status,
                        s.counted_start, s.counted_end, s.finalized_at, t.start_time, t.end_time,
                        COALESCE(u.name, s.teacher_id) AS teacher_name,
                        (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='present') AS n_present,
                        (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='warning') AS n_warning,
                        (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='absent') AS n_absent,
                        (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='unmeasurable') AS n_unmeasurable
                 FROM sessions s LEFT JOIN timetable t ON t.id=s.timetable_id
                 LEFT JOIN users u ON u.roll_no=s.teacher_id WHERE 1=1"""
        params: List[Any] = []
        if date:
            sql += " AND s.date=%s"; params.append(date)
        sql += " ORDER BY s.date DESC, t.start_time DESC, s.session_id LIMIT %s"
        params.append(limit)
        return self._q(sql, params)

    def session_students(self, session_id: str) -> List[Dict[str, Any]]:
        return self._q("""SELECT ss.roll_no, COALESCE(u.name, ss.roll_no) AS name, ss.present_minutes, ss.measurable_minutes,
                                 ss.percentage, COALESCE(a.status, ss.status) AS status, COALESCE(a.is_override,0) AS is_override
                          FROM session_student_summary ss LEFT JOIN users u ON u.roll_no=ss.roll_no
                          LEFT JOIN attendance_log a ON a.session_id=ss.session_id AND a.roll_no=ss.roll_no
                          WHERE ss.session_id=%s ORDER BY ss.roll_no""", (session_id,))

    def teacher_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM teacher_attendance WHERE session_id=%s", (session_id,))
        return rows[0] if rows else None

    def attendance_rows(self, date: Optional[str] = None, session_id: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        sql = """SELECT a.log_id, a.session_id, COALESCE(s.date, DATE(a.timestamp)) AS date, s.subject, s.room_id,
                        a.roll_no, COALESCE(u.name,a.roll_no) AS name, a.role, a.status, a.confidence,
                        a.is_override, a.timestamp
                 FROM attendance_log a LEFT JOIN sessions s ON s.session_id=a.session_id
                 LEFT JOIN users u ON u.roll_no=a.roll_no WHERE 1=1"""
        params: List[Any] = []
        if date:
            sql += " AND s.date=%s"; params.append(date)
        if session_id:
            sql += " AND a.session_id=%s"; params.append(session_id)
        sql += " ORDER BY a.log_id DESC LIMIT %s"
        params.append(limit)
        return self._q(sql, params)

    def teacher_flags(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._q("""SELECT f.*, COALESCE(u.name,f.teacher_id) AS teacher_name, s.subject, s.room_id, s.date
                          FROM teacher_flags f LEFT JOIN users u ON u.roll_no=f.teacher_id
                          LEFT JOIN sessions s ON s.session_id=f.session_id ORDER BY f.started_at DESC LIMIT %s""", (limit,))

    def teacher_report(self, limit: int = 500) -> List[Dict[str, Any]]:
        return self._q("""SELECT ta.date, ta.session_id, COALESCE(t.subject,s.subject,'General Class') AS subject,
                                 COALESCE(t.start_time,'-') AS start_time, COALESCE(t.end_time,'-') AS end_time,
                                 COALESCE(r.room_name,s.room_id,'-') AS room, ta.teacher_id,
                                 COALESCE(u.name,ta.teacher_id) AS teacher_name, ta.present_minutes, ta.absent_minutes,
                                 ta.longest_absence_minutes, ta.status, ta.absence_over_20m, ta.first_seen, ta.last_seen
                          FROM teacher_attendance ta LEFT JOIN sessions s ON s.session_id=ta.session_id
                          LEFT JOIN timetable t ON s.timetable_id=t.id LEFT JOIN rooms r ON r.room_id=COALESCE(t.room_id,s.room_id)
                          LEFT JOIN users u ON ta.teacher_id=u.roll_no
                          ORDER BY ta.date DESC, ta.first_seen DESC LIMIT %s""", (limit,))

    def recent_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._q("""SELECT e.event_id, e.session_id, e.event_type, e.from_state, e.to_state, e.detail, e.occurred_at,
                                 s.subject, s.room_id FROM session_events e LEFT JOIN sessions s ON s.session_id=e.session_id
                          ORDER BY e.event_id DESC LIMIT %s""", (limit,))

    def users_meta(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """Users WITHOUT biometric data."""
        sql = """SELECT u.roll_no, u.name, u.role, u.registered_at, u.embedding_model, (u.embedding IS NOT NULL) AS face_registered,
                        (SELECT GROUP_CONCAT(ss.section_id ORDER BY ss.section_id) FROM student_sections ss WHERE ss.roll_no=u.roll_no) AS sections
                 FROM users u"""
        params: List[Any] = []
        if role:
            sql += " WHERE u.role=%s"; params.append(role)
        return self._q(sql + " ORDER BY u.role, u.roll_no", params)

    def rooms_overview(self) -> List[Dict[str, Any]]:
        return self._q("""SELECT r.room_id, r.room_name, r.camera_source,
                          (SELECT COUNT(*) FROM cameras c WHERE c.room_id=r.room_id) AS cameras,
                          (SELECT COUNT(*) FROM cameras c WHERE c.room_id=r.room_id AND c.enabled=1 AND c.status='ONLINE') AS cameras_online,
                          (SELECT COUNT(*) FROM timetable t WHERE t.room_id=r.room_id) AS slots
                          FROM rooms r ORDER BY r.room_id""")

    def save_room(self, room_id: str, name: str) -> None:
        rid = room_id.strip()
        if not rid or not name.strip():
            raise StoreError("Room id and name are required.", "invalid_room")
        self._x("INSERT INTO rooms (room_id, room_name, camera_source) VALUES (%s,%s,'') "
                "ON DUPLICATE KEY UPDATE room_name=VALUES(room_name)", (rid, name.strip()))

    def delete_room(self, room_id: str) -> None:
        if self._q("SELECT 1 x FROM timetable WHERE room_id=%s LIMIT 1", (room_id,)):
            raise StoreError("Room is used by timetable slots; remove or move them first.", "in_use")
        self._x("DELETE FROM rooms WHERE room_id=%s", (room_id,))

    def timetable_rows(self) -> List[Dict[str, Any]]:
        return self._q("""SELECT t.id, t.day_of_week, t.start_time, t.end_time, t.subject, t.teacher_id, t.room_id, t.section_id,
                                 COALESCE(u.name,t.teacher_id) AS teacher_name
                          FROM timetable t LEFT JOIN users u ON u.roll_no=t.teacher_id
                          ORDER BY FIELD(t.day_of_week,'monday','tuesday','wednesday','thursday','friday','saturday','sunday'), t.start_time, t.id""")

    def set_slot_section(self, timetable_id: int, section_id: Optional[str]) -> None:
        if section_id and not self._q("SELECT 1 x FROM sections WHERE section_id=%s", (section_id,)):
            raise StoreError(f"Section '{section_id}' does not exist.", "unknown_section")
        if not self._x("UPDATE timetable SET section_id=%s WHERE id=%s", (section_id or None, timetable_id)) \
                and not self._q("SELECT 1 x FROM timetable WHERE id=%s", (timetable_id,)):
            raise StoreError("Timetable slot not found.", "not_found")

    # -- timetable draft / validate / publish ----------------------------------------------------
    def validate_timetable_rows(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """rows: dicts with day,start,end,subject,teacher,room. Returns normalised rows + issues."""
        users = {r["roll_no"].casefold(): r for r in self._q("SELECT roll_no, name, role FROM users")}
        by_name = {r["name"].strip().casefold(): r for r in users.values()}
        known_rooms = {r["room_id"] for r in self._q("SELECT room_id FROM rooms")}
        out, issues = [], []
        for i, r in enumerate(rows, start=1):
            day = normalize_day(r.get("day", ""))
            st, et = parse_hhmm(r.get("start", "")), parse_hhmm(r.get("end", ""))
            subject, teacher, room = (str(r.get(k, "") or "").strip() for k in ("subject", "teacher", "room"))
            row_issues = []
            if day not in DAY_MAP.values():
                row_issues.append(("error", f"unrecognised day '{r.get('day')}'"))
            if not st or not et:
                row_issues.append(("error", "start/end time could not be read"))
            elif (et[0], et[1]) <= (st[0], st[1]):
                row_issues.append(("error", "end time must be after start time"))
            if not subject:
                row_issues.append(("error", "subject missing"))
            if not teacher:
                row_issues.append(("error", "teacher missing"))
            elif teacher.casefold() not in users and teacher.casefold() not in by_name:
                row_issues.append(("warning", f"teacher '{teacher}' is not registered yet"))
            elif (users.get(teacher.casefold()) or by_name.get(teacher.casefold()))["role"] != "teacher":
                row_issues.append(("error", f"'{teacher}' is registered but is not a teacher"))
            if not room:
                row_issues.append(("error", "room missing"))
            elif room not in known_rooms:
                row_issues.append(("warning", f"room '{room}' will be created (no camera yet)"))
            for sev, msg in row_issues:
                issues.append({"row": i, "severity": sev, "message": msg})
            if not any(s == "error" for s, _ in row_issues):
                out.append({"day": day, "start": f"{st[0]:02d}:{st[1]:02d}", "end": f"{et[0]:02d}:{et[1]:02d}",
                            "subject": subject, "teacher": teacher, "room": room})
        # same-room overlaps are errors for the later slot
        seen: Dict[Tuple[str, str], List[Tuple[str, str, int]]] = {}
        for idx, r in enumerate(out, start=1):
            for (s, e, j) in seen.get((r["day"], r["room"]), []):
                if r["start"] < e and s < r["end"]:
                    issues.append({"row": idx, "severity": "error",
                                   "message": f"{r['room']} {r['day']} {r['start']}-{r['end']} overlaps another slot ({s}-{e})"})
            seen.setdefault((r["day"], r["room"]), []).append((r["start"], r["end"], idx))
        errors = sum(1 for x in issues if x["severity"] == "error")
        return {"rows": out, "issues": issues, "errors": errors, "warnings": len(issues) - errors}

    def create_draft(self, filename: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = self.validate_timetable_rows(rows)
        draft_id = uuid.uuid4().hex
        self._x("INSERT INTO timetable_drafts (draft_id, filename, rows_json, issues_json) VALUES (%s,%s,%s,%s)",
                (draft_id, (filename or "")[:250], json.dumps(result["rows"]), json.dumps(result["issues"])))
        return {"draft_id": draft_id, **result}

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM timetable_drafts WHERE draft_id=%s", (draft_id,))
        if not rows:
            return None
        d = rows[0]
        issues = json.loads(d["issues_json"])
        errors = sum(1 for x in issues if x["severity"] == "error")
        return {"draft_id": d["draft_id"], "filename": d["filename"], "status": d["status"], "rows": json.loads(d["rows_json"]),
                "issues": issues, "errors": errors, "warnings": len(issues) - errors}

    def publish_draft(self, draft_id: str) -> Dict[str, Any]:
        """Replace the published timetable with the draft in ONE transaction, archiving the old
        one first. Existing section assignments are carried over for unchanged slots."""
        d = self.get_draft(draft_id)
        if not d:
            raise StoreError("Draft not found.", "not_found")
        if d["status"] != "draft":
            raise StoreError(f"Draft was already {d['status']}.", "conflict")
        if d["errors"]:
            raise StoreError(f"Draft has {d['errors']} error(s); fix the file and upload again.", "has_errors")
        if not d["rows"]:
            raise StoreError("Draft contains no valid rows.", "empty")

        def run():
            with self.db._conn() as con:
                old = _rows(con.execute("SELECT * FROM timetable"))
                con.execute("INSERT INTO timetable_archive (reason, data_json) VALUES (%s,%s)",
                            (f"replaced by draft {draft_id}", json.dumps(old, default=str)))
                carry = {(o["day_of_week"], o["start_time"], o["end_time"], o["room_id"], o["subject"]): o["section_id"] for o in old}
                for room in sorted({r["room"] for r in d["rows"]}):
                    con.execute("INSERT INTO rooms (room_id, room_name, camera_source) VALUES (%s,%s,'') "
                                "ON DUPLICATE KEY UPDATE room_id=room_id", (room, room))
                # keep history: sessions lose their link to the slot but keep room/teacher/subject columns
                con.execute("UPDATE sessions SET timetable_id=NULL WHERE timetable_id IS NOT NULL")
                con.execute("DELETE FROM timetable")
                for r in d["rows"]:
                    sec = carry.get((r["day"], r["start"], r["end"], r["room"], r["subject"]))
                    con.execute("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id,section_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                                (r["day"], r["start"], r["end"], r["subject"], r["teacher"], r["room"], sec))
                con.execute("UPDATE timetable_drafts SET status='published' WHERE draft_id=%s", (draft_id,))
                return len(d["rows"]), len(old)
        n, archived = self._retry(run)
        return {"published": n, "archived_previous": archived}

    def discard_draft(self, draft_id: str) -> None:
        self._x("UPDATE timetable_drafts SET status='discarded' WHERE draft_id=%s AND status='draft'", (draft_id,))

    # -- readiness / dashboard data ----------------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        q = lambda sql: self._q(sql)[0]["n"]
        return {
            "students": q("SELECT COUNT(*) n FROM users WHERE role='student' AND embedding IS NOT NULL"),
            "teachers": q("SELECT COUNT(*) n FROM users WHERE role='teacher' AND embedding IS NOT NULL"),
            "rooms": q("SELECT COUNT(*) n FROM rooms"),
            "timetable": q("SELECT COUNT(*) n FROM timetable"),
            "cameras": q("SELECT COUNT(*) n FROM cameras"),
            "cameras_enabled": q("SELECT COUNT(*) n FROM cameras WHERE enabled=1 AND camera_type<>'none'"),
            "sections": q("SELECT COUNT(*) n FROM sections"),
            "slots_without_section": q("SELECT COUNT(*) n FROM timetable WHERE section_id IS NULL OR section_id=''"),
            "slots_without_camera": q("""SELECT COUNT(*) n FROM timetable t WHERE NOT EXISTS
                (SELECT 1 FROM cameras c WHERE c.room_id=t.room_id AND c.enabled=1 AND c.camera_type<>'none')
                AND NOT EXISTS (SELECT 1 FROM rooms r WHERE r.room_id=t.room_id AND TRIM(COALESCE(r.camera_source,''))<>'')"""),
            "slots_teacher_unregistered": q("""SELECT COUNT(*) n FROM timetable t WHERE NOT EXISTS
                (SELECT 1 FROM users u WHERE u.role='teacher' AND u.embedding IS NOT NULL AND (u.roll_no=t.teacher_id OR LOWER(u.name)=LOWER(t.teacher_id)))"""),
        }
