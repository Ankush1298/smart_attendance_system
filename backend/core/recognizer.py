"""Turns camera frames into identified people. The scheduler only depends on ``Recognizer``."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, NamedTuple, Optional, Protocol, Sequence

import numpy as np

log = logging.getLogger("smart_attendance.recognizer")


class Detection(NamedTuple):
    roll_no: str
    name: str
    role: str          # 'student' | 'teacher'
    confidence: float
    camera_id: Optional[int] = None


class Recognizer(Protocol):
    def ready(self) -> bool: ...
    def identify(self, frames_by_camera: Dict[Optional[int], Sequence[np.ndarray]],
                 roster: List[Dict[str, Any]]) -> List[Detection]: ...

    def analyze(self, frame: np.ndarray, roster: List[Dict[str, Any]]) -> List[Dict[str, Any]]: ...


class FaceRecognizer:
    """InsightFace-backed recogniser (wraps backend.core.face_core.FaceEngine).

    Per camera: tiled multi-scale detection on every frame, margin-based matching against the
    whole roster (so similar-looking people near the threshold stay unidentified), and a
    student needs two confirming frames when two or more were captured. A teacher is accepted
    anywhere in the frame (no zone): the classroom is the presence area.
    """

    def __init__(self, engine, mesh_helper=None):
        self.engine = engine
        if mesh_helper is None:
            from backend.core.face_core import FaceMeshHelper
            mesh_helper = FaceMeshHelper()
        self.mesh = mesh_helper

    def ready(self) -> bool:
        return bool(self.engine is not None and getattr(self.engine, "ready", False)
                    and not getattr(self.engine, "_mock_mode", False))

    def identify(self, frames_by_camera, roster) -> List[Detection]:
        best: Dict[str, Detection] = {}
        for cam_id, frames in frames_by_camera.items():
            counts: Dict[str, int] = {}
            need = 2 if len(frames) >= 2 else 1
            for frame in frames:
                try:
                    faces = self.engine.detect_faces_tiled(frame)
                except Exception:
                    log.exception("face detection failed on a frame (camera_id=%s)", cam_id)
                    continue
                for f in faces:
                    if getattr(f, "embedding", None) is None:
                        continue
                    try:
                        pose = self.mesh.estimate_pose(f.kps) if hasattr(f, "kps") else "FRONT"
                        match = self.engine.match_with_margin(f.embedding, roster, is_uplifted="UPLIFTED" in pose)
                    except Exception:
                        log.exception("face matching failed (camera_id=%s)", cam_id)
                        continue
                    if not match:
                        continue
                    uid, name, role, conf = match
                    counts[uid] = counts.get(uid, 0) + 1
                    accepted = role == "teacher" or counts[uid] >= need
                    if accepted and (uid not in best or conf > best[uid].confidence):
                        best[uid] = Detection(str(uid), str(name), str(role), float(conf), cam_id)
        return list(best.values())

    def analyze(self, frame, roster) -> List[Dict[str, Any]]:
        """One frame -> every face found, with the identity if it matched (admin camera test only;
        nothing is recorded). Returns [{bbox:[x1,y1,x2,y2], match: {roll_no,name,role,confidence}|None}]."""
        out: List[Dict[str, Any]] = []
        for f in self.engine.detect_faces_tiled(frame):
            bbox = [int(v) for v in f.bbox]
            match = None
            if getattr(f, "embedding", None) is not None:
                try:
                    pose = self.mesh.estimate_pose(f.kps) if hasattr(f, "kps") else "FRONT"
                    m = self.engine.match_with_margin(f.embedding, roster, is_uplifted="UPLIFTED" in pose)
                except Exception:
                    log.exception("face matching failed during analysis")
                    m = None
                if m:
                    match = {"roll_no": str(m[0]), "name": str(m[1]), "role": str(m[2]), "confidence": round(float(m[3]), 3)}
            out.append({"bbox": bbox, "match": match})
        return out
