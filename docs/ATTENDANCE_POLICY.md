# Attendance Policy

## Counted window (students and teachers)
For a standard 50-minute lecture only the middle 40 minutes count. The margins are configurable on the Settings page (defaults: 5 minutes at the start, 5 at the end).

| Lecture time | Students | Teacher |
|---|---|---|
| 0–5 min | Not counted | Not counted |
| 5–45 min | Counted | Counted |
| 45–50 min | Not counted | Not counted |

`counted_start = session_start + start_margin`, `counted_end = session_end − end_margin`. Detections outside the window are stored as events but never contribute to minutes.

## Students
* Eligible only if they belong to the section of the timetable slot.
* The class must be authorized by the scheduled teacher before students are recorded.
* Recognition about every 60 seconds; each detection counts ±30 s. Overlapping intervals are merged (dwell time), clamped to the counted window and reduced by any camera/engine outage.
* Percentage = present minutes ÷ measurable minutes. ≥ 80 % present, 60–80 % warning, below absent. If less than 50 % of the window was measurable the result is *unmeasurable*.

## Teacher
* Authorization: the **scheduled** teacher is recognised on the room's camera. No other person can authorize the class.
* Presence is the whole classroom (no zone). A short missed recognition (≤ 3 min) is bridged.
* A continuous unrecognised stretch longer than 20 minutes (outside outages) creates an HOD/admin flag. The flag is separate from the teacher's status and never marks the class or students absent.
* If the teacher is not verified within 20 minutes (camera working) the session is suspended and the teacher is recorded absent.

## Hardware or software failure
Camera loss, face-engine failure, a database outage or a restart of the program is **unmeasurable time**: it is never counted as absence of the teacher or students.
