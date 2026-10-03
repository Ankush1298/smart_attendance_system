import base64
import hashlib
import hmac
import os
import pickle
import secrets
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

SCHEMA_VERSION = 2


class _MySQLRow(tuple):
    """Small row-compatible adapter used by the existing application code."""
    def __new__(cls, values, columns):
        obj = super().__new__(cls, values)
        obj._columns = list(columns)
        obj._map = {c: values[i] for i, c in enumerate(columns)}
        return obj
    def __getitem__(self, key):
        if isinstance(key, str):
            return self._map[key]
        return super().__getitem__(key)
    def keys(self):
        return self._columns
    def __iter__(self):
        # Keep tuple-style iteration while supporting named column access.
        return super().__iter__()


class _MySQLCursor:
    def __init__(self, cursor):
        self._cursor = cursor
    @property
    def rowcount(self):
        return self._cursor.rowcount
    @property
    def lastrowid(self):
        return self._cursor.lastrowid
    def _wrap(self, row):
        if row is None:
            return None
        cols = [d[0] for d in (self._cursor.description or [])]
        return _MySQLRow(row, cols)
    def fetchone(self):
        return self._wrap(self._cursor.fetchone())
    def fetchall(self):
        return [self._wrap(r) for r in self._cursor.fetchall()]
    def __iter__(self):
        return iter(self.fetchall())
    def close(self):
        self._cursor.close()


class _MySQLConnection:
    """MySQL compatibility adapter used by the application."""
    def __init__(self, raw):
        self.raw = raw
        self.row_factory = None
    def _sql(self, sql):
        sql = sql.replace('INSERT OR REPLACE INTO', 'INSERT INTO')
        sql = sql.replace('ORDER BY registered_at DESC', 'ORDER BY registered_at DESC')
        sql = re.sub(r'\?', '%s', sql)
        # Add a MySQL upsert clause for the application's INSERT OR REPLACE statements.
        if 'INSERT OR REPLACE' in sql:
            pass
        return sql
    def execute(self, sql, params=()):
        original = sql
        sql = self._sql(sql)
        if original.lstrip().upper().startswith('INSERT OR REPLACE INTO'):
            m = re.match(r'(?is)^\s*INSERT INTO\s+([`\w]+)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*$', sql.strip())
            if m:
                table, cols, vals = m.groups()
                names = [c.strip().strip('`') for c in cols.split(',')]
                quoted = ','.join('`'+c+'`' for c in names)
                updates = ','.join(f'`{c}`=VALUES(`{c}`)' for c in names[1:])
                sql = f'INSERT INTO {table} ({quoted}) VALUES ({vals}) ON DUPLICATE KEY UPDATE {updates}'
        cur=self.raw.cursor()
        cur.execute(sql, tuple(params))
        return _MySQLCursor(cur)
    def executemany(self, sql, seq):
        sql=self._sql(sql)
        cur=self.raw.cursor(); cur.executemany(sql, seq); return _MySQLCursor(cur)
    def executescript(self, script):
        for statement in script.split(';'):
            statement=statement.strip()
            if statement:
                self.execute(statement)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type: self.rollback()
        else: self.commit()
        self.close()


def _mysql_connection_from_env():
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError('MySQL runtime requires mysql-connector-python. Install requirements.txt first.') from exc
    host=os.getenv('MYSQL_HOST','127.0.0.1')
    port=int(os.getenv('MYSQL_PORT','3306'))
    user=os.getenv('MYSQL_USER','root')
    password=os.getenv('MYSQL_PASSWORD','')
    database=os.getenv('MYSQL_DATABASE','smart_attendance')
    try:
        raw=mysql.connector.connect(host=host,port=port,user=user,password=password,database=database,autocommit=False)
    except mysql.connector.Error as exc:
        # If the database itself has not been created, create it once using a server-level connection.
        if getattr(exc, 'errno', None) in (1049,):
            bootstrap=mysql.connector.connect(host=host,port=port,user=user,password=password,autocommit=True)
            cur=bootstrap.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cur.close(); bootstrap.close()
            raw=mysql.connector.connect(host=host,port=port,user=user,password=password,database=database,autocommit=False)
        else:
            raise
    return _MySQLConnection(raw)


