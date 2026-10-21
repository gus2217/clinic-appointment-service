# Section 1 — System Design

## 1.1 The scenario, and the one line that decides the design

A small clinic with five doctors wants online booking. Each doctor has weekly working
hours divided into 30-minute slots. A patient sees a doctor's free slots for a date,
picks one, and books it. The patient can cancel or reschedule.

Almost all of this is ordinary CRUD. One requirement is not:

> "Once booked, that slot must not be available to others."

That single sentence is a **concurrency requirement**, and it is where the design
either earns correctness or only appears to. A read-then-write implementation
(`SELECT ... WHERE NOT EXISTS` then `INSERT`) passes every sequential test and still
double-books under two simultaneous requests, because both transactions read "free"
before either writes. The design below makes double-booking **structurally
impossible** rather than merely unlikely.

## 1.2 Assumptions (decided, not hidden)

The brief invites decisions when requirements are ambiguous. These are mine:

1. **Timezone.** The clinic operates in **Africa/Nairobi (UTC+3)**. All instants are
   stored in **UTC** (`timestamptz`); working hours are clinic-local wall-clock
   `time` values, converted per date.
2. **Slot granularity.** 30 minutes, exactly, and a slot must fit **entirely** inside
   working hours (`start + 30 min ≤ close`).
3. **Working hours** are per doctor, per weekday. No holiday calendar in v1; it is a
   documented extension.
4. **Identity and ownership.** `doctor_id` and `patient_id` are pre-existing
   identifiers owned by external directory services. This service stores only the ids
   and reads doctor facts (working hours, time-off, slot size) through a directory
   port with `fixture` (demo and tests) and `http` adapters. Authentication is not
   implemented: the gateway supplies identity, and the required endpoints stay
   callable for evaluation.
5. **"Not in the past"** means `starts_at > now`. The bonus tightens it to a
   **60-minute lead time**.
6. **Lifecycle.** An optional approval flow creates a `pending` hold that expires;
   otherwise the appointment is `booked`. Cancellation is terminal and requires a
   reason; reschedule is allowed only from `booked`; `completed` and `no_show` are
   terminal.

## 1.3 Domain model

### Entities

Owned here (three tables):

| Entity | Responsibility | Key fields |
|---|---|---|
| **Appointment** | A booking and its lifecycle | `doctor_id`, `patient_id`, `starts_at`, `ends_at`, `status`, `cancel_reason`, `hold_expires_at` |
| **AppointmentEvent** | Audit trail of transitions | `appointment_id`, `event_type`, `actor_id`, `from_status`, `to_status`, `reason` |
| **IdempotencyKey** | Replay-safe POSTs | `actor_id`, `key`, `request_hash`, `response_body` |

Read from directory services, never stored here:

| Entity | Responsibility | Key fields |
|---|---|---|
| **Doctor** | Who can be booked | `id`, `name`, `is_active`, `slot_minutes`, `working_hours` |
| **Patient** | Who books | `id`, `name` |

```
Directory services ──ids──► Appointment ──1──* AppointmentEvent
```

### Invariants

| # | Invariant | Class | Enforcement |
|---|---|---|---|
| I1 | A doctor has no two **overlapping active** appointments | **Hard** | PostgreSQL exclusion constraint |
| I2 | A patient has no two **overlapping active** appointments | **Hard** | PostgreSQL exclusion constraint |
| I3 | An appointment is exactly 30 minutes | Hard (structural) | `CHECK` |
| I4 | A cancelled appointment has a reason | Hard (structural) | `CHECK` |
| I5 | A booking starts inside working hours and on the 30-min grid | Policy | Service layer → 422 |
| I6 | A booking respects the lead time and is not in the past | Policy | Service layer → 422 |
| I7 | State transitions are legal (cancel once; reschedule only active) | Policy | Service layer → 409 |

The split matters: **hard invariants corrupt data if violated, so the database
enforces them**; policy rules produce rejected requests with useful messages, so the
service layer enforces them. Removing every policy check would still leave a system
incapable of double-booking.

## 1.4 Components and responsibilities

```
HTTP layer        FastAPI routers: parse, validate shape, map errors to status codes
Service layer     Booking rules, transaction boundary, state transitions, Clock
Domain layer      Pure functions: intervals, grid alignment, timezone conversion
Repository layer  All SQL; translates DB constraint violations to domain errors
Model layer       SQLAlchemy tables for appointment, event and idempotency
Directory layer   Doctor/patient ports + fixture/http adapters + TTL cache
```

