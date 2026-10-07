"""Read models for the student and teacher portals, plus login-account management.

A student sees only their own results; a teacher only their own classes. Every query here is
parameterised by the *authenticated* id (never by an id supplied by the caller).
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.core.db import DatabaseManager, normalize_day
from backend.core.store import Store, StoreError

FINAL_STATES = ("COMPLETED", "SUSPENDED", "QUARANTINED")
PASSWORD_MIN = {"student": 6, "teacher": 6, "hod": 10, "admin": 10}
# who may create which kind of login
CREATABLE = {"superadmin": {"admin", "hod", "teacher", "student"}, "admin": {"hod", "teacher", "student"}}


def _pct(n: float, d: float) -> Optional[float]:
    return round(n / d * 100.0, 1) if d else None


class Portal:
    def __init__(self, store: Store, db: DatabaseManager):
        self.store, self.db = store, db

    # ------------------------------------------------------------------ students
    def student_sections(self, roll_no: str) -> List[str]:
        return [r["section_id"] for r in self.store._q("SELECT section_id FROM student_sections WHERE roll_no=%s", (roll_no,))]

    def has_face(self, roll_no: str) -> bool:
        return bool(self.store._q("SELECT 1 x FROM users WHERE roll_no=%s AND embedding IS NOT NULL", (roll_no,)))

    def student_summary(self, roll_no: str) -> Dict[str, Any]:
        """Per-class (subject) attendance. 'Attended' = present or warning (60-80 % of the class);
        classes whose camera data was unmeasurable are not counted for or against the student."""
        rows = self.store._q("""
            SELECT COALESCE(s.subject,'Class') AS subject, s.section_id,
                   COUNT(*) AS held,
                   SUM(COALESCE(a.status, ss.status)='present') AS present,
                   SUM(COALESCE(a.status, ss.status)='warning') AS warning,
                   SUM(COALESCE(a.status, ss.status)='absent') AS absent,
                   SUM(COALESCE(a.status, ss.status)='unmeasurable') AS unmeasurable,
                   AVG(CASE WHEN ss.status<>'unmeasurable' THEN ss.percentage END) AS avg_presence,
                   MAX(s.date) AS last_date
            FROM session_student_summary ss JOIN sessions s ON s.session_id=ss.session_id
            LEFT JOIN attendance_log a ON a.session_id=ss.session_id AND a.roll_no=ss.roll_no
            WHERE ss.roll_no=%s GROUP BY COALESCE(s.subject,'Class'), s.section_id ORDER BY subject""", (roll_no,))
        classes = []
        tot_att = tot_cnt = 0
        for r in rows:
            present, warning, absent, unm = (int(r[k] or 0) for k in ("present", "warning", "absent", "unmeasurable"))
            countable = present + warning + absent
            classes.append({"subject": r["subject"], "section_id": r["section_id"], "classes_held": int(r["held"]),
                            "present": present, "warning": warning, "absent": absent, "unmeasurable": unm,
                            "attendance_percent": _pct(present + warning, countable),
                            "average_presence_percent": None if r["avg_presence"] is None else round(float(r["avg_presence"]), 1),
                            "last_class": r["last_date"]})
            tot_att += present + warning
            tot_cnt += countable
        return {"classes": classes, "overall_percent": _pct(tot_att, tot_cnt), "classes_counted": tot_cnt}

    def student_history(self, roll_no: str, limit: int = 60) -> List[Dict[str, Any]]:
        return self.store._q("""
            SELECT s.session_id, s.date, COALESCE(s.subject,'Class') AS subject, s.room_id, t.start_time, t.end_time,
                   COALESCE(a.status, ss.status) AS status, ss.present_minutes, ss.measurable_minutes, ss.percentage,
                   COALESCE(a.is_override,0) AS is_override
            FROM session_student_summary ss JOIN sessions s ON s.session_id=ss.session_id
            LEFT JOIN timetable t ON t.id=s.timetable_id
            LEFT JOIN attendance_log a ON a.session_id=ss.session_id AND a.roll_no=ss.roll_no
            WHERE ss.roll_no=%s ORDER BY s.date DESC, t.start_time DESC LIMIT %s""", (roll_no, limit))

    def student_today(self, roll_no: str, today: str, day_name: str, now_hm: str) -> Dict[str, Any]:
        """Today's classes for the student's sections with a plain-language status for each."""
        secs = self.student_sections(roll_no)
        items: List[Dict[str, Any]] = []
        if secs:
            marks = ",".join(["%s"] * len(secs))
            slots = self.store._q(f"""SELECT id, start_time, end_time, subject, room_id, section_id FROM timetable
                                      WHERE day_of_week=%s AND section_id IN ({marks}) ORDER BY start_time""", (day_name, *secs))
            for sl in slots:
                sess = self.store.find_session_for_slot(today, sl)
                sid = sess["session_id"] if sess else f"SES_{sl['room_id']}_{sl['id']}_{today}"
                res = self.store._q("""SELECT COALESCE(a.status, ss.status) AS status, ss.percentage FROM session_student_summary ss
                                       LEFT JOIN attendance_log a ON a.session_id=ss.session_id AND a.roll_no=ss.roll_no
                                       WHERE ss.session_id=%s AND ss.roll_no=%s""", (sid, roll_no))
                state = (sess or {}).get("state")
                if res:
                    status, pct = res[0]["status"], res[0]["percentage"]
                elif state == "SUSPENDED":
                    status, pct = "cancelled", None
                elif state and state not in FINAL_STATES:
                    status, pct = "in_progress", None
                elif sess is None and now_hm >= sl["end_time"]:
                    status, pct = "not_held", None
                elif state == "COMPLETED":
                    status, pct = "not_recorded", None
                else:
                    status, pct = "upcoming", None
                items.append({**sl, "session_id": sid, "status": status, "percentage": pct, "message": self._message(sl, status, pct)})
        return {"date": today, "has_sections": bool(secs), "items": items}

    @staticmethod
    def _message(sl: Dict[str, Any], status: str, pct: Optional[float]) -> str:
        what = f"{sl['subject']} ({sl['start_time']}–{sl['end_time']})"
        return {
            "present": f"You were marked PRESENT in {what}." + (f" Attendance {pct}%." if pct is not None else ""),
            "warning": f"You were marked PRESENT WITH A WARNING in {what}: you attended only part of the class" + (f" ({pct}%)." if pct is not None else "."),
            "absent": f"You were marked ABSENT in {what}." + (f" You were present {pct}% of the class." if pct is not None else ""),
            "unmeasurable": f"{what}: attendance could not be measured (camera problem). It will not count against you.",
            "in_progress": f"{what} is in progress. Your attendance will be recorded when it ends.",
            "cancelled": f"{what} did not run (the teacher was not verified). No attendance was recorded.",
            "not_held": f"{what}: no attendance was recorded for this class.",
            "not_recorded": f"{what}: no result was recorded for you.",
            "upcoming": f"{what} has not started yet.",
        }.get(status, what)

    # ------------------------------------------------------------------ teachers
    def teacher_keys(self, roll_no: str) -> List[str]:
        rows = self.store._q("SELECT name FROM users WHERE roll_no=%s", (roll_no,))
        keys = {roll_no.casefold()}
        if rows:
            keys.add(str(rows[0]["name"]).strip().casefold())
        return sorted(keys)

    def _teacher_clause(self, roll_no: str, col: str = "t.teacher_id"):
        keys = self.teacher_keys(roll_no)
        return " OR ".join([f"LOWER({col})=%s"] * len(keys)), tuple(keys)

    def teacher_slots(self, roll_no: str) -> List[Dict[str, Any]]:
        clause, params = self._teacher_clause(roll_no)
        return self.store._q(f"""SELECT t.id, t.day_of_week, t.start_time, t.end_time, t.subject, t.room_id, t.section_id FROM timetable t
                                 WHERE {clause} ORDER BY FIELD(t.day_of_week,'monday','tuesday','wednesday','thursday','friday','saturday','sunday'), t.start_time""", params)

    def teacher_sessions(self, roll_no: str, limit: int = 60) -> List[Dict[str, Any]]:
        clause, params = self._teacher_clause(roll_no, "s.teacher_id")
        return self.store._q(f"""SELECT s.session_id, s.date, s.subject, s.room_id, s.section_id, s.state, t.start_time, t.end_time,
                (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='present') AS n_present,
                (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='warning') AS n_warning,
                (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='absent') AS n_absent,
                (SELECT COUNT(*) FROM session_student_summary x WHERE x.session_id=s.session_id AND x.status='unmeasurable') AS n_unmeasurable
                FROM sessions s LEFT JOIN timetable t ON t.id=s.timetable_id WHERE {clause}
                ORDER BY s.date DESC, t.start_time DESC LIMIT %s""", (*params, limit))

    def owns_session(self, roll_no: str, session_id: str) -> bool:
        s = self.store.get_session(session_id)
        return bool(s and str(s.get("teacher_id") or "").casefold() in self.teacher_keys(roll_no))

    def teacher_stats(self, roll_no: str) -> Dict[str, Any]:
        rows = self.store._q("SELECT status, COUNT(*) n FROM teacher_attendance WHERE teacher_id=%s GROUP BY status", (roll_no,))
        return {r["status"]: int(r["n"]) for r in rows}

    # ------------------------------------------------------------------ accounts
    def list_accounts(self, actor_role: str) -> List[Dict[str, Any]]:
        rows = self.store._q("""SELECT c.id_number, c.role, COALESCE(u.name, c.allocated_name) AS name, c.created_at,
                                       (u.embedding IS NOT NULL) AS face_registered
                                FROM authorized_credentials c LEFT JOIN users u ON u.roll_no=c.id_number ORDER BY c.role, c.id_number""")
        if actor_role != "superadmin":
            rows = [r for r in rows if r["role"] in CREATABLE["admin"]]     # admins do not see admin/superadmin accounts
        return rows

    def create_account(self, actor_role: str, id_number: str, role: str, name: str, password: Optional[str]) -> Dict[str, Any]:
        id_number, name, role = id_number.strip(), name.strip(), role.strip().lower()
        if role not in CREATABLE.get(actor_role, set()):
            raise StoreError(f"Your role cannot create '{role}' accounts.", "forbidden_role")
        if not id_number or not name:
            raise StoreError("ID and name are required.", "invalid_account")
        import os
        if id_number.casefold() == os.environ.get("ADMIN_ID", "ADMIN").strip().casefold():
            raise StoreError("That ID is reserved for the super administrator.", "duplicate")
        if self.store._q("SELECT 1 x FROM authorized_credentials WHERE id_number=%s", (id_number,)):
            raise StoreError(f"An account with ID '{id_number}' already exists.", "duplicate")
        generated = None
        if not password:
            generated = password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        if len(password) < PASSWORD_MIN[role]:
            raise StoreError(f"Passwords for {role} accounts need at least {PASSWORD_MIN[role]} characters.", "weak_password")
        self.db.set_authorized_credential(id_number, password, role, name)
        return {"id": id_number, "role": role, "name": name, "generated_password": generated}

    def _target(self, actor_role: str, id_number: str) -> Dict[str, Any]:
        rows = self.store._q("SELECT id_number, role FROM authorized_credentials WHERE id_number=%s", (id_number,))
        if not rows or rows[0]["role"] not in CREATABLE.get(actor_role, set()):
            raise StoreError("Account not found.", "not_found")
        return rows[0]

    def reset_password(self, actor_role: str, id_number: str, password: Optional[str]) -> Dict[str, Any]:
        t = self._target(actor_role, id_number)
        generated = None
        if not password:
            generated = password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        if len(password) < PASSWORD_MIN[t["role"]]:
            raise StoreError(f"Passwords for {t['role']} accounts need at least {PASSWORD_MIN[t['role']]} characters.", "weak_password")
        name = (self.store._q("SELECT allocated_name n FROM authorized_credentials WHERE id_number=%s", (id_number,)) or [{"n": ""}])[0]["n"]
        self.db.set_authorized_credential(id_number, password, t["role"], name or "")
        return {"id": id_number, "generated_password": generated}

    def delete_account(self, actor_role: str, id_number: str) -> None:
        """Removes the login only. The person's face registration and attendance history stay."""
        self._target(actor_role, id_number)
        self.store._x("DELETE FROM authorized_credentials WHERE id_number=%s", (id_number,))

    # ------------------------------------------------------------------ edit / delete registered people
    def update_user(self, roll_no: str, name: Optional[str] = None, role: Optional[str] = None, new_id: Optional[str] = None) -> Dict[str, Any]:
        """Edit a registered student/teacher: name, role and/or ID. An ID change is carried through every table
        that refers to the person (attendance, sections, login, summaries, timetable) in ONE transaction."""
        rows = self.store._q("SELECT roll_no, name, role FROM users WHERE roll_no=%s", (roll_no,))
        if not rows:
            raise StoreError("Person not found.", "not_found")
        cur = rows[0]
        name = (name if name is not None else cur["name"]).strip()
        role = (role or cur["role"]).strip().lower()
        new_id = (new_id or roll_no).strip()
        if not name:
            raise StoreError("Name cannot be empty.", "invalid_user")
        if role not in ("student", "teacher"):
            raise StoreError("Role must be student or teacher.", "invalid_user")
        if cur["role"] not in ("student", "teacher"):
            raise StoreError("Only students and teachers can be edited here.", "invalid_user")
        if new_id != roll_no:
            import os
            if self.store._q("SELECT 1 x FROM users WHERE roll_no=%s", (new_id,)) or self.store._q("SELECT 1 x FROM authorized_credentials WHERE id_number=%s", (new_id,)) \
                    or new_id.casefold() == os.environ.get("ADMIN_ID", "ADMIN").strip().casefold():
                raise StoreError(f"ID '{new_id}' is already in use.", "duplicate")

        def run():
            with self.db._conn() as con:
                con.execute("UPDATE users SET name=%s, role=%s WHERE roll_no=%s", (name, role, roll_no))
                if role != cur["role"]:
                    con.execute("DELETE FROM student_sections WHERE roll_no=%s", (roll_no,))      # a teacher belongs to no section
                    con.execute("UPDATE authorized_credentials SET role=%s WHERE id_number=%s", (role, roll_no))
                con.execute("UPDATE authorized_credentials SET allocated_name=%s WHERE id_number=%s", (name, roll_no))
                if new_id != roll_no:
                    con.execute("UPDATE users SET roll_no=%s WHERE roll_no=%s", (new_id, roll_no))   # cascades to attendance, overrides, sections
                    for sql in ("UPDATE authorized_credentials SET id_number=%s WHERE id_number=%s",
                                "UPDATE session_student_summary SET roll_no=%s WHERE roll_no=%s",
                                "UPDATE recognition_events SET roll_no=%s WHERE roll_no=%s",
                                "UPDATE grace_records SET roll_no=%s WHERE roll_no=%s",
                                "UPDATE teacher_attendance SET teacher_id=%s WHERE teacher_id=%s",
                                "UPDATE teacher_flags SET teacher_id=%s WHERE teacher_id=%s",
                                "UPDATE sessions SET teacher_id=%s WHERE teacher_id=%s",
                                "UPDATE timetable SET teacher_id=%s WHERE teacher_id=%s"):
                        con.execute(sql, (new_id, roll_no))
        self.store._retry(run)
        return {"id": new_id, "name": name, "role": role}

    def delete_user(self, roll_no: str) -> Dict[str, Any]:
        """Permanently remove a person: face data, login, section memberships AND their attendance history."""
        rows = self.store._q("SELECT roll_no, role FROM users WHERE roll_no=%s", (roll_no,))
        if not rows or rows[0]["role"] not in ("student", "teacher"):
            raise StoreError("Person not found.", "not_found")
        slots = self.store._q("SELECT COUNT(*) n FROM timetable WHERE teacher_id=%s", (roll_no,))[0]["n"] if rows[0]["role"] == "teacher" else 0

        def run():
            with self.db._conn() as con:
                for sql in ("DELETE FROM attendance_overrides WHERE roll_no=%s", "DELETE FROM attendance_log WHERE roll_no=%s",
                            "DELETE FROM grace_records WHERE roll_no=%s", "DELETE FROM session_student_summary WHERE roll_no=%s",
                            "DELETE FROM recognition_events WHERE roll_no=%s", "DELETE FROM teacher_attendance WHERE teacher_id=%s",
                            "DELETE FROM teacher_flags WHERE teacher_id=%s", "DELETE FROM authorized_credentials WHERE id_number=%s",
                            "DELETE FROM users WHERE roll_no=%s"):
                    con.execute(sql, (roll_no,))
        self.store._retry(run)
        return {"deleted": roll_no, "timetable_slots_still_naming_them": int(slots)}

    def rename_account(self, actor_role: str, id_number: str, name: str) -> None:
        self._target(actor_role, id_number)
        if not name.strip():
            raise StoreError("Name cannot be empty.", "invalid_account")
        self.store._x("UPDATE authorized_credentials SET allocated_name=%s WHERE id_number=%s", (name.strip(), id_number))

    def reset_face(self, roll_no: str) -> Dict[str, Any]:
        """Remove ONLY the stored face data so the person can register again. Login, section, attendance
        history and timetable entries are untouched; until they re-register the cameras cannot recognise them."""
        rows = self.store._q("SELECT role, (embedding IS NOT NULL) AS has_face FROM users WHERE roll_no=%s", (roll_no,))
        if not rows or rows[0]["role"] not in ("student", "teacher"):
            raise StoreError("Person not found.", "not_found")
        if not rows[0]["has_face"]:
            raise StoreError("This person has no registered face to remove.", "conflict")
        self.store._x("UPDATE users SET embedding=NULL, multi_embeddings=NULL, mesh_path=NULL, embedding_model=NULL WHERE roll_no=%s", (roll_no,))
        return {"id": roll_no, "face_registered": False}
