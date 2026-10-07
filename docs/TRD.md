# Technical Requirements Document (TRD)
## Smart Campus — Campus ERP + AI Support Assistant

| | |
|---|---|
| Status | Draft v0.1 for review |
| Companion | [PRD.md](PRD.md) · current design: [ARCHITECTURE.md](ARCHITECTURE.md) |

---

## 1. Current state (baseline)

From this repository:

- **Backend**: Python, FastAPI (`backend/api/app.py`, ~60 endpoints, single file), a Flask registration portal, background scheduler in the same process.
- **DB**: MySQL 8 via `mysql-connector-python`, raw SQL, versioned migrations (`backend/database/migrations.py`), ~25 tables (users, timetable, sessions, attendance_log, teacher_flags, …).
- **Frontend**: vanilla JS single-page admin UI (`frontend/admin`), role-based views.
- **Vision**: InsightFace `buffalo_sc` on CPU, local cameras/RTSP, embeddings in MySQL.
- **Auth**: ID/password, signed token (8 h), simple roles (admin, HOD, teacher, student).
- **Constraints**: one scheduler per DB, one process, no queue, no cache, no object storage, no CI deployment, desktop app (`main.py`) also exists.

This is a solid single-college attendance product, but it is not yet structured for 10+ business domains, thousands of concurrent users, or an LLM assistant. This TRD defines the evolution without a risky rewrite.

## 2. Architectural decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Modular monolith first, split later.** One deployable FastAPI backend organised into bounded-context modules; extract to services only where load or team size demands (attendance vision worker, chatbot). | Fastest delivery, one DB transaction scope, easy for a small team; avoids microservice overhead. |
| D2 | **Keep Python/FastAPI.** Do not rewrite. Split `app.py` into routers per module. | Reuses working code and tests. |
| D3 | **Move from MySQL raw SQL to SQLAlchemy 2.0 + Alembic**, staying on MySQL 8 initially; PostgreSQL migration is optional (see §6.4). | ORM/typing and migration discipline are needed at 100+ tables; existing migrations are ported once. |
| D4 | **Vision attendance runs as a separate worker process** (same codebase), communicating through DB + Redis. | Isolates CPU-heavy work from the web API; allows horizontal scaling per building. |
| D5 | **Redis** for cache, rate limiting, sessions blacklist, task queue broker. | Needed for notifications, bot, scheduling. |
| D6 | **Async jobs with a worker queue** (Celery or Arq/RQ) for emails/SMS, PDF generation, imports, report exports, embeddings. | Keep API responsive. |
| D7 | **Object storage (S3-compatible / MinIO on-prem)** for documents, PDFs, uploads. | No files in DB or web server disk. |
| D8 | **Frontend: React + TypeScript + Vite PWA** replacing the vanilla admin UI incrementally (module by module, same REST API). | Component reuse, maintainability, mobile/PWA. Existing UI keeps working until each page is replaced. |
| D9 | **Assistant = RAG + tool use over the same internal APIs, running on a self-hosted open-weight LLM inside the institution's own infrastructure.** No student/staff data, prompts, documents or embeddings leave the institution. A provider interface still exists so the model can be swapped. | Binding requirement: *no data may leave the university*. Grounded answers, same permission model. |
| D10 | **OpenAPI-first contract**; generated TypeScript client. | Frontend/backend decoupling. |
| D11 | **Deployment-agnostic: 12-factor config, Docker images; Docker Compose for a single server, Kubernetes optional.** The same images run on an on-prem server or in a private cloud/VPC chosen by the college; storage and compute are sized per college (§6.2). | Hosting is decided per customer. |
| D12 | **Single-tenant deployment now, tenant-ready by design.** Every business table carries `institution_id` from day one, config and branding are per institution, and no code assumes one college. Launch = one college per deployment (own DB, own storage, own LLM server); a shared multi-tenant "platform" edition is a later phase. | Start with one college, expand to several without a rewrite or migration of the schema. |
| D13 | **Everything vendor-specific is an adapter, chosen by config** (payment gateway, SMS/WhatsApp, email, LLM, embeddings, storage). Nothing is hard-wired until the college decides. Self-hosted/local implementations are the default (SMTP relay, MinIO, local LLM). | Providers are undecided; data-residency rule. |

