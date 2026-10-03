"""Lightweight domain models used by the new service/repository boundary.

The legacy runtime continues to use dictionaries for compatibility. These dataclasses
make the intended data ownership explicit without changing the working UI yet.
"""
from dataclasses import dataclass
from typing import Optional

@dataclass
class User:
    roll_no: str
    name: str
    role: str
    embedding: bytes | None = None
    multi_embeddings: bytes | None = None
    mesh_path: Optional[str] = None

@dataclass
class TimetableEntry:
    id: int | None
    day_of_week: str
    start_time: str
    end_time: str
    subject: str
    teacher_id: str
    room_id: str

@dataclass
class Room:
    room_id: str
    room_name: str
    camera_source: str

@dataclass
class AttendanceEvent:
    session_id: str
    roll_no: str
    status: str
    confidence: float | None = None
