"""Offline OCR + table-grid reconstruction for scanned college timetables.

Targets the "class-wise" timetable PDFs that aSc Timetables (and similar
school-admin packages) export -- the format most colleges hand out. Every
page is one class, drawn as a ruled grid:

    * a header row of period numbers, each with its time range underneath
    * a left column of weekday abbreviations (Mo, Tu, We, ...)
    * one cell per (day, period) holding Room / Subject / Teacher stacked
      top-to-bottom, separated by noticeably larger vertical gaps
    * lab slots merged across several period columns (a colspan), which we
      collapse back into a single start-end block

Everything runs locally: PyMuPDF rasterises the page, OpenCV finds the
ruling lines, and RapidOCR (ONNX, on the onnxruntime InsightFace already
uses) reads the text. No network calls, no API keys.

The grid geometry is never hard-coded -- line positions, weekday rows and
period times are all detected per page, so a different college's export
with different period counts or timings still parses.
"""
from __future__ import annotations

import os
import shutil
import sys
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError("opencv-python is required for timetable OCR") from exc

try:
    import fitz  # PyMuPDF
except ImportError as exc:  # pragma: no cover - dependency guard
    raise ImportError("PyMuPDF is required for timetable OCR") from exc

try:  # Reuse the app's canonical weekday map so parsed days always load.
    from backend.core.db import DAY_MAP
except Exception:  # pragma: no cover - standalone use
    DAY_MAP = {
        "mo": "monday", "mon": "monday", "monday": "monday",
        "tu": "tuesday", "tue": "tuesday", "tuesday": "tuesday",
        "we": "wednesday", "wed": "wednesday", "wednesday": "wednesday",
        "th": "thursday", "thu": "thursday", "thurs": "thursday", "thursday": "thursday",
        "fr": "friday", "fri": "friday", "friday": "friday",
        "sa": "saturday", "sat": "saturday", "saturday": "saturday",
        "su": "sunday", "sun": "sunday", "sunday": "sunday",
    }

RENDER_DPI = 220
MIN_TEXT_LAYER_CHARS = 40  # below this a page is treated as a scan

# A ruling line must stay unbroken across at least this share of the longest
# rule on the page. Measured as a *contiguous run*, not a count of inked
# pixels: text is dense but broken, a rule is dense and unbroken, and only
# the second test tells the two apart.
H_LINE_MIN_FRAC = 0.60
V_LINE_MIN_FRAC = 0.60

# Gaps up to this many pixels along a rule are bridged before measuring, so a
# hairline break where two cells' borders meet doesn't split one rule in two.
LINE_BRIDGE_PX = 25

# Fraction of a cell edge that must be inked for the edge to be "drawn".
EDGE_MIN_FRAC = 0.55
EDGE_TOL_PX = 4

# A rule is a pixel or two of ink; a line of lettering, once the closing in
# _line_masks has bridged the gaps between its words, is as tall as the
# glyphs. Both can span a block's full width, so thickness is what tells them
# apart -- and unlike "is any text near this row", it cannot throw away a real
# rule just because aSc set the text tight against it.
MAX_RULE_THICKNESS = 8

# Share of the header row's height a period boundary must span to be believed.
# The header is the one band aSc never merges -- it holds a single cell per
# period -- so every real boundary runs its full height, and nothing a line of
# header text leaves behind comes close.
HEADER_LINE_MIN_FRAC = 0.80

# Two rule positions this close together are one rule found twice.
LINE_MERGE_TOL_PX = 6

# How far across the table a weekday label may sit. Generous on purpose: it
# only has to exclude the body of the grid, not pin the exact column width.
DAY_ZONE_FRAC = 0.30

TIME_RE = re.compile(
    r"(\d{1,2})\s*[:.]\s*(\d{2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*[:.]\s*(\d{2})",
    re.IGNORECASE,
)

# Room codes: "LB-103", "MB-228", "R101", "Room 12", "Lab-3", "Hall A1".
ROOM_RE = re.compile(
    r"^(?:"
    r"[A-Z]{1,6}\s*[-/–]?\s*\d{1,4}[A-Z]?"
    r"|(?:room|hall|lab|class|lecture)\s*(?:no\.?)?\s*[A-Z]?\d{1,4}"
    r")$",
    re.IGNORECASE,
)

# "Mr. Raj Singh", "Dr. Shagupta Khan", "Ms Rani Roy", "Prof. A. Kumar"
TEACHER_TITLE_RE = re.compile(
    r"^(?:mr|mrs|ms|miss|dr|prof|shri|smt|sri|er|adv|ca)\.?\s+\S",
    re.IGNORECASE,
)

# The label aSc prints beside a room when it splits a class into groups, as in
# "MB-332 Group1". Deliberately narrow: it has to be recognisable as a label,
# because the only other thing that follows a room on a line is a second room.
GROUP_LABEL_RE = re.compile(r"^(?:group|grp|batch|section|sec|g)\s*\.?\s*[\w-]*$", re.IGNORECASE)


class OcrUnavailable(RuntimeError):
    """Raised when no local OCR engine can be loaded."""


