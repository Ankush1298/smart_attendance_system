"""KYC scan: real-time (not frame-count) windows. Same strictness at any frame rate, no stuck FRONT."""
import numpy as np
import pytest

import kyc_core as k

S = 640
EMB = np.ones(512, dtype=np.float32) / np.sqrt(512)


class FakeDB:
    def verify_credential(self, **kw): return {"valid": True, "name": "T"}
    def get_all_users(self): return []
    def upsert_user(self, **kw): self.saved = kw


class Clock:
    t = 1000.0
    def __call__(self): return self.t


def face(rng, yaw, pitch, jitter):
    return [{"bbox": (S * .3, S * .3, S * .7, S * .7), "det_score": .9, "norm": 20.0, "embedding": EMB,
             "yaw": yaw + rng.normal(0, jitter), "pitch": pitch + rng.normal(0, jitter),
             "roll": rng.normal(0, 2)}]


# (yaw, pitch) a cooperative user holds per step; LEFT/RIGHT/UP/DOWN beyond the unchanged thresholds
HOLD = {"FRONT": (0.0, 0.5), "UP": (0.0, 0.30), "DOWN": (0.0, 0.76), "LEFT": (0.30, 0.5), "RIGHT": (-0.30, 0.5)}


def run(monkeypatch, interval, jitter=0.01, seed=0, wobble=0.0, max_s=60, pose=HOLD):
    clock = Clock()
    monkeypatch.setattr(k.time, "time", clock)
    rng = np.random.default_rng(seed)
    s = k.KYCSession(FakeDB())
    assert s.start_session("1", "T", "p")["status"] == "success"
    t0, done_at = clock.t, {}
    while s.phase == "scanning" and clock.t - t0 < max_s:
        clock.t += interval
        cur = k.STEPS[s.step_idx]
        yaw, pitch = pose[cur]
        yaw += wobble * np.sin(clock.t * 7)                 # head moving side to side
        s.process(face(rng, yaw, pitch, jitter), S)
        for d in s.done:
            done_at.setdefault(d, clock.t - t0)
    return s, done_at


@pytest.mark.parametrize("interval", [0.1, 0.25, 0.5])      # ~10 fps, 4 fps, 2 fps
def test_all_steps_complete_at_any_frame_rate_in_similar_real_time(monkeypatch, interval):
    s, done_at = run(monkeypatch, interval)
    assert s.phase == "complete", (s.phase, s.done)
    assert done_at["FRONT"] < 5.0, done_at                     # was 8-15 s at low fps
    assert max(done_at.values()) < 20.0, done_at


def test_front_still_rejects_a_moving_head(monkeypatch):
    s, done_at = run(monkeypatch, 0.2, wobble=0.06, max_s=15)
    assert "FRONT" not in s.done


def test_turn_not_far_enough_is_still_rejected(monkeypatch):
    weak = dict(HOLD, LEFT=(0.10, 0.5))                         # 40 deg needed, this is ~14 deg
    s, _ = run(monkeypatch, 0.2, pose=weak, max_s=15)
    assert "LEFT" not in s.done and s.phase == "scanning"
