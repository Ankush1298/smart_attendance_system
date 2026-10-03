"""Optional structured REST API.

The existing desktop app and KYC portal remain unchanged for verification. This API is
an additive boundary for the same MySQL data and can be enabled once the MySQL migration
has been verified.
"""
from fastapi import FastAPI, Query
from backend.services.attendance_service import AttendanceService

app = FastAPI(title="Smart Class Attendance API", version="2.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "smart-attendance-api"}

@app.get("/api/dashboard")
def dashboard(service: AttendanceService = AttendanceService()):
    return service.dashboard()

@app.get("/api/users")
def users(role: str | None = None, service: AttendanceService = AttendanceService()):
    return service.users(role)

@app.get("/api/timetable")
def timetable(day: str | None = None, service: AttendanceService = AttendanceService()):
    return service.timetable(day)

@app.get("/api/attendance")
def attendance(limit: int = Query(default=100, ge=1, le=1000), service: AttendanceService = AttendanceService()):
    return service.recent_attendance(limit)

@app.get("/api/teacher-attendance")
def teacher_attendance(service: AttendanceService = AttendanceService()):
    return service.teacher_attendance_report()
