"""
kyc_core.py - pose maths + KYC scan state machine (no FastAPI / InsightFace / OpenCV needed,
so it can be unit-tested with pytest).  registration_server.py imports from here.
"""
from __future__ import annotations

import math
import os
import threading
import time
from collections import deque

import numpy as np

# ==============================================================================
# SETTINGS & CALIBRATION
# ==============================================================================
STEPS = ["FRONT", "UP", "DOWN", "LEFT", "RIGHT"]
PITCH_GAIN = 2.0
# How far the head must travel from the FRONT baseline, in DEGREES (approximate: converted to the
# nose/mouth metrics with a 3D head model, real faces vary a bit). Override with env vars.
#   DOWN = old 20 deg + 15 deg = 35 deg. LEFT/RIGHT raised from ~29 deg to 40 deg.
YAW_PER_DEG = 0.0070      # yaw metric change per degree of head turn
PITCH_PER_DEG = 0.0120    # pitch metric (after PITCH_GAIN) change per degree of head tilt
TURN_DEG_LEFT = float(os.environ.get("KYC_LEFT_DEG", "40"))
TURN_DEG_RIGHT = float(os.environ.get("KYC_RIGHT_DEG", "40"))
TURN_DEG_UP = float(os.environ.get("KYC_UP_DEG", "25"))
TURN_DEG_DOWN = float(os.environ.get("KYC_DOWN_DEG", "35"))
TURN_THRESHOLD_LEFT = TURN_DEG_LEFT * YAW_PER_DEG
TURN_THRESHOLD_RIGHT = TURN_DEG_RIGHT * YAW_PER_DEG
TURN_THRESHOLD_UP = TURN_DEG_UP * PITCH_PER_DEG
TURN_THRESHOLD_DOWN = TURN_DEG_DOWN * PITCH_PER_DEG
# smallest of each axis, used for "not turned at all yet" and for cross-axis checks
TURN_THRESHOLD_YAW = min(TURN_THRESHOLD_LEFT, TURN_THRESHOLD_RIGHT)
TURN_THRESHOLD_PITCH = min(TURN_THRESHOLD_UP, TURN_THRESHOLD_DOWN)
# The OTHER axis may move at most this fraction of the target axis (stops diagonal cheating).
CROSS_AXIS_RATIO = 0.5
SMOOTH_FRAMES = 5
MIN_HOLD_FRAMES = 8
MAX_FRAME_DT = 0.15
# FRONT (baseline) must be a real, still, straight-on face.
FRONT_YAW_MAX = 0.06
FRONT_PITCH_RANGE = (0.32, 0.68)
FRONT_ROLL_MAX = 12
FRONT_STABLE_STD_YAW = 0.025     # pose must be still over the smoothing window
FRONT_STABLE_STD_PITCH = 0.030
FRONT_MAX_SPREAD = 0.05          # baseline frames must agree with each other (max-min) or FRONT is retried
BASELINE_SAMPLES = 15            # baseline = median of the LAST N stable frames only
HOLD_SECONDS = 0.8
DECAY = 1.0
IDENTITY_MIN_SIM = 0.30
IDENTITY_CONTINUITY_SIM = 0.60
IDENTITY_CONTINUITY_WINDOW = 1.0
DUPLICATE_THRESHOLD = 0.50
FRONT_MIN_DET = 0.70
FACE_MODEL = os.environ.get("FACE_MODEL", "buffalo_sc")
SESSION_TIMEOUT = 180
KYC_DEBUG = os.environ.get("KYC_DEBUG", "0") == "1"


def head_pose(kps):
    """(yaw, pitch, roll_deg) from InsightFace's 5 keypoints. yaw + = right, pitch smaller = up."""
    pts = np.asarray(kps, dtype=np.float64)
    le, re = pts[0], pts[1]
    eye_mid = (le + re) / 2.0
    roll = math.atan2(re[1] - le[1], re[0] - le[0])
    c, s = math.cos(-roll), math.sin(-roll)
    rot = np.array([[c, -s], [s, c]])
    p = (pts - eye_mid) @ rot.T
    eye_dist = max(p[1][0] - p[0][0], 1e-6)
    nose = p[2]
    mouth_mid = (p[3] + p[4]) / 2.0
    face_mid_x = (0.0 + mouth_mid[0]) / 2.0
    yaw = (nose[0] - face_mid_x) / eye_dist
    pitch = nose[1] / max(mouth_mid[1], 1e-6)
    return float(yaw), float(pitch), math.degrees(roll)


def best_match(candidates, registered_users):
    best = None
    for u in registered_users:
        reg_list = u.get("multi_embeddings") or [u.get("embedding")]
        if not reg_list:
            continue
        for cand in candidates:
            for reg in reg_list:
                if reg is None:
                    continue
                sim = float(np.dot(reg, cand))
                if best is None or sim > best[2]:
                    best = (u["roll_no"], u["name"], sim)
    return best


