"""Attendance policy: defaults, env overrides and DB-backed (admin editable) overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any, Dict


@dataclass(frozen=True)
class Policy:
    start_margin_min: float = 5.0        # excluded from the start of every lecture
    end_margin_min: float = 5.0          # excluded from the end of every lecture
    heartbeat_sec: float = 60.0          # recognition cadence while ACTIVE
    waiting_poll_sec: float = 15.0       # recognition cadence while waiting for the teacher
    dwell_half_window_sec: float = 30.0  # each detection covers +/- this much presence
    teacher_bridge_min: float = 3.0      # a missed recognition gap this short is not "left"
    teacher_absence_alert_min: float = 20.0  # continuous unrecognized time that raises a flag
    teacher_wait_limit_min: float = 20.0  # no teacher by then -> session suspended
    min_measurable_ratio: float = 0.5    # below this share of measurable time -> "unmeasurable"
    scan_frames: int = 4                 # frames per heartbeat
    scan_frame_delay_sec: float = 0.2

    def validate(self) -> "Policy":
        errs = []
        for f in fields(self):
            v = getattr(self, f.name)
            if not isinstance(v, (int, float)) or v < 0:
                errs.append(f"{f.name} must be a non-negative number")
        if self.heartbeat_sec < 5:
            errs.append("heartbeat_sec must be >= 5")
        if self.dwell_half_window_sec <= 0:
            errs.append("dwell_half_window_sec must be > 0")
        if self.min_measurable_ratio > 1:
            errs.append("min_measurable_ratio must be <= 1")
        if self.scan_frames < 1:
            errs.append("scan_frames must be >= 1")
        if errs:
            raise ValueError("; ".join(errs))
        return self


def _env_overrides() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields(Policy):
        raw = os.getenv("ATTENDANCE_" + f.name.upper())
        if raw not in (None, ""):
            try:
                out[f.name] = type(f.default)(float(raw))
            except ValueError:
                pass
    return out


def build_policy(db_values: Dict[str, Any] | None = None) -> Policy:
    """defaults < environment (ATTENDANCE_<NAME>) < database app_settings."""
    values = _env_overrides()
    for k, v in (db_values or {}).items():
        if k in {f.name for f in fields(Policy)}:
            values[k] = type(getattr(Policy, k))(float(v))
    return replace(Policy(), **values).validate()


POLICY_KEYS = [f.name for f in fields(Policy)]
