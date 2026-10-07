"""Simulation helpers: a real MySQL-backed world driven by a virtual clock.

Only the *hardware boundaries* are replaced by test doubles that implement the production
interfaces (CameraManager.acquire/get_frames/status and Recognizer.identify): there are no
physical cameras or real faces in CI. Scheduler, math, state machine, store and SQL are real.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

import numpy as np

from backend.core.recognizer import Detection
from backend.core.session_logic import SessionLogic

os.environ.setdefault("APP_TIMEZONE", "UTC")
MONDAY = datetime(2026, 1, 5, tzinfo=timezone.utc)      # 2026-01-05 is a Monday


def at(h: int, m: int = 0, s: int = 0, day: datetime = MONDAY) -> datetime:
    return day.replace(hour=h, minute=m, second=s)


class FakeHandle:
    def __init__(self, cams: "FakeCameras", key: str):
        self.cams, self.key = cams, key

    @property
    def status(self) -> str:
        return "ONLINE" if self.cams.online.get(self.key, True) else "OFFLINE"

    def get_frames(self, n: int, delay: float = 0.0, timeout: float = 0.0):
        if not self.cams.online.get(self.key, True):
            return []
        return [np.full((120, 160, 3), 90, np.uint8) for _ in range(n)]


class FakeCameras:
    def __init__(self):
        self.online: Dict[str, bool] = {}
        self.acquired: List[str] = []
        self.kept: Set[str] = set()

    def acquire(self, spec) -> FakeHandle:
        self.acquired.append(spec.key)
        return FakeHandle(self, spec.key)

    def release_unused(self, keep) -> None:
        self.kept = set(keep)

    def stop_all(self) -> None:
        pass

    def set_online(self, camera_id: Optional[int], online: bool) -> None:
        self.online[f"id:{camera_id}"] = online


class FakeRecognizer:
    """``visible[camera_id]`` = people the camera currently sees. Honest about the roster."""

    def __init__(self):
        self.visible: Dict[Optional[int], Set[str]] = {}
        self.is_ready = True
        self.fail = False
        self.calls = 0

    def ready(self) -> bool:
        return self.is_ready

    def set(self, camera_id: Optional[int], people: Iterable[str]) -> None:
        self.visible[camera_id] = set(people)

    def identify(self, frames_by_camera, roster):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model crashed")
        by_id = {u["roll_no"]: u for u in roster}
        out = []
        for cam_id in frames_by_camera:
            for roll in sorted(self.visible.get(cam_id, ())):
                u = by_id.get(roll)
                if u:
                    out.append(Detection(roll, u["name"], u["role"], 0.91, cam_id))
        return out


def _fake_analyze(self, frame, roster):
    """Test double: reports whoever the camera 'currently sees' (set via ``set(None, ...)``) as faces."""
    by_id = {u["roll_no"]: u for u in roster}
    out, x = [], 10
    for roll in sorted(self.visible.get("analyze", ())):
        u = by_id.get(roll)
        out.append({"bbox": [x, 20, x + 40, 70], "match": ({"roll_no": roll, "name": u["name"], "role": u["role"], "confidence": 0.9} if u else None)})
        x += 60
    return out


FakeRecognizer.analyze = _fake_analyze


def make_user(db, roll: str, name: str, role: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    db.upsert_user(roll, name, role, v / np.linalg.norm(v))


class World:
    """One teacher/room/camera/section by default; ``add_room`` adds more."""

    def __init__(self, db, store):
        import pandas as pd
        self.db, self.store, self.pd = db, store, pd
        self.cams, self.rec = FakeCameras(), FakeRecognizer()
        self.rows: List[dict] = []
        self.sections: Dict[tuple, str] = {}
        self.camera_ids: Dict[str, int] = {}

    def add_people(self, teachers: Iterable[tuple], students: Iterable[str]) -> None:
        for i, (roll, name) in enumerate(teachers):
            make_user(self.db, roll, name, "teacher", 1000 + i)
        for i, roll in enumerate(students):
            make_user(self.db, roll, f"Student {roll}", "student", i + 1)

    def add_room(self, room_id: str, camera: bool = True) -> Optional[int]:
        self.store.save_room(room_id, f"Room {room_id}")
        if camera:
            cid = self.store.create_camera(f"Camera {room_id}", "rtsp", f"rtsp://10.0.0.{len(self.camera_ids) + 5}/{room_id}", room_id)
            self.camera_ids[room_id] = cid
            return cid
        return None

    def add_class(self, room: str, teacher: str, start: str = "10:00", end: str = "10:50", subject: str = "Maths",
                  section: Optional[str] = "SEC-A") -> int:
        self.rows.append({"Day": "monday", "StartTime": start, "EndTime": end, "Subject": subject, "TeacherID": teacher, "RoomID": room})
        self.db.load_timetable_from_df(self.pd.DataFrame(self.rows))
        if section:
            self.sections[(room, start)] = section
        for r in self.store.timetable_rows():              # a reload replaces every slot id: re-apply sections
            sec = self.sections.get((r["room_id"], r["start_time"]))
            if sec:
                self.store.set_slot_section(r["id"], sec)
        return [r for r in self.store.timetable_rows() if r["room_id"] == room and r["start_time"] == start][0]["id"]

    def make_section(self, section: str, students: Iterable[str]) -> None:
        self.store.upsert_section(section, f"Section {section}")
        self.store.set_section_students(section, students)

    def logic(self) -> SessionLogic:
        return SessionLogic(self.db, None, recognizer=self.rec, cameras=self.cams, store=self.store)

    # --- inspection
    def sessions(self) -> List[dict]:
        return self.store._q("SELECT * FROM sessions ORDER BY session_id")

    def att(self, session_id: str) -> Dict[str, dict]:
        return {r["roll_no"]: r for r in self.store._q("SELECT * FROM attendance_log WHERE session_id=%s", (session_id,))}

    def summary(self, session_id: str) -> Dict[str, dict]:
        return {r["roll_no"]: r for r in self.store._q("SELECT * FROM session_student_summary WHERE session_id=%s", (session_id,))}

    def states(self, session_id: str) -> List[str]:
        ev = self.store._q("SELECT to_state FROM session_events WHERE session_id=%s AND event_type='state' ORDER BY event_id", (session_id,))
        return [e["to_state"] for e in ev]


def run(logic: SessionLogic, start: datetime, end: datetime, step_s: int = 15, hook=None) -> None:
    """Advance the virtual clock from ``start`` to ``end`` (inclusive) calling tick every step."""
    t = start
    while t <= end:
        if hook:
            hook(t)
        logic.tick(now=t, wait=True)
        t += timedelta(seconds=step_s)