## 3. Target architecture

```
                         ┌──────────────────────────────┐
 Browser / PWA / WhatsApp│  Reverse proxy (Nginx/Caddy) │ TLS, rate limit, WAF rules
                         └──────────────┬───────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                         FastAPI backend                       │
        │  auth/RBAC │ people │ academics │ attendance │ exams │ fees   │
        │  requests(workflow) │ library │ hostel │ transport │ hr │ cms │
        │  notifications │ reports │ assistant-gateway │ admin         │
        └──────┬─────────────┬───────────────┬──────────────┬──────────┘
               │             │               │              │
        ┌──────▼────┐  ┌─────▼─────┐   ┌─────▼──────┐  ┌────▼───────────┐
        │ MySQL 8   │  │  Redis    │   │ Object     │  │ Vector store   │
        │ (OLTP)    │  │ cache/queue│  │ storage    │  │ (pgvector/     │
        └──────▲────┘  └─────▲─────┘   └────────────┘  │ Qdrant)        │
               │             │                         └────▲───────────┘
   ┌───────────┴──┐   ┌──────┴────────┐   ┌─────────────────┴──────────┐
   │ Attendance   │   │ Job workers   │   │ Assistant service           │
   │ vision worker│   │ (email, PDF,  │   │ (RAG, tools, guardrails)    │
   │ cameras      │   │ imports)      │   │  → LOCAL LLM server         │
   └──────────────┘   └───────────────┘   └─────────────────────────────┘
```

### 3.1 Module boundaries (backend package layout)

```
backend/
  core/            config, security, db session, events, errors, audit
  modules/
    identity/      users, roles, permissions, sessions, MFA
    people/        students, staff, guardians, profiles, face enrolment
    academics/     programs, batches, sections, courses, timetable
    attendance/    (existing engine moved here) sessions, logs, leave
    exams/         schedules, hall tickets, marks, results
    fees/          structures, invoices, payments, receipts, refunds
    requests/      generic workflow engine + document generation
    library/  hostel/  transport/  hr/  admissions/
    comms/         notices, events, notifications
    reports/       dashboards, exports
    assistant/     chat API, retrieval, tools, guardrails, KB admin
    support/       tickets, SLAs, agent console
```

Rules: modules talk through **service interfaces and domain events** (no cross-module table access). Each module owns its tables (prefix per module). A lightweight in-process event bus (`attendance.shortage_detected`, `fee.paid`, `request.approved`) feeds notifications and the assistant.

## 4. Functional technical requirements

### 4.1 Identity, access and security
- **RBAC + scope**: permission = `(resource, action)`; role grants permissions; **scope** limits rows (self, section, department, institution). Enforced in a single dependency (`require(perm, scope)`) and in repository filters, not in each route.
- Replace shared-secret token with **JWT access (15 min) + rotating refresh token (7–30 d)** stored hashed server-side; revocation list in Redis.
- Password hashing: **argon2id**; lockout/backoff (existing 5-attempt rule kept); password reset by email/OTP.
- **MFA (TOTP)** mandatory for admin, accounts, exam cell, principal.
- **OIDC-ready** (Google Workspace/Microsoft Entra SSO) via a pluggable provider.
- Every state-changing call writes an **audit record** (actor, role, action, entity, before/after JSON, IP, request id).
- Security baseline: OWASP ASVS L2, CSRF protection for cookie flows, strict CORS, CSP headers, upload scanning (ClamAV) + type/size limits, SQL via ORM/parameters only, dependency scanning in CI, secrets in env/secret manager, no secrets in repo.
- Biometrics: embeddings encrypted at rest (column-level AES-GCM with key from KMS/env), never returned by APIs, consent record per person, retention + deletion job.

### 4.2 Data model (new core entities; existing tables migrate in place)

Shared: `person`, `user_account`, `role`, `permission`, `role_permission`, `user_role(scope)`, `audit_log`, `notification`, `notification_pref`, `document`, `outbox_event`.

Academic: `institution`, `department`, `program`, `batch`, `section`, `academic_year`, `semester`, `course`, `course_offering` (course × section × teacher × semester), `enrollment`, `timetable_slot` (existing).

