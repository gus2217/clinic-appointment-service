# Clinic Booking API

A clinic with five doctors wants patients to book 30-minute appointments online, see
which slots are free, cancel, and reschedule. This is my submission for the Savannah
Informatics backend assessment.

**Stack:** FastAPI (async) · PostgreSQL 16 · SQLAlchemy 2 async · psycopg 3 (async) ·
Alembic · Pydantic v2
**IDs:** GUID (UUID) primary keys throughout
**Deploy branch:** `main`

---

## The one requirement that shaped everything

Most of the brief is CRUD. One line is not:

> "Once booked, that slot must not be available to others."

That is a concurrency requirement. A read-then-write check passes every sequential test
and still double-books under two simultaneous requests. So correctness is enforced by
PostgreSQL, not by application code: a GiST exclusion constraint on the doctor's active
intervals, and a second on the patient's. The application pre-checks to produce good
errors; it never relies on the pre-check.

---

## Running it

You need Python 3.12 and PostgreSQL.

- Create `clinicdb` and `clinicdb_test`.
- Copy `.env.example` to `.env` and set `CLINIC_DATABASE_URL` and
  `CLINIC_TEST_DATABASE_URL`.
- Install and run: pip install -e . ; alembic upgrade head ; uvicorn app.main:app --reload

The API runs at `http://127.0.0.1:8000`; interactive docs at `/docs`. A VS Code launch
configuration is included, so F5 starts it with the virtual environment and `.env`.

Doctors and patients come from `app/directories/fixtures.py`: five doctors with weekday
hours 09:00–17:00 in Africa/Nairobi and three patients, with fixed GUIDs. No database
seeding is required. `CLINIC_DIRECTORY_MODE=http` switches to the external services.

---

## Trying it in Swagger

1. `GET /doctors/{id}/availability?date=YYYY-MM-DD` on a future weekday → free slots.
2. `POST /appointments` with a doctor, a patient, and one slot → 201.
3. Send the same body again → 409 `SLOT_TAKEN`.
4. `PATCH /appointments/{id}/cancel` with a reason → 200.
5. Re-book the freed slot → 201.
6. `PATCH /appointments/{id}/reschedule` → 200, and the old slot frees up.
7. `GET /patients/{id}/appointments?limit=20` → upcoming, sorted, keyset-paginated.

---

## Decisions I made where the brief was silent

- **Timezone.** Clinic-local wall-clock hours (Africa/Nairobi), UTC storage. Responses
  return clinic-local timestamps with an explicit `+03:00` offset, plus a `timezone`
  field. One representation, converted once at the API boundary. Naive input is
  interpreted as clinic-local time.
- **Slot length.** Exactly 30 minutes, aligned to the half-hour, and fitting entirely
  inside working hours.
- **Identity.** Trusted `X-Actor-Id` / `X-Actor-Role` headers. Enforcement is behind
  `CLINIC_AUTH_REQUIRED` (default `false`) so the required endpoints stay directly
  callable by the evaluator; turning it on yields 401 without headers.
- **Cancellation.** Terminal and requires a reason; cancelling twice is 409.
- **Reschedule.** Moves the existing appointment in place, atomically; only from active.
- **Idempotency.** `Idempotency-Key` on booking, scoped by actor, with a unique
  constraint and a stored response.

---

## Architecture

```
app/
  api/            routes, schemas, dependencies, error envelope
  core/           settings
  db/             async engine + session factory
  directories/    doctor/patient lookup ports
  models/         SQLAlchemy models, one module per aggregate
  repositories/   SQL, constraint translation, idempotency
  services/       booking, availability, appointment lifecycle
  main.py         app factory, request-id middleware, error handlers
```

The models are split by aggregate, not by table, so a change to scheduling never touches
doctor data. Repositories are the only place SQL lives; services own validation and the
transaction; routes own HTTP and status mapping.

---

## Data model