def check_duplicate_face(candidates, registered_users, threshold=DUPLICATE_THRESHOLD):
    bm = best_match(candidates, registered_users)
    return bm if bm and bm[2] >= threshold else None


def classify_pose(dx, dy):
    """dx: sideways movement (+ = RIGHT), dy: vertical movement (+ = UP), both vs FRONT baseline.
    A direction only counts when THAT direction's own threshold is passed AND the other axis stayed
    small. CENTER = not turned enough. MIXED = moved a lot but diagonally."""
    ax, ay = abs(dx), abs(dy)
    need_x = TURN_THRESHOLD_RIGHT if dx > 0 else TURN_THRESHOLD_LEFT
    need_y = TURN_THRESHOLD_UP if dy > 0 else TURN_THRESHOLD_DOWN
    if ax >= need_x and ay <= CROSS_AXIS_RATIO * ax:
        return "RIGHT" if dx > 0 else "LEFT"
    if ay >= need_y and ax <= CROSS_AXIS_RATIO * ay:
        return "UP" if dy > 0 else "DOWN"
    if ax >= need_x or ay >= need_y:
        return "MIXED"
    return "CENTER"


INSTRUCTIONS = {
    "FRONT": "Look straight at the camera and hold still",
    "UP": "Slowly raise your head UP",
    "DOWN": "Now slowly lower your head DOWN",
    "LEFT": "Now turn your head LEFT",
    "RIGHT": "Now turn your head RIGHT",
}