- **Domain** has no I/O and receives an injected `Clock`; `datetime.now()` is never
  called inline, so time-dependent rules are testable.
- **Service** owns the transaction. A booking is one transaction: validate → insert →
  commit; a constraint error rolls back cleanly.
- **Repository** is the only place SQL lives and the only layer that knows SQLSTATE
  `23P01` (exclusion violation) — it maps it to `SLOT_TAKEN`.
- **HTTP** contains no business rules; it only translates.

## 1.5 Key decisions and trade-offs

| # | Decision | Alternatives considered | Why this one |
|---|---|---|---|
| D1 | **Range exclusion constraint** for overlap | check-then-insert; `SELECT FOR UPDATE`; `SERIALIZABLE` + retry; Redis lock; `UNIQUE(doctor_id, starts_at)` | Declarative, covers every writer, no lock discipline in app code. `UNIQUE(start)` is insufficient: different start times can still overlap. |
| D2 | **Partial predicate** on active status | delete cancelled rows; filter in queries only | Cancelled rows remain for history but stop blocking; no cleanup job, no lost audit trail. |
| D3 | **Computed availability**, no slots table | materialise slots per doctor/day | No stale rows, no nightly job, less storage; variable durations later need no migration. |
| D4 | **30-minute slots in the core** | service-driven 5–30 min durations | The brief is explicit. Variable durations are a documented extension, not a contract change. |
| D5 | **No auth on required endpoints** | JWT + roles from day one | The brief never asks; the evaluator must be able to call the endpoints directly. Auth is additive. |
| D6 | **In-place reschedule** | cancel + create new row | Free + claim become one atomic `UPDATE`; no orphaned or duplicated slots. Event history is not required by the brief. |
| D7 | **Three tables**, not twelve | owning doctor/patient rows; departments, services, approvals, rooms | The service owns only what it must transact on: appointments, their events and idempotency keys. |
| D8 | **Directory port with fixture and HTTP adapters** | local tables; direct HTTP calls inside services | Doctor and patient data is owned elsewhere; fixtures keep the demo and tests deterministic, and the HTTP adapter is the production path. |
| D9 | **Fail closed with a short TTL cache** | optimistic empty availability; no cache | A directory outage must not offer unverifiable slots: 503 with `Retry-After`. Caching doctor facts (10 min profile, 60 s time-off) keeps reads fast; writes bypass the time-off cache. |

## 1.6 Concurrency design (the core)

**The guarantee:**

> For any doctor, no two appointments in an active state have overlapping
> `[starts_at, ends_at)` intervals.

**Enforcement:**

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE appointment
  ADD CONSTRAINT ex_doctor_no_overlap EXCLUDE USING gist (
    doctor_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  ) WHERE (status IN ('pending','booked'));