Three tables: `appointment`, `appointment_events`, `idempotency_keys`. All IDs are UUIDs.
Doctors and patients are external (fixtures for the demo), so `appointment` stores only
their ids.

Constraints on `appointment`:

- status in `pending`, `booked`, `cancelled`, `completed`, `no_show`, `rejected`, `expired`;
- exactly 30 minutes;
- slot aligned to the half-hour;
- cancellation and reason are consistent;
- **two exclusion constraints** over `[starts_at, ends_at)`, restricted to active rows,
  one per doctor and one per patient.

The exclusion constraint states the requirement directly: overlap, not equality. A unique
index on the start time would not catch two different start times that overlap.

---

## Concurrency guarantee

> For any doctor, no two active appointments have overlapping intervals.

The second concurrent insert violates the constraint, the transaction rolls back, the
repository translates the constraint name, and the API returns 409 `SLOT_TAKEN`.
Rescheduling updates the interval in one transaction, so free-and-claim are atomic.

Proof: `tests/test_concurrency.py` fires ten parallel bookings with `asyncio.gather` and
asserts exactly one 201 and nine 409s against real PostgreSQL.

Rejected alternatives: row locks (serialise a doctor and depend on every code path
remembering), `SERIALIZABLE` with retries (plumbing everywhere), Redis locks (a second
source of truth), unique-start index (wrong invariant).

---

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| POST | `/appointments` | 201 | 404; 409 `SLOT_TAKEN`; 422 `SLOT_IN_PAST`, `LEAD_TIME_VIOLATION`, `OUTSIDE_WORKING_HOURS`, `SLOT_MISALIGNED` |
| GET | `/appointments/{id}` | 200 | 404 |
| PATCH | `/appointments/{id}/cancel` | 200 | 404; 409 `APPOINTMENT_ALREADY_CANCELLED` |
| PATCH | `/appointments/{id}/reschedule` | 200 | 404; 409 `SLOT_TAKEN`, `APPOINTMENT_NOT_ACTIVE`; 422 |
| GET | `/doctors/{id}/availability?date=` | 200 | 400; 404 |
| GET | `/patients/{id}/appointments` | 200 | 404 |
| GET | `/health`, `/ready`, `/` | 200 | — |

Errors share one envelope: `code`, `detail`, `request_id`. 409 means a valid request
conflicts with current state; 422 means a rule violation; 404 means not found.

---

## Lifecycle decisions

Statuses: **pending, booked, cancelled, completed, no_show, rejected, expired**.

| From | Action | To | Allowed by |
|---|---|---|---|
| — | book | booked | patient |
| — | book with requires_approval | pending | patient |
| pending | approve | booked | doctor, admin |
| pending | reject | rejected | doctor, admin |
| pending / booked | cancel | cancelled | owner, admin |
| booked | complete | completed | doctor, admin |
| booked | no-show | no_show | doctor, admin |
| pending | hold expires | expired | system |

Decisions, stated plainly:

- **Booking defaults to booked.** `requires_approval: true` creates a pending hold instead.
- **A pending hold blocks the slot for 24 hours** (`hold_expires_at`). Expired holds are
  swept on the next booking or availability call and become `expired`.
- **Auth is off by default** (`CLINIC_AUTH_REQUIRED=false`) so the required endpoints are
  directly callable. Turning it on enforces the role matrix and returns 401 without
  `X-Actor-Id` / `X-Actor-Role`.
- **Every transition writes one `appointment_events` row in the same transaction**, so the
  audit trail cannot disagree with the appointment state.

---

## Try the deployed API

**Base URL:** https://clinic-appointment-service.onrender.com

The demo directory provides five doctors (Mon-Fri, 09:00-17:00 Africa/Nairobi) and three patients. Ids are fixed in `app/directories/fixtures.py`.