class KYCSession:
    def __init__(self, db_manager):
        self.db = db_manager
        self.lock = threading.Lock()
        self.last_seen = time.time()
        self.last_frame_t = None
        self.guidance = ("no_face", "Position your face inside the circle")
        self._reset_session()

    def process(self, faces, S):
        now = time.time()
        dt = 0.1 if self.last_frame_t is None else min(now - self.last_frame_t, MAX_FRAME_DT)
        self.last_frame_t = now
        self.last_seen = now
        self._update(faces, S, dt)

    def _reset_session(self):
        self.phase = "idle"  # idle | scanning | complete | rejected
        self.roll = None
        self.name = None
        self.role = "student"
        self.step_idx = 0
        self.progress = {s: 0.0 for s in STEPS}
        self.done = []
        self.embeddings = {}
        self.base_yaw = 0.0
        self.base_pitch = 0.5
        self.front_samples = deque(maxlen=BASELINE_SAMPLES)   # good frames -> baseline
        self.front_track = deque(maxlen=BASELINE_SAMPLES)     # EVERY recent FRONT frame -> coherence check
        self.best_emb = None
        self.best_score = -1.0
        self.best_norm = 0.0
        self.mismatch_time = 0.0
        self.last_emb = None
        self.last_emb_t = 0.0
        self._last_log_t = 0.0
        self.started_at = 0.0
        self.step_samples = []
        self.pose_win = []
        self.result_message = ""
        self._reset_step_buffers()

    def _reset_step_buffers(self):
        self.best_emb = None
        self.best_score = -1.0
        self.best_norm = 0.0
        self.step_samples = []
        self.good_frames = 0

    def start_session(self, roll_no: str, name: str, password: str, role: str = "student"):
        roll_no = roll_no.strip()
        name = name.strip()
        password = password.strip()
        role = role.strip().lower()
        if role not in ("student", "teacher"):
            role = "student"

        cred_check = self.db.verify_credential(id_number=roll_no, password=password, role=role)
        if not cred_check.get("valid", False):
            return {"status": "error",
                    "message": cred_check.get("error", f"Invalid ID or Passcode for {role.capitalize()}. Please check credentials.")}

        all_users = self.db.get_all_users()
        existing = next((u for u in all_users if u["roll_no"].lower() == roll_no.lower()), None)
        if existing:
            return {"status": "error",
                    "message": f"ID '{roll_no}' is ALREADY registered to '{existing['name']}' ({existing['role']})."}

        with self.lock:
            self._reset_session()
            self.roll = roll_no
            self.name = name or cred_check.get("name") or roll_no
            self.role = role
            self.phase = "scanning"
            self.started_at = time.time()
        return {"status": "success", "role": role, "name": self.name}

    def cancel_session(self):
        with self.lock:
            self._reset_session()

    def _assess(self, faces, S):
        if not faces:
            return "no_face", "No face detected. Move into the circle.", None
        if len(faces) > 1:
            return "multi_face", "Only one person should be in front of the camera.", None
        f = faces[0]
        x1, y1, x2, y2 = f["bbox"]
        if f["det_score"] < 0.45:
            return "no_face", "Face not clear. Face the camera in good light.", None
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if math.hypot(cx - S / 2, cy - S / 2) / S > 0.25:
            return "off_center", "Centre your face inside the circle.", None
        width_ratio = (x2 - x1) / S
        if width_ratio < 0.20:
            return "too_far", "Move a little closer.", None
        if width_ratio > 0.82:
            return "too_close", "Move back a little.", None
        return "ok", "", f

    def _decay(self, step, dt):
        if self.progress[step] < 1.0:
            self.progress[step] = max(0.0, self.progress[step] - dt / HOLD_SECONDS * DECAY)
            if self.progress[step] == 0.0:
                self._reset_step_buffers()
                if step == "FRONT":
                    self.front_samples.clear()

    def _update(self, faces, S, dt):
        code, msg, face = self._assess(faces, S)
        with self.lock:
            self.guidance = (code, msg)
            if self.phase != "scanning":
                return
            if time.time() - self.started_at > SESSION_TIMEOUT:
                self._fail("Session timed out. Please tap Start to try again.")
                return

            step = STEPS[self.step_idx]
            if face is None:
                self.pose_win = []
                self.front_track.clear()
                self._decay(step, dt)
                return

            emb = face["embedding"]

            if self.embeddings:
                now = time.time()
                refs = np.vstack([np.asarray(e, dtype=np.float32)
                                  for lst in self.embeddings.values() for e in lst])
                sim_ref = float(np.max(refs @ emb))
                sim_prev = 0.0
                if self.last_emb is not None and now - self.last_emb_t <= IDENTITY_CONTINUITY_WINDOW:
                    sim_prev = float(np.dot(self.last_emb, emb))
                if sim_ref < IDENTITY_MIN_SIM and sim_prev < IDENTITY_CONTINUITY_SIM:
                    self.mismatch_time += dt
                    self.guidance = ("identity", "Different face detected. Hold still.")
                    if now - self._last_log_t > 0.5:
                        self._last_log_t = now
                        print(f"[KYC] identity low step={step} sim_ref={sim_ref:.2f} "
                              f"sim_prev={sim_prev:.2f} pitch={face['pitch']:.2f} yaw={face['yaw']:.2f}")
                    if self.mismatch_time > 3.5:
                        self._fail("Scan aborted: face mismatch detected during verification.")
                    self._decay(step, dt)
                    return
                self.mismatch_time = 0.0
                self.last_emb = emb
                self.last_emb_t = now

            # Smooth the pose. The window must be FULL before any step can count, so a couple of
            # jittery frames right after a step change can never complete anything.
            self.pose_win.append((face["yaw"], face["pitch"], face["roll"]))
            self.pose_win = self.pose_win[-SMOOTH_FRAMES:]
            if len(self.pose_win) < SMOOTH_FRAMES:
                return
            ys = [w[0] for w in self.pose_win]
            ps = [w[1] for w in self.pose_win]
            yaw = float(np.median(ys))
            pitch = float(np.median(ps))
            roll = float(np.median([w[2] for w in self.pose_win]))

            if step == "FRONT":
                self.front_track.append((yaw, pitch))
                visible = face["det_score"] >= FRONT_MIN_DET
                still = (float(np.std(ys)) < FRONT_STABLE_STD_YAW
                         and float(np.std(ps)) < FRONT_STABLE_STD_PITCH)
                good = (visible and still
                        and abs(yaw) < FRONT_YAW_MAX
                        and FRONT_PITCH_RANGE[0] <= pitch <= FRONT_PITCH_RANGE[1]
                        and abs(roll) < FRONT_ROLL_MAX)
                if not visible:
                    self.guidance = ("occluded", "Face not clearly visible. Look directly into camera.")
                elif not still:
                    self.guidance = ("pose", "Hold your head still and look straight ahead.")
                elif not good:
                    self.guidance = ("pose", "Look straight at the camera and keep your head level.")
            else:
                dx = -(yaw - self.base_yaw)
                dy = (self.base_pitch - pitch) * PITCH_GAIN
                pose = classify_pose(dx, dy)
                good = (pose == step)
                if KYC_DEBUG and time.time() - self._last_log_t > 0.5:
                    self._last_log_t = time.time()
                    print(f"[KYC] step={step} dx={dx:+.2f} dy={dy:+.2f} "
                          f"need L/R>={TURN_THRESHOLD_LEFT:.2f}/{TURN_THRESHOLD_RIGHT:.2f} "
                          f"U/D>={TURN_THRESHOLD_UP:.2f}/{TURN_THRESHOLD_DOWN:.2f} pose={pose}")
                if not good:
                    if pose == "CENTER":
                        self.guidance = ("ok", INSTRUCTIONS[step])
                    elif pose == "MIXED":
                        self.guidance = ("wrong_way", f"Move straight {step}, not diagonally.")
                    else:
                        self.guidance = ("wrong_way", f"Turn {step} as indicated.")
            if good:
                self.guidance = ("ok", INSTRUCTIONS[step])
                self.progress[step] = min(1.0, self.progress[step] + dt / HOLD_SECONDS)
                self.good_frames += 1
                if face["det_score"] >= self.best_score:
                    self.best_score = face["det_score"]
                    self.best_emb = emb
                    self.best_norm = face["norm"]
                self.step_samples.append((face["det_score"], emb.copy()))
                self.step_samples.sort(key=lambda x: x[0], reverse=True)
                self.step_samples = self.step_samples[:5]
                if step == "FRONT":
                    self.front_samples.append((yaw, pitch))
            else:
                self._decay(step, dt)

            if step == "FRONT" and self.progress[step] >= 1.0 and self.good_frames >= MIN_HOLD_FRAMES:
                verdict = self._baseline_is_coherent()
                if verdict is None:                      # not enough history yet -> keep holding
                    self.progress[step] = 0.999
                    return
                if verdict is False:
                    # head drifted/nodded during FRONT -> baseline would be wrong. Start FRONT over.
                    self.progress[step] = 0.0
                    self._reset_step_buffers()
                    self.front_samples.clear()
                    self.front_track.clear()
                    self.guidance = ("pose", "Hold your head still and look straight ahead.")
                    return
            if self.progress[step] >= 1.0 and self.good_frames >= MIN_HOLD_FRAMES:
                self._complete_step(step)
            elif self.progress[step] >= 1.0:
                self.progress[step] = 0.999

    def _baseline_is_coherent(self):
        """None = not enough history yet, True = steady baseline, False = head was moving."""
        if len(self.front_samples) < MIN_HOLD_FRAMES or len(self.front_track) < BASELINE_SAMPLES:
            return None
        ys, ps = zip(*self.front_track)
        return (max(ys) - min(ys)) <= FRONT_MAX_SPREAD and (max(ps) - min(ps)) <= FRONT_MAX_SPREAD

    def _complete_step(self, step):
        self.progress[step] = 1.0
        samples = [emb for _, emb in self.step_samples]
        if not samples and self.best_emb is not None:
            samples = [self.best_emb]
        self.embeddings[step] = samples
        self.done.append(step)

        if step == "FRONT":
            ys, ps = zip(*self.front_samples)
            self.base_yaw = float(np.median(ys))
            self.base_pitch = float(np.median(ps))

        all_registered = self.db.get_all_users()
        match = best_match([self.best_emb], all_registered)
        if step == "FRONT":
            closest = f"{match[0]} sim={match[2]:.2f}" if match else "none"
            print(f"[KYC] {self.roll} ({self.role}) front capture. Closest registered: {closest}")
        if match and match[2] >= DUPLICATE_THRESHOLD:
            self._fail_duplicate(match)
            return

        self.step_idx += 1
        self.pose_win = []
        self._reset_step_buffers()
        if self.step_idx >= len(STEPS):
            self._finish()

    def _finish(self):
        all_embeddings = []
        for step in STEPS:
            all_embeddings.extend(self.embeddings.get(step, []))
        stack = np.vstack(all_embeddings)
        dup = check_duplicate_face(list(stack), self.db.get_all_users())
        if dup:
            self._fail_duplicate(dup)
            return
        self.db.upsert_user(roll_no=self.roll, name=self.name, role=self.role,
                            embedding=self.embeddings["FRONT"][0], multi_embeddings=list(stack))
        self.phase = "complete"
        role_label = "Student" if self.role == "student" else "Faculty Member"
        self.result_message = (f"🎉 KYC Verified! {self.name} ({self.roll}) "
                               f"registered as {role_label} with 5-angle biometric profile.")

    def _fail_duplicate(self, dup):
        roll, name, score = dup
        self._fail(f"REJECTED: Face matches already registered person '{name}' (ID: {roll}), match score: {score:.2f}.")

    def _fail(self, message):
        self.phase = "rejected"
        self.result_message = message

    def status(self):
        with self.lock:
            if self.phase == "complete":
                percent = 100
            else:
                percent = int(round(100 * sum(self.progress.values()) / len(STEPS)))
            code, msg = self.guidance
            current = STEPS[self.step_idx] if self.phase == "scanning" and self.step_idx < len(STEPS) else None
            if self.phase in ("complete", "rejected"):
                message = self.result_message
            elif self.phase == "scanning":
                message = msg or INSTRUCTIONS[current]
            else:
                message = "Face detected! Enter credentials and tap Start." if code == "ok" else msg
            return {
                "phase": self.phase, "percent": percent, "current": current,
                "code": code, "message": message,
                "steps": {s: {"progress": round(self.progress[s], 3), "done": s in self.done} for s in STEPS},
            }