```

- Two concurrent inserts for the same slot: the second violates the constraint
  (SQLSTATE **23P01**), its transaction rolls back, and the API returns **409
  `SLOT_TAKEN`**.
- The predicate `status IN ('pending','booked')` means a cancelled row no longer
  participates while a pending hold does, so an approval slot cannot be
  double-booked.
  the slot becomes bookable again without deleting history.
- `[)` half-open ranges mean 09:00–09:30 and 09:30–10:00 do not overlap.

A second constraint mirrors I2 for patients, preventing one person from being booked
with two doctors at the same time.

**Reschedule atomicity.** Rescheduling is an in-place `UPDATE` of `starts_at` and
`ends_at` in one transaction. The old interval is released and the new one claimed in
the same statement; if the new slot conflicts, the transaction rolls back and the
original appointment is untouched.

**Proof.** A concurrency test issues N parallel bookings for the same slot against
real PostgreSQL and asserts exactly one `201` and N−1 `409` responses. SQLite or
mocks cannot prove this, so the test runs against PostgreSQL.

**Rejected alternatives.** Row locks serialise all of a doctor's bookings and depend
on every code path remembering to lock; `SERIALIZABLE` is correct but needs retry
plumbing everywhere; a Redis lock adds a second source of truth that can disagree
with the database.

## 1.7 Availability computation

For a doctor and a clinic-local date:

1. Resolve the date to the clinic timezone; reject malformed dates (**400**).
2. Resolve the doctor through the directory port (fixture or HTTP). The profile is
   cached for 10 minutes, keyed by directory URL and doctor id.
3. Generate 30-minute grid slots from the doctor's working hours for that weekday,
   keeping only slots whose end fits inside the session.
4. Fetch time-off for the date range through the same port (60-second cache; the
   write path passes `fresh=True`).
5. Remove slots overlapping active appointments, including non-expired `pending` holds.
6. Remove slots inside the lead-time window.
7. Return UTC instants with explicit offsets, sorted ascending.

Cost is proportional to the doctor's daily hours (a handful of queries plus interval
filtering), and both lookups are index-backed:

```sql
CREATE INDEX ix_appt_doctor_day ON appointment (doctor_id, starts_at) WHERE status IN ('pending','booked');
CREATE INDEX ix_appt_patient    ON appointment (patient_id, starts_at) WHERE status IN ('pending','booked');
```

Because availability is derived from hours minus exceptions minus bookings, there is
no "slot" row to keep in sync — the source of truth is the appointments table.

## 1.8 Appointment lifecycle

```
                 requires_approval
   (none) ─────────────────────────► pending ──approve──► booked
                                        │                  │
                                        ├──reject──► rejected (terminal)
                                        └──hold expiry──► expired (terminal)

   (none) ──book──► booked ──cancel──► cancelled (terminal)
                      ├──complete──► completed (terminal)
                      ├──no-show───► no_show (terminal)
                      └──reschedule──► booked (in place)
```

- `pending` rows carry `hold_expires_at`; the sweeper marks stale holds `expired`
  before a new booking is validated, and availability ignores expired holds.
- `booked → cancelled` requires a reason; cancelling twice returns **409**.
- Reschedule is allowed only from `booked`; cancelled appointments return **409**.
- `completed` and `no_show` are terminal. Illegal transitions are explicit errors,
  not silent no-ops.

## 1.9 Timezone rules

- Persist `timestamptz` (UTC). Never store naive local timestamps.
- Working hours are clinic-local `time`; the conversion to UTC happens per date.
- A naive input datetime is interpreted as clinic-local wall-clock time; an
  explicit offset is honoured.
- Return timestamps with offsets.
- Interpret `?date=` as a clinic-local date.
- Inject `Clock` so "now"-dependent rules are deterministic in tests.

## 1.10 Failure modes

| Failure | Behavior |
|---|---|
| Concurrent booking of one slot | One 201; others 409 `SLOT_TAKEN` (constraint) |
| Concurrent cancel | One 200; others 409 `APPOINTMENT_ALREADY_CANCELLED` |
| Reschedule into a taken slot | 409; original appointment unchanged (rollback) |
| Doctor/patient missing | 404; no partial write |
| Directory unavailable | 503 `DIRECTORY_UNAVAILABLE` with `Retry-After`; no write |
| Directory payload invalid | 503, treated as unavailable rather than as "no slots" |
| Malformed date/body | 400/422; no write |
| DB unavailable | 503 from the health-aware layer; transaction aborts |
| Clock skew / DST edge | Clinic timezone conversion per date; UTC storage avoids ambiguity |

## 1.11 Growth path

- **Variable durations / services:** add `service` and `duration_minutes`; the range
  exclusion constraint is unchanged because it is interval-based.
- **Departments and category booking:** `department`, `doctor_services`; availability
  becomes "doctors able to deliver this service at this time".
- **Admin approval:** add `pending` to the active set with `hold_expires_at`.
- **Auth and roles:** additive; required endpoints stay public for evaluation.
- **Sessions and date overrides:** the directory contract carries a list of working
  intervals per weekday and per-date overrides; this service only consumes them.
- **Holidays/time off:** part of the directory contract; no local table to keep in
  sync.
- **Multi-clinic:** add `clinic_id` to `appointment` and scope every query by it.
- **Shared cache:** process-local TTLs today; Redis or invalidation events when
  running multiple workers.
- **Rooms/equipment:** the same exclusion-constraint pattern on a second resource.
- **Notifications:** an outbox row written in the booking transaction.

## 1.12 Deliberately out of scope (v1)

Notifications, payments, clinical records, recurring appointments, and full auth.
Each is a large subsystem that would dilute the graded core; each has a documented
growth path above.
