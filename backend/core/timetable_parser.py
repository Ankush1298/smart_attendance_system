"""Timetable extraction -- fully offline, no AI provider and no API key.

Structured spreadsheets (CSV/Excel) are read directly. PDFs, images and
Word documents are parsed locally by :mod:`src.timetable_ocr`, which
reconstructs the ruled table grid and reads it with an on-device OCR
model. Nothing is uploaded anywhere.

The returned DataFrame keeps the long-standing contract used by
``DatabaseManager.load_timetable_from_df``::

    Day, StartTime, EndTime, Subject, TeacherID, RoomID

``extract_timetable_data`` also attaches a report to ``df.attrs["report"]``
so callers can show what happened without changing the return type.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd

from backend.core.timetable_ocr import ClassPage, OcrUnavailable, ocr_available

COLUMNS = ["Day", "StartTime", "EndTime", "Subject", "TeacherID", "RoomID"]

ProgressFn = Optional[Callable[[int, int, str], None]]


# ---------------------------------------------------------------------------
# Non-PDF inputs
# ---------------------------------------------------------------------------


def _read_spreadsheet(filepath: str, ext: str) -> Optional[pd.DataFrame]:
    """Return a ready DataFrame for a spreadsheet, or None to keep parsing."""
    try:
        df = pd.read_csv(filepath) if ext == ".csv" else pd.read_excel(filepath)
    except Exception as exc:
        raise ValueError(f"Failed to read spreadsheet: {exc}") from exc

    if set(COLUMNS).issubset(set(df.columns)):
        return df
    return None


def _spreadsheet_to_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort mapping of an arbitrary spreadsheet onto our columns."""
    lookup = {str(c).strip().lower().replace(" ", "").replace("_", ""): c for c in df.columns}

    aliases = {
        "Day": ["day", "dayofweek", "weekday"],
        "StartTime": ["starttime", "start", "from", "begin"],
        "EndTime": ["endtime", "end", "to", "finish"],
        "Subject": ["subject", "course", "paper", "class"],
        "TeacherID": ["teacherid", "teacher", "faculty", "staff", "instructor"],
        "RoomID": ["roomid", "room", "hall", "venue"],
    }

    resolved: Dict[str, str] = {}
    for target, names in aliases.items():
        for name in names:
            if name in lookup:
                resolved[target] = lookup[name]
                break

    missing = [c for c in COLUMNS if c not in resolved]
    if missing:
        raise ValueError(
            "Spreadsheet is missing these columns: " + ", ".join(missing) +
            ". Expected headers: " + ", ".join(COLUMNS)
        )
    return pd.DataFrame({target: df[source] for target, source in resolved.items()})


