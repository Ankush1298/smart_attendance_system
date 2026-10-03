# Attendance Policy

## Standard 50-minute lecture

Only the middle 40 minutes count toward attendance.

| Lecture time | Students | Teacher |
|---|---|---|
| 0–5 min | Excluded | Excluded |
| 5–45 min | Counted | Counted |
| 45–50 min | Excluded | Excluded |

## Students

The active attendance scanner checks every 60 seconds. Presence is accumulated from detection intervals, so attendance is not calculated as `number_of_samples × 1 minute`.

## Teacher

The scheduled teacher is checked every 60 seconds during the counted 40-minute window. Recognition anywhere in the classroom counts as presence. A missed recognition starts or continues an unrecognized interval. If the teacher becomes recognized again, the interval ends. More than 20 continuous minutes unrecognized is recorded as a teacher absence/flag for reporting.

The first/last five-minute exclusion applies equally to students and teachers.
