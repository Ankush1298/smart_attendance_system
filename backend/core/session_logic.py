"""Timetable-driven automatic attendance.

TIMETABLE -> class session -> room -> camera -> teacher verification -> authorization ->
student recognition -> attendance engine -> MySQL -> reports.

Key guarantees (each is covered by tests/):
* Idempotent: running ``tick`` any number of times yields exactly one session per class/day.
* A session is never created for a class that already ended, for a room without an enabled
  camera, or on a camera belonging to a different room.
* Only the *scheduled* teacher, recognised in this room, authorizes the session.
* Only enrolled students (section membership) can receive attendance.
* Nothing outside [start + start_margin, end - end_margin] is counted, for teacher or students.
* A camera / face-engine / database problem is *unmeasurable time*, never "absent".
* Every state change goes through the explicit state machine and is logged and persisted.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.core import attendance_math as am
from backend.core.camera_manager import CameraManager, CameraSpec, ONLINE
from backend.core.camera_types import normalize_source
from backend.core.clock import at_time, now_local, parse_hhmm, to_utc_naive, utcnow_naive
from backend.core.db import DatabaseManager, normalize_day
from backend.core.policy import Policy
from backend.core.recognizer import Detection, FaceRecognizer, Recognizer
from backend.core.session_state import InvalidTransition, SessionStateMachine, State as S
from backend.core.store import Store

log = logging.getLogger("smart_attendance.session")

TERMINAL = {S.COMPLETED, S.SUSPENDED, S.QUARANTINED}
RUNNING = {S.ACTIVE, S.TEACHER_ABSENT, S.RESUMED}


def normalize_camera_source(cam_source: Any) -> Any:
    """Backward-compatible helper (rooms.camera_source holds bare indexes or URLs)."""
    from backend.core.camera_types import infer_camera_type
    return normalize_source(infer_camera_type(cam_source), cam_source)


def _kv(**kw) -> str:
    return " ".join(f"{k}={v}" for k, v in kw.items() if v is not None)


@dataclass
class ClassRuntime:
    session_id: str
    timetable_id: Optional[int]
    date: str
    subject: str
    room_id: str
    teacher_id: str
    section_id: Optional[str]
    start_u: datetime
    end_u: datetime
    cs_u: datetime
    ce_u: datetime
    sm: SessionStateMachine = field(default_factory=SessionStateMachine)
    created: bool = False
    finished: bool = False
    note: str = ""
    eligible: Optional[Set[str]] = None
    teacher_authorized: bool = False
    teacher_points: List[datetime] = field(default_factory=list)
    student_points: Dict[str, List[datetime]] = field(default_factory=dict)
    student_conf: Dict[str, float] = field(default_factory=dict)
    holes: List[List[Optional[datetime]]] = field(default_factory=list)     # [start, end|None]
    last_good_scan: Optional[datetime] = None
    last_teacher_seen: Optional[datetime] = None
    authorized_at: Optional[datetime] = None
    next_scan_at: Optional[datetime] = None
    pending: List[Callable[[], Any]] = field(default_factory=list)
    unauth_seen: Set[str] = field(default_factory=set)
    alert_raised: bool = False
    preconditions_ok: bool = False
    last_scan_info: Dict[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> S:
        return self.sm.state


class SessionLogic:
    TICK_SECONDS = 5.0
    CACHE_TTL = 20.0          # timetable / rooms / cameras / settings
    USERS_TTL = 120.0         # face roster (embeddings are heavy to load)

    def __init__(self, db: DatabaseManager, engine=None, *, recognizer: Optional[Recognizer] = None,
                 cameras: Optional[CameraManager] = None, clock: Callable[[], datetime] = now_local,
                 store: Optional[Store] = None):
        self.db = db
        self.engine = engine
        self.store = store or Store(db)
        self.recognizer: Optional[Recognizer] = recognizer or (FaceRecognizer(engine) if engine is not None else None)
        self.cameras = cameras or CameraManager(on_health=self._camera_health)
        self.clock = clock
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.runtimes: Dict[str, ClassRuntime] = {}
        self._busy_rooms: Set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=int(os.getenv("ROOM_WORKERS", "8")), thread_name_prefix="room")
        self.allow_unsectioned = os.getenv("ALLOW_UNSECTIONED_CLASSES", "0") == "1"
        self._policy = Policy()
        self._timetable: List[Dict[str, Any]] = []
        self._room_cams: Dict[str, List[Dict[str, Any]]] = {}
        self._users: List[Dict[str, Any]] = []
        self._cache_at = 0.0
        self._users_at = 0.0
        self._recovered = False
        self._plan_lock = threading.Lock()
        self._virtual_now: Optional[datetime] = None
        self.last_tick_at: Optional[datetime] = None
        self.last_tick_error: Optional[str] = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="attendance-scheduler", daemon=True)
        self._thread.start()
        log.info("attendance scheduler started")

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=10.0)
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.cameras.stop_all()
        log.info("attendance scheduler stopped")

    def invalidate_timetable_cache(self) -> None:
        self._cache_at = 0.0
        self._users_at = 0.0

    def _loop(self) -> None:
        while self.running:
            t0 = time.monotonic()
            try:
                self.tick(wait=False)
                self.last_tick_error = None
            except Exception as exc:  # noqa: BLE001 - the scheduler must outlive any single failure
                self.last_tick_error = f"{type(exc).__name__}: {exc}"
                log.exception("scheduler tick failed")
            time.sleep(max(0.2, self.TICK_SECONDS - (time.monotonic() - t0)))

    # ------------------------------------------------------------------ caches
    def _now_u(self) -> datetime:
        """Scheduler time (virtual when a tick was given an explicit time, else the wall clock)."""
        return self._virtual_now or utcnow_naive()

    def _camera_health(self, spec: CameraSpec, status: str, error: Optional[str], reconnected: bool) -> None:
        self.store.set_camera_health(spec.camera_id, status, error,
                                     ok_at=utcnow_naive() if status == ONLINE else None, reconnected=reconnected)

    def _refresh(self) -> None:
        mono = time.monotonic()
        if mono - self._cache_at >= self.CACHE_TTL:
            try:
                self._policy = self.store.get_policy()
                self._timetable = self.db.get_timetable() or []
                room_ids = {t.get("room_id") for t in self._timetable}
                self._room_cams = {rid: self.store.room_cameras(rid) for rid in room_ids if rid}
                self._cache_at = mono
            except Exception:
                log.exception("cache refresh failed; keeping previous data")
        if mono - self._users_at >= self.USERS_TTL:
            try:
                self._users = [u for u in (self.db.get_all_users() or []) if u.get("role") in ("student", "teacher")]
                self._users_at = mono
            except Exception:
                log.exception("roster refresh failed; keeping previous roster")

    # ------------------------------------------------------------------ status for the API / UI
    def get_live_status(self) -> List[Dict[str, Any]]:
        out = []
        with self._lock:
            rts = list(self.runtimes.values())
        for rt in rts:
            seen = {r for r in list(rt.student_points) if rt.eligible is None or r in rt.eligible}
            out.append({
                "session_id": rt.session_id, "date": rt.date, "subject": rt.subject, "room_id": rt.room_id,
                "teacher_id": rt.teacher_id, "section_id": rt.section_id, "state": rt.state.value,
                "finished": rt.finished, "note": rt.note, "teacher_authorized": rt.teacher_authorized,
                "teacher_last_seen": rt.last_teacher_seen.isoformat() + "Z" if rt.last_teacher_seen else None,
                "students_seen": len(seen), "students_eligible": None if rt.eligible is None else len(rt.eligible),
                "start": rt.start_u.isoformat() + "Z", "end": rt.end_u.isoformat() + "Z",
                "counted_start": rt.cs_u.isoformat() + "Z", "counted_end": rt.ce_u.isoformat() + "Z",
                "camera_blind": any(h[1] is None for h in rt.holes), "teacher_absence_alert": rt.alert_raised,
                "last_scan": rt.last_scan_info,
            })
        return sorted(out, key=lambda r: (r["start"], r["room_id"]))

    get_active_sessions_status = get_live_status          # name used by the desktop UI

    # ------------------------------------------------------------------ tick
    @staticmethod
    def _slot_times(e: Dict[str, Any], now: datetime) -> Optional[Tuple[datetime, datetime]]:
        st, et = parse_hhmm(e.get("start_time", "")), parse_hhmm(e.get("end_time", ""))
        if not st or not et or et <= st:
            return None
        return to_utc_naive(at_time(now, st)), to_utc_naive(at_time(now, et))

    def tick(self, now: Optional[datetime] = None, wait: bool = True) -> None:
        """One scheduler cycle. Safe to call repeatedly: sessions are created once per class/day."""
        self._virtual_now = to_utc_naive(now) if now is not None else None
        now = now or self.clock()
        nowu = to_utc_naive(now)
        self._refresh()
        today = now.strftime("%Y-%m-%d")
        day = now.strftime("%A").lower()
        with self._plan_lock:                    # planning is serialised; room work below runs in parallel
            if not self._recovered:
                self._recover_open_sessions(nowu)
                self._recovered = True
            entries = [t for t in self._timetable if normalize_day(t.get("day_of_week", "")) == day]
            slots: Dict[Any, Tuple[datetime, datetime]] = {}
            for e in entries:
                sid = f"SES_{e.get('room_id')}_{e.get('id')}_{today}"
                times = self._slot_times(e, now)
                if times is None:
                    if sid not in self.runtimes:
                        self._quarantine_entry(sid, e, today, f"unreadable or inverted times {e.get('start_time')!r}-{e.get('end_time')!r}")
                else:
                    slots[e.get("id")] = times

            # Two slots in the same room that overlap cannot both own the camera: the earlier one wins.
            loser: Set[Any] = set()
            latest_end: Dict[Any, datetime] = {}
            for e in sorted((e for e in entries if e.get("id") in slots), key=lambda e: (str(e.get("room_id")), slots[e.get("id")][0])):
                s_u, e_u = slots[e.get("id")]
                room = e.get("room_id")
                if room in latest_end and s_u < latest_end[room]:
                    loser.add(e.get("id"))
                else:
                    latest_end[room] = e_u

            pol = self._policy
            for e in entries:
                sid = f"SES_{e.get('room_id')}_{e.get('id')}_{today}"
                if e.get("id") not in slots or sid in self.runtimes:
                    continue
                if e.get("id") in loser:
                    self._quarantine_entry(sid, e, today, "overlaps another slot in the same room")
                    continue
                start_u, end_u = slots[e.get("id")]
                if not (start_u <= nowu < end_u):
                    continue                     # not started yet, or already over: a late session is never invented
                if any(r.room_id == e.get("room_id") and r.date == today and r.subject == (e.get("subject") or "Class") and r.start_u == start_u
                       for r in self.runtimes.values()):
                    continue                     # same class under a new slot id (timetable republished mid-class)
                cs, ce = am.counted_window(start_u, end_u, pol.start_margin_min, pol.end_margin_min)
                rt = ClassRuntime(session_id=sid, timetable_id=e.get("id"), date=today, subject=e.get("subject") or "Class",
                                  room_id=e.get("room_id"), teacher_id=str(e.get("teacher_id") or "").strip(),
                                  section_id=(e.get("section_id") or None), start_u=start_u, end_u=end_u, cs_u=cs, ce_u=ce)
                rt.sm = SessionStateMachine(on_transition=lambda a, b, r, rt=rt: self._on_transition(rt, a, b, r))
                with self._lock:
                    self.runtimes[sid] = rt
            with self._lock:
                live = [rt for rt in self.runtimes.values() if not rt.finished]

        by_room: Dict[str, List[ClassRuntime]] = {}
        for rt in live:
            by_room.setdefault(rt.room_id, []).append(rt)
        futures = []
        for room, rts in by_room.items():
            with self._lock:
                if room in self._busy_rooms:
                    continue                     # the previous cycle for this room is still capturing
                self._busy_rooms.add(room)
            futures.append(self._pool.submit(self._run_room, room, sorted(rts, key=lambda r: r.start_u), nowu))
        if wait:
            for f in futures:
                f.result()

        keep: Set[str] = set()
        for rt in live:
            if not rt.finished:
                keep |= {CameraSpec.from_row(c).key for c in self._room_cams.get(rt.room_id, [])}
        self.cameras.release_unused(keep)
        with self._lock:
            for k in [k for k, r in self.runtimes.items() if r.finished and r.date != today]:
                del self.runtimes[k]
        self.last_tick_at = now

    def process_now(self, now: Optional[datetime] = None) -> None:
        self.tick(now, wait=True)

    def _run_room(self, room: str, rts: List[ClassRuntime], nowu: datetime) -> None:
        try:
            for rt in rts:
                try:
                    self._step(rt, nowu)
                except InvalidTransition:
                    log.exception("invalid state transition %s", _kv(session_id=rt.session_id, room_id=rt.room_id))
                    self._quarantine(rt, "internal state error")
                except Exception:  # noqa: BLE001 - one class must never stop the others
                    log.exception("class step failed %s", _kv(session_id=rt.session_id, room_id=rt.room_id))
                    self._db_error(rt, "unexpected error while processing the class")
        finally:
            with self._lock:
                self._busy_rooms.discard(room)

    # ------------------------------------------------------------------ persistence helpers
    def _persist(self, rt: ClassRuntime, fn: Callable[[], Any]) -> bool:
        """Run a DB write. On failure queue it (kept in order) and report DB_ERROR; a later
        cycle flushes the queue and the session recovers. Presence data is never lost."""
        if rt.pending:
            rt.pending.append(fn)
            return False
        try:
            fn()
            return True
        except Exception:  # noqa: BLE001
            log.exception("database write failed, buffering %s", _kv(session_id=rt.session_id))
            rt.pending.append(fn)
            self._db_error(rt, "database write failed")
            return False

    def _flush(self, rt: ClassRuntime) -> None:
        if not rt.pending:
            return
        try:
            while rt.pending:
                rt.pending[0]()
                rt.pending.pop(0)
        except Exception:  # noqa: BLE001
            log.warning("database still unavailable %s pending=%d", _kv(session_id=rt.session_id), len(rt.pending))
            return
        log.info("database writes flushed %s", _kv(session_id=rt.session_id))
        if rt.state == S.DB_ERROR:
            rt.sm.recover("database is back")

    def _db_error(self, rt: ClassRuntime, why: str) -> None:
        if rt.sm.can(S.DB_ERROR):
            rt.sm.go(S.DB_ERROR, why)

    def _on_transition(self, rt: ClassRuntime, old: S, new: S, reason: str) -> None:
        log.info("session state %s", _kv(session_id=rt.session_id, room_id=rt.room_id, teacher_id=rt.teacher_id,
                                          change=f"{old.value}->{new.value}", reason=f'"{reason}"'))
        if not rt.created:
            return
        sid, now = rt.session_id, self._now_u()
        self._persist(rt, lambda: self.store.add_event(sid, "state", old.value, new.value, reason, now))
        if new not in (S.DB_ERROR,):
            self._persist(rt, lambda: self.store.set_session_state(sid, new.value))

    def _event(self, rt: ClassRuntime, event_type: str, detail: str, at: Optional[datetime] = None) -> None:
        log.info("%s %s", event_type, _kv(session_id=rt.session_id, room_id=rt.room_id, teacher_id=rt.teacher_id, detail=f'"{detail}"'))
        if rt.created:
            sid = rt.session_id
            self._persist(rt, lambda: self.store.add_event(sid, event_type, rt.state.value, None, detail, at or self._now_u()))

    # ------------------------------------------------------------------ quarantine
    def _quarantine_entry(self, sid: str, e: Dict[str, Any], today: str, why: str) -> None:
        log.error("timetable slot quarantined %s", _kv(session_id=sid, timetable_id=e.get("id"), room_id=e.get("room_id"), reason=f'"{why}"'))
        rt = ClassRuntime(session_id=sid, timetable_id=e.get("id"), date=today, subject=e.get("subject") or "Class",
                          room_id=e.get("room_id") or "?", teacher_id=str(e.get("teacher_id") or ""), section_id=None,
                          start_u=datetime.min, end_u=datetime.min, cs_u=datetime.min, ce_u=datetime.min,
                          finished=True, note=why)
        rt.sm.state = S.QUARANTINED
        with self._lock:
            self.runtimes[sid] = rt

    def _quarantine(self, rt: ClassRuntime, why: str) -> None:
        rt.note = why
        if rt.sm.can(S.QUARANTINED):
            rt.sm.go(S.QUARANTINED, why)
        if rt.created:
            self._persist(rt, lambda: self.store.finalize_without_attendance(rt.session_id, "QUARANTINED", "", rt.date))
        rt.finished = not rt.pending
        log.error("session quarantined %s", _kv(session_id=rt.session_id, room_id=rt.room_id, reason=f'"{why}"'))

    # ------------------------------------------------------------------ the per-class step
    def _step(self, rt: ClassRuntime, nowu: datetime) -> None:
        if rt.pending:
            self._flush(rt)
        if rt.finished:
            return
        if rt.state in (S.SUSPENDED, S.QUARANTINED):      # outcome already decided; wait for its DB writes
            rt.finished = not rt.pending
            return
        pol = self._policy

        if nowu >= rt.end_u:                                  # class is over
            if not rt.created:
                rt.finished = True                            # never started: no late session is invented
                return
            self._finish(rt, nowu)
            return

        if not rt.preconditions_ok and not self._check_preconditions(rt):
            return
        if not rt.created:
            if self.recognizer is None or not self.recognizer.ready():
                rt.note = "face engine is not ready: automatic attendance has not started"
                return                                      # nothing is created or counted until it is ready
            if not self._ensure_session(rt, nowu):
                return
            rt.note = rt.note if rt.note.startswith("no section") else ""
        if rt.state == S.SCHEDULED:
            rt.sm.go(S.WAITING_TEACHER, "class started; waiting for the scheduled teacher")

        # teacher never showed up (and the camera was healthy while we waited) -> suspend
        if not rt.teacher_authorized and nowu >= rt.start_u + timedelta(minutes=pol.teacher_wait_limit_min):
            blind = am.minutes(am.clamp([(h[0], h[1] or nowu) for h in rt.holes],
                                        (rt.start_u, rt.start_u + timedelta(minutes=pol.teacher_wait_limit_min))))
            if pol.teacher_wait_limit_min - blind >= pol.teacher_wait_limit_min * pol.min_measurable_ratio:
                self._suspend(rt, nowu)
                return

        if rt.next_scan_at is not None and nowu < rt.next_scan_at:
            return
        self._scan_cycle(rt, nowu)
        # cadence follows the state *after* the scan: 60 s heartbeat while running, fast retries
        # while waiting for the teacher or while the camera is blind
        blind = any(h[1] is None for h in rt.holes)
        interval = pol.heartbeat_sec if rt.state in RUNNING and not blind else pol.waiting_poll_sec
        rt.next_scan_at = nowu + timedelta(seconds=interval)

    def _check_preconditions(self, rt: ClassRuntime) -> bool:
        if not rt.teacher_id:
            self._quarantine(rt, "no teacher is assigned to this class")
            return False
        if not any(u["role"] == "teacher" and (str(u["roll_no"]).casefold() == rt.teacher_id.casefold()
                                                or str(u["name"]).strip().casefold() == rt.teacher_id.casefold())
                   for u in self._users):
            rt.note = f"scheduled teacher '{rt.teacher_id}' has no enrolled face; they cannot authorize the class"
            if not rt.last_scan_info.get("teacher_unreg_logged"):
                log.error("scheduled teacher is not face-enrolled %s", _kv(session_id=rt.session_id, teacher_id=rt.teacher_id))
                rt.last_scan_info["teacher_unreg_logged"] = True
            self._users_at = 0.0                       # re-read the roster: they may have just enrolled
            return False
        cams = self._room_cams.get(rt.room_id) or []
        if not cams:
            rt.note = "room has no enabled camera"
            if not rt.last_scan_info.get("no_camera_logged"):
                log.warning("no enabled camera; attendance not started %s", _kv(session_id=rt.session_id, room_id=rt.room_id))
                rt.last_scan_info["no_camera_logged"] = True
            return False                       # re-checked every tick: a camera added later starts the class
        if rt.section_id:
            rt.eligible = self.store.eligible_students(rt.section_id)
        elif self.allow_unsectioned:
            rt.eligible = None
            log.warning("class has no section; ALLOW_UNSECTIONED_CLASSES=1 so every student is eligible %s", _kv(session_id=rt.session_id))
        else:
            rt.eligible = set()
            rt.note = "no section assigned: student attendance disabled for this class"
        rt.preconditions_ok = True
        return True

    def _ensure_session(self, rt: ClassRuntime, nowu: datetime) -> bool:
        try:
            prior = self.store.find_session_by_schedule(rt.date, rt.room_id, rt.subject, rt.start_u)
            if prior and prior["session_id"] != rt.session_id:
                rt.session_id = prior["session_id"]          # adopt the session this class already has
            created = self.db.create_session(rt.session_id, rt.timetable_id, rt.date, rt.subject, room_id=rt.room_id,
                                             teacher_id=rt.teacher_id, section_id=rt.section_id, counted_start=rt.cs_u,
                                             counted_end=rt.ce_u, scheduled_start=rt.start_u, scheduled_end=rt.end_u,
                                             state=S.SCHEDULED.value)
            row = None if created else self.store.get_session(rt.session_id)
        except Exception:  # noqa: BLE001
            log.exception("could not create/load session %s", _kv(session_id=rt.session_id))
            return False                                           # retried next tick
        rt.created = True
        if created:
            self.store.add_event(rt.session_id, "session_created", None, S.SCHEDULED.value,
                                 f"{rt.subject} {rt.room_id} teacher={rt.teacher_id}", nowu)
            log.info("session created %s", _kv(session_id=rt.session_id, room_id=rt.room_id, teacher_id=rt.teacher_id, section_id=rt.section_id))
            return True
        if row is None or row.get("finalized_at") is not None:
            rt.finished = True                                      # already completed earlier (restart / duplicate tick)
            log.info("session already finalized; skipping %s", _kv(session_id=rt.session_id))
            return False
        self._rehydrate(rt, row, nowu)
        return True

    def _rehydrate(self, rt: ClassRuntime, row: Dict[str, Any], nowu: datetime) -> None:
        """Resume a session that exists in MySQL (process restarted mid-class)."""
        pol = self._policy
        state = S(row["state"]) if row.get("state") in S.__members__ else S.WAITING_TEACHER
        rt.sm.state = state
        rt.sm.resume_to = state if state in RUNNING | {S.WAITING_TEACHER} else S.ACTIVE if state != S.SCHEDULED else S.WAITING_TEACHER
        last = rt.start_u
        for r in self.store.load_recognitions(rt.session_id):
            at = r["detected_at"]
            last = max(last, at)
            if r["role"] == "teacher":            # only the scheduled teacher is ever stored
                rt.teacher_points.append(at)
                rt.last_teacher_seen = max(at, rt.last_teacher_seen or at)
                rt.teacher_authorized = True
            else:
                rt.student_points.setdefault(r["roll_no"], []).append(at)
                rt.student_conf[r["roll_no"]] = max(rt.student_conf.get(r["roll_no"], 0.0), float(r["confidence"] or 0))
        for h in self.store.load_holes(rt.session_id):
            rt.holes.append([h["started_at"], h["ended_at"]])
            last = max(last, h["ended_at"] or h["started_at"])
        for ev in self.store.list_events(rt.session_id):
            last = max(last, ev["occurred_at"])
        if state in RUNNING:
            rt.teacher_authorized = True
        # Downtime of this program is blind time, not absence.
        start = last + timedelta(seconds=pol.dwell_half_window_sec)
        if nowu - start > timedelta(seconds=pol.heartbeat_sec * 1.5):
            rt.holes.append([start, nowu])
            sid = rt.session_id
            self._persist(rt, lambda: (self.store.open_hole(sid, start, "engine was not running"),
                                       self.store.close_hole(sid, start, nowu)))
            log.warning("engine restart gap recorded as unmeasurable %s", _kv(session_id=rt.session_id, from_=start.isoformat(), to=nowu.isoformat()))
        log.info("session rehydrated %s", _kv(session_id=rt.session_id, state=state.value, teacher_points=len(rt.teacher_points),
                                               students=len(rt.student_points)))

    def _recover_open_sessions(self, nowu: datetime) -> None:
        """At startup: sessions left open in MySQL by a crash are resumed (class still running)
        or finalized (class over) instead of staying 'active' forever."""
        try:
            rows = self.store.open_sessions()
        except Exception:
            log.exception("could not look for unfinished sessions")
            self._recovered = False
            return
        for row in rows:
            sid = row["session_id"]
            if sid in self.runtimes or not row.get("scheduled_end"):
                continue
            pol = self._policy
            rt = ClassRuntime(session_id=sid, timetable_id=row.get("timetable_id"), date=str(row.get("date")),
                              subject=row.get("subject") or "Class", room_id=row.get("room_id") or "?",
                              teacher_id=str(row.get("teacher_id") or ""), section_id=row.get("section_id"),
                              start_u=row["scheduled_start"], end_u=row["scheduled_end"],
                              cs_u=row["counted_start"] or row["scheduled_start"], ce_u=row["counted_end"] or row["scheduled_end"])
            rt.sm = SessionStateMachine(on_transition=lambda a, b, r, rt=rt: self._on_transition(rt, a, b, r))
            rt.created = True
            rt.preconditions_ok = True
            rt.eligible = self.store.eligible_students(rt.section_id) if rt.section_id else (None if self.allow_unsectioned else set())
            self._rehydrate(rt, row, nowu)
            with self._lock:
                self.runtimes[sid] = rt
            log.warning("recovered unfinished session %s", _kv(session_id=sid, state=rt.state.value))

    # ------------------------------------------------------------------ scanning
    def _refresh_eligibility(self, rt: ClassRuntime) -> None:
        if rt.section_id:
            try:
                rt.eligible = self.store.eligible_students(rt.section_id)
            except Exception:
                log.exception("could not refresh section roster %s", _kv(session_id=rt.session_id))

    def _scan_cycle(self, rt: ClassRuntime, nowu: datetime) -> None:
        pol = self._policy
        self._refresh_eligibility(rt)
        cams = [CameraSpec.from_row(c) for c in self._room_cams.get(rt.room_id, [])]
        frames_by_cam: Dict[Optional[int], list] = {}
        cam_status: Dict[str, str] = {}
        for spec in cams:
            h = self.cameras.acquire(spec)
            frames = h.get_frames(pol.scan_frames, pol.scan_frame_delay_sec, timeout=6.0)
            cam_status[spec.name] = h.status
            if frames:
                frames_by_cam[spec.camera_id] = frames
        at = self._virtual_now or max(utcnow_naive(), nowu)
        rt.last_scan_info = {"at": at.isoformat() + "Z", "cameras": cam_status}

        if not frames_by_cam:
            self._enter_loss(rt, at, "no camera is delivering frames: " + ", ".join(f"{n}={s}" for n, s in cam_status.items()))
            return
        if self.recognizer is None or not self.recognizer.ready():
            self._enter_loss(rt, at, "face engine is not ready")
            return
        try:
            detections = self.recognizer.identify(frames_by_cam, self._users)
        except Exception:  # noqa: BLE001
            log.exception("recognition failed %s", _kv(session_id=rt.session_id, room_id=rt.room_id))
            self._enter_loss(rt, at, "face recognition raised an error")
            return

        self._end_loss(rt, at)
        if rt.last_good_scan is None and not rt.holes:
            half = timedelta(seconds=pol.dwell_half_window_sec)
            if at - rt.start_u > half:                      # measurement started late (e.g. face model was still loading)
                late_end = at - half
                rt.holes.append([rt.start_u, late_end])
                sid, start = rt.session_id, rt.start_u
                self._persist(rt, lambda: (self.store.open_hole(sid, start, "measurement started late"),
                                           self.store.close_hole(sid, start, late_end)))
                log.warning("measurement started late; earlier time is unmeasurable %s", _kv(session_id=sid, room_id=rt.room_id))
        rt.last_good_scan = at
        rt.last_scan_info.update(students_detected=sum(1 for d in detections if d.role == "student"),
                                 teachers_detected=sum(1 for d in detections if d.role == "teacher"))
        self._handle_detections(rt, detections, at)

    def _enter_loss(self, rt: ClassRuntime, at: datetime, reason: str) -> None:
        """No usable measurement: open an *unmeasurable* interval. Never counts as absence."""
        if any(h[1] is None for h in rt.holes):
            rt.last_scan_info["unmeasurable_reason"] = reason
            return
        half = timedelta(seconds=self._policy.dwell_half_window_sec)
        start = (rt.last_good_scan + half) if rt.last_good_scan else rt.start_u
        start = min(max(start, rt.start_u), at)
        rt.holes.append([start, None])
        rt.last_scan_info["unmeasurable_reason"] = reason
        sid = rt.session_id
        log.warning("camera/recognition unavailable %s", _kv(session_id=sid, room_id=rt.room_id, reason=f'"{reason}"'))
        self._persist(rt, lambda: self.store.open_hole(sid, start, reason))
        if rt.sm.can(S.CAMERA_LOST) and rt.state != S.CAMERA_LOST:
            rt.sm.go(S.CAMERA_LOST, reason)

    def _end_loss(self, rt: ClassRuntime, at: datetime) -> None:
        open_holes = [h for h in rt.holes if h[1] is None]
        if not open_holes:
            return
        end = max(open_holes[0][0], at - timedelta(seconds=self._policy.dwell_half_window_sec))
        for h in open_holes:
            h[1] = end
            sid, start = rt.session_id, h[0]
            self._persist(rt, lambda sid=sid, start=start: self.store.close_hole(sid, start, end))
        log.info("camera/recognition recovered %s", _kv(session_id=rt.session_id, room_id=rt.room_id))
        rt.last_scan_info.pop("unmeasurable_reason", None)
        if rt.state == S.CAMERA_LOST:
            rt.sm.recover("measurements are available again")

    def _matches_teacher(self, rt: ClassRuntime, d: Detection) -> bool:
        exp = rt.teacher_id.casefold()
        return d.role == "teacher" and exp != "" and exp in {str(d.roll_no).casefold(), str(d.name).strip().casefold()}

    def _handle_detections(self, rt: ClassRuntime, dets: List[Detection], at: datetime) -> None:
        sid = rt.session_id
        auth = next((d for d in dets if self._matches_teacher(rt, d)), None)
        for d in dets:
            if d.role == "teacher" and not self._matches_teacher(rt, d) and d.roll_no not in rt.unauth_seen:
                rt.unauth_seen.add(d.roll_no)
                self._event(rt, "unauthorized_teacher_seen",
                            f"{d.roll_no} was recognised but is not the scheduled teacher ({rt.teacher_id}); session not authorized by them", at)

        if rt.state == S.WAITING_TEACHER and auth is not None:
            rt.teacher_authorized, rt.authorized_at = True, at
            self._event(rt, "teacher_authorized", f"{auth.roll_no} confidence={auth.confidence:.2f}", at)
            rt.sm.go(S.ACTIVE, "scheduled teacher recognised")

        if rt.state not in RUNNING and not (rt.state in (S.DB_ERROR, S.CAMERA_LOST) and rt.teacher_authorized):
            return                                         # not authorized yet: students are not recorded

        events = []
        if auth is not None:
            rt.teacher_points.append(at)
            rt.last_teacher_seen = at
            events.append((auth.roll_no, "teacher", auth.camera_id, at, auth.confidence))
            if rt.alert_raised:
                self._event(rt, "teacher_returned", "teacher recognised again after an absence alert", at)
                rt.alert_raised = False
            if rt.state == S.TEACHER_ABSENT:
                rt.sm.go(S.RESUMED, "teacher recognised again")
                rt.sm.go(S.ACTIVE, "resumed")
        else:
            self._check_teacher_absence(rt, at)

        for d in dets:
            if d.role != "student":
                continue
            if rt.eligible is not None and d.roll_no not in rt.eligible:
                continue                                   # not enrolled in this class's section
            rt.student_points.setdefault(d.roll_no, []).append(at)
            rt.student_conf[d.roll_no] = max(rt.student_conf.get(d.roll_no, 0.0), d.confidence)
            events.append((d.roll_no, "student", d.camera_id, at, d.confidence))
        if events:
            self._persist(rt, lambda: self.store.add_recognitions(sid, events))

    def _check_teacher_absence(self, rt: ClassRuntime, at: datetime) -> None:
        pol = self._policy
        ref = rt.last_teacher_seen or rt.authorized_at or rt.start_u
        holes = [(h[0], h[1] or at) for h in rt.holes]
        unseen = am.minutes(am.subtract([(ref, at)], holes))        # blind time does not count
        if unseen > pol.teacher_bridge_min and rt.state in (S.ACTIVE, S.RESUMED) and rt.sm.can(S.TEACHER_ABSENT):
            rt.sm.go(S.TEACHER_ABSENT, f"teacher not recognised for {unseen:.1f} min")
        if unseen > pol.teacher_absence_alert_min and not rt.alert_raised:
            rt.alert_raised = True
            self._event(rt, "teacher_absence_alert",
                        f"teacher unrecognised for {unseen:.0f} continuous minutes (> {pol.teacher_absence_alert_min:.0f}); flagged for HOD/admin", at)

    # ------------------------------------------------------------------ outcomes
    def _suspend(self, rt: ClassRuntime, nowu: datetime) -> None:
        self._event(rt, "session_suspended", f"scheduled teacher not recognised within {self._policy.teacher_wait_limit_min:.0f} min", nowu)
        rt.sm.go(S.SUSPENDED, "teacher not verified in time")
        sid = rt.session_id
        self._persist(rt, lambda: self.store.finalize_without_attendance(sid, "SUSPENDED", rt.teacher_id, rt.date, "absent"))
        rt.finished = not rt.pending

    def _finish(self, rt: ClassRuntime, nowu: datetime) -> None:
        pol = self._policy
        for h in rt.holes:
            if h[1] is None:
                h[1] = rt.end_u
                sid, start = rt.session_id, h[0]
                self._persist(rt, lambda sid=sid, start=start: self.store.close_hole(sid, start, rt.end_u))
        if rt.pending:                                   # DB still down: keep trying next cycle, nothing is lost
            self._flush(rt)
            if rt.pending:
                return
        self._refresh_eligibility(rt)
        window = (rt.cs_u, rt.ce_u)
        holes = [(h[0], h[1]) for h in rt.holes if h[1] is not None]
        half = pol.dwell_half_window_sec

        t_pres = am.compute_presence(rt.teacher_points, window, holes, half, pol.teacher_bridge_min)
        no_data = t_pres.window_minutes > 0 and t_pres.measurable_minutes < t_pres.window_minutes * pol.min_measurable_ratio
        if t_pres.present_minutes == 0:
            t_status = "unmeasurable" if no_data or t_pres.measurable_minutes == 0 else "absent"
        elif t_pres.longest_absence_minutes > pol.teacher_absence_alert_min:
            t_status = "partial_absent"
        else:
            t_status = "present"
        flags = [{"teacher_id": rt.teacher_id, "flag_type": "TEACHER_ABSENCE_ALERT", "started_at": a, "ended_at": b, "minutes": m}
                 for a, b, m in t_pres.absence_runs if m > pol.teacher_absence_alert_min]
        iso = lambda d: d.isoformat(timespec="seconds") if d else None
        teacher = {"teacher_id": rt.teacher_id, "date": rt.date, "counted_start": iso(rt.cs_u), "counted_end": iso(rt.ce_u),
                   "first_seen": iso(min(rt.teacher_points)) if rt.teacher_points else None,
                   "last_seen": iso(max(rt.teacher_points)) if rt.teacher_points else None,
                   "present_minutes": t_pres.present_minutes,
                   "absent_minutes": max(0.0, t_pres.measurable_minutes - t_pres.present_minutes),
                   "longest_absence_minutes": t_pres.longest_absence_minutes, "status": t_status,
                   "absence_over_20m": bool(flags)}

        students: List[Dict[str, Any]] = []
        grace_used: List[Tuple[Dict[str, Any], str]] = []
        if rt.teacher_authorized:
            roster = rt.eligible if rt.eligible is not None else {u["roll_no"] for u in self._users if u["role"] == "student"}
            for roll in sorted(roster):
                p = am.compute_presence(rt.student_points.get(roll, []), window, holes, half)
                too_blind = p.measurable_minutes < p.window_minutes * pol.min_measurable_ratio
                if p.percentage is None or (too_blind and not rt.student_points.get(roll)):
                    status, pct = "unmeasurable", p.percentage
                else:
                    grace = self.db.get_valid_transition_grace(roll, rt.date, rt.timetable_id) if rt.timetable_id else None
                    calc = self.db.calculate_attendance_with_grace(
                        roll, p.present_minutes, p.measurable_minutes,
                        float(grace.get("grace_minutes", 5.0)) if grace else 0.0)
                    status, pct = calc["status"], calc["effective_percent"]
                    if grace:
                        grace_used.append((grace, rt.session_id))
                students.append({"roll_no": roll, "present_minutes": round(p.present_minutes, 2),
                                 "measurable_minutes": round(p.measurable_minutes, 2), "percentage": pct, "status": status,
                                 "confidence": rt.student_conf.get(roll, 0.0)})
        final_state = "COMPLETED"
        try:
            self.store.finalize_session(rt.session_id, teacher=teacher, students=students, flags=flags, final_state=final_state)
        except Exception:  # noqa: BLE001
            log.exception("finalization failed, will retry %s", _kv(session_id=rt.session_id))
            self._db_error(rt, "could not write final attendance")
            return                                        # retried on the next cycle; state is still in memory
        for grace, sid in grace_used:
            try:
                self.db.consume_transition_grace(grace["id"], sid)
            except Exception:
                log.exception("could not mark grace used %s", _kv(session_id=sid))
        if rt.sm.can(S.COMPLETED):
            rt.sm.go(S.COMPLETED, "class ended; attendance finalized")
        rt.finished = True
        log.info("session finalized %s", _kv(session_id=rt.session_id, room_id=rt.room_id, teacher_id=rt.teacher_id,
                                              teacher_status=t_status, students=len(students),
                                              unmeasurable_min=round(t_pres.unmeasurable_minutes, 1)))
