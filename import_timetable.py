"""One-shot CLI: import a pre-parsed timetable CSV into the app database.

Usage:
    ./venv/bin/python import_timetable.py timetable_import.csv

What it does:
    1. Reads the CSV produced by timetable_parser.
    2. Creates any room entries that are missing from the rooms table
       (without a camera source -- assign real cameras from the Cameras tab).
    3. Wipes the existing timetable and inserts the new rows.
    4. Prints a per-day summary so you can spot gaps before starting the app.
"""
from __future__ import annotations

import sys
import os

# Make sure src/ imports resolve when running from the project root.
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
import pandas as pd
from collections import Counter
from backend.core.db import DatabaseManager, normalize_day
import backend.core.timetable_parser as tparser

BASE_DIR = Path(__file__).parent


def main(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        print(f"ERROR: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(csv_path)
    print(f"Read {len(df)} rows from {csv_path}")

    required = {"Day", "StartTime", "EndTime", "Subject", "TeacherID", "RoomID"}
    missing = required - set(df.columns)
    if missing:
        print(f"ERROR: CSV is missing columns: {', '.join(sorted(missing))}", file=sys.stderr)
        sys.exit(1)

    db = DatabaseManager(BASE_DIR)

    # 1. Ensure every room code in the CSV exists in the rooms table.
    created = tparser.ensure_rooms(db, df)
    if created:
        print(f"\nCreated {len(created)} new room(s):")
        for r in created:
            print(f"  {r}  (no camera assigned yet — set one from the Cameras tab)")

    # 2. Load the timetable — this wipes the old one first.
    db.load_timetable_from_df(df)
    print(f"\nTimetable replaced. {len(df)} rows loaded into the database.")

    # 3. Print a per-day count so it's easy to spot a bad day.
    day_counts: Counter = Counter()
    for _, row in df.iterrows():
        day = normalize_day(str(row["Day"]))
        if day:
            day_counts[day] += 1

    order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    print("\nSlots per day:")
    for day in order:
        count = day_counts.get(day, 0)
        bar = "█" * min(count // 2, 50)
        print(f"  {day:<12} {count:>4}  {bar}")

    # 4. Unique rooms and teachers — sanity check.
    rooms = sorted({str(r).strip() for r in df["RoomID"] if str(r).strip()})
    teachers = sorted({str(t).strip() for t in df["TeacherID"] if str(t).strip()})
    print(f"\nUnique rooms   : {len(rooms)}")
    print(f"Unique teachers: {len(teachers)}")
    print("\nDone. Start the app and check Settings → Timetable to verify.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./venv/bin/python import_timetable.py timetable_import.csv")
        sys.exit(1)
    main(sys.argv[1])
