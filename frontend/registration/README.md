The mobile KYC UI is currently served by register.py so the existing tested camera/KYC flow is unchanged. This folder is reserved for extracted frontend assets during the next verification phase.


## Attendance Window Policy

For a standard 50-minute lecture, attendance is counted only during minutes 5 through 45.

- First 5 minutes: excluded for **students and teachers**
- Middle 40 minutes: counted for **students and teachers**
- Last 5 minutes: excluded for **students and teachers**
- Student camera sampling during the counted window: every 60 seconds
- Student presence is accumulated using detection intervals rather than discrete sample counts.
- Teacher presence is evaluated every 60 seconds; the scheduled teacher is considered present when recognized anywhere inside the classroom.
- A missed teacher recognition starts/continues an unrecognized interval; it does not immediately mean the teacher left.
- Teacher absence is reported when the teacher remains unrecognized for more than 20 continuous minutes during the counted window.