def _read_docx(filepath: str) -> pd.DataFrame:
    """Read a Word timetable laid out as a table.

    Without an AI model there is nothing sane to do with free-form prose,
    so only real tables are accepted -- the same grid shape the OCR path
    understands, which is what timetable documents actually contain.
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise ValueError("python-docx is required to read .docx timetables") from exc

    document = Document(filepath)
    for table in document.tables:
        grid = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if len(grid) < 2 or len(grid[0]) < 2:
            continue
        frame = _docx_grid_to_frame(grid)
        if frame is not None and not frame.empty:
            return frame

    raise ValueError(
        "No timetable table found in this Word document. Save the timetable as "
        "CSV/Excel, or upload the PDF."
    )


def _docx_grid_to_frame(grid: Sequence[Sequence[str]]) -> Optional[pd.DataFrame]:
    from backend.core.timetable_ocr import canonical_day, split_cell_fields

    header = grid[0]
    # Period times live in the header row (often a second header line).
    times: Dict[int, tuple] = {}
    for c, text in enumerate(header):
        parsed = _parse_period_time(text)
        if parsed:
            times[c] = parsed
    if not times and len(grid) > 1:
        for c, text in enumerate(grid[1]):
            parsed = _parse_period_time(text)
            if parsed:
                times[c] = parsed
        header_rows = 2
    else:
        header_rows = 1

    rows: List[Dict[str, str]] = []
    for grid_row in grid[header_rows:]:
        if not grid_row:
            continue
        day = canonical_day(grid_row[0])
        if not day:
            continue
        for c in range(1, min(len(grid_row), max(times) + 1 if times else len(grid_row))):
            cell = grid_row[c]
            if not cell or c not in times:
                continue
            fields = [f.strip() for f in re.split(r"[\n\r]+", cell) if f.strip()]
            room, subject, teacher = split_cell_fields(fields)
            if not subject or not room or not teacher:
                continue
            rows.append(
                {
                    "Day": day,
                    "StartTime": times[c][0],
                    "EndTime": times[c][1],
                    "Subject": subject,
                    "TeacherID": teacher,
                    "RoomID": room,
                }
            )
    return pd.DataFrame(rows, columns=COLUMNS) if rows else None


def _parse_period_time(text: str) -> Optional[tuple]:
    from backend.core.timetable_ocr import _parse_times

    return _parse_times(text or "")


def _read_image(filepath: str) -> pd.DataFrame:
    """OCR a timetable supplied as a plain image."""
    import cv2
    import numpy as np

    from backend.core.timetable_ocr import parse_page

    img = cv2.imread(filepath)
    if img is None:
        raw = np.fromfile(filepath, dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not read image file: {filepath}")

    page = _ImagePage(img)
    result = parse_page(page)
    return _frames_from_pages([result])


class _ImagePage:
    """Adapts a bare image to the Page interface parse_page expects."""

    def __init__(self, img):
        self._img = img

    def get_text(self, *_args, **_kwargs) -> str:
        return ""

    def get_pixmap(self, matrix=None, alpha=False):
        import fitz
        import numpy as np

        h, w = self._img.shape[:2]
        zoom = matrix.a if matrix is not None else 1.0
        new_w, new_h = max(1, int(round(w * zoom))), max(1, int(round(h * zoom)))
        if (new_w, new_h) != (w, h):
            import cv2

            resized = cv2.resize(self._img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        else:
            resized = self._img

        class _Pix:
            pass

        pix = _Pix()
        rgb = resized[:, :, ::-1] if resized.ndim == 3 else resized
        pix.samples = np.ascontiguousarray(rgb).tobytes()
        pix.width = new_w
        pix.height = new_h
        pix.n = 3 if resized.ndim == 3 else 1
        return pix


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _frames_from_pages(pages: Sequence[ClassPage]) -> pd.DataFrame:
    rows: List[Dict[str, str]] = []
    for page in pages:
        rows.extend(page.rows)
    return pd.DataFrame(rows, columns=COLUMNS)


def extract_timetable_data(
    filepath: str,
    api_config: Optional[dict] = None,
    progress: ProgressFn = None,
    pages: Optional[Sequence[int]] = None,
) -> pd.DataFrame:
    """Parse a timetable file into the canonical DataFrame.

    ``api_config`` is accepted only for backwards compatibility with the
    old AI-backed signature; it is ignored -- this parser never calls out
    to a network service.
    """
    if not os.path.exists(filepath):
        raise ValueError(f"File not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".csv", ".xlsx", ".xls"):
        direct = _read_spreadsheet(filepath, ext)
        if direct is not None:
            direct.attrs["report"] = {"mode": "spreadsheet", "pages": 0, "skipped": []}
            return direct
        df = _spreadsheet_to_frame(pd.read_csv(filepath) if ext == ".csv" else pd.read_excel(filepath))
        df.attrs["report"] = {"mode": "spreadsheet", "pages": 0, "skipped": []}
        return df

    if ext in (".doc", ".docx"):
        df = _read_docx(filepath)
        df.attrs["report"] = {"mode": "docx", "pages": 0, "skipped": []}
        return df

    if not ocr_available() and ext not in (".pdf",):
        raise OcrUnavailable(
            "No local OCR engine is installed.\n\n"
            "    ./venv/bin/pip install rapidocr-onnxruntime\n"
        )

    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        df = _read_image(filepath)
        df.attrs["report"] = {
            "mode": "image-ocr",
            "pages": 1,
            "skipped": [],
            "classes": [],
        }
        return df

    if ext == ".pdf":
        from backend.core.timetable_ocr import iter_pdf_pages

        parsed: List[ClassPage] = list(
            iter_pdf_pages(filepath, pages=pages, progress=progress)
        )
        df = _frames_from_pages(parsed)
        sources = {p.source for p in parsed if p.source}
        df.attrs["report"] = {
            "mode": "pdf-text" if sources == {"text-layer"} else "pdf-ocr",
            "pages": len(parsed),
            "classes": [{"page": p.index + 1, "title": p.title} for p in parsed],
            "warnings": [
                {"page": p.index + 1, "title": p.title, "warning": p.warning}
                for p in parsed
                if p.warning
            ],
            "inferred": [
                dict(item, page=p.index + 1, title=p.title)
                for p in parsed
                for item in p.inferred
            ],
            "skipped": [
                dict(item, page=p.index + 1, title=p.title)
                for p in parsed
                for item in p.skipped
            ],
        }
        return df

    raise ValueError(f"Unsupported file format: {ext}")


def ensure_rooms(db, df: pd.DataFrame, camera_source: str = "") -> List[str]:
    """Create timetable rooms that don't exist yet.

    ``timetable.room_id`` is a foreign key onto ``rooms``, so an imported
    room must exist before the rows referencing it can be inserted. New
    rooms get an empty camera source, which the capture code treats as
    "not configured" instead of silently grabbing the laptop webcam --
    assign real cameras from the Cameras tab.
    """
    existing = {r["room_id"] for r in db.get_rooms()}
    created: List[str] = []
    if "RoomID" not in df.columns:
        return created
    for room in sorted({str(r).strip() for r in df["RoomID"] if str(r).strip()}):
        if room not in existing:
            db.upsert_room(room, room, camera_source)
            existing.add(room)
            created.append(room)
    return created


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse a timetable file offline (no API key required)."
    )
    parser.add_argument("filepath")
    parser.add_argument("--pages", help="comma-separated 0-based page numbers")
    parser.add_argument("--csv", help="write the parsed rows to this CSV")
    parser.add_argument("--limit", type=int, help="only parse the first N pages")
    args = parser.parse_args(argv)

    chosen = None
    if args.pages:
        chosen = [int(p) for p in args.pages.split(",") if p.strip() != ""]
    elif args.limit:
        chosen = list(range(args.limit))

    def progress(done: int, total: int, label: str) -> None:
        print(f"  [{done}/{total}] {label}", flush=True)

    df = extract_timetable_data(args.filepath, progress=progress, pages=chosen)
    report = df.attrs.get("report", {})

    print(f"\nmode           : {report.get('mode')}")
    print(f"pages parsed   : {report.get('pages')}")
    print(f"class rows     : {len(df)}")
    if report.get("classes"):
        print("classes found  :")
        for entry in report["classes"]:
            print(f"   p{entry['page']:>3}  {entry['title']}")
    if report.get("warnings"):
        print(f"page warnings  : {len(report['warnings'])}")
        for entry in report["warnings"][:15]:
            print(f"   p{entry['page']:>3}  {entry['title']}: {entry['warning']}")
    if report.get("inferred"):
        print(f"rooms inferred : {len(report['inferred'])} (left blank on the sheet)")
        for entry in report["inferred"][:10]:
            print(f"   p{entry['page']:>3}  {entry.get('Day','')} {entry.get('StartTime','')} "
                  f"{entry.get('Subject','')} -> {entry.get('RoomID','')} ({entry.get('how','')})")
    if report.get("skipped"):
        skipped = report["skipped"]
        print(f"cells skipped  : {len(skipped)}")
        # Grouped first: the total alone says nothing about whether these are
        # one systemic misread or a long tail of genuinely empty cells.
        for reason, count in Counter(e.get("reason", "") for e in skipped).most_common():
            print(f"     {count:>4}  {reason}")
        # Then by subject. A single repeated subject dominating the list is one
        # bug to fix; a long tail of different ones is not the same problem at
        # all, and the reason counts alone can't tell those apart.
        print("   most common subjects skipped:")
        for subject, count in Counter(e.get("Subject", "") for e in skipped).most_common(8):
            shown = subject if len(subject) <= 56 else subject[:53] + "..."
            print(f"     {count:>4}  {shown}")
        for entry in skipped[:15]:
            print(f"   p{entry['page']:>3}  {entry.get('Day','')} {entry.get('StartTime','')} "
                  f"{entry.get('Subject','')}: {entry.get('reason','')}")

    if not df.empty:
        print("\nfirst rows:")
        print(df.head(10).to_string(index=False))

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
