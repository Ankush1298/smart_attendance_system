"""Business services for the structured API."""
from backend.repositories.attendance_repository import AttendanceRepository

class AttendanceService:
    def __init__(self, repository=None):
        self.repository = repository or AttendanceRepository()

    def dashboard(self):
        return self.repository.dashboard_counts()

    def users(self, role=None):
        return self.repository.users(role)

    def timetable(self, day=None):
        return self.repository.timetable(day)

    def recent_attendance(self, limit=100):
        return self.repository.recent_attendance(limit)

    def teacher_attendance_report(self):
        # The repository is intentionally not responsible for the desktop-only
        # detailed report yet; this service exposes the DB-backed report when
        # the API is used by an HOD/admin client.
        return self.repository.teacher_attendance_report()
