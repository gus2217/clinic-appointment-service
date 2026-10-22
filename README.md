# Clinic Booking API

A booking service for a clinic with five doctors and 30-minute slots. Patients see which slots are free, book one, and can cancel or reschedule. **A booked slot is never available to anyone else.** This is my submission for the Savannah Informatics backend assessment.

**Reviewer summary:** I treat this as the booking microservice of a larger clinic platform. It owns appointments and their state transitions, and nothing else. Doctors and patients live in other services, reached through a port. The core guarantee (no double booking) is enforced by **GiST exclusion constraints** in PostgreSQL, not by application checks. Start at [/docs](https://github.com/gus2217/clinic-appointment-service/blob/main/docs/SECTION1_SYSTEM_DESIGN.md).

**Stack:** Python 3.12, FastAPI (async), SQLAlchemy 2 (async), psycopg 3, Alembic, Pydantic v2, PostgreSQL 16, Docker, GitHub Actions, Render, Neon.

|                                        |                                                      |
| -------------------------------------- | ---------------------------------------------------- |
| Live app                               | https://clinic-appointment-service.onrender.com      |
| Interactive API docs                   | https://clinic-appointment-service.onrender.com/docs |
| AI reflection (Section 4 of the brief) | [AI_REFLECTION.md](AI_REFLECTION.md)                 |
| Deploy branch                          | `main`                                               |

**Quick try** in `/docs`: `GET /doctors/6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14/availability?date=<a future weekday>`, then `POST /appointments` with one of the returned slots.

### Test-data UUIDs

Every ID is a UUID. Doctors and patients are owned by other services, so this service only stores their ids. These are **fixed values** in `app/directories/fixtures.py`, so they are stable across runs and can be pasted into `/docs`. No database seeding is required.

**Doctors** — all work Monday to Friday, 09:00 - 17:00 Africa/Nairobi (EAT).

| Doctor       | UUID                                   |
| ------------ | -------------------------------------- |
| Dr. Achieng  | `6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14` |
| Dr. Bett     | `891c1683-7204-44c7-a68d-9c1ca6a31814` |
| Dr. Chege    | `efea9b80-d2f4-4a3b-906f-f4919fc59784` |
| Dr. Odhiambo | `1b761fe4-fbcd-476d-8c10-2eeb7d905680` |
| Dr. Wanjiku  | `cd955052-2fe8-4db1-b382-e5638beb9ab9` |

**Patients**

| Patient       | UUID                                   |
| ------------- | -------------------------------------- |
| Patient One   | `1b6c25f0-45c6-426a-a3dd-33ab4e226e9d` |
| Patient Two   | `3e80c513-3391-40c4-aae9-802bb0ec7e3f` |
| Patient Three | `20922d98-a9b4-41fc-9c4b-07d01593d990` |

Times without an offset are read as EAT (see [2.7](#27-time-zones)).

## Contents

1. [Repository structure](#1-repository-structure)
2. [System design](#2-system-design)
3. [API reference](#3-api-reference)
4. [Concurrency and atomicity](#4-concurrency-and-atomicity)
5. [Validation and errors](#5-validation-and-errors)
6. [Testing](#6-testing)
7. [Performance](#7-performance)
8. [Quality gates](#8-quality-gates)
9. [Deployment and CI/CD](#9-deployment-and-cicd)
10. [Trade-offs](#10-trade-offs)
11. [Growth path](#11-growth-path)
12. [Run locally](#12-run-locally)
13. [Try the deployed API](#13-try-the-deployed-api)
14. [AI reflection](#14-ai-reflection)

---

## 1. Repository structure

```
.
├── .github/
│   └── workflows/                 - CI: test on PR; test + deploy on merge to main
├── .vscode/                       - launch configuration for F5 debugging
├── alembic/
│   ├── env.py                     - migration environment
│   └── versions/                  - migrations 0001-0006
├── app/
│   ├── api/                       - routes, schemas, dependencies, error envelope, idempotency guard
│   ├── core/                      - settings
│   ├── db/                        - async engine + session factory
│   ├── directories/               - doctor/patient ports, fixture and HTTP adapters, TTL cache
│   ├── models/                    - appointment, appointment_events, idempotency_keys
│   ├── repositories/              - all SQL, constraint translation, idempotency rows
│   ├── services/                  - booking, availability, appointment lifecycle
│   └── main.py                    - app factory, request-id middleware, error handlers
├── docs/
│   └── SECTION1_SYSTEM_DESIGN.md  - the detailed design document
├── scripts/
│   └── perf/                      - explain_availability, load_book, seed_bulk
├── tests/                         - API, timezone, idempotency, concurrency, directory boundary
├── .env.example
├── .gitignore
├── AI_REFLECTION.md
├── DECISIONS.md
├── Dockerfile
├── Makefile
├── README.md
├── alembic.ini
├── docker-compose.yml
├── docker-entrypoint.sh
├── pyproject.toml
├── render.yaml
└── requirements.txt
```

Tracked files only — `.env`, `.venv/` and tool caches are local and intentionally
absent. The detailed design lives in
[`docs/SECTION1_SYSTEM_DESIGN.md`](docs/SECTION1_SYSTEM_DESIGN.md).

Routes own HTTP and status mapping. Services own validation and the transaction. Repositories are the only place SQL lives. Models are split by aggregate, not by table, so a change to scheduling never touches doctor data.

---

## 2. System design

The detailed design document — assumptions, invariants, the concurrency proof,
failure modes and deployment — is [`docs/SECTION1_SYSTEM_DESIGN.md`](docs/SECTION1_SYSTEM_DESIGN.md). This section is the short version.

### 2.1 Assumptions (the brief is silent or ambiguous, so these are my decisions)

| Topic                | Decision                                                                                                                                                                  | Why                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Scope                | A **booking microservice** inside a larger clinic platform. It owns appointments and their state transitions, and nothing else                                            | A single-domain service can be plugged into others later                                         |
| Doctors and patients | Owned by separate directory services, reached through a port (`fixture` for demo and tests, `http` in production). No foreign keys across that boundary and no user table | See [2.6](#26-doctor-and-patient-data)                                                           |
| Timezone             | Clinic-local wall-clock hours (Africa/Nairobi), UTC storage. Responses return `+03:00` timestamps plus a `timezone` field. Naive input is read as clinic-local            | One representation, converted once at the API boundary                                           |
| Slot length          | Exactly 30 minutes, aligned to the half hour, fitting entirely inside working hours                                                                                       | Keeps the grid simple and availability exact                                                     |
| Identity             | Trusted `X-Actor-Id` / `X-Actor-Role` headers injected by the gateway. Enforcement is behind `CLINIC_AUTH_REQUIRED` (default `false`)                                     | Keeps the required endpoints callable by the evaluator; turning it on yields 401 without headers |
| Cancellation         | Terminal and requires a reason; cancelling twice is 409                                                                                                                   | Keeps the history truthful                                                                       |
| Reschedule           | Moves the existing appointment in place, atomically; only from active                                                                                                     | Atomicity is free and the appointment keeps its id                                               |
| Idempotency          | `Idempotency-Key` on every mutating endpoint, scoped to `(actor, endpoint, key)`                                                                                          | Safe retries after a timeout                                                                     |
| Availability         | Computed on every call, not materialised                                                                                                                                  | No stale rows, no nightly job                                                                    |

### 2.2 Data model

Three tables, all with UUID primary keys. Doctors and patients are external, so
`appointment` stores only their ids. Migration 0005 dropped the doctor and patient
tables.

| Table                | Columns (exactly as in the model)                                                                                                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `appointment`        | `id`, `doctor_id`, `patient_id`, `starts_at`, `ends_at`, `duration_minutes`, `status`, `cancel_reason`, `cancelled_by`, `hold_expires_at`, `created_at`, `updated_at`      |
| `appointment_events` | `id`, `appointment_id` (FK → `appointment.id`, `ON DELETE CASCADE`), `event_type`, `actor_id`, `actor_role`, `from_status`, `to_status`, `reason`, `payload`, `created_at` |
| `idempotency_keys`   | `id`, `actor_id`, `endpoint`, `key`, `request_hash`, `state`, `status_code`, `response_body`, `created_at`                                                                 |

`state` is `in_progress` while a request is running and `completed` once its
response is stored; the response columns are null until then.

**Constraints on `appointment`:**

| Constraint              | Meaning                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------- |
| `ck_status`             | `status` is one of `pending`, `booked`, `cancelled`, `completed`, `no_show`, `rejected`, `expired` |
| `ck_duration`           | `ends_at = starts_at + interval '30 minutes'`                                                      |
| `ck_cancel_consistent`  | `status = 'cancelled'` ⇔ `cancel_reason IS NOT NULL`                                               |
| `ck_hold`               | `status = 'pending'` ⇔ `hold_expires_at IS NOT NULL`                                               |
| `ex_doctor_no_overlap`  | `EXCLUDE USING gist (doctor_id WITH =, tstzrange(starts_at, ends_at) WITH &&)` on active rows      |
| `ex_patient_no_overlap` | the same exclusion on `patient_id`                                                                 |

**Indexes:** `ix_appt_doctor_day (doctor_id, starts_at)` and
`ix_appt_patient (patient_id, starts_at)`, both partial on
`status IN ('pending','booked')`; `ix_events_appointment (appointment_id, created_at)`.

Half-hour alignment is a service-layer rule (`SLOT_MISALIGNED`), not a database
constraint, because it depends on clinic-local time.

The exclusion constraint states the requirement directly: overlap, not equality. A unique
index on the start time would not catch two different start times that overlap.

### 2.3 Components

```
Client → API routes → Services → Repositories → PostgreSQL
                         │
                         ├─ Directories (fixture now, HTTP clients later)
                         └─ Injected clock + timezone helper
```

| Layer           | Responsibility                                                                      |
| --------------- | ----------------------------------------------------------------------------------- |
| `api/`          | Routes, schemas, dependencies, error envelope. HTTP parsing and status mapping only |
| `services/`     | Validation, lifecycle rules, the transaction                                        |
| `repositories/` | The only place SQL lives; constraint-name translation; idempotency storage          |
| `directories/`  | Doctor and patient lookup ports                                                     |
| `models/`       | SQLAlchemy models, one module per aggregate                                         |
| `core/`, `db/`  | Settings; async engine and session factory                                          |

Time is injected, so rules like "in the past" and "lead time" are testable without sleeping.

### 2.4 Appointment lifecycle

Statuses: `pending`, `booked`, `cancelled`, `completed`, `no_show`, `rejected`, `expired`.

| From                 | Action                        | To          | Allowed by    |
| -------------------- | ----------------------------- | ----------- | ------------- |
| none                 | book                          | `booked`    | patient       |
| none                 | book with `requires_approval` | `pending`   | patient       |
| `pending`            | approve                       | `booked`    | doctor, admin |
| `pending`            | reject                        | `rejected`  | doctor, admin |
| `pending` / `booked` | cancel                        | `cancelled` | owner, admin  |
| `booked`             | complete                      | `completed` | doctor, admin |
| `booked`             | no-show                       | `no_show`   | doctor, admin |
| `pending`            | hold expires                  | `expired`   | system        |

- **Booking defaults to `booked`.** `requires_approval: true` creates a pending hold instead.
- **A pending hold blocks the slot for 24 hours** (`hold_expires_at`). Expired holds are swept on the next booking or availability call and become `expired`.
- **Auth is off by default.** Turning it on enforces the role matrix above and returns 401 without `X-Actor-Id` / `X-Actor-Role`.
- **Every transition writes one `appointment_events` row in the same transaction**, so the audit trail cannot disagree with the appointment state.

### 2.5 Slots are computed, not stored

Availability is computed on every call from the doctor's working hours and their active appointments, so there is nothing to keep in sync and no nightly job. Computed slots are never cached.

### 2.6 Doctor and patient data

This service does not assume it owns doctors or patients. Both are read through `app/directories`, a port with two adapters.

| Mode                | Behaviour                                                   |
| ------------------- | ----------------------------------------------------------- |
| `fixture` (default) | In-memory demo data with fixed ids                          |
| `http`              | Calls the external services. Boot fails if a URL is missing |

```text
CLINIC_DIRECTORY_MODE=fixture|http
CLINIC_DOCTORS_URL=https://doctors.internal
CLINIC_PATIENTS_URL=https://patients.internal
```

**Contract:**

```text
GET {doctors_url}/doctors/{id}
    -> {"id","name","is_active","slot_minutes",
        "working_hours":[{"weekday","start_time","end_time"}],
        "overrides":[{"date","intervals"}]}
GET {doctors_url}/doctors/{id}/time-off?from=&to=
    -> [{"starts_at","ends_at"}]
GET {patients_url}/patients/{id}
    -> {"id","name"}
404 = unknown
```

**Boundary policy:**

| Data              | Cache                 | On outage             |
| ----------------- | --------------------- | --------------------- |
| Doctor profile    | 10 min                | Serve cache, else 503 |
| Time-off (reads)  | 60 s                  | Serve cache, else 503 |
| Time-off (writes) | Bypass (`fresh=True`) | 503                   |
| Computed slots    | Never                 | none                  |

Failures are typed: 404 stays 404; 5xx, timeout and malformed payloads become 503 `DIRECTORY_UNAVAILABLE` with `Retry-After`. The service refuses to answer rather than offer a slot it cannot verify.

The caches are process-local and shared across requests (keyed by directory URL and doctor/date). With more than one worker each holds its own copy, so either accept one upstream fetch per worker per TTL or introduce a shared cache.

### 2.7 Time zones

| Rule                    | Detail                                                                     |
| ----------------------- | -------------------------------------------------------------------------- |
| Clinic zone             | Africa/Nairobi (EAT, UTC+3, no DST)                                        |
| Storage                 | UTC                                                                        |
| Input with an offset    | Converted to UTC                                                           |
| Input without an offset | Read as clinic-local, so `"2026-10-26T10:00:00"` means 10:00 at the clinic |
| Working hours           | Clinic-local times, judged in clinic-local time                            |
| Responses               | Clinic-local with an explicit `+03:00` offset, plus a `timezone` field     |

Because times are stored in UTC and conversion happens once at the API boundary, several clinics with their own zones would not need any stored data migrated.

---

## 3. API reference

Interactive docs: `/docs`. Every `{id}` in a path is a UUID. Errors share one envelope: `code`, `detail`, `request_id`.

### 3.1 Endpoints

| Method | Path                                         | Body                                                                 | Success                                 | Errors                                                                                                       |
| ------ | -------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| POST   | `/appointments`                              | `doctor_id`, `patient_id`, `starts_at`, optional `requires_approval` | 201                                     | 404; 409 `SLOT_TAKEN`; 422 `SLOT_IN_PAST`, `LEAD_TIME_VIOLATION`, `OUTSIDE_WORKING_HOURS`, `SLOT_MISALIGNED` |
| GET    | `/appointments/{id}`                         | none                                                                 | 200                                     | 404                                                                                                          |
| GET    | `/appointments/{id}/events`                  | none                                                                 | 200                                     | 404                                                                                                          |
| PATCH  | `/appointments/{id}/cancel`                  | `reason`                                                             | 200                                     | 404; 409 `APPOINTMENT_ALREADY_CANCELLED`                                                                     |
| PATCH  | `/appointments/{id}/reschedule`              | `starts_at`                                                          | 200                                     | 404; 409 `SLOT_TAKEN`, `APPOINTMENT_NOT_ACTIVE`; 422                                                         |
| GET    | `/doctors/{id}/availability?date=YYYY-MM-DD` | none                                                                 | 200                                     | 400; 404                                                                                                     |
| GET    | `/patients/{id}/appointments?limit=`         | none                                                                 | 200, keyset-paginated, upcoming, sorted | 404                                                                                                          |
| PATCH  | `/appointments/{id}/approve`                 | none                                                                 | 200                                     | see note                                                                                                     |
| PATCH  | `/appointments/{id}/reject`                  | none                                                                 | 200                                     | see note                                                                                                     |
| PATCH  | `/appointments/{id}/complete`                | none                                                                 | 200                                     | see note                                                                                                     |
| PATCH  | `/appointments/{id}/no-show`                 | none                                                                 | 200                                     | see note                                                                                                     |
| GET    | `/health`, `/ready`, `/`, `/version`         | none                                                                 | 200                                     | none                                                                                                         |

> **Note:** the lifecycle transition paths follow the cancel/reschedule pattern; confirm them against `/docs`.

**Status code meaning:** 404 not found, 422 a rule violation, 409 a valid request that conflicts with current state, 401 missing actor headers (only when `CLINIC_AUTH_REQUIRED=true`), 503 a directory service is unavailable.

### 3.2 Headers

| Header                           | Used on                   | Purpose                                   |
| -------------------------------- | ------------------------- | ----------------------------------------- |
| `Content-Type: application/json` | every request with a body | Required                                  |
| `Idempotency-Key`                | every mutating endpoint   | Safe retries (see [3.4](#34-idempotency)) |
| `X-Actor-Id`                     | all, when auth is on      | Caller identity injected by the gateway   |
| `X-Actor-Role`                   | all, when auth is on      | `patient`, `doctor` or `admin`            |

### 3.3 Test request bodies

Replace `2026-10-26` with a future weekday. Times without an offset are read as EAT.

**POST /appointments**

| Case                                         | Request body                                                                                                                                                                       | Expected                                           |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Book a slot                                  | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T10:00:00+03:00"}`                            | 201, status `booked`                               |
| Book with no offset (read as EAT)            | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T10:30:00"}`                                  | 201                                                |
| Book needing approval                        | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "3e80c513-3391-40c4-aae9-802bb0ec7e3f", "starts_at": "2026-10-26T14:00:00+03:00", "requires_approval": true}` | 201, status `pending`, holds the slot for 24 hours |
| Same slot again                              | the first body, sent twice                                                                                                                                                         | 409 `SLOT_TAKEN`                                   |
| Same patient, overlapping time, other doctor | `{"doctor_id": "891c1683-7204-44c7-a68d-9c1ca6a31814", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T10:00:00+03:00"}`                            | 409 (patient exclusion constraint)                 |
| Slot in the past                             | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2020-01-06T10:00:00+03:00"}`                            | 422 `SLOT_IN_PAST`                                 |
| Inside the lead time                         | start time less than the lead time from now                                                                                                                                        | 422 `LEAD_TIME_VIOLATION`                          |
| Not on the half hour                         | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T10:15:00+03:00"}`                            | 422 `SLOT_MISALIGNED`                              |
| Before opening                               | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T07:30:00+03:00"}`                            | 422 `OUTSIDE_WORKING_HOURS`                        |
| Last slot overruns closing                   | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T17:00:00+03:00"}`                            | 422 `OUTSIDE_WORKING_HOURS`                        |
| Weekend                                      | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-31T10:00:00+03:00"}`                            | 422 `OUTSIDE_WORKING_HOURS`                        |
| Unknown doctor                               | `{"doctor_id": "00000000-0000-4000-8000-000000000999", "patient_id": "1b6c25f0-45c6-426a-a3dd-33ab4e226e9d", "starts_at": "2026-10-26T11:00:00+03:00"}`                            | 404 `NOT_FOUND`                                    |
| Unknown patient                              | `{"doctor_id": "6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14", "patient_id": "00000000-0000-4000-8000-000000000999", "starts_at": "2026-10-26T11:00:00+03:00"}`                            | 404 `NOT_FOUND`                                    |

**GET /appointments/{id}**

| Case                 | Request                                                  | Expected        |
| -------------------- | -------------------------------------------------------- | --------------- |
| Existing appointment | `GET /appointments/<id from the booking response>`       | 200             |
| Unknown id           | `GET /appointments/00000000-0000-4000-8000-000000000999` | 404 `NOT_FOUND` |

**GET /appointments/{id}/events**

| Case        | Request                         | Expected                    |
| ----------- | ------------------------------- | --------------------------- |
| Audit trail | `GET /appointments/<id>/events` | 200, one row per transition |

**PATCH /appointments/{id}/cancel**

| Case                | Request body                                         | Expected                                    |
| ------------------- | ---------------------------------------------------- | ------------------------------------------- |
| Cancel              | `{"reason": "patient unwell"}`                       | 200, status `cancelled`, slot is free again |
| Cancel again        | `{"reason": "again"}`                                | 409 `APPOINTMENT_ALREADY_CANCELLED`         |
| Missing reason      | `{}`                                                 | 422                                         |
| Unknown appointment | `{"reason": "patient unwell"}` against an unknown id | 404 `NOT_FOUND`                             |

**PATCH /appointments/{id}/reschedule**

| Case                          | Request body                                                               | Expected                                     |
| ----------------------------- | -------------------------------------------------------------------------- | -------------------------------------------- |
| Move to a free slot           | `{"starts_at": "2026-10-26T11:00:00+03:00"}`                               | 200, same id, old slot freed                 |
| Move onto a taken slot        | `{"starts_at": "2026-10-26T10:00:00+03:00"}` (held by another appointment) | 409 `SLOT_TAKEN`, original booking untouched |
| Appointment already cancelled | `{"starts_at": "2026-10-26T12:00:00+03:00"}`                               | 409 `APPOINTMENT_NOT_ACTIVE`                 |
| Misaligned time               | `{"starts_at": "2026-10-26T12:10:00+03:00"}`                               | 422 `SLOT_MISALIGNED`                        |
| Outside hours                 | `{"starts_at": "2026-10-26T20:00:00+03:00"}`                               | 422 `OUTSIDE_WORKING_HOURS`                  |

**GET /doctors/{id}/availability**

| Case                    | Request                                                                          | Expected                                                |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Free slots on a weekday | `GET /doctors/6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14/availability?date=2026-10-26` | 200, slots with `+03:00` offsets and a `timezone` field |
| Weekend                 | `GET /doctors/6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14/availability?date=2026-10-31` | 200, empty list                                         |
| Bad date                | `GET /doctors/6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14/availability?date=26-10-2026` | 400                                                     |
| Unknown doctor          | `GET /doctors/00000000-0000-4000-8000-000000000999/availability?date=2026-10-26` | 404 `NOT_FOUND`                                         |

**GET /patients/{id}/appointments**

| Case            | Request                                                                    | Expected                       |
| --------------- | -------------------------------------------------------------------------- | ------------------------------ |
| First page      | `GET /patients/1b6c25f0-45c6-426a-a3dd-33ab4e226e9d/appointments?limit=20` | 200, upcoming, sorted by start |
| Unknown patient | `GET /patients/00000000-0000-4000-8000-000000000999/appointments`          | 404 `NOT_FOUND`                |

**Lifecycle transitions** (headers when auth is on: `X-Actor-Id: <doctor id>`, `X-Actor-Role: doctor`)

| Case                          | Request                             | Actor         | Expected                |
| ----------------------------- | ----------------------------------- | ------------- | ----------------------- |
| Approve a pending hold        | `PATCH /appointments/{id}/approve`  | doctor, admin | 200, status `booked`    |
| Reject a pending hold         | `PATCH /appointments/{id}/reject`   | doctor, admin | 200, status `rejected`  |
| Complete a booked appointment | `PATCH /appointments/{id}/complete` | doctor, admin | 200, status `completed` |
| Mark a no-show                | `PATCH /appointments/{id}/no-show`  | doctor, admin | 200, status `no_show`   |

**Health and meta**

| Request        | Expected                           |
| -------------- | ---------------------------------- |
| `GET /health`  | 200                                |
| `GET /ready`   | 200 when the database is reachable |
| `GET /`        | 200, short JSON message            |
| `GET /version` | 200, deployed commit SHA           |

### 3.4 Idempotency

All mutating endpoints accept `Idempotency-Key`, scoped to `(actor, endpoint, key)`. The key row and the write commit in one transaction.

| Case                                   | Request                                                                                                | Expected                                                                |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| First call                             | `POST /appointments` with `Idempotency-Key: 7c2f1a90-5d4b-4e21-9a77-1f3b6c8e0d42` and the booking body | 201                                                                     |
| Replay: same key, same body            | the identical request                                                                                  | 200, identical body, `Idempotent-Replayed: true`, nothing written twice |
| Same key, different body               | same key with `"starts_at": "2026-10-26T15:00:00+03:00"`                                               | 409 `IDEMPOTENCY_CONFLICT`                                              |
| Retry while the first is still running | the identical request, concurrently                                                                    | 409 `IDEMPOTENCY_IN_PROGRESS`                                           |
| No key, same body twice                | the booking body twice                                                                                 | second returns 409 `SLOT_TAKEN`                                         |

---

## 4. Concurrency and atomicity

The requirement that shaped everything:

> "Once booked, that slot must not be available to others."

A read-then-write check passes every sequential test and still double-books under two simultaneous requests. So correctness is enforced by PostgreSQL: a GiST exclusion constraint on the doctor's active intervals, and a second on the patient's. The application pre-checks to produce good errors; it never relies on the pre-check.

**Guarantee:** for any doctor, no two active appointments have overlapping intervals.

| Race                                              | Handled by                                                     |
| ------------------------------------------------- | -------------------------------------------------------------- |
| Two bookings for the same slot                    | Exclusion constraint: one gets 201, the other 409 `SLOT_TAKEN` |
| Reschedule and another booking on the target slot | Exclusion constraint                                           |
| Reschedule and cancel on the same appointment     | Single transaction; the second sees the new status             |

**Reschedule** updates the interval of the same row in one transaction, so free-and-claim are atomic. If the target slot is held, the constraint raises, the transaction rolls back, and the original booking is untouched.

**Failure path:** the second concurrent insert violates the constraint, the transaction rolls back, the repository translates the constraint name, and the API returns 409 `SLOT_TAKEN`.

**Proof:** `tests/test_concurrency.py` fires ten parallel bookings with `asyncio.gather` and asserts exactly one 201 and nine 409s against real PostgreSQL.

| Rejected alternative        | Why                                                               |
| --------------------------- | ----------------------------------------------------------------- |
| Row locks                   | Serialise a doctor and depend on every code path remembering them |
| `SERIALIZABLE` with retries | Plumbing everywhere                                               |
| Redis locks                 | A second source of truth                                          |
| Unique-start index          | Wrong invariant: misses overlapping, unequal start times          |

---

## 5. Validation and errors

| Code                            | Status | Raised when                                                                                            |
| ------------------------------- | ------ | ------------------------------------------------------------------------------------------------------ |
| `NOT_FOUND`                     | 404    | Unknown doctor, patient or appointment                                                                 |
| `SLOT_TAKEN`                    | 409    | The slot overlaps an active appointment for the doctor or patient                                      |
| `APPOINTMENT_ALREADY_CANCELLED` | 409    | Cancel on a cancelled appointment                                                                      |
| `APPOINTMENT_NOT_ACTIVE`        | 409    | Reschedule on an appointment that is not active                                                        |
| `IDEMPOTENCY_CONFLICT`          | 409    | Key reused with a different request                                                                    |
| `IDEMPOTENCY_IN_PROGRESS`       | 409    | Retry while the first request is still running                                                         |
| `SLOT_IN_PAST`                  | 422    | Start time is in the past                                                                              |
| `LEAD_TIME_VIOLATION`           | 422    | Start time is inside the minimum lead time                                                             |
| `SLOT_MISALIGNED`               | 422    | Start time is not on the half hour                                                                     |
| `OUTSIDE_WORKING_HOURS`         | 422    | Not a working day, or the slot does not fit inside the doctor's hours                                  |
| `DIRECTORY_UNAVAILABLE`         | 503    | Doctor or patient service returned 5xx, timed out, or sent a malformed payload; includes `Retry-After` |

**Status code meaning:** 404 not found, 422 a rule violation, 409 a valid request that conflicts with current state.

---

## 6. Testing

| Level              | What it covers                                                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| API                | Every endpoint and every error code                                                                                               |
| Timezone           | Local/UTC equivalence and offset handling                                                                                         |
| Idempotency        | Replay returns 200 with the same appointment; a reused key with a different body returns 409                                      |
| Concurrency        | Ten parallel bookings, exactly one 201 and nine 409s                                                                              |
| Directory boundary | Cache hit and expiry, write-path cache bypass, upstream 5xx and timeout mapping to 503 + `Retry-After`, and the `/version` marker |

Tests run against a real PostgreSQL test database migrated by Alembic, so the tested schema is the deployed schema. Doctors and patients come from fixtures; no seeding step is required.

```bash
pytest --cov=app --cov-fail-under=85
```

---

## 7. Performance

Run all scripts with `make perf`.

| Script                                 | What it does                                                                                                                                                                                                             |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `scripts/perf/explain_availability.py` | Runs `EXPLAIN (ANALYZE, BUFFERS)` on the availability lookup and fails if the plan uses a sequential scan on `appointment`. The partial indexes `ix_appt_doctor_day` and `ix_appt_patient` exist for this access pattern |
| `scripts/perf/load_book.py`            | Fires concurrent bookings at one slot, asserts exactly one success, reports p50 and p95 latency                                                                                                                          |
| `scripts/perf/seed_bulk.py`            | Generates appointments for realistic plans; doctors and patients come from fixtures                                                                                                                                      |

CI runs the structural checks (index used, one winner), not latency thresholds, because wall-clock assertions are flaky across runners.

---

## 8. Quality gates

`make check` runs the three gates locally; CI runs the same three on every pull request.

| Gate             | Command                                |
| ---------------- | -------------------------------------- |
| Lint             | `ruff check app tests`                 |
| Types            | `mypy app` (strict)                    |
| Tests + coverage | `pytest --cov=app --cov-fail-under=85` |

Coverage is measured over `app/`, excluding tests. The gate is enforced in CI so a red pipeline blocks a merge.

---

## 9. Deployment and CI/CD

**Hosting:** Render (web service) with Neon (PostgreSQL). **Branch that deploys:** `main`.

| Step                          | What happens                                                                                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Branch                     | Work happens on a branch; `main` is protected, so there are no direct pushes                                                                            |
| 2. Pull request               | Runs the `test` job: `ruff`, `mypy` (strict), `pytest` with the 85% coverage gate, `alembic check` for model/migration drift, and a Docker build |
| 3. Review                     | The diff is read before merge. A green pipeline is necessary, not sufficient                                                                            |
| 4. Merge                      | Squash merge into `main`. That merge is the only thing that can deploy                                                                                  |
| 5. Pipeline                   | The suite runs again on `main`; only if green does the workflow POST the Render deploy hook                                                             |
| 6. Build and migrate          | Render builds the multi-stage image. The entrypoint takes a PostgreSQL advisory lock, runs `alembic upgrade head` with bounded retries, then serves Uvicorn on `$PORT` |
| 7. Smoke check                | The workflow polls `/health` until it returns 200, for up to ten minutes |
| 8. Prove the new code is live | It polls `/version` until the reported commit matches the pushed SHA, also for up to ten minutes |

If step 8 fails, the deploy is treated as failed even when the old instance still answers. The deploy job also fails when `RENDER_DEPLOY_HOOK_URL` or `DEPLOY_BASE_URL` is missing, instead of reporting a deploy that never happened. The runtime image carries runtime dependencies only; test and lint tools are installed in CI from the `dev` extra and never shipped.

| Variable                                    | Where it is set                 | Why                                                              |
| ------------------------------------------- | ------------------------------- | ---------------------------------------------------------------- |
| `CLINIC_DATABASE_URL`                       | Render dashboard, `sync: false` | Neon connection string; never committed                          |
| `CLINIC_AUTH_REQUIRED`                      | `render.yaml`                   | `false` keeps every endpoint callable for evaluation             |
| `CLINIC_DIRECTORY_MODE`                     | Render dashboard                | `fixture` for the demo, `http` when the directory services exist |
| `CLINIC_DOCTORS_URL`, `CLINIC_PATIENTS_URL` | Render dashboard                | Required only in `http` mode                                     |
| `PORT`                                      | Render                          | Uvicorn binds it; the entrypoint passes it through               |

Platform auto-deploy is disabled, so a deployment only ever follows a green pipeline. **Rollback** is a revert commit on `main`: the same pipeline runs, the entrypoint migrates forward, and `/version` proves which build is serving.

**Auth is open by default** (`CLINIC_AUTH_REQUIRED=false`) so every endpoint is directly callable for evaluation. Behind a gateway, the `X-Actor-Id` / `X-Actor-Role` headers it injects are honoured; this service still does not authenticate anyone.

---

## 10. Trade-offs

| Choice                                              | Benefit                                                                                |
| --------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Database constraint over application locking        | Correctness is declarative and covers every writer, including manual SQL               |
| Computed availability instead of materialised slots | No stale rows, no nightly job                                                          |
| Three tables                                        | Doctors and patients stay with the services that own them                              |
| In-place reschedule                                 | Atomicity is free; the appointment keeps its id and the change is recorded as an event |
| Auth optional by default                            | Keeps the required endpoints callable for evaluation                                   |

---

## 11. Growth path

**Today:** one clinic, one timezone, 30-minute slots. Doctors and patients come from the fixture directory. A merge to `main` deploys, and the pipeline verifies the deployed commit through `/version`.

**Scheduling evolution**

| Item                                           | Note                                                                                                                                               |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sessions                                       | Several `[start, end)` intervals per weekday per doctor. The contract and schema already allow a list; the fixture uses a single 09:00-17:00 block |
| Date overrides                                 | Holidays, extra clinics, room closures                                                                                                             |
| Per-service duration and buffers               | 15/30/45-minute appointment types                                                                                                                  |
| Booking horizon, lead time, overbooking policy | Per clinic                                                                                                                                         |
| 24/7 services                                  | Dialysis and emergency modelled as sessions; on-call rotas stay a staffing concern                                                                 |

**Scale and operations**

| Area                 | Plan                                                                                                                                                         |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Multi-clinic tenancy | Add `clinic_id` to `appointment` and scope every query                                                                                                       |
| Events               | An outbox table for `appointment.created/cancelled/rescheduled`, consumed by notifications and analytics. `appointment_events` is the audit trail, not a bus |
| Security             | mTLS or JWT between services; identity headers only from the gateway; rate limiting at the edge                                                              |
| Observability        | Propagate `X-Request-Id`, emit dependency latency and 503 counters, alert on directory error rate                                                            |
| Deployment           | Staging environment, canary by doctor service, and the commit check extended with a smoke booking against a staging slot                                     |

**Rollout**

1. Stand up the doctor and patient services against the contract in [2.6](#26-doctor-and-patient-data).
2. Run `CLINIC_DIRECTORY_MODE=http` in staging and compare availability with fixture mode.
3. Canary one doctor; watch the 503 rate and latency.
4. Keep fixtures for tests only.

---

## 12. Run locally

**Prerequisites:** Python 3.12 and PostgreSQL.

1. Create the databases `clinicdb` and `clinicdb_test`.
2. Copy `.env.example` to `.env` and set `CLINIC_DATABASE_URL` and `CLINIC_TEST_DATABASE_URL`.
3. Install, migrate and run:

```bash
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`, with interactive docs at `/docs`. A VS Code launch configuration is included, so F5 starts it with the virtual environment and `.env`.

| Variable                                    | Used for                                                                         |
| ------------------------------------------- | -------------------------------------------------------------------------------- |
| `CLINIC_DATABASE_URL`                       | The app and Alembic                                                              |
| `CLINIC_TEST_DATABASE_URL`                  | The test suite (must be a separate database)                                     |
| `CLINIC_AUTH_REQUIRED`                      | `false` (default) keeps every endpoint callable; `true` enforces the role matrix |
| `CLINIC_DIRECTORY_MODE`                     | `fixture` (default) or `http`                                                    |
| `CLINIC_DOCTORS_URL`, `CLINIC_PATIENTS_URL` | Required only in `http` mode; boot fails if missing                              |

---

## 13. Try the deployed API

**Base URL:** https://clinic-appointment-service.onrender.com

Dates must be future weekdays; doctors have no hours at weekends. The script uses GNU `date`; on macOS use `date -v+mon +%F`.

```bash
BASE=https://clinic-appointment-service.onrender.com
DOCTOR=6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14
PATIENT=1b6c25f0-45c6-426a-a3dd-33ab4e226e9d
DATE=$(date -d 'next monday' +%F)
TEN="$DATE"T10:00:00+03:00
ELEVEN="$DATE"T11:00:00+03:00
```

```bash
curl -s $BASE/health
curl -s $BASE/ready
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | head -c 300
```

```bash
# book; copy the id from the response
curl -s -X POST $BASE/appointments \
  -H 'Content-Type: application/json' \
  -d "{\"doctor_id\":\"$DOCTOR\",\"patient_id\":\"$PATIENT\",\"starts_at\":\"$TEN\"}"
```

```bash
# the slot is now gone
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | grep -c "T10:00:00"
APPT=<id from the booking response>
curl -s -X PATCH $BASE/appointments/$APPT/cancel \
  -H 'Content-Type: application/json' -d '{"reason":"patient unwell"}'
# slot is free again
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | grep -c "T10:00:00"
# second cancel -> 409 APPOINTMENT_ALREADY_CANCELLED
curl -s -X PATCH $BASE/appointments/$APPT/cancel \
  -H 'Content-Type: application/json' -d '{"reason":"again"}'
```

```bash
# reschedule needs an active appointment
curl -s -X PATCH $BASE/appointments/$APPT/reschedule \
  -H 'Content-Type: application/json' -d "{\"starts_at\":\"$ELEVEN\"}"
curl -s "$BASE/patients/$PATIENT/appointments?limit=20"
```

| Expected                                                                                 | Endpoint                                                           |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 201 booked                                                                               | `POST /appointments`                                               |
| 409 `SLOT_TAKEN`                                                                         | second booking of the same slot                                    |
| 409 `APPOINTMENT_ALREADY_CANCELLED`                                                      | second cancel                                                      |
| 422 `SLOT_IN_PAST` / `LEAD_TIME_VIOLATION` / `SLOT_MISALIGNED` / `OUTSIDE_WORKING_HOURS` | invalid bookings                                                   |
| 200                                                                                      | `GET /appointments/{id}`, `/events`, `/patients/{id}/appointments` |
| 404 `NOT_FOUND`                                                                          | unknown doctor, patient, or appointment                            |

---

## 14. AI reflection

Built spec-first: the schema, the constraint and the lifecycle were decided before any code was generated, and the agents worked inside those boundaries. The full account — what I used AI for in each section, a suggestion that changed the work, a case where it was wrong and how I caught it, and the two calls I made without it — is in [AI_REFLECTION.md](AI_REFLECTION.md).

_Maintained alongside the code: any change to the design, API contract, or run instructions updates this document in the same commit._