Attendance (existing + new): `sessions`, `attendance_log`, `attendance_overrides`, `teacher_flags` … plus `leave_request`, `attendance_summary` (materialised per student × course), `eligibility_rule`.

Exams/marks: `assessment`, `mark`, `exam`, `exam_schedule`, `hall_ticket`, `result`, `revaluation_request`.

Finance: `fee_head`, `fee_structure`, `invoice`, `invoice_line`, `payment`, `receipt`, `concession`, `refund`, `ledger_entry`.

Requests: `request_type` (config: form schema JSON, approval chain, SLA, output template), `request`, `request_step`, `request_comment`, `generated_document`.

Support/assistant: `ticket`, `ticket_message`, `kb_source`, `kb_document`, `kb_chunk` (+ vector), `chat_session`, `chat_message`, `chat_feedback`, `bot_action_log`.

Other modules: `book`, `book_copy`, `loan`, `fine`; `hostel`, `room`, `allotment`, `outpass`; `route`, `stop`, `vehicle`, `bus_pass`; `staff`, `leave_balance`, `leave_application`; `application` (admissions).

Conventions: surrogate `BIGINT`/UUIDv7 keys, `created_at/updated_at/created_by`, soft-delete where legally required, money as `DECIMAL(12,2)` with currency, all timestamps UTC with institution timezone in config.

### 4.3 Request / workflow engine
- Declarative definitions (JSON/YAML in DB): form schema (JSON Schema), steps `[role|dynamic approver, SLA hours, can_reject, can_return]`, output template (HTML→PDF), notifications.
- State machine: `DRAFT → SUBMITTED → IN_REVIEW(step n) → APPROVED | REJECTED | RETURNED → FULFILLED`. Transitions validated server-side; every transition audited.
- Approver resolution: by role+department (e.g. "student's HOD"), delegation when on leave, escalation on SLA breach (reminder → next level).
- Documents: Jinja2 template → PDF (WeasyPrint) → stored in object storage; QR code links to public **verification page** (`/verify/{token}`) showing validity, no personal data beyond name and document type.
- Adding a new service = configuration + template, not code (target: ≤ 1 day).

### 4.4 Attendance module changes
- Move engine into `modules/attendance` unchanged in behaviour (existing tests must stay green, see §10).
- Vision worker: separate entrypoint `python -m worker.vision`; scheduler uses **DB advisory lock / Redis lease** so exactly one active scheduler per room group (removes the "one scheduler per DB" limit).
- Add materialised `attendance_summary` refreshed on session completion → feeds dashboards, shortage alerts, hall-ticket eligibility, assistant answers (no heavy queries at chat time).
- Camera ingestion scaling: one worker per N cameras; GPU optional (onnxruntime-gpu); recognition burst cadence unchanged. Capacity guide: ~1 CPU core per 3–4 concurrent rooms at current cadence (to be benchmarked).
- Alternate capture: QR/OTP code in class and manual marking by teacher, same tables, `source` column.

### 4.5 Fees and payments
- Payment gateway behind an interface (`PaymentProvider`), webhook endpoint with **signature verification and idempotency keys**; payment state: `CREATED → PENDING → SUCCESS | FAILED | REFUNDED`.
- Double-entry-style `ledger_entry` for reconciliation; daily reconciliation job against provider settlement report.
- Never store card data (hosted checkout only). Receipts are immutable documents; corrections via credit/debit notes.

### 4.6 Notifications
- Outbox pattern: domain event → `outbox_event` (same DB transaction) → worker publishes to channels.
- Channels via adapters: email (SMTP/SES), SMS (DLT-registered templates in India), WhatsApp Business API, web push. User preferences + quiet hours; retries with back-off; delivery status stored.

### 4.7 Reporting and analytics
- Operational dashboards read from summary tables and read-replica-friendly queries.
- Scheduled exports (CSV/PDF) via job queue; heavy ad-hoc reports run async and are delivered as downloads.
- Optional later: ClickHouse/BigQuery sink for institution-level analytics; early warning model (attendance + marks + dues) as simple, explainable scoring first.

## 5. AI Support Assistant — technical design