| Doctor | Id |
|---|---|
| Dr. Achieng | 6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14 |
| Dr. Bett | 891c1683-7204-44c7-a68d-9c1ca6a31814 |
| Dr. Chege | efea9b80-d2f4-4a3b-906f-f4919fc59784 |
| Dr. Odhiambo | 1b761fe4-fbcd-476d-8c10-2eeb7d905680 |
| Dr. Wanjiku | cd955052-2fe8-4db1-b382-e5638beb9ab9 |

| Patient | Id |
|---|---|
| Patient One | 1b6c25f0-45c6-426a-a3dd-33ab4e226e9d |
| Patient Two | 3e80c513-3391-40c4-aae9-802bb0ec7e3f |
| Patient Three | 20922d98-a9b4-41fc-9c4b-07d01593d990 |

Dates must be future weekdays; doctors have no hours at weekends.

~~~bash
BASE=https://clinic-appointment-service.onrender.com
DOCTOR=6ad88888-ae86-45ca-ab0a-b2e5ddc0fb14
PATIENT=1b6c25f0-45c6-426a-a3dd-33ab4e226e9d
DATE=$(date -d 'next monday' +%F)
TEN="$DATE"T10:00:00+03:00
ELEVEN="$DATE"T11:00:00+03:00
~~~

~~~bash
curl -s $BASE/health
curl -s $BASE/ready
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | head -c 300
~~~

~~~bash
# book; copy the id from the response
curl -s -X POST $BASE/appointments -H 'Content-Type: application/json' -d "{\"doctor_id\":\"$DOCTOR\",\"patient_id\":\"$PATIENT\",\"starts_at\":\"$TEN\"}"
~~~

~~~bash
# the slot is now gone
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | grep -c "T10:00:00"
APPT=<id from the booking response>
curl -s -X PATCH $BASE/appointments/$APPT/cancel -H 'Content-Type: application/json' -d '{"reason":"patient unwell"}'
# slot is free again
curl -s "$BASE/doctors/$DOCTOR/availability?date=$DATE" | grep -c "T10:00:00"
# second cancel -> 409 APPOINTMENT_ALREADY_CANCELLED
curl -s -X PATCH $BASE/appointments/$APPT/cancel -H 'Content-Type: application/json' -d '{"reason":"again"}'
~~~

~~~bash
# reschedule needs an active appointment
curl -s -X PATCH $BASE/appointments/$APPT/reschedule -H 'Content-Type: application/json' -d "{\"starts_at\":\"$ELEVEN\"}"
curl -s "$BASE/patients/$PATIENT/appointments?limit=20"
~~~

| Expected | Endpoint |
|---|---|
| 201 booked | POST /appointments |
| 409 SLOT_TAKEN | second booking of the same slot |
| 409 APPOINTMENT_ALREADY_CANCELLED | second cancel |
| 422 SLOT_IN_PAST / LEAD_TIME_VIOLATION / SLOT_MISALIGNED / OUTSIDE_WORKING_HOURS | invalid bookings |
| 200 | GET /appointments/{id}, /events, /patients/{id}/appointments |
| 404 NOT_FOUND | unknown doctor, patient, or appointment |

Idempotency: send the header Idempotency-Key: <unique-string> twice with the same body; the second call returns the same appointment. Reuse the key with a different body and the API returns 400 IDEMPOTENCY_CONFLICT.

---

## Doctor and patient data

This service does not assume it owns doctors or patients. Both are read through app/directories, a port with two adapters:

- fixture (default): in-memory demo data with fixed ids.
- http: calls the external services.

~~~text
CLINIC_DIRECTORY_MODE=fixture|http
CLINIC_DOCTORS_URL=https://doctors.internal
CLINIC_PATIENTS_URL=https://patients.internal
~~~

Contract:

~~~text
GET {doctors_url}/doctors/{id}   -> {"id","name","is_active","slot_minutes","working_hours":[{"weekday","start_time","end_time"}]}
GET {doctors_url}/doctors/{id}/time-off?from=&to= -> [{"starts_at","ends_at"}]
GET {patients_url}/patients/{id} -> {"id","name"}
404 = unknown
~~~