def _ensure_mysql_schema():
    con=_mysql_connection_from_env()
    schema_path=Path(__file__).resolve().parents[1] / 'database' / 'schema_mysql.sql'
    script=schema_path.read_text(encoding='utf-8')
    # The schema file has a CREATE DATABASE/USE preamble; the connection is already on the target DB.
    statements=[]
    current=[]
    for line in script.splitlines():
        if line.strip().startswith('--'):
            continue
        current.append(line)
        if line.rstrip().endswith(';'):
            st='\n'.join(current).strip(); current=[]
            if st.upper().startswith('CREATE DATABASE') or st.upper().startswith('USE '):
                continue
            if st: statements.append(st)
    for st in statements:
        con.execute(st)
    con.commit(); con.close()

DAY_MAP = {
    "mo": "monday", "mon": "monday", "monday": "monday",
    "tu": "tuesday", "tue": "tuesday", "tuesday": "tuesday",
    "we": "wednesday", "wed": "wednesday", "wednesday": "wednesday",
    "th": "thursday", "thu": "thursday", "thurs": "thursday", "thursday": "thursday",
    "fr": "friday", "fri": "friday", "friday": "friday",
    "sa": "saturday", "sat": "saturday", "saturday": "saturday",
    "su": "sunday", "sun": "sunday", "sunday": "sunday",
}


def normalize_day(value: str) -> str:
    raw = str(value or "").strip().lower()
    return DAY_MAP.get(raw, raw)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _verify_password(password: str, stored: str) -> tuple[bool, bool]:
    """Return (valid, needs_upgrade). Supports legacy plaintext once for migration."""
    if not stored:
        return False, False
    if not stored.startswith("scrypt$"):
        return hmac.compare_digest(stored, password), True
    try:
        _, n, r, p, salt_b64, digest_b64 = stored.split("$", 5)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected), False
    except Exception:
        return False, False


