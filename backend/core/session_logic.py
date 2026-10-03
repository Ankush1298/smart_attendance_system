import time
import threading
import cv2
from datetime import datetime, timedelta
import numpy as np
from typing import List, Dict, Any, Optional, Set

from backend.core.db import DatabaseManager
from backend.core.face_core import FaceEngine, FaceMeshHelper


def normalize_camera_source(cam_source: Any) -> Any:
    """Normalizes camera sources (indexes, DroidCam, IP Webcam, RTSP). Returns None if not set."""
    if cam_source is None:
        return None
    src_str = str(cam_source).strip()
    if not src_str:
        return None
    if src_str.isdigit():
        return int(src_str)
    if src_str.endswith("/"):
        src_str = src_str[:-1]
    if ":4747" in src_str and not src_str.endswith(("/video", "/mjpegfeed")):
        src_str += "/video"
    if ":8080" in src_str and not src_str.endswith(("/video", "/video.mjpg", "/mjpegfeed")):
        src_str += "/video"
    if not src_str.startswith(("http://", "https://", "rtsp://", "rtmp://", "/")):
        if ":" in src_str or "." in src_str:
            src_str = "http://" + src_str
    return src_str


class SessionLogic:
    """
    Fully Automated Timetable-Driven Attendance Orchestrator.

    Runtime rules:
    1. Teacher verification authorizes the scheduled room/session; it can happen
       at any point after the scheduled start and does not require continuous
       teacher presence to keep the class active.
    2. ACTIVE sessions use a 60-second recognition heartbeat. Student attendance
       is calculated from the union of short presence intervals, not sample counts.
    3. Teacher attendance is counted only in the middle 40 minutes of a 50-minute
       lecture (first 5 and last 5 minutes excluded). The teacher must be inside
       the room's configured whiteboard/front zone.
    4. A teacher absence gap greater than 20 minutes is flagged in the HOD report;
       the report also records total present/absent minutes.
    5. Existing timetable, room, camera, face-recognition and MySQL workflows are
       preserved; the dwell-time logic replaces only the old 5-minute sample-count
       calculation.
    """
    # Classroom-scale attendance scan: capture a short burst of frames per
    # heartbeat instead of one, so a student who is mid-blink/turned away in
    # one frame still has other chances within the same one-minute heartbeat.
    SCAN_FRAMES = 4
    SCAN_FRAME_DELAY = 0.2
    # Attendance heartbeat: a one-minute cadence is frequent enough to
    # estimate dwell time without turning a classroom laptop into a
    # continuous full-rate recognition workload.
    ACTIVE_SCAN_INTERVAL_SEC = 60.0
    DWELL_HALF_WINDOW_SEC = 30.0
    TEACHER_CORE_MARGIN_MIN = 5.0
    TEACHER_ABSENCE_ALERT_MIN = 20.0

    # How often (seconds) to re-fetch timetable/users/rooms from the DB.
    # Between refreshes the in-memory cache is reused, so the DB and GIL are
    # barely touched and Tkinter's main thread stays responsive.
    CACHE_TTL = 300  # 5 minutes

    def __init__(self, db: DatabaseManager, engine: FaceEngine):
        self.db = db
        self.engine = engine
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.mesh_helper = FaceMeshHelper()
        self.lock = threading.Lock()

        # State tracking for each scheduled class: { class_key: class_state_dict }
        self.class_states: Dict[str, Dict[str, Any]] = {}
        self.active_cameras: Dict[str, cv2.VideoCapture] = {}

        # --- In-memory data cache ---
        self._cache_users: List[Dict[str, Any]] = []
        self._cache_rooms: Dict[str, Dict[str, Any]] = {}
        self._cache_timetable: List[Dict[str, Any]] = []
        self._cache_fetched_at: float = 0.0  # epoch seconds of last DB refresh

    def invalidate_timetable_cache(self):
        """Force the next orchestrator tick to reload rooms/timetable/users from MySQL."""
        self._cache_fetched_at = 0.0

    def _refresh_cache_if_needed(self):
        """Re-fetches users / rooms / timetable from the DB at most once per CACHE_TTL seconds.
        Uses the in-memory (and file-backed) cache between refreshes."""
        now_ts = time.time()
        if now_ts - self._cache_fetched_at < self.CACHE_TTL:
            return  # still fresh

        # Fetch fresh data from MySQL
        try:
            users = self.db.get_all_users() or []
            rooms = {r["room_id"]: r for r in (self.db.get_rooms() or [])}
            timetable = self.db.get_timetable() or []

            self._cache_users = users
            self._cache_rooms = rooms
            self._cache_timetable = timetable
            self._cache_fetched_at = now_ts

        except Exception as e:
            print(f"⚠️  Cache refresh error: {e}")

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        if not self.running:
            self.running = True
            self._thread = threading.Thread(target=self._orchestrator_loop, daemon=True)
            self._thread.start()
            print("🕒 Automated Attendance SessionLogic engine started.")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        with self.lock:
            for cap in self.active_cameras.values():
                try:
                    cap.release()
                except Exception:
                    pass
            self.active_cameras.clear()
        print("⏹ Automated Attendance SessionLogic engine stopped.")

    def get_active_sessions_status(self) -> List[Dict[str, Any]]:
        """Returns snapshot of current class states for UI display."""
        with self.lock:
            return list(self.class_states.values())

    def _capture_frame(self, room_id: str, raw_source: Any) -> Optional[np.ndarray]:
        """Safely captures a single frame from room camera."""
        source = normalize_camera_source(raw_source)
        if source is None:
            return None
        try:
            if room_id not in self.active_cameras or not self.active_cameras[room_id].isOpened():
                cap = cv2.VideoCapture(source)
                if isinstance(source, int):
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                self.active_cameras[room_id] = cap
            
            cap = self.active_cameras[room_id]
            if not cap.isOpened():
                return None
            
            # Read frame
            ret, frame = cap.read()
            if not ret or frame is None:
                # Try re-opening once if disconnected
                cap.release()
                cap = cv2.VideoCapture(source)
                self.active_cameras[room_id] = cap
                ret, frame = cap.read()
            return frame if ret else None
        except Exception as e:
            print(f"Error capturing frame for room {room_id}: {e}")
            return None

    def _capture_multi_frame(self, room_id: str, raw_source: Any,
                              n: Optional[int] = None, delay: Optional[float] = None) -> List[np.ndarray]:
        """Captures a short burst of frames in quick succession for one
        attendance check, instead of relying on a single snapshot."""
        n = n if n is not None else self.SCAN_FRAMES
        delay = delay if delay is not None else self.SCAN_FRAME_DELAY
        frames = []
        for i in range(n):
            f = self._capture_frame(room_id, raw_source)
            if f is not None:
                frames.append(f)
            if i < n - 1:
                time.sleep(delay)
        return frames

    def _orchestrator_loop(self):
        """Main polling loop running in background.

        Polls every 10 seconds for class state transitions, but only
        hits the database at most once every CACHE_TTL (5 min) so the
        database lock and the GIL are not hammered on every tick.
        """
        while self.running:
            try:
                now = datetime.now()
                current_day = now.strftime("%A").lower()
                today_date = now.strftime("%Y-%m-%d")

                # Refresh data from DB only when the cache has expired.
                # Between refreshes the loop uses cheap in-memory lists.
                self._refresh_cache_if_needed()

                users     = self._cache_users
                rooms     = self._cache_rooms
                timetable = self._cache_timetable

                # Filter timetable for today
                today_classes = [
                    t for t in timetable
                    if str(t.get("day_of_week", "")).strip().lower() == current_day
                    and str((rooms.get(t.get("room_id"), {}) or {}).get("camera_source", "")).strip()
                ]

                # Only camera-enabled rooms are eligible for automatic attendance.
                # This is intentional for multi-classroom deployments: if several
                # classes are scheduled at the same time but this machine has a
                # camera assigned to only one room, only that room is processed.
                for entry in today_classes:
                    self._process_class_schedule(entry, rooms, users, now, today_date)

                # Cleanup old finished states from previous days
                with self.lock:
                    keys_to_remove = [
                        k for k, state in self.class_states.items()
                        if state.get("date") != today_date and state.get("state") in ["COMPLETED", "SUSPENDED"]
                    ]
                    for k in keys_to_remove:
                        del self.class_states[k]

            except Exception as e:
                print(f"Automated SessionLogic error in orchestrator loop: {e}")

            time.sleep(10)  # Poll every 10 seconds

    def _process_class_schedule(
        self, 
        entry: Dict[str, Any], 
        rooms: Dict[str, Dict[str, Any]], 
        users: List[Dict[str, Any]], 
        now: datetime, 
        today_date: str
    ):
        class_id = entry.get("id") or entry.get("class_id", 1)
        room_id = entry.get("room_id", "Default")
        subject = entry.get("subject", "General Class")
        teacher_id = str(entry.get("teacher_id", "")).strip()
        
        # Parse start and end time for today
        try:
            st_p = datetime.strptime(entry["start_time"], "%H:%M")
            et_p = datetime.strptime(entry["end_time"], "%H:%M")
            start_dt = now.replace(hour=st_p.hour, minute=st_p.minute, second=0, microsecond=0)
            end_dt = now.replace(hour=et_p.hour, minute=et_p.minute, second=0, microsecond=0)
        except Exception:
            return

        session_id = f"SES_{room_id}_{class_id}_{today_date}"
        # Room is part of the state key. Multiple rooms can have the same
        # timetable slot/class identifier and must never share camera/session state.
        class_key = f"{room_id}_{class_id}_{today_date}"

        with self.lock:
            if class_key not in self.class_states:
                duration_mins = max(1.0, (end_dt - start_dt).total_seconds() / 60.0)
                expected_slots = max(1, int(duration_mins // 5))
                teacher_count_start = start_dt + timedelta(minutes=self.TEACHER_CORE_MARGIN_MIN)
                teacher_count_end = end_dt - timedelta(minutes=self.TEACHER_CORE_MARGIN_MIN)
                self.class_states[class_key] = {
                    "session_id": session_id,
                    "timetable_id": class_id,
                    "date": today_date,
                    "subject": subject,
                    "teacher_id": teacher_id,
                    "room_id": room_id,
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "duration_mins": duration_mins,
                    "teacher_count_start": teacher_count_start,
                    "teacher_count_end": teacher_count_end,
                    "expected_slots": expected_slots,
                    "state": "SCHEDULED", # SCHEDULED, WAITING_TEACHER, ACTIVE, SUSPENDED, AWAITING_POST_CHECK, COMPLETED
                    "teacher_detected": False,
                    "teacher_detected_at": None,
                    "checks": [], # list of {"timestamp": dt, "teacher_present": bool, "students": set(roll_no), "confs": {}}
                    "student_presence_counts": {}, # backward-compatible diagnostic count
                    "student_presence_points": {}, # {roll_no: [datetime, ...]}
                    "student_best_confs": {}, # {roll_no: conf}
                    "teacher_presence_points": [], # datetimes when scheduled teacher was seen in whiteboard zone
                    "last_check_time": None, # timestamp float
                    "created_in_db": False,
                    "finalized": False,
                }

            state = self.class_states[class_key]

        elapsed_mins = (now - start_dt).total_seconds() / 60.0
        current_state = state["state"]

        # 1. Before class start: Wait
        # (Original: if elapsed_mins < 5.0: return)
        if elapsed_mins < 0:
            return

        # Check if already terminated
        if current_state in ["SUSPENDED", "COMPLETED"]:
            return

        room_data = rooms.get(room_id, {})
        cam_source = room_data.get("camera_source")
        # If this room does not have a camera configured by the user, leave it blank and skip
        if not cam_source or str(cam_source).strip() == "":
            return

        # 2. Timing check. Active classes use a one-minute attendance heartbeat.
        # Teacher authorization still checks immediately while waiting; once the
        # session is active, the same heartbeat is used to build teacher/student
        # dwell intervals.
        last_check = state["last_check_time"]
        should_run_check = False
        if last_check is None:
            should_run_check = True
        elif (time.time() - last_check) >= self.ACTIVE_SCAN_INTERVAL_SEC:
            should_run_check = True

        # -------------------------------------------------------------------------
        # AUTOMATIC CLASS ACTIVATION
        # -------------------------------------------------------------------------
        # A scheduled class does not become active merely because the clock says
        # it has started. The configured room camera must first see the teacher
        # assigned to that timetable slot. This prevents an unattended room from
        # starting attendance and makes the teacher the authorization for the
        # session.
        if current_state in ["SCHEDULED", "WAITING_TEACHER"]:
            if should_run_check:
                frames = self._capture_multi_frame(room_id, cam_source)
                teacher_found, found_students, conf_map = self._scan_faces_multi(frames, users, teacher_id)
                state["last_check_time"] = time.time()

                if teacher_found:
                    state["teacher_detected"] = True
                    state["teacher_detected_at"] = now
                    state["state"] = "ACTIVE"
                    if not state.get("created_in_db"):
                        self.db.create_session(session_id, class_id, today_date, subject)
                        state["created_in_db"] = True
                    self.db.log_attendance(session_id, teacher_id, "teacher", "present", conf_map.get(teacher_id, 1.0))
                    self._record_check(state, True, found_students, conf_map, today_date)
                    print(f"✅ Teacher {teacher_id} verified in room {room_id}; {subject} attendance activated.")
                else:
                    state["state"] = "WAITING_TEACHER"
                    if elapsed_mins >= 20.0:
                        state["state"] = "SUSPENDED"
                        if not state.get("created_in_db"):
                            self.db.create_session(session_id, class_id, today_date, subject)
                            state["created_in_db"] = True
                        self.db.update_session_status(session_id, "suspended")
                        self.db.log_attendance(session_id, teacher_id, "teacher", "absent", 1.0)
                        print(f"⛔ Teacher {teacher_id} not verified in room {room_id} within 20 minutes; {subject} suspended.")
                    else:
                        print(f"⏳ Waiting for scheduled teacher {teacher_id} in camera-enabled room {room_id} for {subject}.")
            return

        # --- ACTIVE CLASS: one-minute scans + dwell-time accumulation ---
        if state["state"] == "ACTIVE":
            if now < end_dt:
                if should_run_check:
                    frames = self._capture_multi_frame(room_id, cam_source)
                    teacher_found, found_students, conf_map = self._scan_faces_multi(
                        frames, users, teacher_id, room_data=room_data
                    )
                    state["last_check_time"] = time.time()
                    self._record_check(state, teacher_found, found_students, conf_map, today_date, now=now)
                    print(f"📸 60s attendance heartbeat for {subject} in {room_id} at {now.strftime('%H:%M:%S')}: {len(found_students)} student(s) detected; teacher {'present' if teacher_found else 'not visible'}.")
            else:
                print(f"⏹ Class {subject} in room {room_id} ended. Finalizing attendance session.")
                self._finalize_session(state)
            return

        # -------------------------------------------------------------------------
        # POST-CLASS CHECK
        # -------------------------------------------------------------------------
        # # --- A. WAITING FOR TEACHER (from minute 5 to 20) ---
        # if current_state in ["SCHEDULED", "WAITING_TEACHER"]:
        #     if should_run_check:
        #         frames = self._capture_multi_frame(room_id, cam_source)
        #         teacher_found, found_students, conf_map = self._scan_faces_multi(frames, users, teacher_id)
        #         state["last_check_time"] = time.time()
        # 
        #         if teacher_found:
        #             state["teacher_detected"] = True
        #             state["teacher_detected_at"] = now
        #             state["state"] = "ACTIVE"
        #             
        #             # Create session in DB
        #             self.db.create_session(session_id, class_id, today_date, subject)
        #             state["created_in_db"] = True
        #             self.db.log_attendance(session_id, teacher_id, "teacher", "present", conf_map.get(teacher_id, 1.0))
        #             
        #             # Record first attendance check
        #             self._record_check(state, True, found_students, conf_map, today_date)
        #             print(f"✅ Teacher {teacher_id} detected at min {int(elapsed_mins)} for {subject}. Attendance active!")
        #         else:
        #             if elapsed_mins >= 20.0:
        #                 # Teacher absent for 20 minutes -> Class automatically SUSPENDED
        #                 state["state"] = "SUSPENDED"
        #                 self.db.create_session(session_id, class_id, today_date, subject)
        #                 self.db.update_session_status(session_id, "suspended")
        #                 self.db.log_attendance(session_id, teacher_id, "teacher", "absent", 1.0)
        #                 print(f"⛔ Teacher {teacher_id} absent for 20 minutes from start. Class {subject} ({session_id}) automatically SUSPENDED.")
        #             else:
        #                 state["state"] = "WAITING_TEACHER"
        #                 print(f"⏳ Class {subject}: Teacher not detected at min {int(elapsed_mins)}. Waiting up to 20 mins...")
        #     return
        # 
        # # --- B. ACTIVE CLASS (Teacher was detected, class ongoing) ---
        # if current_state == "ACTIVE":
        #     if now < end_dt:
        #         if should_run_check:
        #             frames = self._capture_multi_frame(room_id, cam_source)
        #             t_present, found_students, conf_map = self._scan_faces_multi(frames, users, teacher_id)
        #             state["last_check_time"] = time.time()
        #             self._record_check(state, t_present, found_students, conf_map, today_date)
        #             print(f"📸 5-min attendance check for {subject} at {now.strftime('%H:%M')}: {len(found_students)} students present, teacher {'present' if t_present else 'absent'}.")
        #     else:
        #         # Class scheduled time has ended!
        #         # Evaluate the last 2 attendance checks before scheduled end
        #         recent_checks = state["checks"][-2:]
        #         # Criteria: Both teacher and students present in those last two checks
        #         if len(recent_checks) >= 2 and all(c["teacher_present"] for c in recent_checks):
        #             state["state"] = "AWAITING_POST_CHECK"
        #             post_time = end_dt + timedelta(minutes=5)
        #             print(f"🔔 Class {subject} ended. Both teacher and students present in last 2 checks. Scheduling post-class check at {post_time.strftime('%H:%M')}.")
        #         else:
        #             print(f"⏹ Class {subject} ended without meeting post-class check criteria. Finalizing session.")
        #             self._finalize_session(state)
        #     return

        # --- C. AWAITING POST-CLASS CHECK (5 minutes after class ended) ---
        if current_state == "AWAITING_POST_CHECK":
            post_check_time = end_dt + timedelta(minutes=5)
            if now >= post_check_time:
                frames = self._capture_multi_frame(room_id, cam_source)
                t_present, found_students, conf_map = self._scan_faces_multi(frames, users, teacher_id)
                
                # If teacher is still present and students remained with teacher
                if t_present:
                    # Find immediately following class today for targeted grace
                    next_class = self.db.get_immediately_following_class(class_id, now.strftime("%A"))
                    target_id = next_class["id"] if next_class else None
                    target_sub = next_class["subject"] if next_class else "Next Lecture"

                    for uid in found_students:
                        self.db.record_transition_grace(
                            roll_no=uid,
                            date=today_date,
                            from_class_id=class_id,
                            target_next_class_id=target_id,
                            room_id=room_id,
                            grace_minutes=5.0
                        )
                        print(f"✨ Transition grace (5-min time adjustment) granted to student {uid} specifically for immediately following class '{target_sub}' (ID: {target_id}).")

                print(f"🏁 Post-class check executed for {subject}. Finalizing attendance.")
                self._finalize_session(state)
            return

    def _scan_faces_multi(
        self,
        frames: List[np.ndarray],
        users: List[Dict[str, Any]],
        expected_teacher_id: str,
        room_data: Optional[Dict[str, Any]] = None
    ) -> (bool, Set[str], Dict[str, float]):
        """
        Runs face recognition across a short burst of frames for one
        attendance check and returns (teacher_present, set of student
        roll_nos, confidences) -- same shape as the old single-frame
        _scan_faces, so callers don't change.

        Per frame: tiled multi-scale detection (so a small back-row face
        gets re-detected at a larger relative scale) -> margin-based
        matching against the roster, so two similar-looking students near
        the threshold are left ambiguous instead of guessed.
        Across frames: duplicate detections collapse to one best-confidence
        record per student; a student is only marked present once they
        have a second confirming detection (when more than one frame was
        available), instead of accepting a single low-confidence glimpse.
        """
        if not frames or not self.engine:
            return False, set(), {}

        teacher_present = False
        students_present: Set[str] = set()
        conf_map: Dict[str, float] = {}
        confirmations: Dict[str, int] = {}
        need_confirmations = 2 if len(frames) >= 2 else 1
        room_data = room_data or {}
        zone = (
            float(room_data.get("teacher_zone_x1", 0.0)),
            float(room_data.get("teacher_zone_y1", 0.0)),
            float(room_data.get("teacher_zone_x2", 1.0)),
            float(room_data.get("teacher_zone_y2", 1.0)),
        )

        for frame in frames:
            try:
                faces = self.engine.detect_faces_tiled(frame)
            except Exception:
                continue

            for f in faces:
                if f.embedding is None:
                    continue
                pose = self.mesh_helper.estimate_pose(f.kps) if hasattr(f, 'kps') else "FRONT"
                is_up = "UPLIFTED" in pose
                match = self.engine.match_with_margin(f.embedding, users, is_uplifted=is_up)
                if not match:
                    continue
                uid, name, role, conf = match
                conf_map[uid] = max(conf_map.get(uid, 0.0), float(conf))
                confirmations[uid] = confirmations.get(uid, 0) + 1

                if role == "teacher":
                    expected = str(expected_teacher_id or "").strip().casefold()
                    candidate_ids = {str(uid).strip().casefold(), str(name).strip().casefold()}
                    # Teacher attendance is only credited when the scheduled
                    # teacher is inside the room's configured whiteboard/front
                    # zone. The default zone is the full frame, preserving
                    # existing deployments until a room-specific zone is set.
                    in_zone = self._face_in_normalized_zone(f, frame, zone)
                    if (expected == "" or expected in candidate_ids) and in_zone:
                        teacher_present = True
                elif role == "student":
                    if confirmations[uid] >= need_confirmations:
                        students_present.add(uid)

        return teacher_present, students_present, conf_map

    @staticmethod
    def _face_in_normalized_zone(face: Any, frame: np.ndarray, zone: tuple) -> bool:
        try:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [max(0.0, min(1.0, float(v))) for v in zone]
            bx1, by1, bx2, by2 = map(float, face.bbox)
            cx = ((bx1 + bx2) / 2.0) / max(1.0, float(w))
            cy = ((by1 + by2) / 2.0) / max(1.0, float(h))
            return x1 <= cx <= x2 and y1 <= cy <= y2
        except Exception:
            return True

    def _scan_faces(
        self,
        frame: Optional[np.ndarray],
        users: List[Dict[str, Any]],
        expected_teacher_id: str
    ) -> (bool, Set[str], Dict[str, float]):
        """Single-frame wrapper kept for backward compatibility (tests /
        callers that still pass one frame). Prefer _scan_faces_multi."""
        frames = [frame] if frame is not None else []
        return self._scan_faces_multi(frames, users, expected_teacher_id)

    def _record_check(
        self,
        state: Dict[str, Any],
        teacher_present: bool,
        students: Set[str],
        conf_map: Dict[str, float],
        today_date: str,
        now: Optional[datetime] = None
    ):
        """Record a heartbeat and store detection timestamps for dwell-time math.

        A detection is treated as a short presence interval around the sample
        (30 seconds on either side by default). Finalization unions these
        intervals, so attendance reflects elapsed presence rather than the
        number of coarse samples.
        """
        now = now or datetime.now()
        state["checks"].append({
            "timestamp": now,
            "teacher_present": teacher_present,
            "students": students,
            "confs": conf_map
        })

        session_id = state["session_id"]
        for uid in students:
            state["student_presence_counts"][uid] = state["student_presence_counts"].get(uid, 0) + 1
            state.setdefault("student_presence_points", {}).setdefault(uid, []).append(now)
            state["student_best_confs"][uid] = max(
                state["student_best_confs"].get(uid, 0.0), conf_map.get(uid, 0.9)
            )
            self.db.log_attendance(session_id, uid, "student", "present", state["student_best_confs"][uid])

        # Teacher attendance is counted only inside the middle 40 minutes of a
        # 50-minute lecture (first/last five minutes excluded). Teacher detection
        # still authorizes the session even if it happens during the first five.
        core_start = state.get("teacher_count_start", state["start_dt"])
        core_end = state.get("teacher_count_end", state["end_dt"])
        if teacher_present and core_start <= now <= core_end:
            state.setdefault("teacher_presence_points", []).append(now)

    def _finalize_session(self, state: Dict[str, Any]):
        """Finalize student dwell attendance and teacher 40-minute attendance report."""
        if state.get("finalized"):
            return

        state["finalized"] = True
        state["state"] = "COMPLETED"
        session_id = state["session_id"]
        timetable_id = state["timetable_id"]
        duration_mins = state.get("duration_mins", 60.0)
        session_start = state["start_dt"]
        session_end = state["end_dt"]

        self.db.update_session_status(session_id, "completed")

        def union_minutes(points: List[datetime], start: datetime, end: datetime) -> tuple[float, float, float]:
            if end <= start or not points:
                return 0.0, 0.0, 0.0
            half = timedelta(seconds=self.DWELL_HALF_WINDOW_SEC)
            ordered = sorted(set(points))
            intervals = []
            for idx, point in enumerate(ordered):
                # Use midpoints between neighboring successful detections as
                # boundaries. This keeps a one-missed-scan gap from unfairly
                # deleting a full minute, while a genuinely long gap remains
                # an absence interval.
                prev_point = ordered[idx-1] if idx else None
                next_point = ordered[idx+1] if idx + 1 < len(ordered) else None
                left = point - half if prev_point is None else point - (point-prev_point)/2
                right = point + half if next_point is None else point + (next_point-point)/2
                a = max(start, left)
                b = min(end, right)
                if b > a:
                    intervals.append((a, b))
            if not intervals:
                return 0.0, 0.0, 0.0
            merged = [list(intervals[0])]
            for a, b in intervals[1:]:
                if a <= merged[-1][1] + timedelta(seconds=self.ACTIVE_SCAN_INTERVAL_SEC * 0.25):
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            present = sum((b-a).total_seconds() for a, b in merged) / 60.0
            longest_gap = 0.0
            cursor = start
            for a, b in merged:
                longest_gap = max(longest_gap, (a-cursor).total_seconds()/60.0)
                cursor = max(cursor, b)
            longest_gap = max(longest_gap, (end-cursor).total_seconds()/60.0)
            return min(present, (end-start).total_seconds()/60.0), longest_gap, len(merged)

        for uid, points in state.get("student_presence_points", {}).items():
            detected_minutes, _, _ = union_minutes(points, session_start, session_end)
            grace_rec = self.db.get_valid_transition_grace(uid, state["date"], timetable_id)
            grace_mins_available = float(grace_rec.get("grace_minutes", 5.0)) if grace_rec else 0.0
            calc_res = self.db.calculate_attendance_with_grace(
                roll_no=uid,
                detected_minutes=detected_minutes,
                scheduled_duration=duration_mins,
                grace_minutes_available=grace_mins_available
            )
            if grace_rec:
                self.db.consume_transition_grace(grace_rec["id"], session_id)
            conf = state["student_best_confs"].get(uid, 0.85)
            final_status = calc_res["status"]
            self.db.log_attendance(session_id, uid, "student", final_status, float(conf))
            print(
                f"📊 Student {uid}: {detected_minutes:.1f}/{duration_mins:.0f} mins detected "
                f"({calc_res['effective_percent']}%) -> {final_status.upper()}"
            )

        # Teacher: only the middle 40 minutes of a 50-minute lecture count.
        teacher_start = state.get("teacher_count_start", session_start)
        teacher_end = state.get("teacher_count_end", session_end)
        core_minutes = max(0.0, (teacher_end-teacher_start).total_seconds()/60.0)
        teacher_points = state.get("teacher_presence_points", [])
        teacher_present_mins, longest_absence, _ = union_minutes(teacher_points, teacher_start, teacher_end)
        teacher_absent_mins = max(0.0, core_minutes - teacher_present_mins)
        first_seen = min(teacher_points).isoformat(timespec="seconds") if teacher_points else None
        last_seen = max(teacher_points).isoformat(timespec="seconds") if teacher_points else None
        absence_over_20 = longest_absence > self.TEACHER_ABSENCE_ALERT_MIN
        if not teacher_points:
            teacher_status = "absent"
        elif absence_over_20:
            teacher_status = "partial_absent"
        else:
            teacher_status = "present"
        self.db.upsert_teacher_attendance(
            session_id=session_id, teacher_id=state["teacher_id"], date=state["date"],
            counted_start=teacher_start.isoformat(timespec="seconds"),
            counted_end=teacher_end.isoformat(timespec="seconds"),
            first_seen=first_seen, last_seen=last_seen,
            present_minutes=teacher_present_mins, absent_minutes=teacher_absent_mins,
            longest_absence_minutes=longest_absence, status=teacher_status,
            absence_over_20m=absence_over_20
        )

        # Keep the legacy teacher attendance log so existing dashboards continue
        # to work; the detailed HOD report comes from teacher_attendance.
        if teacher_points:
            self.db.log_attendance(session_id, state["teacher_id"], "teacher", "present", 1.0)
        else:
            self.db.log_attendance(session_id, state["teacher_id"], "teacher", "absent", 1.0)

        print(
            f"👨‍🏫 Teacher {state['teacher_id']}: {teacher_present_mins:.1f}/{core_minutes:.0f} mins present, "
            f"{teacher_absent_mins:.1f} mins absent; longest absence {longest_absence:.1f} mins; "
            f"status={teacher_status.upper()}"
        )
        print(f"✅ Session {session_id} finalized and saved to database.")

