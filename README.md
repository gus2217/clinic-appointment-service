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
- Install the project, run migrations, seed, start the server.

The API runs at `http://127.0.0.1:8000`; interactive docs at `/docs`. A VS Code launch
configuration is included, so F5 starts it with the virtual environment and `.env`.

The seed creates five doctors with weekday hours 09:00–17:00 in Africa/Nairobi and three
patients. It is idempotent and prints the generated GUIDs.

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

Six tables plus idempotency: `clinic`, `doctor`, `doctor_working_hours`,
`doctor_time_off`, `patient`, `appointment`, `idempotency_keys`. All IDs are UUIDs.

Constraints on `appointment`:

- status in `booked`, `cancelled`;
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

## Testing

- API tests: every endpoint and every error code.
- Timezone: local/UTC equivalence and offset handling.
- Idempotency: replay returns the same appointment.
- Concurrency: ten parallel bookings, one winner.
- Tests run against a real PostgreSQL test database migrated by Alembic, so the tested
  schema is the deployed schema.

---

## Deployment and CI/CD

- Multi-stage image; the entrypoint runs migrations then serves, so a deploy can never
  serve an old schema.
- GitHub Actions: on **pull request**, lint, type-check and test; on **merge to main**,
  re-run the suite and deploy, followed by a `/health` smoke check.
- Platform auto-deploy is disabled; a deployment only follows a green pipeline.

---

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