CLINIC_DIRECTORY_MODE=http fails at boot if a URL is missing. appointment stores only the ids; migration 0005 dropped the doctor and patient tables, so ids owned by another service are valid here.

## Testing

- API tests: every endpoint and every error code.
- Timezone: local/UTC equivalence and offset handling.
- Idempotency: replay returns the same appointment.
- Concurrency: ten parallel bookings, one winner.
- Tests run against a real PostgreSQL test database migrated by Alembic, so the tested
  schema is the deployed schema.

---

## Performance

Two scripts, both runnable with `make perf`:

- `scripts/perf/explain_availability.py` runs `EXPLAIN (ANALYZE, BUFFERS)` on the
  availability lookup and fails if the plan uses a sequential scan on `appointment`.
  The partial indexes `ix_appt_doctor_day` and `ix_appt_patient` exist for exactly this
  access pattern.
- `scripts/perf/load_book.py` fires concurrent bookings at one slot and asserts exactly
  one success, reporting p50 and p95 latency.
- `scripts/perf/seed_bulk.py` generates appointments for realistic plans; doctors and patients come from fixtures.

CI runs the structural checks (index used, one winner), not latency thresholds, because
wall-clock assertions are flaky across runners.

---

## Quality gates

`make check` runs the three gates locally; CI runs the same three on every pull request:

- **Lint:** `ruff check app tests`
- **Types:** `mypy app` (strict)
- **Tests + coverage:** `pytest --cov=app --cov-fail-under=85`

Coverage is measured over `app/`, excluding tests. The gate is intentionally enforced
in CI so a red pipeline blocks a merge.

---

## Deployment and CI/CD

**Public URL:** https://clinic-appointment-service.onrender.com

**Deploy branch:** `main`. A merge into `main` is the only thing that triggers a
deployment.

- **Image:** multi-stage Docker build. The builder installs the package into
  `/opt/venv`; the runtime image copies that venv, runs as an unprivileged user, and
  health-checks `/health`. The entrypoint runs `alembic upgrade head` and then serves
  Uvicorn on `$PORT`, so a deploy can never serve an old schema.
- **Pipeline:** GitHub Actions runs `ruff`, `mypy` (strict) and `pytest` with the 85%
  coverage gate on every pull request. On a merge to `main` the suite runs again; only
  if it is green does the workflow POST the Render deploy hook and then poll `/health`
  until it returns 200.
- **Platform auto-deploy is disabled**, so a deployment only ever follows a green
  pipeline.
- **Database:** Neon PostgreSQL, reached over TLS (`sslmode=require`) through
  `CLINIC_DATABASE_URL`.
- **Auth is open by default** (`CLINIC_AUTH_REQUIRED=false`) so every endpoint is
  directly callable for evaluation. When deployed behind a gateway, the
  `X-Actor-Id` / `X-Actor-Role` headers it injects are honoured.

## Trade-offs

- **Database constraint over application locking** — correctness is declarative and covers
  every writer, including manual SQL.
- **Computed availability instead of materialised slots** — no stale rows, no nightly job.
- **Six tables** — a working, tested, deployed core beats a half-built larger surface.
- **In-place reschedule** — atomicity is free; no per-appointment event history.
- **Auth optional by default** — keeps the required endpoints callable for evaluation.

---

## Growth

Services and variable durations (the constraint is range-based, so it survives), departments,
approval workflow, rooms/equipment (same constraint pattern), notifications via outbox,
and multi-clinic tenancy through the doctor.

---

## AI reflection

See `AI_REFLECTION.md`. In short: AI was used for scaffolding, edge-case test ideas, and
pressure-testing the concurrency design. It was not used for the schema, the constraint
choice, or the decision to enforce overlap in the database. Several suggestions were
rejected because they moved correctness into application code.

---

_Maintained alongside the code: any change to the design, API contract, or run
instructions updates this document in the same commit._
