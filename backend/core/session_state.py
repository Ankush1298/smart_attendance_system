"""Explicit session state machine. Every transition is validated and reported."""
from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, FrozenSet, Optional


class State(str, Enum):
    SCHEDULED = "SCHEDULED"
    WAITING_TEACHER = "WAITING_TEACHER"
    ACTIVE = "ACTIVE"
    TEACHER_ABSENT = "TEACHER_ABSENT"
    RESUMED = "RESUMED"
    CAMERA_LOST = "CAMERA_LOST"
    RECOVERED = "RECOVERED"
    DB_ERROR = "DB_ERROR"
    QUARANTINED = "QUARANTINED"
    SUSPENDED = "SUSPENDED"      # teacher never authorized the session in time
    COMPLETED = "COMPLETED"


S = State
_RUNNING = frozenset({S.WAITING_TEACHER, S.ACTIVE, S.TEACHER_ABSENT, S.RESUMED})
# States a CAMERA_LOST / DB_ERROR excursion may return to (via RECOVERED).
RESUMABLE = _RUNNING

TRANSITIONS: Dict[State, FrozenSet[State]] = {
    S.SCHEDULED: frozenset({S.WAITING_TEACHER, S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.SUSPENDED, S.COMPLETED}),
    S.WAITING_TEACHER: frozenset({S.ACTIVE, S.SUSPENDED, S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.COMPLETED}),
    S.ACTIVE: frozenset({S.TEACHER_ABSENT, S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.COMPLETED}),
    S.TEACHER_ABSENT: frozenset({S.RESUMED, S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.COMPLETED}),
    S.RESUMED: frozenset({S.ACTIVE, S.TEACHER_ABSENT, S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.COMPLETED}),
    S.CAMERA_LOST: frozenset({S.RECOVERED, S.DB_ERROR, S.QUARANTINED, S.SUSPENDED, S.COMPLETED}),
    S.DB_ERROR: frozenset({S.RECOVERED, S.QUARANTINED, S.COMPLETED}),
    S.RECOVERED: frozenset(RESUMABLE | {S.CAMERA_LOST, S.DB_ERROR, S.QUARANTINED, S.COMPLETED}),
    S.QUARANTINED: frozenset({S.COMPLETED}),
    S.SUSPENDED: frozenset({S.COMPLETED}),
    S.COMPLETED: frozenset(),
}


class InvalidTransition(Exception):
    pass


class SessionStateMachine:
    """Holds the state; ``on_transition(old, new, reason)`` is called after each valid move."""

    def __init__(self, initial: State = S.SCHEDULED,
                 on_transition: Optional[Callable[[State, State, str], None]] = None):
        self.state = initial
        self.resume_to: State = S.WAITING_TEACHER  # where RECOVERED returns to
        self._cb = on_transition

    def can(self, new: State) -> bool:
        return new in TRANSITIONS[self.state]

    def go(self, new: State, reason: str = "") -> bool:
        """Move to ``new``. Same-state is a no-op (False); an illegal move raises."""
        if new == self.state:
            return False
        if not self.can(new):
            raise InvalidTransition(f"{self.state.value} -> {new.value} is not allowed ({reason})")
        old = self.state
        if new in (S.CAMERA_LOST, S.DB_ERROR):
            if old in RESUMABLE:
                self.resume_to = old
            elif old == S.SCHEDULED:
                self.resume_to = S.WAITING_TEACHER
        self.state = new
        if self._cb:
            self._cb(old, new, reason)
        return True

    def recover(self, reason: str = "") -> State:
        """CAMERA_LOST/DB_ERROR -> RECOVERED -> the state held before the excursion."""
        self.go(S.RECOVERED, reason)
        target = self.resume_to
        # An excursion from RESUMED is only the transient marker; continue as ACTIVE.
        if target == S.RESUMED:
            target = S.ACTIVE
        self.go(target, "resume after recovery")
        return self.state

    @property
    def running(self) -> bool:
        return self.state in RESUMABLE | {S.CAMERA_LOST, S.DB_ERROR, S.RECOVERED}

    @property
    def terminal(self) -> bool:
        return self.state in (S.COMPLETED, S.SUSPENDED, S.QUARANTINED)
