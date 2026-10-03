from __future__ import annotations
import numpy as np
import cv2
import threading
import time
import os
from typing import Optional, List, Dict, Tuple
from pathlib import Path

try:
    import insightface
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False

class FaceEngine:
    DEFAULT_THRESHOLD  = 0.50
    UPLIFTED_THRESHOLD = 0.50

    # Classroom-scale scanning: a face smaller than this (in the full
    # frame) is a back-row candidate for tiled re-detection at a larger
    # relative scale. Two different users must be separated by at least
    # MATCH_MARGIN in similarity or the match is treated as ambiguous
    # rather than guessed.
    SMALL_FACE_PX  = 70
    MATCH_MARGIN   = 0.05
    TILE_IOU_MERGE = 0.4

    def __init__(self, model_dir: Path):
        self.model_dir = str(model_dir)
        self._app: Optional[FaceAnalysis] = None
        self.ready = False
        self._mock_mode = os.environ.get("FACE_DEMO_MODE", "0") == "1"
        self.load_error: Optional[str] = None
        self._load_event = threading.Event()
        self._inference_lock = threading.Lock()

    def load_async(self, callback=None) -> None:
        def _load():
            if self._mock_mode:
                time.sleep(0.5)
                self.ready = True
                self._load_event.set()
                if callback: callback(success=True, mock=True)
                return
            try:
                self._app = FaceAnalysis(
                    name="buffalo_sc",
                    root=self.model_dir,
                    providers=["CPUExecutionProvider"],
                )
                self._app.prepare(ctx_id=0, det_size=(640, 640))
                self.ready = True
                self._load_event.set()
                if callback: callback(success=True, mock=False)
            except Exception as exc:
                self.load_error = str(exc)
                self.ready = False
                self._load_event.set()
                if callback: callback(success=False, mock=False, error=str(exc))

        threading.Thread(target=_load, daemon=True).start()

    def extract_embedding(self, frame: np.ndarray) -> Optional[np.ndarray]:
        if self._mock_mode:
            vec = np.random.randn(512).astype(np.float32)
            return vec / np.linalg.norm(vec)

        if self._app is None:
            return None
        
        with self._inference_lock:
            faces = self._app.get(frame)
            
        if not faces:
            return None
        faces.sort(key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]), reverse=True)
        emb = faces[0].embedding
        return emb / np.linalg.norm(emb)

    def get_all_faces(self, frame: np.ndarray):
        if self._mock_mode:
            return []
        if self._app is None:
            return []
        
        with self._inference_lock:
            faces = self._app.get(frame)
            
        for f in faces:
            if f.embedding is not None:
                f.embedding = f.embedding / np.linalg.norm(f.embedding)
        return faces

    def try_get_all_faces(self, frame: np.ndarray):
        """Non-blocking variant of get_all_faces.

        Returns None (instead of blocking) if another thread — e.g. the
        background SessionLogic orchestrator — currently holds the
        inference lock. Intended for use inside GUI ``.after()`` callbacks,
        so the Tk main-loop thread never sits blocked waiting on a
        background thread mid-callback (a known trigger for Tcl/Tk
        stability issues, especially on Tk 9.0).
        """
        if self._mock_mode:
            return []
        if self._app is None:
            return []

        if not self._inference_lock.acquire(blocking=False):
            return None
        try:
            faces = self._app.get(frame)
        finally:
            self._inference_lock.release()

        for f in faces:
            if f.embedding is not None:
                f.embedding = f.embedding / np.linalg.norm(f.embedding)
        return faces

    def detect_faces_tiled(self, frame: np.ndarray,
                            small_face_px: Optional[int] = None,
                            tile_grid: Tuple[int, int] = (2, 2),
                            overlap: float = 0.25):
        """
        Multi-scale face detection for a classroom-wide shot.

        A single full-frame pass under-serves back-row students: their
        faces may be far smaller than what the detector resolves well.
        This runs the full frame first; only if that pass finds a small
        face (or nothing at all -- the whole frame may just be too wide)
        does it also run detection on overlapping tiles, where the same
        physical face occupies a larger fraction of the tile. Duplicate
        detections of one face (full frame + tile, or overlapping tiles)
        are merged by IoU on bounding boxes mapped back to full-frame
        coordinates, keeping the highest-confidence detection.

        Returns a list of face objects shaped like get_all_faces(), with
        .bbox/.kps in full-frame coordinates. Cost stays at one inference
        pass for ordinary close-up shots; tiling only triggers for the
        classroom case it exists for.
        """
        if self._mock_mode or self._app is None:
            return self.get_all_faces(frame)

        small_face_px = small_face_px if small_face_px is not None else self.SMALL_FACE_PX
        h, w = frame.shape[:2]
        full_faces = self.get_all_faces(frame)

        has_small_face = any(
            (f.bbox[2] - f.bbox[0]) < small_face_px or (f.bbox[3] - f.bbox[1]) < small_face_px
            for f in full_faces
        )
        if full_faces and not has_small_face:
            return full_faces

        rows, cols = tile_grid
        step_h, step_w = h // rows, w // cols
        pad_h, pad_w = int(step_h * overlap / 2), int(step_w * overlap / 2)

        all_detections = list(full_faces)
        for r in range(rows):
            for c in range(cols):
                y0 = max(0, r * step_h - pad_h)
                x0 = max(0, c * step_w - pad_w)
                y1 = min(h, (r + 1) * step_h + pad_h)
                x1 = min(w, (c + 1) * step_w + pad_w)
                tile = frame[y0:y1, x0:x1]
                if tile.size == 0:
                    continue
                for f in self.get_all_faces(tile):
                    f.bbox = f.bbox + np.array([x0, y0, x0, y0], dtype=f.bbox.dtype)
                    if getattr(f, "kps", None) is not None:
                        f.kps = f.kps + np.array([x0, y0], dtype=f.kps.dtype)
                    all_detections.append(f)

        return self._dedup_by_iou(all_detections)

    @staticmethod
    def _iou(box_a, box_b) -> float:
        xa1, ya1, xa2, ya2 = box_a
        xb1, yb1, xb2, yb2 = box_b
        ix1, iy1 = max(xa1, xb1), max(ya1, yb1)
        ix2, iy2 = min(xa2, xb2), min(ya2, yb2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        area_a = max(0.0, xa2 - xa1) * max(0.0, ya2 - ya1)
        area_b = max(0.0, xb2 - xb1) * max(0.0, yb2 - yb1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def _dedup_by_iou(self, detections, iou_threshold: Optional[float] = None):
        """Collapse duplicate detections of the same physical face (seen in
        the full frame and/or an overlapping tile) down to one, keeping the
        highest-confidence detection."""
        iou_threshold = iou_threshold if iou_threshold is not None else self.TILE_IOU_MERGE
        if not detections:
            return []
        detections = sorted(detections, key=lambda f: float(getattr(f, "det_score", 0.0)), reverse=True)
        kept = []
        for f in detections:
            if not any(self._iou(f.bbox, k.bbox) >= iou_threshold for k in kept):
                kept.append(f)
        return kept

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def match_with_margin(self, query_emb: np.ndarray, users: List[Dict],
                           is_uplifted: bool = False,
                           margin: Optional[float] = None) -> Optional[Tuple[str, str, str, float]]:
        """
        Same acceptance rule as match() (best user must clear the
        threshold), plus a safety check match() doesn't have: the winner
        must beat the next-best *different* user by at least `margin`.
        Two students who both score near the threshold are left
        ambiguous (returns None) rather than guessed -- this is what
        keeps a fixed 0.50 threshold safe to use across a full 50-student
        roster instead of needing to lower it.
        """
        threshold = self.UPLIFTED_THRESHOLD if is_uplifted else self.DEFAULT_THRESHOLD
        margin = margin if margin is not None else self.MATCH_MARGIN

        best_per_user = []
        for u in users:
            all_embs = [u["embedding"]]
            if u.get("multi_embeddings"):
                all_embs.extend(u["multi_embeddings"])
            u_best = max(self.cosine_similarity(query_emb, emb) for emb in all_embs)
            best_per_user.append((u_best, u))

        if not best_per_user:
            return None

        best_per_user.sort(key=lambda t: t[0], reverse=True)
        top_conf, top_user = best_per_user[0]
        second_conf = best_per_user[1][0] if len(best_per_user) > 1 else -1.0

        if top_conf < threshold or (top_conf - second_conf) < margin:
            return None

        uid = top_user.get("roll_no") or top_user.get("user_id")
        return uid, top_user["name"], top_user["role"], top_conf

    def match(self, query_emb: np.ndarray,
              users: List[Dict],
              is_uplifted: bool = False) -> Optional[Tuple[str, str, str, float]]:
        threshold = self.UPLIFTED_THRESHOLD if is_uplifted else self.DEFAULT_THRESHOLD
        best_conf  = -1.0
        best_user = None

        for u in users:
            all_embs = [u["embedding"]]
            if u.get("multi_embeddings"):
                all_embs.extend(u["multi_embeddings"])

            for emb in all_embs:
                c = self.cosine_similarity(query_emb, emb)
                if c > best_conf:
                    best_conf    = c
                    best_user = u

        if best_user and best_conf >= threshold:
            uid = best_user.get("roll_no") or best_user.get("user_id")
            return uid, best_user["name"], best_user["role"], best_conf
        return None

class FaceMeshHelper:
    def compute_eye_aspect(self, frame_bgr: np.ndarray, kps: np.ndarray) -> float:
        if kps is None or len(kps) < 2:
            return 0.25
        h, w = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        scores = []
        for eye_pt in [kps[0], kps[1]]:
            ex, ey = int(eye_pt[0]), int(eye_pt[1])
            rw, rh = int(w * 0.05), int(h * 0.04)
            x1, x2 = max(0, ex - rw), min(w, ex + rw)
            y1, y2 = max(0, ey - rh), min(h, ey + rh)
            crop = gray[y1:y2, x1:x2]
            if crop.size == 0: continue
            sobel_y = cv2.Sobel(crop, cv2.CV_64F, 0, 1, ksize=3)
            vert_grad = float(np.mean(np.abs(sobel_y)))
            scores.append(vert_grad / 40.0)
        return float(np.mean(scores)) if scores else 0.25

    def estimate_pose(self, kps: np.ndarray) -> str:
        """Estimate head pose direction from 5 face keypoints.
        
        Supports all angles including deep head-down (writing/looking at desk)
        which is critical for attendance when students may be looking at their
        notes or writing an exam.
        
        Returns one of: FRONT, LEFT, RIGHT, UPLIFTED (TILT-UP), TILT_DOWN, WRITING
        """
        if kps is None or len(kps) < 5:
            return "FRONT"
        left_eye, right_eye, nose, left_mouth, right_mouth = kps[:5]
        d_left = np.linalg.norm(nose - left_eye)
        d_right = np.linalg.norm(nose - right_eye)
        if d_right < 1e-5: return "FRONT"
        yaw_ratio = d_left / d_right
        eyes_y = (left_eye[1] + right_eye[1]) / 2.0
        mouth_y = (left_mouth[1] + right_mouth[1]) / 2.0
        eye_to_nose = nose[1] - eyes_y
        nose_to_mouth = mouth_y - nose[1]
        pitch_ratio = eye_to_nose / (nose_to_mouth + 1e-6)

        # WRITING: extreme head-down pose (student looking at desk/paper)
        # pitch_ratio very large means eyes far above, nose barely above mouth
        if pitch_ratio > 2.80: return "WRITING"
        if yaw_ratio > 1.55: return "RIGHT"
        elif yaw_ratio < 0.65: return "LEFT"
        elif pitch_ratio < 0.78: return "UPLIFTED (TILT-UP)"
        elif pitch_ratio > 1.40: return "TILT_DOWN"
        return "FRONT"

    def draw_mesh_overlay(self, frame_rgb: np.ndarray, bbox: np.ndarray, kps: np.ndarray, pose: str = "FRONT") -> np.ndarray:
        canvas = frame_rgb.copy()
        if bbox is None: return canvas
        x1, y1, x2, y2 = map(int, bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (79, 142, 247), 2)
        if kps is not None:
            for pt in kps:
                px, py = int(pt[0]), int(pt[1])
                cv2.circle(canvas, (px, py), 4, (34, 197, 94), -1)
            le, re, nose, lm, rm = map(lambda p: (int(p[0]), int(p[1])), kps[:5])
            cv2.line(canvas, le, nose, (34, 197, 94), 1)
            cv2.line(canvas, re, nose, (34, 197, 94), 1)
            cv2.line(canvas, nose, lm, (34, 197, 94), 1)
            cv2.line(canvas, nose, rm, (34, 197, 94), 1)
            cv2.line(canvas, lm, rm, (34, 197, 94), 1)
            nx, ny = nose
            if "UPLIFTED" in pose or "TILT-UP" in pose:
                cv2.arrowedLine(canvas, (nx, ny), (nx, ny - 35), (245, 158, 11), 3)
            elif "DOWN" in pose:
                cv2.arrowedLine(canvas, (nx, ny), (nx, ny + 35), (245, 158, 11), 3)
            elif pose == "LEFT":
                cv2.arrowedLine(canvas, (nx, ny), (nx - 35, ny), (79, 142, 247), 3)
            elif pose == "RIGHT":
                cv2.arrowedLine(canvas, (nx, ny), (nx + 35, ny), (79, 142, 247), 3)
        return canvas