# Smart Class Attendance

A production-structured Smart Class Attendance application using:

- Python + CustomTkinter desktop application
- FastAPI registration/API services
- InsightFace/OpenCV face recognition
- MySQL 8 as the only persistent application database
- Organized backend layers: API, services, repositories, database, models and core
- Timetable, users, rooms, sessions and attendance stored in MySQL

## Project structure

```text
smart_attendance/
├── backend/
│   ├── api/             # REST API
│   ├── config/          # Environment configuration
│   ├── core/            # Face recognition, session engine, desktop UI and DB manager
│   ├── database/        # MySQL connection layer
│   ├── models/          # Application entities
│   ├── repositories/    # Database access for API services
│   └── services/        # Business logic
├── database/
│   └── schema_mysql.sql # MySQL schema
├── frontend/
│   ├── admin/           # Admin web assets
│   └── registration/    # Registration documentation/assets
├── docs/
│   └── ARCHITECTURE.md
├── models/              # InsightFace model assets
├── static/              # Existing web portal assets
├── main.py              # Desktop application entry point
├── register.py          # Student/faculty KYC registration server
├── import_timetable.py  # Timetable import command
├── run.py               # Desktop launcher
├── .env.example
└── requirements.txt
```

## Database

SQLite is not used by the application.

MySQL 8 is the single persistent store for:

- users and biometric embeddings
- authorized credentials
- rooms and camera sources
- timetable
- attendance sessions
- attendance logs
- attendance overrides
- grace records
- scan logs
- attendance reports

The application creates the database/schema on startup if the configured MySQL account has permission to create databases/tables.

### Configure MySQL

Copy `.env.example` to `.env` and set:

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=smart_attendance
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
```

Never commit `.env`.

If the existing application's private user/biometric data has already been imported into MySQL, the application will use it directly. The source repository intentionally does not contain biometric/user data exports.

## Install

Create a virtual environment:

### Windows

```powershell
python -m venv venv
venv\Scripts\activate
```

### macOS / Windows / Linux
The application uses the same Python code and the same timetable OCR pipeline on all supported desktop operating systems. Scanned/image-only aSc-style timetable PDFs are handled by the bundled RapidOCR ONNXRuntime engine. No Apple Vision, Windows-only OCR API, or OS-specific OCR code is required.

Install the Python dependencies from `requirements.txt` on the target machine. Camera access is provided through OpenCV, and persistence is provided through MySQL.


## Cross-platform runtime

This package is intended to run the same way on Windows, macOS and Linux.

- **OCR:** RapidOCR + ONNX Runtime (primary, cross-platform)
- **Camera:** OpenCV
- **Face recognition:** InsightFace/ONNX Runtime
- **Database:** MySQL
- **UI:** CustomTkinter / web components
- **PDF parsing:** PyMuPDF

There is no macOS-only OCR dependency in this build. Tesseract is optional and is used only as a fallback if it is installed on the machine.

### Attendance timing model

- Active student recognition heartbeat: **60 seconds**.
- Student attendance: **dwell-time / union of presence intervals**, not five-minute sample counting.
- Teacher attendance: only the **middle 40 minutes of a 50-minute lecture** count; first and last 5 minutes are excluded from the teacher attendance total.
- Teacher presence is credited only inside the room's configured **front/whiteboard zone**.
- A teacher absence gap **over 20 minutes** is flagged in the HOD/management teacher attendance report.
- The existing room/timetable/teacher authorization flow remains unchanged.


## Attendance Window Policy

For a standard 50-minute lecture, attendance is counted only during minutes 5 through 45.

- First 5 minutes: excluded for **students and teachers**
- Middle 40 minutes: counted for **students and teachers**
- Last 5 minutes: excluded for **students and teachers**
- Student camera sampling during the counted window: every 60 seconds
- Student presence is accumulated using detection intervals rather than discrete sample counts.
- Teacher presence is evaluated every 60 seconds; the scheduled teacher is considered present when recognized anywhere inside the classroom.
- A missed teacher recognition starts/continues an unrecognized interval; it does not immediately mean the teacher left.
- Teacher absence is reported when the teacher remains unrecognized for more than 20 continuous minutes during the counted window.
