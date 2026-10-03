"""Persistence operations for the structured API.

All persistent attendance/user/timetable data belongs in MySQL when the structured API
is enabled. No timetable cache is treated as authoritative storage.
"""
from backend.database.mysql import MySQLDatabase

class AttendanceRepository:
    def __init__(self, db: MySQLDatabase | None = None):
        self.db = db or MySQLDatabase()

    def dashboard_counts(self):
        rows = self.db.fetch_all("""
            SELECT role, COUNT(*) AS total FROM users GROUP BY role
        """)
        counts = {"students": 0, "teachers": 0, "rooms": 0, "timetable": 0}
        for r in rows:
            if r["role"] == "student": counts["students"] = r["total"]
            elif r["role"] == "teacher": counts["teachers"] = r["total"]
        counts["rooms"] = self.db.fetch_one("SELECT COUNT(*) AS total FROM rooms")["total"]
        counts["timetable"] = self.db.fetch_one("SELECT COUNT(*) AS total FROM timetable")["total"]
        return counts

    def users(self, role=None):
        sql = "SELECT roll_no,name,role,registered_at FROM users"
        params=[]
        if role:
            sql += " WHERE role=%s"; params.append(role)
        sql += " ORDER BY roll_no"
        return self.db.fetch_all(sql, params)

    def timetable(self, day=None):
        sql = "SELECT id,day_of_week,start_time,end_time,subject,teacher_id,room_id FROM timetable"
        params=[]
        if day:
            sql += " WHERE day_of_week=%s"; params.append(day.lower())
        sql += " ORDER BY day_of_week,start_time,id"
        return self.db.fetch_all(sql, params)

    def recent_attendance(self, limit=100):
        return self.db.fetch_all("""
            SELECT a.log_id,a.session_id,a.roll_no,a.status,a.confidence,a.timestamp,
                   u.name AS student_name
            FROM attendance_log a LEFT JOIN users u ON u.roll_no=a.roll_no
            ORDER BY a.log_id DESC LIMIT %s
        """, [limit])


    def teacher_attendance_report(self):
        return self.db.fetch_all("""SELECT ta.date, ta.session_id, COALESCE(t.subject,'General Class') subject,
                                      COALESCE(t.start_time,'-') start_time, COALESCE(t.end_time,'-') end_time,
                                      COALESCE(r.room_name,'Default Room') room, ta.teacher_id,
                                      COALESCE(u.name,ta.teacher_id) teacher_name, ta.present_minutes, ta.absent_minutes,
                                      ta.longest_absence_minutes, ta.status, ta.absence_over_20m, ta.first_seen, ta.last_seen
                               FROM teacher_attendance ta
                               LEFT JOIN sessions s ON s.session_id=ta.session_id
                               LEFT JOIN timetable t ON s.timetable_id=t.id
                               LEFT JOIN rooms r ON t.room_id=r.room_id
                               LEFT JOIN users u ON ta.teacher_id=u.roll_no
                               ORDER BY ta.date DESC, ta.first_seen DESC""")
