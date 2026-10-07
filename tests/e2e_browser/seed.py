"""Seed a realistic database for browser tests / demos, using the real engine for the finished lecture."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from tests.sim import FakeCameras, FakeRecognizer, make_user, run


def make_video(path: Path, seconds: int = 4, fps: int = 20, size=(640, 360)) -> Path:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for i in range(seconds * fps):
        img = np.full((size[1], size[0], 3), (70, 95, 130), np.uint8)
        cv2.rectangle(img, (0, 250), (size[0], size[1]), (50, 70, 100), -1)
        cv2.rectangle(img, (40 + (i * 5) % 520, 120), (110 + (i * 5) % 520, 260), (230, 230, 230), -1)
        w.write(img)
    w.release()
    return path


def seed(db, store, video: Path) -> dict:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    midnight = now.replace(hour=0, minute=0)
    day = now.strftime("%A").lower()
    names = ["Aarav Mehta", "Diya Nair", "Kabir Singh", "Meera Iyer", "Rohan Das", "Sana Khan", "Vikram Rao", "Isha Gupta", "Arjun Pillai", "Neha Joshi", "Tara Menon", "Yash Patel"]
    make_user(db, "T-A", "Ms. Rao", "teacher", 9001)
    make_user(db, "T-B", "Mr. Khan", "teacher", 9002)
    studs = [f"S{i:02d}" for i in range(1, 13)]
    for i, (roll, nm) in enumerate(zip(studs, names)):
        make_user(db, roll, nm, "student", 100 + i)
    for r, n in (("R101", "Room 101"), ("R102", "Room 102"), ("R103", "Room 103 (Lab)")):
        store.save_room(r, n)
    cam101 = store.create_camera("Camera R101", "file", str(video), "R101")
    cam102 = store.create_camera("Camera R102", "file", str(video), "R102")
    store.create_camera("Lab webcam", "usb", "3", None)
    for cid, pw, role, nm in [("S01", "stud-pass-1", "student", names[0]), ("S09", "stud-pass-9", "student", names[8]), ("T-A", "teach-pass-1", "teacher", "Ms. Rao"),
                              ("hod1", "hod-password-1", "hod", "Dr. Iyer (HOD)"), ("adm1", "admin-password-1", "admin", "Office Admin")]:
        db.set_authorized_credential(cid, pw, role, nm)
    store.upsert_section("SEC-A", "CSE Year 2 – A"); store.set_section_students("SEC-A", studs[:10])
    store.upsert_section("SEC-B", "CSE Year 2 – B"); store.set_section_students("SEC-B", studs[10:])
    live_start, live_end = now - timedelta(minutes=10), now + timedelta(minutes=40)
    up_start = now + timedelta(hours=2)
    hm = lambda d: d.strftime("%H:%M")
    rows = [
        {"Day": day, "StartTime": "08:00", "EndTime": "08:50", "Subject": "Mathematics", "TeacherID": "T-A", "RoomID": "R101"},
        {"Day": day, "StartTime": hm(live_start), "EndTime": hm(live_end), "Subject": "Physics", "TeacherID": "T-B", "RoomID": "R102"},
        {"Day": day, "StartTime": hm(up_start), "EndTime": hm(up_start + timedelta(minutes=50)), "Subject": "Chemistry Lab", "TeacherID": "T-A", "RoomID": "R103"},
    ]
    db.load_timetable_from_df(pd.DataFrame(rows))
    for r in store.timetable_rows():
        if r["room_id"] in ("R101", "R102"):
            store.set_slot_section(r["id"], "SEC-A")

    # finished 08:00 lecture, simulated through the real scheduler/state machine/MySQL (people and camera are scripted)
    from backend.core.session_logic import SessionLogic
    cams, rec = FakeCameras(), FakeRecognizer()
    logic = SessionLogic(db, None, recognizer=rec, cameras=cams, store=store)

    def scene(t):
        m = (t - midnight.replace(hour=8)).total_seconds() / 60
        seen = {f"S{i:02d}" for i in (1, 2, 3, 4, 5, 6, 7, 8)}
        if m >= 12: seen.add("S09")
        if m > 30: seen.discard("S08")
        rec.set(cam101, seen | ({"T-A"} if (1 <= m < 25 or m >= 31) else set()))
        cams.set_online(cam101, not (14 <= m < 19))
    run(logic, midnight.replace(hour=8), midnight.replace(hour=8, minute=52), hook=scene)
    return {"live_start": hm(live_start), "live_end": hm(live_end), "cam101": cam101, "cam102": cam102}
