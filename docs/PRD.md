# Product Requirements Document (PRD)
## Smart Campus — Campus ERP + AI Support Assistant

| | |
|---|---|
| Status | Draft v0.1 for review |
| Builds on | Smart Attendance (this repository) |
| Audience | Founders / college management, product, engineering, department heads |
| Companion | [TRD.md](TRD.md) |

---

## 1. Summary

Smart Attendance today automates classroom attendance from the timetable using camera-based face recognition, with roles for super admin, admin, HOD, teacher and student.

**Smart Campus** extends it into a single system that runs the whole college: academics, fees, exams, library, hostel, transport, HR, admissions, notices, and a **24×7 AI support assistant** that answers students' questions and completes routine requests (certificates, fee receipts, leave, bonafide, etc.) without visiting a department or standing in a queue.

Attendance stays the "hero" feature and becomes the first module of a larger platform; its data (who attended what) feeds exams eligibility, fee/fine rules, parent alerts and analytics.

## 2. Problem

| Who | Pain today |
|---|---|
| Students | Visit the office/department repeatedly for bonafide, fee receipts, marks, no-dues, ID cards, leave; queues; office hours; no status visibility; same questions asked daily. |
| Faculty | Manual marks entry, leave approvals on paper, chasing students, duplicate data entry across registers. |
| Office / department staff | Spend most of the day answering repetitive questions and issuing documents by hand. |
| HOD / Principal | No single live view of attendance, results, fee collection, faculty workload; reports assembled manually. |
| Parents | Learn about low attendance or dues too late. |

## 3. Goals and non-goals

### Goals
1. **Deflect ≥ 70 % of repetitive office visits** within two semesters of rollout (measured by ticket/visit counts before vs after).
2. **One login, one source of truth** for every student, teacher and staff member across all college processes.
3. **Self-service for the top 20 student requests** end-to-end (see §7), with status tracking and notifications.
4. **Assistant answers instantly** (target p50 < 5 s) from official college information, and hands off to a human when unsure.
5. **Paper-free approvals**: every request has a defined workflow, owner, SLA and audit trail.
6. Keep the existing attendance system working, unchanged in behaviour, throughout the migration.

### Non-goals (v1)
- Replacing statutory/government portals (university exam registration, AICTE/NAAC submissions) — we export data for them.
- Full LMS (video lectures, rich course authoring). Only basic course material sharing and assignments.
- Online payment gateway *building* — we integrate a provider (Razorpay/Stripe/PayU).
- Multi-college SaaS tenancy (design allows it later; v1 is a single institution).
- Autonomous decisions on grades, discipline or admissions by the bot. The assistant informs and routes; humans decide.

## 4. Users and roles

Existing roles are kept; new ones added.

| Role | Status | Main jobs |
|---|---|---|
| Super admin / Admin | existing | Configuration, accounts, audit. |
| HOD / Management | existing | Department oversight, approvals, analytics. |
| Teacher | existing | Attendance, marks, leave, student mentoring. |
| Student | existing | Self-service, attendance, results, fees, requests. |
| **Principal / Dean** | new | Institution-wide dashboards, final approvals. |
| **Accounts officer** | new | Fees, receipts, refunds, payroll inputs. |
| **Exam cell** | new | Exam schedule, hall tickets, marks, results. |
| **Librarian** | new | Catalogue, issue/return, fines. |
| **Hostel warden / Transport in-charge** | new | Rooms, mess, passes, routes. |
| **Admissions officer** | new | Applications, merit lists, onboarding. |
| **Support agent** | new | Handles tickets the bot escalates. |
| **Parent / Guardian** | new (read-only) | Attendance, fees, results, notices, meeting requests. |

Permissions are role- and department-scoped (an HOD sees only their department).

## 5. Product principles

1. **Answer first, form second.** The student asks in plain language; the bot answers or starts the request. Forms are the fallback.
2. **Official sources only.** The bot answers from college-approved documents and live system data, never from guesses; it cites the source and its "last updated" date.
3. **A human is always reachable.** Low confidence, sensitive topics, or user request → ticket to the right department with full context so the student never repeats themselves.
4. **Privacy by default.** A student can only see their own data; the bot enforces the same permissions as the app.
5. **Mobile first.** Most students will use phones; works on low bandwidth; installable (PWA).
6. **Everything is audited.** Who changed or approved what, and when.

## 6. Scope by module

Priority: **P0** = launch, **P1** = fast follow, **P2** = later.

### 6.1 Foundation (P0)
- Unified identity (one profile per person; roles can combine, e.g. teacher + HOD).
- SSO-style login, password reset by email/OTP, MFA for admin/finance roles.
- Academic structure: institution → departments → programs → batches → sections → semesters → courses.
- Notifications: in-app, email, SMS/WhatsApp (provider-based), push (PWA).
- Document store (certificates, uploads) and generated PDFs with QR verification.
- Audit log and role-based access control.