@dataclass
class TextBox:
    """One line of recognised text, in rendered-image pixel coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class ClassPage:
    """Parsed contents of one timetable page (one class/group)."""

    index: int
    title: str = ""
    # "text-layer" for a born-digital PDF, "ocr" for a scan, "" if unknown.
    source: str = ""
    rows: List[Dict[str, str]] = field(default_factory=list)
    # Cells holding text that cannot form a usable class: no teacher, no
    # room, or no period time. Reported so nothing vanishes silently.
    skipped: List[Dict[str, str]] = field(default_factory=list)
    # Rows whose room aSc left blank and which we filled back in, with the
    # evidence used. Kept separate so an import can be audited.
    inferred: List[Dict[str, str]] = field(default_factory=list)
    warning: str = ""

    @property
    def importable(self) -> List[Dict[str, str]]:
        return self.rows


# ---------------------------------------------------------------------------
# OCR engine
# ---------------------------------------------------------------------------

_ENGINE = None
_ENGINE_TRIED = False
ENGINE_NAME = ""


def _build_engine():
    """Load whichever RapidOCR generation is installed, once per process."""
    global _ENGINE, _ENGINE_TRIED, ENGINE_NAME
    if _ENGINE_TRIED:
        return _ENGINE
    _ENGINE_TRIED = True

    # Use the ONNXRuntime distribution first. It is the most predictable
    # cross-platform backend for this application (Windows/macOS/Linux).
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
        ENGINE_NAME = "rapidocr-onnxruntime"
        return _ENGINE
    except Exception:
        pass

    # Also support newer RapidOCR releases when available.
    try:
        from rapidocr import RapidOCR

        _ENGINE = RapidOCR()
        ENGINE_NAME = "rapidocr"
        return _ENGINE
    except Exception:
        pass

    _ENGINE = None
    return None


def ocr_available() -> bool:
    return _build_engine() is not None


def _bbox(points) -> Tuple[float, float, float, float]:
    arr = np.asarray(points, dtype=float).reshape(-1, 2)
    return (
        float(arr[:, 0].min()),
        float(arr[:, 1].min()),
        float(arr[:, 0].max()),
        float(arr[:, 1].max()),
    )


def _normalise_ocr_output(out) -> List[Tuple[object, str, float]]:
    """Yield (points, text, score) for both RapidOCR result shapes."""
    items: List[Tuple[object, str, float]] = []

    boxes = getattr(out, "boxes", None)
    txts = getattr(out, "txts", None)
    scores = getattr(out, "scores", None)
    if boxes is not None and txts is not None:
        for i, text in enumerate(txts):
            score = float(scores[i]) if scores is not None and i < len(scores) else 1.0
            items.append((boxes[i], str(text), score))
        return items

    if isinstance(out, dict) and out.get("boxes") is not None:
        for i, text in enumerate(out.get("txts") or []):
            sc = out.get("scores")
            items.append((out["boxes"][i], str(text), float(sc[i]) if sc is not None else 1.0))
        return items

    # RapidOCR v1 returns (result, elapse); v2 with return_word_box=False too.
    result = out[0] if isinstance(out, (tuple, list)) and len(out) == 2 else out
    if result:
        for entry in result:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                points, text, score = entry[0], entry[1], entry[2]
                try:
                    items.append((points, str(text), float(score)))
                except (TypeError, ValueError):
                    continue
    return items



def run_ocr(img_bgr: np.ndarray) -> List[TextBox]:
    """Run the bundled RapidOCR engine and return normalised text boxes.

    The project supports both ``rapidocr`` v2 and ``rapidocr-onnxruntime`` v1.
    Keeping this adapter here is important: the parser must not depend on a
    system Tesseract installation just to read a scanned timetable.
    """
    engine = _build_engine()
    if engine is None:
        return []
    try:
        out = engine(img_bgr)
    except TypeError:
        # Some RapidOCR releases expose ``__call__(image, ...)`` with a
        # slightly different optional signature.
        out = engine(img_bgr, use_det=True, use_cls=True, use_rec=True)
    except Exception:
        return []

    boxes: List[TextBox] = []
    for points, text, score in _normalise_ocr_output(out):
        text = str(text).strip()
        if not text:
            continue
        try:
            x0, y0, x1, y1 = _bbox(points)
        except Exception:
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        boxes.append(TextBox(text=text, x0=x0, y0=y0, x1=x1, y1=y1,
                             score=max(0.0, min(1.0, float(score)))))
    return boxes


def _ocr_quality(boxes: Sequence[TextBox]) -> int:
    """Score OCR output for timetable-specific evidence."""
    score = len(boxes)
    day_hits = 0
    time_hits = 0
    for box in boxes:
        if _weekday_label(box.text):
            day_hits += 1
        if _parse_times(box.text):
            time_hits += 1
    return score + day_hits * 20 + time_hits * 25


def run_tesseract_ocr(img_bgr: np.ndarray, psm: int = 6) -> List[TextBox]:
    """OCR fallback using the locally installed Tesseract engine.

    RapidOCR can fail to initialize on some macOS/Python environments even
    when its package is installed. Tesseract is used only as a local fallback
    and returns word boxes in the same coordinate system as RapidOCR.
    """
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return []

    # Timetable text is small and light. A modest grayscale/contrast pass
    # improves room codes, period times and teacher names without destroying
    # the ruling grid used by the parser.
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    data = pytesseract.image_to_data(
        gray,
        config=f"--oem 3 --psm {psm}",
        output_type=Output.DICT,
    )
    boxes: List[TextBox] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = 0.0
        if conf < 15:
            continue
        x = float(data["left"][i])
        y = float(data["top"][i])
        w = float(data["width"][i])
        h = float(data["height"][i])
        if w <= 0 or h <= 0:
            continue
        boxes.append(TextBox(text=text, x0=x, y0=y, x1=x+w, y1=y+h,
                             score=max(0.0, min(1.0, conf / 100.0))))
    return boxes


def _tesseract_robust(img_bgr: np.ndarray) -> List[TextBox]:
    """Try Tesseract configurations suited to ruled timetable pages."""
    candidates: List[List[TextBox]] = []
    h, w = img_bgr.shape[:2]
    for psm in (6, 11):
        try:
            candidates.append(run_tesseract_ocr(img_bgr, psm=psm))
        except Exception:
            pass

    # Small text in a dense timetable benefits from a 2x pass.
    up = cv2.resize(img_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    for psm in (6, 11):
        try:
            raw = run_tesseract_ocr(up, psm=psm)
            candidates.append([
                TextBox(b.text, b.x0/2, b.y0/2, b.x1/2, b.y1/2, b.score)
                for b in raw
            ])
        except Exception:
            pass
    return max(candidates, key=_ocr_quality, default=[])


def run_ocr_robust(img_bgr: np.ndarray) -> List[TextBox]:
    """Run OCR with lightweight fallbacks for scanned/low-contrast PDFs.

    The first pass keeps the original image. If timetable-specific evidence
    (weekday labels/time ranges) is weak, retry with contrast enhancement and
    an upscaled grayscale image. Coordinates are mapped back to the original
    image so the grid parser remains aligned with the PDF geometry.
    """
    candidates: List[List[TextBox]] = []

    try:
        candidates.append(run_ocr(img_bgr))
    except Exception:
        candidates.append([])

    best = max(candidates, key=_ocr_quality, default=[])
    best_score = _ocr_quality(best)
    h, w = img_bgr.shape[:2]

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    variants = [enhanced_bgr]
    # Upscaling is especially useful for small timetable text in scanned PDFs.
    variants.append(cv2.resize(enhanced_bgr, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC))

    for variant in variants:
        try:
            boxes = run_ocr(variant)
        except Exception:
            continue
        scale_x = w / float(variant.shape[1])
        scale_y = h / float(variant.shape[0])
        mapped = [
            TextBox(
                text=b.text,
                x0=b.x0 * scale_x, y0=b.y0 * scale_y,
                x1=b.x1 * scale_x, y1=b.y1 * scale_y,
                score=b.score,
            )
            for b in boxes
        ]
        score = _ocr_quality(mapped)
        if score > best_score:
            best, best_score = mapped, score

    # RapidOCR is the primary cross-platform engine. It ships its ONNX
    # recognition models through the Python package, so the application does
    # not depend on a macOS-only OCR framework or a platform-specific API.
    #
    # Tesseract is only an optional secondary fallback when the user has a
    # local Tesseract executable installed. The timetable parser never relies
    # on it being present.
    if not best or _ocr_quality(best) < 8 or not any(_parse_times(b.text) for b in best):
        try:
            fallback = _tesseract_robust(img_bgr)
            if _ocr_quality(fallback) > best_score:
                best = fallback
        except Exception:
            pass

    return best


# ---------------------------------------------------------------------------
# Page rasterisation / text layer
# ---------------------------------------------------------------------------


def render_page_bgr(page, dpi: int = RENDER_DPI) -> np.ndarray:
    """Rasterise a PyMuPDF page to a BGR numpy image."""
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    buf = np.frombuffer(pix.samples, dtype=np.uint8)
    if pix.n == 1:
        img = buf.reshape(pix.height, pix.width).copy()
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = buf.reshape(pix.height, pix.width, pix.n).copy()
    if pix.n == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def text_layer_boxes(page, dpi: int = RENDER_DPI) -> List[TextBox]:
    """Word-level boxes from a real PDF text layer, in image coordinates.

    Lets born-digital timetables skip OCR entirely and use exact glyph
    positions instead of recognised ones.

    One box per *word*, deliberately. PyMuPDF's own block/line numbering
    runs layout analysis that happily puts two side-by-side timetable cells
    on the same "line"; a box spanning both would have its centre land in
    whichever cell happened to be in the middle. Keeping boxes atomic means
    a cell only ever sees its own words, and ``_group_lines`` rebuilds the
    lines within that cell afterwards.
    """
    zoom = dpi / 72.0
    try:
        words = page.get_text("words")
    except Exception:
        return []

    boxes: List[TextBox] = []
    for w in words:
        if len(w) < 5:
            continue
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if not str(text).strip():
            continue
        boxes.append(
            TextBox(
                text=str(text).strip(),
                x0=float(x0) * zoom,
                y0=float(y0) * zoom,
                x1=float(x1) * zoom,
                y1=float(y1) * zoom,
                score=1.0,
            )
        )
    return boxes


def page_boxes(page, dpi: int = RENDER_DPI) -> Tuple[List[TextBox], str]:
    """Return text boxes for a page plus how they were obtained."""
    if len(page.get_text().strip()) >= MIN_TEXT_LAYER_CHARS:
        boxes = text_layer_boxes(page, dpi)
        if boxes:
            return boxes, "text-layer"
    return run_ocr_robust(render_page_bgr(page, dpi)), "ocr"


# ---------------------------------------------------------------------------
# Ruling-line detection
# ---------------------------------------------------------------------------


# Adaptive thresholding subtracts this from the local mean, so ink means a
# pixel *darker* than its surroundings and the value has to be positive. Sign
# it the other way and the threshold lands above the mean, where the uniform
# paper -- being exactly equal to its own mean -- clears the bar and is read
# as ink: every blank margin becomes an inked run and the grid detector finds
# rules in empty white space.
BINARIZE_C = 2


def _binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, BINARIZE_C
    )


def _line_masks(bw: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Split the ink into horizontal and vertical ruling.

    The opening kernel is deliberately short -- it only has to outlast a glyph
    stroke, which is what keeps lettering out. Deciding which of the survivors
    is a real rule belongs to :func:`_line_positions`, which does it by
    continuity and does it far better than a long kernel can. Keeping the
    kernel short matters because aSc draws its borders per cell: a longer one
    would erase a rule that arrives as a chain of short segments. The closing
    that follows then bridges the hairline joins between those segments, so
    one rule measures as one run.
    """
    h, w = bw.shape
    horiz_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, w // 60), 1))
    vert_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, h // 60)))
    bridge_h = cv2.getStructuringElement(cv2.MORPH_RECT, (LINE_BRIDGE_PX, 1))
    bridge_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, LINE_BRIDGE_PX))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horiz_k)
    horiz = cv2.morphologyEx(horiz, cv2.MORPH_CLOSE, bridge_h)
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vert_k)
    vert = cv2.morphologyEx(vert, cv2.MORPH_CLOSE, bridge_v)
    return horiz, vert


