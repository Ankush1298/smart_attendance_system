import pytest

from backend.core.session_state import InvalidTransition, SessionStateMachine, State as S


def test_happy_path_and_logging():
    log = []
    sm = SessionStateMachine(on_transition=lambda a, b, r: log.append((a, b, r)))
    sm.go(S.WAITING_TEACHER, "start"); sm.go(S.ACTIVE, "teacher"); sm.go(S.COMPLETED, "end")
    assert [(a.value, b.value) for a, b, _ in log] == [("SCHEDULED", "WAITING_TEACHER"),
                                                       ("WAITING_TEACHER", "ACTIVE"), ("ACTIVE", "COMPLETED")]


@pytest.mark.parametrize("start,bad", [
    (S.SCHEDULED, S.ACTIVE),            # cannot skip teacher verification
    (S.WAITING_TEACHER, S.RESUMED),
    (S.COMPLETED, S.ACTIVE),
    (S.ACTIVE, S.WAITING_TEACHER),
    (S.QUARANTINED, S.ACTIVE),
    (S.SUSPENDED, S.ACTIVE),
])
def test_invalid_transitions_rejected(start, bad):
    sm = SessionStateMachine(start)
    with pytest.raises(InvalidTransition):
        sm.go(bad)


def test_camera_lost_returns_to_previous_state():
    sm = SessionStateMachine()
    sm.go(S.WAITING_TEACHER); sm.go(S.ACTIVE); sm.go(S.TEACHER_ABSENT)
    sm.go(S.CAMERA_LOST, "usb unplugged")
    assert sm.recover("camera back") == S.TEACHER_ABSENT


def test_camera_lost_before_start_resumes_waiting():
    sm = SessionStateMachine()
    sm.go(S.CAMERA_LOST)
    assert sm.recover() == S.WAITING_TEACHER


def test_same_state_is_noop_and_repeated_loss_keeps_resume_target():
    sm = SessionStateMachine()
    sm.go(S.WAITING_TEACHER); sm.go(S.ACTIVE); sm.go(S.CAMERA_LOST)
    assert sm.go(S.CAMERA_LOST) is False
    sm.go(S.DB_ERROR)
    assert sm.recover() == S.ACTIVE


def test_teacher_absent_then_resumed():
    sm = SessionStateMachine()
    sm.go(S.WAITING_TEACHER); sm.go(S.ACTIVE); sm.go(S.TEACHER_ABSENT); sm.go(S.RESUMED); sm.go(S.ACTIVE)
    assert sm.state == S.ACTIVE