class DatabaseManager:
    """MySQL-only persistence layer for the Smart Attendance application.

    All persistent application data lives in MySQL. The db_path argument is retained
    for backwards-compatible construction by the existing UI/registration modules,
    but it is never opened or used as a database file.
    """

    def __init__(self, db_path=None):
        self.db_path = None
        self.backend = "mysql"
        self._init_db()

    def _conn(self):
        return _mysql_connection_from_env()

    def _init_db(self) -> None:
        _ensure_mysql_schema()
        with self._conn() as con:
            self._upgrade_legacy_passwords(con)
            rows = con.execute("SELECT id, day_of_week FROM timetable").fetchall()
            for tid, day in rows:
                canonical = normalize_day(day)
                if canonical != day:
                    con.execute("UPDATE timetable SET day_of_week=%s WHERE id=%s", (canonical, tid))
    def _upgrade_legacy_passwords(self, con) -> None:
        rows = con.execute("SELECT id_number, password FROM authorized_credentials").fetchall()
        for row in rows:
            stored = row[1] or ""
            if not stored.startswith("scrypt$"):
                con.execute("UPDATE authorized_credentials SET password=? WHERE id_number=?", (_hash_password(stored), row[0]))

    # --- Credentials ---
    def set_authorized_credential(self, id_number: str, password: str, role: str, allocated_name: str = "") -> None:
        with self._conn() as con:
            con.execute("""INSERT OR REPLACE INTO authorized_credentials
                (id_number, password, role, allocated_name) VALUES (?, ?, ?, ?)""",
                (id_number.strip(), _hash_password(password.strip()), role.strip().lower(), allocated_name.strip()))

    def verify_credential(self, id_number: str, password: str, role: str) -> Dict[str, Any]:
        with self._conn() as con:
            role = role.strip().lower()
            # Bootstrap admin authentication from environment without storing the admin password in the database.
            if role == "admin":
                env_id = os.environ.get("ADMIN_ID", "ADMIN").strip()
                env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
                if env_pw and hmac.compare_digest(id_number.strip(), env_id):
                    if hmac.compare_digest(password.strip(), env_pw):
                        return {"valid": True, "name": os.environ.get("ADMIN_NAME", "Administrator")}
                    return {"valid": False, "error": "Incorrect administrator password."}
            row = con.execute("SELECT * FROM authorized_credentials WHERE id_number=? AND role=?", (id_number.strip(), role)).fetchone()
            if not row:
                total = con.execute("SELECT COUNT(*) FROM authorized_credentials WHERE role=?", (role,)).fetchone()[0]
                # Preserve the existing enrollment experience until credentials are configured for that role.
                if total == 0 and role in {"student", "teacher"} and os.environ.get("ALLOW_OPEN_REGISTRATION", "1") == "1":
                    return {"valid": True, "name": "", "open_registration": True}
                return {"valid": False, "error": f"Invalid {role.capitalize()} ID number. Please check with administrator."}
            valid, needs_upgrade = _verify_password(password.strip(), row["password"])
            if not valid:
                return {"valid": False, "error": f"Incorrect password for {role.capitalize()} ID {id_number}."}
            if needs_upgrade:
                con.execute("UPDATE authorized_credentials SET password=? WHERE id_number=?", (_hash_password(password.strip()), id_number.strip()))
            return {"valid": True, "name": row["allocated_name"] or ""}

    def get_authorized_credentials(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as con:
            if role:
                rows = con.execute("SELECT id_number, role, allocated_name, created_at FROM authorized_credentials WHERE role=? ORDER BY id_number", (role,)).fetchall()
            else:
                rows = con.execute("SELECT id_number, role, allocated_name, created_at FROM authorized_credentials ORDER BY role, id_number").fetchall()
            return [dict(r) for r in rows]

    def delete_authorized_credential(self, id_number: str) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM authorized_credentials WHERE id_number=?", (id_number,))

    # --- User Management ---
    def upsert_user(self, roll_no: str, name: str, role: str, embedding: np.ndarray,
                    multi_embeddings: Optional[List[np.ndarray]] = None, mesh_path: str = "") -> None:
        if not multi_embeddings:
            multi_embeddings = [embedding]
        blob = pickle.dumps(np.asarray(embedding, dtype=np.float32))
        multi_blob = pickle.dumps([np.asarray(e, dtype=np.float32) for e in multi_embeddings])
        with self._conn() as con:
            con.execute("""INSERT OR REPLACE INTO users
                (roll_no, name, role, embedding, multi_embeddings, mesh_path)
                VALUES (?,?,?,?,?,?)""", (roll_no.strip(), name.strip(), role.strip().lower(), blob, multi_blob, mesh_path))

    @staticmethod
    def _decode_embeddings(row) -> tuple[np.ndarray, List[np.ndarray]]:
        emb = pickle.loads(row["embedding"])
        multi = pickle.loads(row["multi_embeddings"]) if row["multi_embeddings"] else [emb]
        return emb, multi

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM users ORDER BY roll_no").fetchall()
        result = []
        for r in rows:
            emb, multi = self._decode_embeddings(r)
            result.append({"roll_no": r["roll_no"], "user_id": r["roll_no"], "name": r["name"], "role": r["role"],
                           "embedding": emb, "multi_embeddings": multi, "mesh_path": r["mesh_path"], "registered_at": r["registered_at"]})
        return result

    def get_dashboard_counts(self) -> Dict[str, int]:
        """Lightning-fast counts for dashboard cards without unpickling embeddings."""
        with self._conn() as con:
            students = con.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
            teachers = con.execute("SELECT COUNT(*) FROM users WHERE role='teacher'").fetchone()[0]
            rooms = con.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
            timetable = con.execute("SELECT COUNT(*) FROM timetable").fetchone()[0]
            return {
                "students": students,
                "teachers": teachers,
                "rooms": rooms,
                "timetable": timetable
            }

    def get_recent_users_meta(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch recent user headers without unpickling heavy embeddings."""
        with self._conn() as con:
            rows = con.execute("SELECT roll_no, name, role, registered_at FROM users ORDER BY registered_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def delete_user(self, roll_no: str) -> bool:
        roll_no_str = str(roll_no).strip()
        try:
            with self._conn() as con:
                con.execute("DELETE FROM attendance_log WHERE roll_no=?", (roll_no_str,))
                con.execute("DELETE FROM grace_records WHERE roll_no=?", (roll_no_str,))
                con.execute("DELETE FROM attendance_overrides WHERE roll_no=?", (roll_no_str,))
                cur = con.execute("DELETE FROM users WHERE roll_no=?", (roll_no_str,))
                return cur.rowcount > 0
        except Exception as e:
            print(f"Error deleting user: {e}")
            return False

    def update_user_details(self, old_roll_no: str, new_roll_no: str, new_name: str, new_role: str) -> bool:
        old_r = str(old_roll_no).strip()
        new_r = str(new_roll_no).strip()
        new_n = str(new_name).strip()
        new_rl = str(new_role).strip().lower()
        try:
            con = self._conn()
            try:
                with con:
                    if old_r != new_r:
                        con.execute("UPDATE attendance_log SET roll_no=? WHERE roll_no=?", (new_r, old_r))
                        con.execute("UPDATE grace_records SET roll_no=? WHERE roll_no=?", (new_r, old_r))
                        con.execute("UPDATE attendance_overrides SET roll_no=? WHERE roll_no=?", (new_r, old_r))
                    cur = con.execute("UPDATE users SET roll_no=?, name=?, role=? WHERE roll_no=?",
                                      (new_r, new_n, new_rl, old_r))
                    res = cur.rowcount > 0
            finally:
                con.close()
            return res
        except Exception as e:
            print(f"Error updating user details: {e}")
            return False

    def get_user_by_roll(self, roll_no: str) -> Optional[Dict[str, Any]]:
        with self._conn() as con:
            r = con.execute("SELECT * FROM users WHERE roll_no=?", (roll_no.strip(),)).fetchone()
            if not r:
                return None
            emb, multi = self._decode_embeddings(r)
            return {"roll_no": r["roll_no"], "name": r["name"], "role": r["role"], "embedding": emb, "multi_embeddings": multi, "registered_at": r["registered_at"]}

    # --- Rooms / timetable ---
    def upsert_room(self, room_id: str, name: str, camera_url: str,
                    teacher_zone: Optional[Dict[str, float]] = None) -> None:
        rid = room_id.strip()
        with self._conn() as con:
            con.execute("INSERT OR REPLACE INTO rooms (room_id, room_name, camera_source) VALUES (?,?,?)",
                        (rid, name.strip(), str(camera_url).strip()))
            z = teacher_zone or {}
            x1 = float(z.get("x1", 0.0)); y1 = float(z.get("y1", 0.0))
            x2 = float(z.get("x2", 1.0)); y2 = float(z.get("y2", 1.0))
            x1, x2 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))
            y1, y2 = max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))
            if x2 < x1: x1, x2 = x2, x1
            if y2 < y1: y1, y2 = y2, y1
            con.execute("INSERT INTO room_camera_config (room_id,teacher_zone_x1,teacher_zone_y1,teacher_zone_x2,teacher_zone_y2) VALUES (?,?,?,?,?) "
                        "ON DUPLICATE KEY UPDATE teacher_zone_x1=VALUES(teacher_zone_x1),teacher_zone_y1=VALUES(teacher_zone_y1),teacher_zone_x2=VALUES(teacher_zone_x2),teacher_zone_y2=VALUES(teacher_zone_y2)",
                        (rid, x1, y1, x2, y2))

    def get_rooms(self) -> List[Dict[str, str]]:
        with self._conn() as con:
            rows = con.execute("""SELECT r.*,
                       COALESCE(c.teacher_zone_x1,0.0) teacher_zone_x1,
                       COALESCE(c.teacher_zone_y1,0.0) teacher_zone_y1,
                       COALESCE(c.teacher_zone_x2,1.0) teacher_zone_x2,
                       COALESCE(c.teacher_zone_y2,1.0) teacher_zone_y2
                       FROM rooms r LEFT JOIN room_camera_config c ON c.room_id=r.room_id
                       ORDER BY r.room_id""").fetchall()
            return [dict(r) for r in rows]

    def delete_room(self, room_id: str) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM rooms WHERE room_id=?", (room_id,))

    def clear_timetable(self) -> None:
        with self._conn() as con:
            con.execute("DELETE FROM timetable")

    def load_timetable_from_df(self, df: pd.DataFrame) -> None:
        required = {"Day", "StartTime", "EndTime", "Subject", "TeacherID", "RoomID"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing timetable columns: {', '.join(sorted(missing))}")
        rows = []
        for _, row in df.iterrows():
            day = normalize_day(row["Day"])
            start = str(row["StartTime"]).strip()
            end = str(row["EndTime"]).strip()
            subject = str(row["Subject"]).strip()
            teacher = str(row["TeacherID"]).strip()
            room = str(row["RoomID"]).strip()
            if day not in DAY_MAP.values() or not start or not end or not subject or not teacher or not room:
                continue
            rows.append((day, start, end, subject, teacher, room))
        with self._conn() as con:
            # sessions.timetable_id is a FK onto timetable.id, so a plain
            # DELETE FROM timetable fails when any session still references a
            # row. Historical sessions keep their audit data; their link to the
            # old slot is NULLed so the timetable can be safely replaced.
            con.execute("UPDATE sessions SET timetable_id = NULL WHERE timetable_id IS NOT NULL")
            con.execute("DELETE FROM timetable")
            con.executemany("INSERT INTO timetable (day_of_week,start_time,end_time,subject,teacher_id,room_id) VALUES (?,?,?,?,?,?)", rows)

    def get_timetable(self) -> List[Dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("""SELECT t.*, u.name AS teacher_name, r.room_name AS room_name
                                  FROM timetable t LEFT JOIN users u ON t.teacher_id=u.roll_no
                                  LEFT JOIN rooms r ON t.room_id=r.room_id ORDER BY t.day_of_week,t.start_time,t.id""").fetchall()
            return [dict(r) for r in rows]

    # --- Attendance / sessions ---
    def create_session(self, session_id: str, timetable_id: int, date: str, subject: str = "General Class") -> None:
        with self._conn() as con:
            con.execute("INSERT OR REPLACE INTO sessions (session_id,timetable_id,date,status,subject) VALUES (?,?,?,?,?)",
                        (session_id, timetable_id, date, "active", subject))

    def update_session_status(self, session_id: str, status: str) -> None:
        with self._conn() as con:
            con.execute("UPDATE sessions SET status=? WHERE session_id=?", (status, session_id))

    def log_attendance(self, session_id: str, roll_no: str, role: str, status: str, confidence: float) -> None:
        with self._conn() as con:
            existing = con.execute("SELECT log_id,is_override,original_auto_status FROM attendance_log WHERE session_id=? AND roll_no=?", (session_id, roll_no)).fetchone()
            now = datetime.now().isoformat(timespec="seconds")
            if not existing:
                con.execute("""INSERT INTO attendance_log
                    (session_id,roll_no,role,timestamp,status,confidence,original_auto_status)
                    VALUES (?,?,?,?,?,?,?)""", (session_id, roll_no, role, now, status, confidence, status))
            elif existing["is_override"] != 1:
                con.execute("UPDATE attendance_log SET status=?,timestamp=?,confidence=?,original_auto_status=? WHERE session_id=? AND roll_no=?",
                            (status, now, confidence, status, session_id, roll_no))

    def get_warning_count(self, roll_no: str) -> int:
        with self._conn() as con:
            row = con.execute("SELECT COUNT(*) FROM attendance_log WHERE roll_no=? AND (status='warning' OR original_auto_status='warning')", (roll_no.strip(),)).fetchone()
            return row[0] if row else 0

    def get_immediately_following_class(self, current_timetable_id: int, day_of_week: str) -> Optional[Dict[str, Any]]:
        with self._conn() as con:
            curr = con.execute("SELECT * FROM timetable WHERE id=?", (current_timetable_id,)).fetchone()
            if not curr:
                return None
            row = con.execute("""SELECT * FROM timetable
                WHERE day_of_week=? AND start_time>=? AND id!=? ORDER BY start_time,id LIMIT 1""",
                (normalize_day(day_of_week), curr["end_time"], current_timetable_id)).fetchone()
            return dict(row) if row else None

    def record_transition_grace(self, roll_no: str, date: str, from_class_id: int, target_next_class_id: Optional[int], room_id: str = "", grace_minutes: float = 5.0) -> None:
        with self._conn() as con:
            con.execute("""INSERT INTO grace_records
                (roll_no,date,from_class_id,target_next_timetable_id,room_id,grace_minutes,is_used)
                VALUES (?,?,?,?,?,?,0)""", (roll_no.strip(), date.strip(), from_class_id, target_next_class_id, room_id.strip(), float(grace_minutes)))

    def get_valid_transition_grace(self, roll_no: str, date: str, timetable_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as con:
            row = con.execute("""SELECT * FROM grace_records
                WHERE roll_no=? AND date=? AND target_next_timetable_id=? AND is_used=0
                ORDER BY granted_at DESC LIMIT 1""", (roll_no.strip(), date.strip(), timetable_id)).fetchone()
            return dict(row) if row else None

    def consume_transition_grace(self, grace_id: int, session_id: str) -> None:
        with self._conn() as con:
            con.execute("UPDATE grace_records SET is_used=1,used_in_session_id=? WHERE id=?", (session_id, grace_id))

    def record_grace(self, roll_no: str, date: str, room_id: str = "") -> None:
        self.record_transition_grace(roll_no, date, 0, None, room_id, 5.0)

    def has_grace(self, roll_no: str, date: str) -> bool:
        with self._conn() as con:
            return con.execute("SELECT 1 FROM grace_records WHERE roll_no=? AND date=? AND is_used=0 LIMIT 1", (roll_no, date)).fetchone() is not None

    def calculate_attendance_with_grace(self, roll_no: str, detected_minutes: float, scheduled_duration: float, grace_minutes_available: float = 0.0) -> Dict[str, Any]:
        scheduled_duration = max(float(scheduled_duration), 1.0)
        detected_minutes = max(0.0, min(float(detected_minutes), scheduled_duration))
        
        # Student attendance uses the full scheduled session. The first/last
        # five-minute exclusion is a teacher-attendance rule only. Student
        # presence is already derived from the union of short detection
        # intervals, so there is no sample-count multiplication here.
        raw_percent = (detected_minutes / scheduled_duration) * 100.0
        req_present_mins = scheduled_duration * 0.80

        grace_used = 0.0
        effective_minutes = detected_minutes
        if detected_minutes < req_present_mins and grace_minutes_available > 0:
            effective_minutes = min(scheduled_duration, detected_minutes + float(grace_minutes_available))
            grace_used = min(float(grace_minutes_available), scheduled_duration - detected_minutes)

        effective_percent = min(100.0, (effective_minutes / scheduled_duration) * 100.0)
        warn_count = self.get_warning_count(roll_no)
        
        if effective_percent >= 80.0:
            status = "present"
        elif effective_percent >= 60.0 and warn_count < 3:
            status = "warning"
        else:
            status = "absent"
        return {"status": status, "detected_minutes": detected_minutes, "effective_minutes": effective_minutes,
                "grace_minutes_used": grace_used, "effective_percent": round(effective_percent,1),
                "raw_percent": round(raw_percent,1), "warning_count": warn_count,
                "grace_applied": grace_used > 0}

    def calculate_final_status(self, roll_no: str, percent: float) -> str:
        if percent >= 80.0:
            return "present"
        if percent >= 60.0 and self.get_warning_count(roll_no) < 3:
            return "warning"
        return "absent"

    def get_teacher_assigned_classes(self, teacher_id: str) -> List[Dict[str, Any]]:
        with self._conn() as con:
            tid = teacher_id.strip()
            rows = con.execute("""SELECT t.*,u.name AS teacher_name,r.room_name AS room_name
                FROM timetable t LEFT JOIN users u ON t.teacher_id=u.roll_no LEFT JOIN rooms r ON t.room_id=r.room_id
                LEFT JOIN authorized_credentials c ON c.id_number=?
                WHERE t.teacher_id=? OR lower(t.teacher_id)=lower(COALESCE(u.name,'')) OR lower(t.teacher_id)=lower(COALESCE(c.allocated_name,''))
                ORDER BY t.day_of_week,t.start_time""", (tid, tid)).fetchall()
            return [dict(r) for r in rows]

    def get_teacher_sessions(self, teacher_id: str) -> List[Dict[str, Any]]:
        with self._conn() as con:
            base = """SELECT s.session_id,s.timetable_id,s.date,s.status,s.subject,t.start_time,t.end_time,t.teacher_id,t.room_id,
                             u.name AS teacher_name,r.room_name FROM sessions s LEFT JOIN timetable t ON s.timetable_id=t.id
                             LEFT JOIN users u ON t.teacher_id=u.roll_no LEFT JOIN rooms r ON t.room_id=r.room_id"""
            if not teacher_id or teacher_id.upper() in {"ADMIN","HOD","ALL"}:
                rows = con.execute(base + " ORDER BY s.date DESC,t.start_time DESC,s.session_id DESC").fetchall()
            else:
                tid = teacher_id.strip()
                rows = con.execute(base + " LEFT JOIN authorized_credentials c ON c.id_number=? WHERE t.teacher_id=? OR lower(t.teacher_id)=lower(COALESCE(u.name,'')) OR lower(t.teacher_id)=lower(COALESCE(c.allocated_name,'')) ORDER BY s.date DESC,t.start_time DESC,s.session_id DESC", (tid, tid)).fetchall()
            return [dict(r) for r in rows]

    def get_session_students_for_override(self, session_id: str) -> List[Dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("""SELECT u.roll_no,u.name,u.role,COALESCE(a.status,'absent') current_status,
                       COALESCE(a.original_auto_status,a.status,'absent') original_auto_status,
                       COALESCE(a.is_override,0) is_override,COALESCE(a.override_reason,'') override_reason,
                       a.timestamp last_scanned,a.confidence
                       FROM users u LEFT JOIN attendance_log a ON u.roll_no=a.roll_no AND a.session_id=?
                       WHERE u.role='student' ORDER BY u.roll_no""", (session_id.strip(),)).fetchall()
            return [dict(r) for r in rows]

    def apply_manual_override(self, session_id: str, roll_no: str, new_status: str, teacher_id: str, reason: str,
                              password: Optional[str] = None, is_admin: bool = False) -> Dict[str, Any]:
        reason = reason.strip()
        new_status = new_status.strip().lower()
        if not reason:
            return {"success": False, "error": "A specific reason is required for manual attendance override."}
        if new_status not in {"present","warning","absent","grace"}:
            return {"success": False, "error": "Invalid attendance status."}
        with self._conn() as con:
            teacher_row = con.execute("SELECT * FROM users WHERE roll_no=?", (teacher_id.strip(),)).fetchone()
            teacher_name = teacher_row["name"] if teacher_row else teacher_id.strip()
            if not is_admin:
                if not password:
                    return {"success": False, "error": "Teacher authentication is required."}
                cred = self.verify_credential(teacher_id, password, "teacher")
                if not cred.get("valid"):
                    return {"success": False, "error": f"Teacher authentication failed: {cred.get('error','Invalid password')}"}
                teacher_name = cred.get("name") or teacher_name
            sess = con.execute("""SELECT s.*,t.teacher_id assigned_teacher,t.start_time,t.end_time,t.subject t_subject
                                FROM sessions s LEFT JOIN timetable t ON s.timetable_id=t.id WHERE s.session_id=?""", (session_id.strip(),)).fetchone()
            if not sess:
                return {"success": False, "error": f"Session '{session_id}' not found."}
            assigned = (sess["assigned_teacher"] or "").strip()
            if not is_admin and assigned and assigned != teacher_id.strip():
                return {"success": False, "error": f"Access denied: this session is assigned to {assigned}."}
            student = con.execute("SELECT * FROM users WHERE roll_no=? AND role='student'", (roll_no.strip(),)).fetchone()
            if not student:
                return {"success": False, "error": f"Student '{roll_no}' not found in database."}
            existing = con.execute("SELECT * FROM attendance_log WHERE session_id=? AND roll_no=?", (session_id.strip(), roll_no.strip())).fetchone()
            orig = (existing["original_auto_status"] if existing else None) or (existing["status"] if existing else "absent")
            subject = sess["subject"] or sess["t_subject"] or "General Class"
            class_date = sess["date"] or datetime.now().strftime("%Y-%m-%d")
            con.execute("""INSERT INTO attendance_overrides
                (session_id,timetable_id,date,start_time,end_time,subject,roll_no,student_name,original_status,new_status,teacher_id,teacher_name,reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id.strip(),sess["timetable_id"],class_date,sess["start_time"] or "-",sess["end_time"] or "-",subject,
                 roll_no.strip(),student["name"],orig,new_status,teacher_id.strip(),teacher_name,reason))
            if existing:
                con.execute("""UPDATE attendance_log SET status=?,is_override=1,override_reason=?,original_auto_status=COALESCE(original_auto_status,?),timestamp=CURRENT_TIMESTAMP
                               WHERE session_id=? AND roll_no=?""", (new_status,reason,orig,session_id.strip(),roll_no.strip()))
            else:
                con.execute("""INSERT INTO attendance_log
                    (session_id,roll_no,role,timestamp,status,confidence,is_override,override_reason,original_auto_status)
                    VALUES (?,?,'student',CURRENT_TIMESTAMP,?,1.0,1,?,?)""", (session_id.strip(),roll_no.strip(),new_status,reason,orig))
        return {"success": True, "message": f"Successfully overridden attendance for {student['name']} ({roll_no}) from '{orig.upper()}' to '{new_status.upper()}'. Audit record logged."}

    def get_override_audit_logs(self, date: Optional[str]=None, teacher_id: Optional[str]=None, roll_no: Optional[str]=None, subject: Optional[str]=None) -> List[Dict[str,Any]]:
        query="SELECT * FROM attendance_overrides WHERE 1=1"; params=[]
        if date: query += " AND date=?"; params.append(date.strip())
        if teacher_id: query += " AND teacher_id=?"; params.append(teacher_id.strip())
        if roll_no: query += " AND roll_no=?"; params.append(roll_no.strip())
        if subject: query += " AND subject LIKE ?"; params.append(f"%{subject.strip()}%")
        query += " ORDER BY overridden_at DESC"
        with self._conn() as con:
            return [dict(r) for r in con.execute(query,params).fetchall()]

    def get_override_audit_report(self) -> pd.DataFrame:
        query="""SELECT override_id AuditID,date ClassDate,subject Subject,start_time StartTime,end_time EndTime,roll_no StudentID,
                  student_name StudentName,original_status OriginalAutoStatus,new_status OverriddenStatus,teacher_id OverriddenByTeacherID,
                  teacher_name TeacherName,reason OverrideReason,overridden_at TimestampOfChange FROM attendance_overrides ORDER BY overridden_at DESC"""
        with self._conn() as con:
            return pd.read_sql_query(query, con)

    def upsert_teacher_attendance(self, session_id: str, teacher_id: str, date: str,
                                  counted_start: Optional[str], counted_end: Optional[str],
                                  first_seen: Optional[str], last_seen: Optional[str],
                                  present_minutes: float, absent_minutes: float,
                                  longest_absence_minutes: float, status: str,
                                  absence_over_20m: bool) -> None:
        with self._conn() as con:
            con.execute("""INSERT INTO teacher_attendance
                (session_id,teacher_id,date,counted_start,counted_end,first_seen,last_seen,
                 present_minutes,absent_minutes,longest_absence_minutes,status,absence_over_20m)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE
                  counted_start=VALUES(counted_start), counted_end=VALUES(counted_end),
                  first_seen=VALUES(first_seen), last_seen=VALUES(last_seen),
                  present_minutes=VALUES(present_minutes), absent_minutes=VALUES(absent_minutes),
                  longest_absence_minutes=VALUES(longest_absence_minutes), status=VALUES(status),
                  absence_over_20m=VALUES(absence_over_20m)""",
                (session_id, teacher_id, date, counted_start, counted_end, first_seen, last_seen,
                 float(present_minutes), float(absent_minutes), float(longest_absence_minutes),
                 status, 1 if absence_over_20m else 0))

    def get_teacher_attendance_report(self) -> pd.DataFrame:
        query="""SELECT ta.date Date, ta.session_id SessionID, COALESCE(t.subject,'General Class') Subject,
                         COALESCE(t.start_time,'-') StartTime, COALESCE(t.end_time,'-') EndTime,
                         COALESCE(r.room_name,'Default Room') Room, ta.teacher_id TeacherID,
                         COALESCE(u.name,ta.teacher_id) TeacherName, ta.present_minutes PresentMinutes,
                         ta.absent_minutes AbsentMinutes, ta.longest_absence_minutes LongestAbsenceMinutes,
                         ta.status Status, ta.absence_over_20m AbsenceOver20Minutes,
                         ta.first_seen FirstSeen, ta.last_seen LastSeen
                  FROM teacher_attendance ta
                  LEFT JOIN sessions s ON s.session_id=ta.session_id
                  LEFT JOIN timetable t ON s.timetable_id=t.id
                  LEFT JOIN rooms r ON t.room_id=r.room_id
                  LEFT JOIN users u ON ta.teacher_id=u.roll_no
                  ORDER BY ta.date DESC, ta.first_seen DESC"""
        with self._conn() as con:
            rows = con.execute(query).fetchall()
            return pd.DataFrame([dict(r) for r in rows])

    def get_attendance_report(self) -> pd.DataFrame:
        query="""SELECT COALESCE(s.date,date(a.timestamp)) Date,COALESCE(t.subject,'General Class') Subject,
                  COALESCE(t.start_time,'-') StartTime,COALESCE(t.end_time,'-') EndTime,COALESCE(r.room_name,'Default Room') Room,
                  a.roll_no RollNo,COALESCE(u.name,a.roll_no) StudentName,a.role Role,a.status Status,
                  COALESCE(a.is_override,0) IsManualOverride,COALESCE(a.original_auto_status,a.status) OriginalAutoStatus,
                  COALESCE(a.override_reason,'-') OverrideReason,a.timestamp Timestamp
                  FROM attendance_log a LEFT JOIN sessions s ON a.session_id=s.session_id LEFT JOIN timetable t ON s.timetable_id=t.id
                  LEFT JOIN rooms r ON t.room_id=r.room_id LEFT JOIN users u ON a.roll_no=u.roll_no ORDER BY a.timestamp DESC"""
        with self._conn() as con:
            return pd.read_sql_query(query, con)