### 6.2 Attendance (exists → extend)
- P0: keep all current features (camera auto-attendance, corrections, teacher flags, reports).
- P0: eligibility rules (e.g. < 75 % → exam-ineligible warning), shortage alerts to student, mentor and parent.
- P1: leave/medical/on-duty requests that adjust attendance with approval trail.
- P1: manual/QR fallback when a room has no camera.

### 6.3 Academics (P0)
- Course allocation to teachers, syllabus, lesson plans, timetable (reuse existing timetable import).
- Internal assessments and marks entry with moderation and lock dates.
- Assignments: post, submit, grade (basic).
- Student academic profile: marks, CGPA, backlog tracking.

### 6.4 Examinations (P1)
- Exam schedule, seating, invigilation duty allocation.
- Hall tickets (auto-block ineligible students, with override workflow).
- Result publication, revaluation requests, transcripts/marksheets (PDF with QR).

### 6.5 Fees and accounts (P0)
- Fee structures per program/category, installments, scholarships, concessions.
- Online payment, receipts, dues, fines, refunds, reconciliation reports.
- Reminders (before due, on due, overdue) and parent notifications.

### 6.6 Student services and requests (P0)
A generic **request engine**: type → form → approval chain → output document → notification. Initial catalogue:
bonafide, character certificate, fee receipt copy, no-dues, ID card (new/duplicate), transfer certificate, marksheet copy, name/detail correction, leave, bus pass, hostel change, library clearance, scholarship forms, grievance, event permission.

### 6.7 Library (P1)
Catalogue search, issue/return/renew, reservations, fines, digital resources links, due alerts.

### 6.8 Hostel and transport (P1)
Room allotment, mess menu/attendance, outing/leave passes, complaints; routes, stops, bus pass, vehicle/driver records.

### 6.9 HR and payroll inputs (P1)
Staff profiles, leave balance and approvals, faculty workload, appraisal records. Payroll *inputs* (attendance, leave) exported; salary calculation optional later (P2).

### 6.10 Admissions and onboarding (P2)
Online application, document verification, merit list, seat allotment, fee payment, auto-creation of student account and face-registration invite.

### 6.11 Communication and campus life (P0/P1)
Notice board by audience (all, department, section), circulars with read receipts, events and calendar, clubs, placements/training announcements (P1), feedback surveys on teaching (P1).

### 6.12 Analytics and reporting (P0/P1)
Role-based dashboards: attendance trends, at-risk students (low attendance + low marks + dues), fee collection, request SLAs, bot performance. Exports (CSV/PDF).

## 7. AI Support Assistant ("Campus Assistant")

### 7.1 What it does
| Capability | Examples | Needs login? |
|---|---|---|
| **Answer FAQs** from official documents | "What is the last date for exam form?", "Hostel rules?", "How do I get a bonafide?" | No (public knowledge) |
| **Answer personal questions** from live data | "What's my attendance in DBMS?", "How much fee is pending?", "When is my next class?", "Is my certificate ready?" | Yes |
| **Complete requests** by conversation | "I need a bonafide for a bank loan" → collects fields → confirms → submits → returns tracking ID and later the PDF | Yes |
| **Track status** | "Where is my no-dues request?" | Yes |
| **Guide to a person/place** | "Who is my exam coordinator, where is the office, what are its hours?" | No |
| **Escalate** | Unknown / sensitive / angry user → ticket to the right team with transcript | Both |
| **Proactive nudges** (P1) | Low-attendance warning, fee due reminder, exam hall-ticket ready | Yes |

### 7.2 Channels
Web chat widget inside the app (P0), mobile PWA (P0), WhatsApp (P1), voice/IVR (P2). Languages: English first; Hindi and regional languages (P1) with auto-detect.

### 7.3 Behavioural requirements
1. Answers factual college questions **only from the approved knowledge base** and live data; if not found, says so and offers to create a ticket. Never invents dates, fees, rules.
2. Shows **source and last-updated date** for policy answers.
3. Personal data answers require authenticated session and the same permission checks as the app.
4. **Actions with side effects need explicit confirmation** ("Submit this bonafide request? Yes/No") and are logged.
5. Refuses or escalates: grade changes, disciplinary matters, medical/mental-health crises (shows helpline + notifies counsellor with consent), admissions decisions, anything outside college scope.
6. Handoff to human with context; the agent can reply in the same thread; the student is notified.
7. Students can rate every answer (👍/👎); negative feedback is reviewed weekly and drives knowledge-base fixes.
8. Content is moderated; no personal data of other people ever appears in answers.

### 7.4 Knowledge management
Department staff upload/edit FAQs and documents (PDF, DOCX, web page) with owner, audience, effective date and expiry. Outdated or expiring content is flagged. Changes go live after approval. Gap report: top unanswered questions → "create FAQ" in one click.