def _group_positions(indices: np.ndarray, gap: int = 4) -> List[int]:
    if len(indices) == 0:
        return []
    centres: List[int] = []
    start = prev = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value - prev <= gap:
            prev = value
        else:
            centres.append((start + prev) // 2)
            start = prev = value
    centres.append((start + prev) // 2)
    return centres


def _longest_runs(binary: np.ndarray) -> np.ndarray:
    """Length of the longest contiguous True run in each row."""
    if binary.size == 0:
        return np.zeros(0, dtype=np.int64)
    columns = np.arange(binary.shape[1], dtype=np.int64)[None, :]
    # Index of the most recent False at or before each position, so a run
    # ending at j measures j minus that index. Both steps accumulate into the
    # one array: at 200 DPI it holds tens of megabytes, and the import builds
    # it twice for each of 192 pages.
    last_false = np.where(binary, -1, columns)
    np.maximum.accumulate(last_false, axis=1, out=last_false)
    np.subtract(columns, last_false, out=last_false)
    return last_false.max(axis=1)


def _line_positions(mask: np.ndarray, along_rows: bool, min_frac: float) -> List[int]:
    """Positions of the ruling lines, scored by their longest unbroken run.

    Scoring a row by how many of its pixels are inked is what a projection
    gives you for free, and it is the wrong measure: a row crossing a line of
    text carries plenty of ink, and a column running down centred cell text
    carries a great deal more, so both sail past a share-of-the-page bar and
    a dozen phantom lines appear among the real ones. A rule is unbroken where
    text is not, so that is what gets measured.
    """
    binary = mask > 0
    if not along_rows:
        binary = binary.T  # one measure then serves both orientations
    if binary.size == 0:
        return []

    runs = _longest_runs(binary)
    longest = int(runs.max())
    if longest <= 0:
        return []
    # Measured against the longest rule on the page -- the table's own extent
    # -- rather than the page size, so a table that doesn't fill the sheet
    # still has its full-height column separators detected.
    return _group_positions(np.where(runs >= longest * min_frac)[0])


def _contiguous_runs(indices: Sequence[int], gap: int = 2) -> List[Tuple[int, int]]:
    """Group sorted indices into (first, last) runs, stepping over tiny gaps."""
    runs: List[Tuple[int, int]] = []
    for value in indices:
        value = int(value)
        if runs and value - runs[-1][1] <= gap:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return runs


def _band_columns(vert: np.ndarray, y0: float, y1: float) -> List[int]:
    """Vertical rules spanning the full height of one band of the table.

    aSc rules each period's cells separately, so wherever a lesson spans two
    periods the boundary between them is simply not drawn, in that row only.
    A boundary left out of half the sheet never reaches the page-wide bar in
    :func:`_line_positions`, which then reports a column three periods wide
    and hands the lessons inside it over as one merged cell.

    The header is the one band that is never merged -- it holds a single cell
    per period -- so every period boundary runs its full height there. Scored
    against the band by itself, that is what shows up.
    """
    top, bottom = max(0, int(y0)), int(y1)
    if bottom - top < 2:
        return []
    band = np.zeros_like(vert)
    band[top:bottom] = vert[top:bottom]
    return _line_positions(band, along_rows=False, min_frac=HEADER_LINE_MIN_FRAC)


def _merge_columns(primary: Sequence[int], extra: Sequence[int]) -> List[int]:
    """Union two sets of rule positions, near-coincident ones counting as one."""
    merged = sorted(int(p) for p in primary)
    for value in sorted(int(e) for e in extra):
        if not any(abs(value - p) <= LINE_MERGE_TOL_PX for p in merged):
            merged.append(value)
    return sorted(merged)


def _vertical_edge_present(vert: np.ndarray, x: float, y0: float, y1: float) -> bool:
    """Is the vertical ruling at `x` actually drawn between y0 and y1?

    Missing means the two cells either side are merged (a lab block).
    """
    h, w = vert.shape
    xs0, xs1 = max(0, int(x) - EDGE_TOL_PX), min(w, int(x) + EDGE_TOL_PX + 1)
    ys0, ys1 = max(0, int(y0) + 2), min(h, int(y1) - 2)
    if xs1 <= xs0 or ys1 <= ys0:
        return False
    band = vert[ys0:ys1, xs0:xs1]
    return float((band > 0).any(axis=1).mean()) >= EDGE_MIN_FRAC


def _horizontal_edge_present(horiz: np.ndarray, y: float, x0: float, x1: float) -> bool:
    h, w = horiz.shape
    ys0, ys1 = max(0, int(y) - EDGE_TOL_PX), min(h, int(y) + EDGE_TOL_PX + 1)
    xs0, xs1 = max(0, int(x0) + 2), min(w, int(x1) - 2)
    if xs1 <= xs0 or ys1 <= ys0:
        return False
    band = horiz[ys0:ys1, xs0:xs1]
    return float((band > 0).any(axis=0).mean()) >= EDGE_MIN_FRAC


def _cell_blocks(
    ys: Sequence[int], xs: Sequence[int], horiz: np.ndarray, vert: np.ndarray
) -> List[Tuple[int, int, int, int]]:
    """Group lattice cells into merged rectangular blocks (r0, r1, c0, c1)."""
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    used = np.zeros((n_rows, n_cols), dtype=bool)
    blocks: List[Tuple[int, int, int, int]] = []

    for r in range(n_rows):
        for c in range(n_cols):
            if used[r, c]:
                continue
            c1 = c
            while c1 + 1 < n_cols and not _vertical_edge_present(
                vert, xs[c1 + 1], ys[r], ys[r + 1]
            ):
                c1 += 1
            r1 = r
            while r1 + 1 < n_rows and all(
                not _horizontal_edge_present(horiz, ys[r1 + 1], xs[cc], xs[cc + 1])
                for cc in range(c, c1 + 1)
            ):
                r1 += 1
            used[r : r1 + 1, c : c1 + 1] = True
            blocks.append((r, r1, c, c1))
    return blocks


def _horizontal_dividers(
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    horiz: np.ndarray,
) -> List[int]:
    """Y positions strictly inside a block where a rule crosses its full width.

    :func:`_line_positions` keeps only rules running nearly the full width of
    the table, and that bar is what stops a row of lettering being read as a
    rule. The cost is that aSc also draws short rules -- the one dividing two
    group lessons stacked in a single merged block -- and those were dropped.
    The block then arrived as one cell, its rooms and teachers ran together
    into a string that matches nothing, and every lesson in it was thrown
    away. Measured against the block rather than the page, that rule clears
    the bar easily.

    Only horizontal cuts are taken. A wrapped subject is left-aligned, so the
    first letters of successive lines share an x, and the closing in
    :func:`_line_masks` bridges the gaps between them into a convincing
    vertical rule down the middle of the text. The page-wide bar is far above
    what that can reach; a block-local one is not, and cutting there slices a
    single lesson in half.

    A row of lettering passes the width test too, for the same bridging
    reason, so a run of covered rows is only accepted when it is thin enough
    to be a rule: glyphs make a band many times that tall.
    """
    height = int(y1) - int(y0)
    if height < 2 or int(x1) - int(x0) < 2:
        return []

    band = horiz[int(y0) : int(y1), int(x0) : int(x1)] > 0
    # A rule is a pixel or two thick and can fall between two sample rows, so
    # a position counts as covered when the row either side of it is covered.
    spread = band.copy()
    if len(spread) > 1:
        spread[1:] |= band[:-1]
        spread[:-1] |= band[1:]

    dividers: List[int] = []
    covered = np.where(spread.mean(axis=1) >= EDGE_MIN_FRAC)[0]
    for start, end in _contiguous_runs(covered):
        if end - start + 1 > MAX_RULE_THICKNESS:
            continue  # too tall to be a rule: this is a line of text
        centre = (start + end) // 2
        # The block's own borders span its full width too and sit at 0 and
        # height, so anything within a tolerance of either is that border.
        if EDGE_TOL_PX < centre < height - 1 - EDGE_TOL_PX:
            dividers.append(int(y0) + centre)
    return dividers


def _block_parts(
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    horiz: np.ndarray,
) -> List[Tuple[float, float]]:
    """The y spans of the lessons a merged block holds, top to bottom.

    A block with no rule drawn inside it comes back as a single span covering
    itself, so the pages that already parse are untouched by this.
    """
    edges = [y0] + _horizontal_dividers(y0, y1, x0, x1, horiz) + [y1]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


# ---------------------------------------------------------------------------
# Cell text interpretation
# ---------------------------------------------------------------------------


def _group_lines(boxes: Sequence[TextBox]) -> List[Dict[str, object]]:
    """Merge OCR boxes into visual text lines, top to bottom."""
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: (b.y0, b.x0))
    lines: List[Dict[str, object]] = []
    for box in ordered:
        for line in lines:
            overlap = min(line["y1"], box.y1) - max(line["y0"], box.y0)
            if overlap > 0.5 * min(line["y1"] - line["y0"], box.height):
                line["boxes"].append(box)
                line["y0"] = min(line["y0"], box.y0)
                line["y1"] = max(line["y1"], box.y1)
                break
        else:
            lines.append({"y0": box.y0, "y1": box.y1, "boxes": [box]})

    lines.sort(key=lambda l: l["y0"])
    for line in lines:
        line["boxes"].sort(key=lambda b: b.x0)
        line["text"] = " ".join(b.text for b in line["boxes"]).strip()
        line["height"] = line["y1"] - line["y0"]
    return [l for l in lines if l["text"]]


def _line_centre(line: Dict[str, object]) -> float:
    return (float(line["y0"]) + float(line["y1"])) / 2.0


def _runs_by_pitch(items: Sequence[Dict[str, object]]) -> List[List[int]]:
    """Group a cell's lines into runs that share the cell's tightest spacing.

    Deliberately scale-free. aSc packs a wrapped subject tightly and leaves
    a wider gap around the room (anchored to the cell's top) and the teacher
    (anchored to the bottom), so the *relative* line pitch separates the
    fields whatever the font size or how much padding OCR put round the
    glyphs -- a fixed pixel threshold does not survive that variation.
    """
    if not items:
        return []
    if len(items) == 1:
        return [[0]]

    pitches = [
        max(0.0, _line_centre(items[i]) - _line_centre(items[i - 1]))
        for i in range(1, len(items))
    ]
    positive = [p for p in pitches if p > 0]
    if positive:
        reference = min(positive)
    else:  # stacked at the same height: fall back to the glyph size
        reference = float(np.median([float(l["height"]) for l in items])) or 1.0
    limit = max(reference * 1.25, 1.0)

    runs: List[List[int]] = [[0]]
    for i, pitch in enumerate(pitches, start=1):
        if pitch > limit:
            runs.append([i])
        else:
            runs[-1].append(i)
    return runs


def _looks_like_room(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 24 or len(text.split()) > 2:
        return False
    if not any(ch.isdigit() for ch in text):
        return False
    return bool(ROOM_RE.match(text))


def _looks_like_teacher(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 60 or any(ch.isdigit() for ch in text):
        return False
    if TEACHER_TITLE_RE.match(text):
        return True
    words = text.split()
    # Untitled names ("Rajesh Bhatt") are only trusted when the cell has
    # enough other content that something must be the teacher.
    return 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words)


def _room_from_line(text: str) -> str:
    """The room code at the front of a line, or "" if there isn't one.

    aSc prints the group beside the room it splits off -- "MB-332 Group1" --
    and the label is not part of the room. Testing the whole line against the
    room shape rejects the one line that does carry a room, and the cell is
    then dropped for having none, so the leading token is tested instead.

    The trailing part has to be recognisably a group label, not merely
    "something". A merged cell holding four parallel lessons prints its four
    rooms side by side -- "MB-325 MB-325 MB-325 MB-325" -- and a looser rule
    would read the first of them, fold the other three into the subject, and
    emit one plausible-looking row standing for four classes. Reporting that
    cell as unreadable is recoverable; inventing a class is not.
    """
    text = text.strip()
    if not text:
        return ""
    if _looks_like_room(text):
        return text
    head, sep, tail = text.partition(" ")
    if sep and _looks_like_room(head) and GROUP_LABEL_RE.match(tail.strip()):
        return head
    return ""


def split_cell_fields(fields: Sequence[str]) -> Tuple[str, str, str]:
    """Return (room, subject, teacher) from a cell's top-to-bottom fields."""
    room = teacher = ""
    rest = [f for f in fields if f.strip()]

    if rest and _looks_like_room(rest[0]):
        room = rest.pop(0)
    if rest and _looks_like_teacher(rest[-1]) and (room or len(rest) >= 2):
        teacher = rest.pop(-1)
    return room, " ".join(rest).strip(), teacher


def split_cell_lines(
    lines: Sequence[Dict[str, object]]
) -> Tuple[str, str, str]:
    """Return (room, subject, teacher) from a cell's positioned lines.

    Uses *where* the lines sit, not only what they say: aSc anchors the room
    to the top of the cell and the teacher to the bottom, with the subject
    wrapped tightly in between. Reading the layout makes this robust to a
    subject that happens to look like a name -- "Financial Accounting" is
    two capitalised words, but it is not in its own paragraph at the foot
    of the cell, so it is never mistaken for a teacher.
    """
    items = [l for l in lines if str(l.get("text", "")).strip()]
    if not items:
        return "", "", ""

    runs = _runs_by_pitch(items)

    room_idx: Optional[int] = None
    room_text = ""
    for i in runs[0]:  # the room is top-anchored
        found = _room_from_line(str(items[i]["text"]))
        if found:
            room_idx, room_text = i, found
            break

    teacher_lines: List[int] = []
    last = len(items) - 1
    floor = 0 if room_idx is None else room_idx + 1
    if last >= floor:
        tail = [i for i in runs[-1] if i >= floor]
        if len(runs) > 1 and tail:
            # The teacher sits at the foot of the cell and the name often
            # wraps. Reading the whole run keeps "Mrs. Bhagya Shree" together
            # with "Sharma"; testing the final line alone discards the title,
            # and the surname left over is then read as part of the subject,
            # which loses the teacher and with it the whole cell. A wrapped
            # name still leads with its title, so that is what is matched.
            joined = " ".join(str(items[i]["text"]) for i in tail).strip()
            leads_with_title = (
                bool(TEACHER_TITLE_RE.match(joined))
                and len(joined) <= 80
                and not any(ch.isdigit() for ch in joined)
            )
            if leads_with_title or (
                len(tail) == 1 and room_idx is not None and _looks_like_teacher(joined)
            ):
                teacher_lines = tail
        if not teacher_lines:
            # No run to trust -- the cell is one flat block, so the single
            # final line is all the evidence there is.
            text = str(items[last]["text"])
            if _looks_like_teacher(text):
                # An untitled name is only trusted when it stands alone at
                # the foot of the cell -- the position aSc puts a teacher in.
                alone_at_foot = len(runs[-1]) == 1 and len(runs) > 1 and room_idx is not None
                if TEACHER_TITLE_RE.match(text) or alone_at_foot:
                    teacher_lines = [last]

    taken = set(teacher_lines)
    subject = " ".join(
        str(l["text"]) for i, l in enumerate(items)
        if i != room_idx and i not in taken
    ).strip()
    room = room_text
    teacher = " ".join(str(items[i]["text"]) for i in teacher_lines).strip()
    return room, subject, teacher


def _pair_key(subject: str, teacher: str) -> Tuple[str, str]:
    """Whitespace/case-insensitive key for matching the same class slot."""
    return (_squash(subject), _squash(teacher))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _nearest_band(ys: Sequence[int], y: float) -> Optional[int]:
    """Index of the row band whose middle sits closest to y."""
    if len(ys) < 2:
        return None
    best = 0
    best_distance: Optional[float] = None
    for r in range(len(ys) - 1):
        distance = abs(y - (ys[r] + ys[r + 1]) / 2.0)
        if best_distance is None or distance < best_distance:
            best, best_distance = r, distance
    return best


def _largest_text_line(boxes: Sequence[TextBox]) -> str:
    """The fullest line of the largest text in a box list.

    Grouped into lines before anything is measured, because a word's box is
    only as tall as its own letters. "B.Com-1st" and "Sem" are set identically
    yet measure differently, so comparing boxes would drop whichever word
    happened to carry no ascender and hand back a silently truncated class
    name. A line is sized by its tallest word, which no single word's letters
    can skew, and once the line is chosen every word on it is kept.
    """
    if not boxes:
        return ""
    size = max(b.height for b in boxes)
    tolerance = max(6.0, size * 0.4)

    ordered = sorted(boxes, key=lambda b: b.cy)
    lines: List[List[TextBox]] = [[ordered[0]]]
    for box in ordered[1:]:
        # Anchored on the line's first box so a long run of slightly
        # descending centres cannot drift into the next line's territory.
        if box.cy - lines[-1][0].cy <= tolerance:
            lines[-1].append(box)
        else:
            lines.append([box])

    best = max(lines, key=lambda line: max(b.height for b in line))
    best.sort(key=lambda b: b.x0)
    return " ".join(b.text for b in best).strip()


def _weekday_label(text: str) -> str:
    """Canonical weekday for a *label* box, else "".

    The length guard matters: without it a subject like "Monday Special"
    would canonicalise through its first three letters and be read as a
    weekday, inventing a row that isn't there.
    """
    letters = re.sub(r"[^a-z]", "", (text or "").lower())
    if not 2 <= len(letters) <= 9:
        return ""
    return canonical_day(text)


def canonical_day(text: str) -> str:
    """Map an OCR'd weekday label onto the app's canonical weekday name."""
    key = re.sub(r"[^a-z]", "", (text or "").lower())
    if not key:
        return ""
    if key in DAY_MAP:
        return DAY_MAP[key]
    return DAY_MAP.get(key[:3], "")


def _parse_times(text: str) -> Optional[Tuple[str, str]]:
    match = TIME_RE.search(text or "")
    if not match:
        return None
    start_h, start_m, end_h, end_m = (int(g) for g in match.groups())
    if start_h > 23 or end_h > 23 or start_m > 59 or end_m > 59:
        return None
    return f"{start_h:02d}:{start_m:02d}", f"{end_h:02d}:{end_m:02d}"


def _boxes_in(
    boxes: Sequence[TextBox], x0: float, y0: float, x1: float, y1: float
) -> List[TextBox]:
    return [b for b in boxes if x0 <= b.cx < x1 and y0 <= b.cy < y1]


def _join_text(boxes: Sequence[TextBox]) -> str:
    """Join a box list into one string, line by line from the top.

    Note ``_group_lines`` yields dicts, not TextBoxes, so the text has to be
    read as a key -- reading it as an attribute raises only once a box list
    is actually non-empty, which is exactly the case worth getting right.
    """
    return " ".join(str(line["text"]) for line in _group_lines(boxes))



def _axis_candidates(mask: np.ndarray, axis: str, start: int = 0, end: Optional[int] = None,
                     threshold: float = 0.35) -> List[int]:
    """Find strong ruling positions from a cropped projection."""
    if end is None:
        end = mask.shape[1] if axis == "x" else mask.shape[0]
    if axis == "x":
        crop = mask[start:end, :]
        score = (crop > 0).mean(axis=0)
    else:
        crop = mask[:, start:end]
        score = (crop > 0).mean(axis=1)
    idx = np.where(score >= threshold)[0]
    return _group_positions(idx, gap=8)


def _regular_column_sequence(candidates: Sequence[int]) -> List[int]:
    """Pick the long, nearly equally-spaced timetable grid from header rules."""
    vals = sorted(set(int(v) for v in candidates))
    if len(vals) < 6:
        return vals
    best: List[int] = []
    best_cv = float("inf")
    n = len(vals)
    for i in range(n):
        seq = [vals[i]]
        gaps: List[float] = []
        for j in range(i + 1, n):
            gap = vals[j] - vals[j - 1]
            if gaps:
                med = float(np.median(gaps))
                if gap < 0.65 * med or gap > 1.55 * med:
                    break
            gaps.append(gap)
            seq.append(vals[j])
        if len(gaps) >= 3:
            mean = float(np.mean(gaps))
            cv = float(np.std(gaps) / mean) if mean else float("inf")
        else:
            cv = float("inf")
        if len(seq) > len(best) or (len(seq) == len(best) and cv < best_cv):
            best, best_cv = seq, cv
    return best if len(best) >= 6 else vals


def _regular_row_sequence(candidates: Sequence[int]) -> List[int]:
    """Choose the timetable rows while dropping outer page borders."""
    vals = sorted(set(int(v) for v in candidates))
    if len(vals) < 6:
        return vals
    best: List[int] = []
    n = len(vals)
    for i in range(n):
        seq = [vals[i]]
        gaps: List[float] = []
        for j in range(i + 1, n):
            gap = vals[j] - vals[j - 1]
            if gaps:
                med = float(np.median(gaps))
                # The first header row is intentionally shorter than the
                # weekday rows. After that, row heights are stable.
                if gap < 0.45 * med or gap > 1.55 * med:
                    break
            gaps.append(gap)
            seq.append(vals[j])
        if len(seq) > len(best):
            best = seq
    return best if len(best) >= 6 else vals


def _extract_header_period_times(
    boxes: Sequence[TextBox], xs: Sequence[int], y0: int, y1: int
) -> Dict[int, Tuple[str, str]]:
    """Read period times and infer a missing endpoint from adjacent periods."""
    raw: Dict[int, List[str]] = {}
    time_pat = re.compile(r"(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})(?!\d)")
    for c in range(1, len(xs) - 1):
        text = _join_text(_boxes_in(boxes, xs[c], y0, xs[c + 1], y1))
        vals = [f"{int(h):02d}:{int(m):02d}" for h, m in time_pat.findall(text)]
        if vals:
            raw[c] = vals[:2]

    result: Dict[int, Tuple[str, str]] = {}
    for c, vals in raw.items():
        if len(vals) >= 2:
            result[c] = (vals[0], vals[1])
    # Single visible time: in this aSc format it is the start when the next
    # period supplies the missing end, or the end when the previous period
    # supplies the start. This fixes common OCR misses such as "15:20 -".
    for c, vals in raw.items():
        if c in result or not vals:
            continue
        t = vals[0]
        prev_end = result.get(c - 1, ("", ""))[1]
        next_start = result.get(c + 1, ("", ""))[0]
        if prev_end == t and next_start:
            result[c] = (t, next_start)
        elif next_start == t and prev_end:
            result[c] = (prev_end, t)
        elif next_start:
            result[c] = (t, next_start)
        elif prev_end:
            result[c] = (prev_end, t)
    # A second pass handles a chain where the following period was itself
    # initially missing one endpoint.
    for c in range(1, len(xs) - 1):
        if c not in result:
            prev = result.get(c - 1)
            nxt = result.get(c + 1)
            vals = raw.get(c, [])
            if vals and prev and nxt:
                result[c] = (vals[0], nxt[0])
            elif vals and prev:
                result[c] = (prev[1], vals[0])
    return result


def _normalise_room_ocr(text: str) -> str:
    """Repair the small OCR errors common in this timetable's room codes."""
    text = re.sub(r"\s+", "", (text or "").strip().upper())
    if not text:
        return ""
    # The sample/export format uses codes such as MB-235. If OCR inserts a
    # single letter into an otherwise numeric three-digit suffix, discard the
    # stray glyph rather than creating a new room in MySQL.
    m = re.match(r"^([A-Z]{1,6})[-/]?([0-9][A-Z][0-9]{2})$", text)
    if m and m.group(2)[1] in "ZSOIQ":
        return f"{m.group(1)}-{m.group(2)[0]}{m.group(2)[2:]}"
    m = re.match(r"^([A-Z]{1,6})[-/]?([0-9]{3})$", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return text


def _split_asc_cell(
    lines: Sequence[Dict[str, object]],
    cell_y0: Optional[float] = None,
    cell_y1: Optional[float] = None,
) -> Tuple[str, str, str]:
    """Read aSc class cells: room at top, subject in middle, teacher at bottom."""
    items = [l for l in lines if str(l.get("text", "")).strip()]
    if not items:
        return "", "", ""

    room = _normalise_room_ocr(_room_from_line(str(items[0]["text"])))
    room_idx = 0 if room else None

    teacher_idx: List[int] = []
    if len(items) >= 2:
        last = len(items) - 1
        last_center = _line_centre(items[last])
        # aSc anchors faculty text close to the bottom of each cell. Using the
        # cell geometry is more reliable than line spacing: a one-line subject
        # such as "Digital Electronics" has a similar pitch to its teacher.
        bottom_anchored = (
            cell_y0 is not None and cell_y1 is not None
            and last_center >= float(cell_y0) + 0.72 * (float(cell_y1) - float(cell_y0))
        )
        joined = " ".join(str(items[i]["text"]) for i in range(last, -1, -1)).strip()
        title_like = bool(re.match(
            r"^(?:mr|mrs|ms|miss|dr|prof|shri|smt|sri|er|adv|ca)\.?\s+",
            str(items[last]["text"]), re.IGNORECASE
        ))
        if bottom_anchored or title_like:
            teacher_idx = [last]

    taken = set(teacher_idx)
    subject = " ".join(
        str(items[i]["text"]) for i in range(len(items))
        if i != room_idx and i not in taken
    ).strip()
    teacher = " ".join(str(items[i]["text"]) for i in teacher_idx).strip()
    return room, subject, teacher


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------


def parse_page(page, index: int = 0, dpi: int = RENDER_DPI,
               debug_dir: Optional[str] = None) -> ClassPage:
    """Parse one aSc-style class timetable page.

    This format is the fixed class-wise timetable exported by aSc Timetables:
    a title, a weekday column, nine period columns, and room/subject/teacher
    stacked inside each cell. The grid is detected from the PDF image, so new
    PDFs with the same layout can be uploaded directly.
    """
    result = ClassPage(index=index)
    img = render_page_bgr(page, dpi)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bw = _binarize(gray)
    horiz, vert = _line_masks(bw)

    try:
        boxes, source = page_boxes(page, dpi)
    except Exception:
        boxes, source = run_ocr_robust(img), "ocr"
    result.source = source
    if not boxes:
        result.warning = "no text found on the page"
        return result

    time_boxes = [b for b in boxes if re.search(r"\d{1,2}\s*[:.]\s*\d{2}", b.text)]
    if not time_boxes:
        result.warning = "no timetable period times detected in the header"
        return result

    y_candidates = _axis_candidates(horiz, "y", threshold=0.50)
    first_time_y = min(b.y0 for b in time_boxes)
    last_time_y = max(b.y1 for b in time_boxes)
    below = [y for y in y_candidates if y > last_time_y]
    above = [y for y in y_candidates if y < first_time_y]
    if not below or not above:
        result.warning = "could not locate the timetable header boundaries"
        return result
    header_bottom = min(below)
    header_top = max(above)

    # Use the header as the anchor, then collect the body row rules. The
    # outer page border above/below the table is intentionally excluded.
    body_candidates = [y for y in y_candidates if y >= header_top]
    if header_bottom not in body_candidates:
        body_candidates.append(header_bottom)
    body_candidates = sorted(set(body_candidates))
    start_i = body_candidates.index(header_top) if header_top in body_candidates else 0
    ys = body_candidates[start_i:]
    if len(ys) >= 3:
        gaps = np.diff(ys)
        # Stop at a short final gap (the page border below the last Saturday
        # row). Keep the shorter header gap as part of the table.
        body_pitch = float(np.median(gaps[1:])) if len(gaps) > 1 else float(gaps[0])
        cut = None
        for i, gap in enumerate(gaps):
            if i >= 1 and gap < 0.55 * body_pitch:
                cut = i
                break
        if cut is not None:
            ys = ys[:cut + 1]

    if len(ys) < 4:
        result.warning = f"could not find timetable rows ({len(ys)} rules detected)"
        return result

    x_candidates = _axis_candidates(
        vert, "x", start=max(0, header_top), end=min(vert.shape[0], header_bottom),
        threshold=0.35,
    )
    xs = _regular_column_sequence(x_candidates)
    if len(xs) < 6:
        result.warning = f"could not find timetable columns ({len(xs)} rules detected)"
        return result

    period_times = _extract_header_period_times(boxes, xs, header_top, header_bottom)
    if len(period_times) < 2:
        try:
            hi = cv2.resize(img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            hi_boxes = run_ocr_robust(hi)
            scale = 1.5
            boxes = [
                TextBox(b.text, b.x0/scale, b.y0/scale, b.x1/scale, b.y1/scale, b.score)
                for b in hi_boxes
            ]
            period_times = _extract_header_period_times(boxes, xs, header_top, header_bottom)
            if period_times:
                result.source = "ocr-upscaled"
        except Exception:
            pass
    if not period_times:
        result.warning = "no usable period times found in the timetable header"
        return result

    # Day labels are useful when OCR gets them; row order is authoritative for
    # this export format and recovers blank Saturday/Sunday labels.
    day_names = ["monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"]
    label_zone = xs[1]
    recognised: Dict[int, str] = {}
    for box in boxes:
        if box.cx >= label_zone or not (header_top <= box.cy <= ys[-1]):
            continue
        day = _weekday_label(box.text)
        if not day:
            continue
        row = _nearest_band(ys, box.cy)
        if row is not None and row >= 1:
            recognised[row] = day

    day_count = min(len(ys) - 2, len(day_names))
    day_rows = {r: day_names[r - 1] for r in range(1, day_count + 1)}
    for r, day in recognised.items():
        if 1 <= r <= day_count:
            day_rows[r] = day

    result.title = _largest_text_line([
        b for b in boxes
        if b.cy < header_top and b.height > 10 and not _parse_times(b.text)
    ])

    cells: List[Dict[str, str]] = []
    for r0, r1, c0, c1 in _cell_blocks(ys, xs, horiz, vert):
        if r0 not in day_rows or c0 == 0:
            continue
        span = [c for c in range(c0, c1 + 1) if c in period_times]
        if not span:
            continue

        for by0, by1 in _block_parts(
            ys[r0], ys[r1 + 1], xs[c0], xs[c1 + 1], horiz
        ):
            cell_lines = _group_lines(
                _boxes_in(boxes, xs[c0], by0, xs[c1 + 1], by1)
            )
            if not cell_lines:
                continue
            room, subject, teacher = _split_asc_cell(cell_lines, by0, by1)
            if not subject:
                continue
            cells.append({
                "Day": day_rows[r0],
                "StartTime": period_times[span[0]][0],
                "EndTime": period_times[span[-1]][1],
                "Subject": subject,
                "TeacherID": teacher,
                "RoomID": room,
            })

    # Conservative repairs: reuse a room only when the same subject+teacher
    # is explicitly seen elsewhere on the page, otherwise leave the row
    # unimportable instead of inventing data.
    named: Dict[Tuple[str, str], str] = {}
    for c in cells:
        if c["RoomID"] and c["TeacherID"]:
            named.setdefault(_pair_key(c["Subject"], c["TeacherID"]), c["RoomID"])

    for c in cells:
        if not c["RoomID"] and c["TeacherID"]:
            room = named.get(_pair_key(c["Subject"], c["TeacherID"]))
            if room:
                c["RoomID"] = room
                result.inferred.append({
                    "Day": c["Day"], "StartTime": c["StartTime"],
                    "Subject": c["Subject"], "RoomID": room,
                    "how": "same subject+teacher elsewhere on page",
                })

    for c in cells:
        if not c["RoomID"] or not c["TeacherID"]:
            result.skipped.append({
                "Day": c["Day"], "StartTime": c["StartTime"],
                "EndTime": c["EndTime"], "Subject": c["Subject"],
                "reason": "no room or teacher",
            })
            continue
        result.rows.append(c)

    if debug_dir:
        _write_debug_overlay(debug_dir, index, img, ys, xs, boxes)
    if not result.rows and not result.skipped:
        result.warning = "grid found but no readable class cells"
    return result


def _write_debug_overlay(debug_dir, index, img, ys, xs, boxes) -> None:
    """Save an annotated page so a bad parse can be diagnosed visually."""
    try:
        os.makedirs(debug_dir, exist_ok=True)
        canvas = img.copy()
        for y in ys:
            cv2.line(canvas, (0, int(y)), (canvas.shape[1], int(y)), (0, 0, 255), 2)
        for x in xs:
            cv2.line(canvas, (int(x), 0), (int(x), canvas.shape[0]), (255, 0, 0), 2)
        for b in boxes:
            cv2.rectangle(canvas, (int(b.x0), int(b.y0)), (int(b.x1), int(b.y1)), (0, 200, 0), 1)
        cv2.imwrite(os.path.join(debug_dir, f"grid_{index:03d}.png"), canvas)
    except Exception:
        pass


def iter_pdf_pages(
    pdf_path: str,
    pages: Optional[Sequence[int]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    debug_dir: Optional[str] = None,
    dpi: int = RENDER_DPI,
) -> Iterator[ClassPage]:
    """Parse timetable pages from a PDF, yielding one ClassPage each."""
    doc = fitz.open(pdf_path)
    try:
        wanted = list(pages) if pages is not None else list(range(doc.page_count))
        total = len(wanted)
        for position, page_index in enumerate(wanted):
            page = doc.load_page(page_index)
            try:
                result = parse_page(page, index=page_index, dpi=dpi, debug_dir=debug_dir)
            except OcrUnavailable:
                raise
            except Exception as exc:
                result = ClassPage(index=page_index, warning=f"unreadable page: {exc}")
            if progress:
                progress(position + 1, total, result.title or f"page {page_index + 1}")
            yield result
    finally:
        doc.close()
