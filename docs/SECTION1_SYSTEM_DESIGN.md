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
4. **Identity.** `doctor_id` and `patient_id` are pre-existing identifiers. The brief
   does not ask for authentication, so the required endpoints are directly callable;
   auth is an extension, not a precondition.
5. **"Not in the past"** means `starts_at > now`. The bonus tightens it to a
   **60-minute lead time**.
6. **Cancellation is terminal** and requires a reason. **Reschedule is allowed only
   from an active booking.**

## 1.3 Domain model

### Entities

| Entity | Responsibility | Key fields |
|---|---|---|
| **Doctor** | Who can be booked; owns hours | `id`, `name`, `clinic_id`, `is_active` |
| **DoctorWorkingHours** | Weekly availability template | `doctor_id`, `weekday` (0–6), `start_time`, `end_time` |
| **Patient** | Who books | `id`, `name` |
| **Appointment** | A booking and its lifecycle | `doctor_id`, `patient_id`, `starts_at`, `ends_at`, `status`, `cancel_reason` |
| **DoctorTimeOff** | Exceptions to the template | `doctor_id`, `starts_at`, `ends_at` |
| **Clinic** | Tenancy + timezone | `id`, `name`, `timezone` |

```
Clinic 1──* Doctor 1──* DoctorWorkingHours
                 │
                 ├──* DoctorTimeOff
                 │
                 └──* Appointment *──1 Patient
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
Model layer       SQLAlchemy tables and constraints
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
| D7 | **Six tables**, not twelve | departments, services, approvals, rooms | 3–5 days must yield a deployed, tested core. Extensions are documented, not half-built. |

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
  ) WHERE (status = 'booked');
```

- Two concurrent inserts for the same slot: the second violates the constraint
  (SQLSTATE **23P01**), its transaction rolls back, and the API returns **409
  `SLOT_TAKEN`**.
- The predicate `status = 'booked'` means a cancelled row no longer participates, so
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
2. Load the doctor's working hours for that weekday.
3. Generate 30-minute grid slots from `start_time` to `end_time`, keeping only slots
   whose end fits inside the hours.
4. Remove slots overlapping `doctor_time_off`.
5. Remove slots overlapping active appointments.
6. Remove slots inside the lead-time window.
7. Return UTC instants with explicit offsets, sorted ascending.

Cost is proportional to the doctor's daily hours (a handful of queries plus interval
filtering), and both lookups are index-backed:

```sql
CREATE INDEX ix_appt_doctor_day ON appointment (doctor_id, starts_at) WHERE status = 'booked';
CREATE INDEX ix_appt_patient    ON appointment (patient_id, starts_at) WHERE status = 'booked';
```

Because availability is derived from hours minus exceptions minus bookings, there is
no "slot" row to keep in sync — the source of truth is the appointments table.

## 1.8 Appointment lifecycle

```
            book
   (none) ────────► booked ──── cancel ────► cancelled (terminal)
                      │  ▲
              reschedule  │
                      └──┘
```

- `booked → cancelled`: requires a reason; cancelling twice returns **409**.
- `booked → booked`: reschedule to a new valid slot; cancelled appointments cannot be
  rescheduled (**409**).
- No other transitions exist. Illegal transitions are explicit errors, not silent
  no-ops.

## 1.9 Timezone rules

- Persist `timestamptz` (UTC). Never store naive local timestamps.
- Working hours are clinic-local `time`; the conversion to UTC happens per date.
- Reject naive input datetimes (**422**); require an explicit offset.
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
- **Holidays/time off:** `doctor_time_off` already exists; add `clinic_closures`.
- **Rooms/equipment:** the same exclusion-constraint pattern on a second resource.
- **Notifications:** an outbox row written in the booking transaction.
- **Multi-clinic:** `clinic_id` on doctors; appointments derive tenancy through the
  doctor.

## 1.12 Deliberately out of scope (v1)

Notifications, payments, clinical records, recurring appointments, and full auth.
Each is a large subsystem that would dilute the graded core; each has a documented
growth path above.
