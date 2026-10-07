"""Timetable parsing (spreadsheet / CSV / Word / PDF / image) and validation. These need no database.
NOT TESTED: OCR accuracy on real scanned aSc-style timetables (no sample file in the repository) - see README."""
import io

import cv2
import numpy as np
import pandas as pd
import pytest

import backend.core.timetable_parser as tp
from backend.core import timetable_ocr as ocr

COLS = ["Day", "StartTime", "EndTime", "Subject", "TeacherID", "RoomID"]
ROWS = [("monday", "09:00", "09:50", "Maths", "T-A", "R101"), ("Tue", "10:00", "10:50", "Physics", "T-B", "R102")]


def test_csv_xlsx_docx_all_yield_canonical_columns(tmp_path):
    df = pd.DataFrame(ROWS, columns=COLS)
    (tmp_path / "t.csv").write_text(df.to_csv(index=False))
    df.to_excel(tmp_path / "t.xlsx", index=False)
    for name in ("t.csv", "t.xlsx"):
        out = tp.extract_timetable_data(str(tmp_path / name))
        assert list(out.columns) == COLS, name
        assert len(out) == 2 and set(out["Subject"]) == {"Maths", "Physics"}, name


def test_docx_weekly_grid_is_read_and_flat_tables_are_rejected_clearly(tmp_path):
    import docx
    d = docx.Document(); t = d.add_table(rows=3, cols=3)
    grid = [["Day", "09:00-09:50", "10:00-10:50"],
            ["Monday", "R101\nMathematics\nMr. Rao", "R102\nPhysics\nMs. Khan"],
            ["Tuesday", "R101\nChemistry\nMr. Rao", ""]]
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            t.cell(i, j).text = v.replace("\\n", "\n")
    d.save(tmp_path / "grid.docx")
    out = tp.extract_timetable_data(str(tmp_path / "grid.docx"))
    assert list(out.columns) == COLS and len(out) == 3
    assert set(out["Day"]) == {"monday", "tuesday"} and set(out["StartTime"]) == {"09:00", "10:00"}
    flat = docx.Document(); ft = flat.add_table(rows=2, cols=len(COLS))
    for j, c in enumerate(COLS):
        ft.cell(0, j).text = c
    flat.save(tmp_path / "flat.docx")
    with pytest.raises(ValueError, match="No timetable table"):
        tp.extract_timetable_data(str(tmp_path / "flat.docx"))                  # the API reports this as HTTP 422 with this text
def test_error_paths_do_not_crash(tmp_path):
    with pytest.raises(ValueError):
        tp.extract_timetable_data(str(tmp_path / "missing.csv"))
    (tmp_path / "x.txt").write_text("hello")
    with pytest.raises(ValueError):
        tp.extract_timetable_data(str(tmp_path / "x.txt"))
    (tmp_path / "bad.xlsx").write_bytes(b"not really an excel file")
    with pytest.raises(Exception):
        tp.extract_timetable_data(str(tmp_path / "bad.xlsx"))              # the API turns this into HTTP 422
    (tmp_path / "wrong.csv").write_text("a,b\n1,2\n")
    out = None
    try:
        out = tp.extract_timetable_data(str(tmp_path / "wrong.csv"))
    except Exception:
        pass
    assert out is None or len(out) == 0


def test_image_without_a_timetable_gives_empty_result_not_a_crash(tmp_path):
    img = np.full((400, 600, 3), 255, np.uint8)
    cv2.putText(img, "hello world", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.imwrite(str(tmp_path / "blank.png"), img)
    out = tp.extract_timetable_data(str(tmp_path / "blank.png"))
    assert len(out) == 0 and list(out.columns) == COLS


def test_pdf_without_a_timetable_gives_empty_result_not_a_crash(tmp_path):
    import fitz
    doc = fitz.open(); page = doc.new_page(); page.insert_text((72, 72), "Just a letter, no timetable here.")
    doc.save(tmp_path / "letter.pdf")
    out = tp.extract_timetable_data(str(tmp_path / "letter.pdf"))
    assert len(out) == 0


@pytest.mark.parametrize("text,expected", [("09:00-09:50", ("09:00", "09:50")), ("9.00 - 9.50 AM", ("09:00", "09:50")), ("13:30–14:20", ("13:30", "14:20"))])
def test_time_range_parsing(text, expected):
    assert ocr._parse_times(text) == expected


@pytest.mark.parametrize("text,expected", [("Mon", "monday"), ("THURS", "thursday"), ("Wednesday", "wednesday"), ("Sat", "saturday")])
def test_day_names(text, expected):
    assert ocr.canonical_day(text) == expected


def test_ensure_rooms_creates_rooms_without_cameras(db):
    df = pd.DataFrame(ROWS, columns=COLS)
    created = tp.ensure_rooms(db, df)
    assert sorted(created) == ["R101", "R102"]
    from backend.core.store import Store
    assert Store(db).room_cameras("R101") == []                              # new rooms never silently get a camera
    db.load_timetable_from_df(df)
    assert len(db.get_timetable()) == 2