### 5.1 Components
1. **Chat API** (`/api/v1/assistant/chat`, SSE streaming) — authenticates the user, loads role/scope, rate limits, creates/continues a `chat_session`.
2. **Orchestrator** — one LLM call loop with tools (below). Small/cheap model for intent routing and greeting; stronger model for answer generation and multi-step requests.
3. **Retrieval (RAG)** — hybrid search (BM25 + embeddings) over `kb_chunk`, filtered by audience (public / student / staff / department) and effective dates; reranking; top-k with source metadata.
4. **Tools (function calling)** — thin wrappers over *existing service interfaces*, executed with the **caller's identity** (never a super-user):
   | Tool | Type |
   |---|---|
   | `get_my_attendance(course?)`, `get_my_timetable(day)` | read |
   | `get_my_fees()`, `get_fee_payment_link(invoice)` | read |
   | `get_my_results(sem)`, `get_exam_schedule()` | read |
   | `list_request_types()`, `start_request(type, fields)` | write (needs confirmation) |
   | `get_request_status(id)` | read |
   | `search_directory(role/dept)` (office hours, contacts, rooms) | read |
   | `create_ticket(summary, category)` / `handoff_to_agent()` | write |
5. **Guardrails** — input: PII/prompt-injection screening, length limits; output: grounding check (every factual claim about policy must map to a retrieved chunk or tool result, otherwise answer "not found" + offer ticket), blocked-topic policy (grades changes, discipline, medical crises → fixed safe responses + escalation).
6. **Confirmation protocol** — any write tool returns a *pending action* that the UI renders as a confirm card; execution happens only on a signed user confirmation event, not on LLM text.
7. **Knowledge pipeline** — admin uploads → parse (PDF/DOCX/HTML) → clean → chunk (300–500 tokens, heading-aware) → embed → index; versioning, owner, audience, `valid_from/valid_to`; re-index on change; stale-content alerts; unanswered-question report.
8. **Observability** — store prompts/responses (PII-redacted), tool calls, latency, token cost, retrieval hits, feedback; dashboards; sampled human review queue; offline **eval set** (200+ real questions with expected sources) run in CI on prompt/model changes.

### 5.2 Request flow
```
user msg → authN/Z → input guard → router (intent: faq | personal-data | action | smalltalk | escalate)
   → retrieve KB (if faq) / call read tools (if personal) / draft pending action (if action)
   → generate answer with citations → output guard (grounding, PII scope)
   → stream to UI → log + feedback widget
```