### 7.5 Success metrics
| Metric | Target at 6 months |
|---|---|
| Containment (resolved without human) | ≥ 65 % |
| Answer helpfulness (👍 rate) | ≥ 85 % |
| Median first response | < 5 s |
| Escalations handled within SLA | ≥ 90 % |
| Hallucination / wrong-fact reports | < 1 % of answers, each reviewed within 48 h |
| Reduction in physical office visits | ≥ 50 % in first semester, ≥ 70 % by second |

## 8. Key user journeys

1. **Bonafide in 2 minutes.** Student chats "need bonafide" → bot asks purpose → confirms → request created → HOD approves on phone → PDF with QR is delivered in chat + email.
2. **Fee check and pay.** "How much do I owe?" → bot shows breakdown → "Pay now" link → receipt delivered and dues updated instantly.
3. **Shortage warning.** Attendance in a subject drops under threshold → student, mentor, and parent get an alert; student can apply for medical leave from the alert.
4. **Hall ticket.** Eligible students see hall ticket on release; ineligible see the reason and how to request condonation; the request routes to HOD → Principal.
5. **Staff day.** Teacher opens "My Day": today's classes, live attendance, pending approvals, marks due; one-tap approve/reject.
6. **Principal view.** One dashboard: attendance, fee collection, open requests by SLA, bot containment.

## 9. Non-functional requirements (product level)

| Area | Requirement |
|---|---|
| Availability | 99.5 % during academic hours; graceful degradation: attendance engine continues if chatbot/LLM is down. |
| Performance | Pages < 2 s on 4G; chat first token < 3 s; supports peak of ~5,000 concurrent users (fee/result days). |
| Security | RBAC, MFA for privileged roles, encryption in transit and at rest, audit trails, secrets management, regular backups with restore tests. |
| Privacy & compliance | Compliant with India's DPDP Act 2023 (consent, purpose limitation, deletion/retention); biometrics (face embeddings) stored with explicit consent, never exposed; parental consent for minors; FERPA/GDPR-style rights if deployed elsewhere. |
| Accessibility | WCAG 2.1 AA, keyboard and screen-reader friendly. |
| Localisation | Multi-language UI and bot; date/currency formatting. |
| Auditability | Every approval, mark change, fee adjustment and bot action is recorded with actor and timestamp. |
| Data portability | CSV/Excel import for onboarding existing data; full export. |

## 10. Rollout plan

| Phase | Duration | Content | Exit criteria |
|---|---|---|---|
| 0. Foundations | 4–6 wks | Unified identity/RBAC, academic structure, notifications, audit, request engine skeleton, data import tools. | Existing attendance still green; staff data imported. |
| 1. Student self-service + Assistant v1 | 8–10 wks | Notices, fees (view + pay), top 8 certificates/requests, FAQ bot with knowledge base, ticketing + human handoff, personal-data answers (attendance, dues, timetable). | Pilot with 1 department; containment ≥ 50 %. |
| 2. Academics & exams | 8–10 wks | Marks, assignments, exam cell, hall tickets, results, parent portal, alerts. | One full exam cycle run on the system. |
| 3. Campus services | 8 wks | Library, hostel, transport, HR/leave, wider request catalogue, WhatsApp channel. | All departments onboarded. |
| 4. Analytics & optimisation | ongoing | Risk analytics, admissions, regional languages, voice. | Targets in §3 reached. |

Pilot first in one department/year; run old (paper) and new in parallel for one cycle; train staff; appoint a "knowledge owner" per department.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bot gives wrong policy answer | Retrieval from approved sources only, citations, "I don't know" fallback, weekly review, kill-switch per topic. |
| Staff adoption (paper habits) | Workflows that reduce their work first (approvals on phone), training, parallel run, management mandate. |
| Data quality of existing records | Import with validation reports; reconcile before go-live. |
| Privacy / biometric concerns | Consent flow, clear retention policy, DPO contact, minimal data, access logs. |
| Scope creep ("ERP for everything") | Strict phase gates; request engine lets new services be added by config, not code. |
| Single-server architecture limits | TRD defines the path from the current single-process to scalable deployment. |
| LLM cost / outages | Caching, cheaper model for routing, per-user rate limits, fallback to FAQ search and ticket form. |

## 12. Open questions (need your decisions)

1. One college or a **group of colleges** (multi-tenant)? Affects data model now.
2. Hosting: **on-premise server**, or cloud (and which region/budget)?
3. Which existing systems must we integrate with (university portal, accounting software, biometric/RFID gates)?
4. Payment provider and fee complexity (number of fee heads, scholarships).
5. Languages needed at launch.
6. Do parents get logins in phase 1?
7. Approx. numbers: students, staff, concurrent peak — to size infrastructure.
8. Who owns content for the bot in each department?