### 5.3 Self-hosted models (data never leaves the institution)
- **Policy:** no calls to any external AI API. Network egress from the assistant, vector DB and worker hosts is blocked by default (allow-list only for OS/package mirrors during maintenance); this is verified in the security checklist.
- **Serving:** an inference server (vLLM, or llama.cpp/Ollama for small installs) exposing an OpenAI-compatible endpoint on the internal network. Application code talks only to the `LLMClient` / `Embedder` interfaces.
- **Models (open-weight, commercially usable licences; final choice by benchmark on the college's eval set):**
  - Chat/answering: an instruction-tuned model in the 7–14B class (quantised) for small installs, 30–70B class on larger GPUs; a small 1–3B model for intent routing.
  - Embeddings and reranker: multilingual open models (e.g. bge-m3 / multilingual-e5, bge-reranker).
  - Speech (later, voice channel): Whisper-class models, also local.
- **Hardware tiers** (see §6.2): Tier S can run a 7–8B quantised model on one 16–24 GB GPU (or CPU-only with low concurrency and higher latency); Tier M/L add GPU capacity or more replicas. Capacity is sized from measured tokens/s and the target concurrent chats.
- **Quality trade-off, stated plainly:** smaller local models are weaker at reasoning and non-English languages than frontier hosted models. We compensate with strict retrieval grounding, short fixed answer templates for common cases, deterministic tools for anything numeric (attendance, fees), a larger eval set, and mandatory "not found → ticket" behaviour. The eval harness gates every model or prompt change.
- **Cost/performance control:** per-user rate limits, request queue with back-pressure, semantic cache for public FAQ answers, prompt/KV-cache reuse of the static system prompt and tool schemas, graceful degradation to FAQ search + ticket form if the model server is down or saturated.
- **Data handling:** chat logs, KB, embeddings and eval data stay in the institution's DB/storage; logs are PII-redacted and retained ≤ 90 days; no telemetry leaves the network. Face embeddings and credentials are never given to the LLM.

### 5.4 Languages
Multilingual embeddings + model instruction to answer in the user's language; KB can hold translated variants; quality tested per language in the eval set.

### 5.5 Vector store
Self-hosted only. Use **Qdrant** (single container, on-prem) while the OLTP DB is MySQL; pgvector if the OLTP DB later moves to PostgreSQL. Both run inside the institution's network.

## 6. Platform and infrastructure

### 6.1 Environments
`local` (Docker Compose) → `staging` (prod-like, anonymised data) → `production`. Config via environment variables; per-env secrets in a secret manager (or sealed `.env` on-prem).

### 6.2 Deployment topology and sizing profiles
Hosting is the college's choice and is not fixed in advance: **on-prem server(s), a private cloud/VPC, or a hybrid** (e.g. cameras and vision worker on campus, the rest in a private cloud). Because no data may leave the university, any cloud used must be one the institution controls and that satisfies its data-residency rule; the assistant's LLM server always runs inside that boundary. Storage (DB, object storage, backups) is provisioned by demand and grows with usage.

Sizing is **profile-based and flexible**, driven by the college's numbers (students, staff, rooms/cameras, peak concurrency). Starting points, to be validated by load tests:

| Component | Tier S (≈ ≤ 2k users, ≤ 20 rooms) | Tier M (≈ 2–10k users, ≤ 100 rooms) | Tier L (≈ 10k+ users / multi-campus) |
|---|---|---|---|
| API | 1–2 × (2 vCPU, 4 GB) | 2–4 × (4 vCPU, 8 GB) | autoscaled pool |
| Workers (jobs) | 1 × (2 vCPU, 4 GB) | 2 × (4 vCPU, 8 GB) | autoscaled pool |
| Vision workers | 1 × (4 vCPU, 8 GB) per ~10–12 rooms; GPU optional | add workers per building | per-campus edge nodes |
| MySQL 8 | 1 node, 4 vCPU, 16 GB, SSD | primary + replica | primary + replicas / managed cluster |
| Redis | 1–2 GB | 4 GB | HA pair |
| Object storage | MinIO, expandable volume | MinIO distributed | S3-compatible cluster |
| Vector DB | shared host | dedicated 2 vCPU, 8 GB | dedicated cluster |
| LLM server | 1 GPU (16–24 GB) or CPU-only low concurrency | 1–2 GPUs (24–48 GB) | multiple GPUs / multiple replicas |

Scaling rules: API and workers scale horizontally; storage grows by volume expansion; DB is sized up first; GPU/LLM capacity is added when chat p95 latency or queue depth crosses its threshold. A **capacity calculator sheet** (students × requests/day, rooms × cameras, chat sessions × tokens) is part of the onboarding kit so each college is quoted from its own numbers.

### 6.3 Reliability
- Backups: nightly full + binlog PITR, 30-day retention, **monthly restore drill**; object storage versioning; encrypted offsite copy.
- Health endpoints, readiness (extend the existing **System Readiness** page to all modules), graceful shutdown, idempotent jobs.
- SLOs: API p95 < 500 ms (non-report), availability 99.5 % in academic hours; RPO ≤ 15 min, RTO ≤ 2 h.
- Peak events (fee deadline, result day): rate limits, queue-based processing, cached read models, pre-scaling playbook.

### 6.4 Database path
Stay on MySQL 8 in phases 0–2 (no forced migration); the schema is tenant-ready (`institution_id`, D12). Decide on PostgreSQL at the end of phase 1 using criteria: need for pgvector/full-text/JSONB-heavy workflows vs. operational familiarity. Using SQLAlchemy + Alembic keeps this switch cheap.

### 6.5 Observability
Structured JSON logs (existing logger extended) with `request_id`, `user_id`, `module`; metrics (Prometheus) and dashboards (Grafana); tracing (OpenTelemetry); error tracking (Sentry); alerting on SLO burn, queue depth, camera offline, payment webhook failures, bot error/negative-feedback spikes.

### 6.6 CI/CD
GitHub Actions: lint (ruff, eslint), type-check (mypy/pyright, tsc), unit + integration tests (MySQL service container), frontend tests (existing jsdom/axe), migration check (upgrade from previous release + downgrade dry-run), container build, SAST/dependency/secret scan, deploy to staging on merge, manual gate to production, DB migration job before rollout (expand/contract pattern, no breaking migrations in one step).

## 7. API standards
- REST + JSON under `/api/v1`, OpenAPI published, consistent error model `{code, message, details, request_id}`.
- Pagination (`limit`, cursor), filtering, sorting conventions; idempotency keys for payment and request creation.
- Versioning: additive changes in `v1`; breaking changes → `v2`.
- File upload via pre-signed URLs; webhooks signed (HMAC) with retry.
- Rate limits per user/IP/route class (stricter for login, OTP, chat).

## 8. Frontend requirements
- React + TypeScript, router per module, generated API client, design system (accessible components, dark/light, responsive), i18n framework (ICU messages).
- PWA: installable, offline shell, push notifications.
- Role-aware navigation built from permissions returned by `/me`.
- Chat widget: streaming, citations, confirm cards, file attach, feedback, handoff status, keyboard/screen-reader accessible.
- Migration strategy: new React shell hosts existing vanilla pages in an iframe/route fallback; replace page by page; no big-bang.

## 9. Data migration and integration
- Import tools (CSV/Excel templates + validation report + dry run) for students, staff, courses, fee structures, historic marks/attendance.
- Idempotent import jobs keyed by external IDs; rollback by batch id.
- Integration adapters (each behind an interface, mocked in tests): payment gateway, SMS/WhatsApp, email, SSO/OIDC, optional university portal export, accounting export (Tally CSV), RFID/gate turnstile events (webhook).
- Existing data: current tables are migrated forward by Alembic revisions; **no data deletion**; face embeddings re-encrypted in a one-time job.

## 10. Testing and quality
| Layer | Approach |
|---|---|
| Unit | pytest; domain rules (eligibility, fee calculation, workflow transitions) with property tests where useful. |
| Integration | Real MySQL (existing approach), Redis; API tests per module; migration tests. |
| Contract | OpenAPI schema diff in CI; generated client type-checks. |
| E2E | Playwright for key journeys (§8 of PRD); existing `tests/test_e2e_classroom.py` retained. |
| Security | SAST, dependency audit, authZ matrix tests (every endpoint × role must have an explicit expectation), periodic pen-test before go-live. |
| Load | k6/Locust: 5,000 concurrent users on fee/result paths; chat load test with mocked LLM and with real provider quotas. |
| AI eval | Golden question set, groundedness/citation checks, refusal tests, prompt-injection suite, regression gate on model/prompt changes. |
| Vision | Offline evaluation set with consented images per lighting/angle; FAR/FRR tracked; pilot room before rollout. |
| Accessibility | axe checks in CI + manual screen-reader pass. |
Coverage target: ≥ 80 % lines on domain/services, 100 % of endpoints in authZ matrix.

## 11. Privacy, compliance, governance
- DPDP Act 2023: consent capture/versioning, purpose tags per data category, data-subject requests (access, correction, erasure) via the request engine, retention schedules and purge jobs, breach-notification runbook, DPO contact.
- Biometric data: explicit opt-in, opt-out path with alternative attendance method, separate encryption key, access-logged.
- Minors/parents: guardian consent and guardian-scoped views.
- Audit logs immutable (append-only, hash-chained optional) and retained ≥ 3 years; access reviews each semester.
- LLM governance: all inference on-premise (no third-party processor for AI), egress blocked and tested, data-flow diagram, prompt/response retention ≤ 90 days with redaction, human review of flagged conversations.
- Payment and SMS/WhatsApp are the only integrations that necessarily send limited data out (payment hosted checkout, message text); they are optional adapters, minimised (no academic data in messages), and each needs an explicit institutional approval before enabling.

## 12. Delivery plan (engineering view)

| Phase | Weeks | Technical deliverables |
|---|---|---|
| **0 Foundation** | 4–6 | Repo restructure to modules; routers split from `app.py`; SQLAlchemy + Alembic baseline of existing schema; Docker Compose (api, worker, mysql, redis, minio); CI pipeline; RBAC/scope + audit; JWT/refresh + MFA; notification outbox; object storage; React shell + design system; attendance moved into module with all current tests green. |
| **1 Self-service + Assistant v1** | 8–10 | Request engine + PDF/QR docs; 8 request types; fees view/pay + webhooks; notices; KB admin + ingestion; chat API (RAG + read tools + confirm-write for requests) ; ticketing + agent console; eval harness; student PWA. |
| **2 Academics & exams** | 8–10 | Marks, assessments, exam cell, hall tickets with eligibility, results, revaluation, parent portal, attendance leave + shortage alerts, analytics v1. |
| **3 Campus services** | 8 | Library, hostel, transport, HR leave, WhatsApp channel, SMS, more request types, proactive nudges. |
| **4 Scale & intelligence** | ongoing | Read replica/reporting store, risk scoring, admissions, multilingual, voice, optional PostgreSQL move, HA tuning. |
| **5 Multi-college platform** | after 1st college is stable | Tenant provisioning, per-tenant branding/config, super-tenant console, billing/licensing, isolation tests (cross-tenant access must be impossible), upgrade/rollout tooling across tenants. |

Suggested team: 1 tech lead, 2 backend, 2 frontend, 1 ML/AI engineer, 1 DevOps (part-time), 1 QA, 1 product/UX, plus department "knowledge owners". Can be run by a smaller team over a longer timeline (pilot-first).

## 13. Key risks (technical)

| Risk | Mitigation |
|---|---|
| Big refactor breaks working attendance | Strangler approach; keep all existing tests as the safety net; ship module moves with no behaviour change first. |
| Single-process assumptions in current code (scheduler, in-memory buffers) | Introduce leases/queues in phase 0; test failover. |
| LLM hallucination / prompt injection from uploaded docs | Grounding checks, tool permissions bound to user, treat KB text as data, eval suite, human-review queue. |
| Payment edge cases (double debit, webhook loss) | Idempotency, reconciliation job, manual-resolve console. |
| Data volume (recognition events) | Partitioning/archival of `recognition_events`/`scan_logs`, retention policy, summary tables. |
| Vendor lock-in (SMS, payment, storage) | Adapters, config-driven selection (D13); providers not yet chosen. |
| Local LLM quality below hosted frontier models | Strong grounding + deterministic tools, narrower bot scope at launch, larger eval set, model benchmarking per release, option to enlarge GPU tier. |
| GPU cost/availability for small colleges | CPU-only/quantised small-model profile with FAQ-first behaviour; scale GPUs only if demand justifies. |
| Underestimated ops load on small team | Managed services where allowed, runbooks, automated backups/restores, alerting from day one. |

## 14. Decisions (recorded) and what is still open

| # | Question | Decision | Consequence |
|---|---|---|---|
| 1 | One college or several? | **Start with one; later several, launched as a full product/startup.** | Tenant-ready schema and config from day one (D12); one deployment per college at launch; a hosted multi-tenant platform edition is phase 5. |
| 2 | Hosting | **Flexible: cloud or on-prem/storage-based, per demand.** | Deployment-agnostic containers (D11); sizing profiles (§6.2). |
| 3 | Student data to hosted AI? | **No. No data may leave the university.** | Self-hosted LLM, embeddings and vector DB (D9, §5.3); egress blocked. |
| 4 | Payment / SMS providers | **Undecided.** | Adapter interfaces (D13); a sandbox/mock provider ships first; decide before phase 1 fee go-live. |
| 5 | Scale (students, staff, rooms, cameras) | **Flexible, per college.** | Tiered profiles and capacity calculator (§6.2). |

**Still open (not blocking phase 0):**
1. Which GPU budget the first college can provide for the assistant (determines model size and languages at launch).
2. Languages required at launch.
3. Payment gateway and SMS/WhatsApp provider (needed before phase 1 fee go-live).
4. Team size and timeline.
5. Module priority after phase 1.
6. Licensing/commercial model for the later multi-college product (affects tenant isolation level: shared DB with `institution_id` vs. database-per-tenant).
