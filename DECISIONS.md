# Decisions

## ADR-001 — Doctor and patient data are external

**Status:** accepted (2026-10-22)

**Context.** The assessment requires availability and booking validation against doctors'
working hours, but this is one service in a larger ecosystem.

**Decision.** Doctor and patient facts are read through `app/directories`, a port with
two adapters: `fixture` (in-memory demo and tests) and `http` (directory services).
This service owns only `appointment`, `appointment_events` and `idempotency_keys`.

**Consequences.** No local doctor/patient tables (migration 0005); the demo is
zero-config; the external contract carries working hours and time-off. A directory
outage fails closed rather than offering unverifiable slots.

## ADR-002 — No foreign keys to external entities

**Status:** accepted

**Decision.** `appointment.doctor_id` and `appointment.patient_id` are opaque UUIDs.
Migration 0004 dropped the local foreign keys.

**Consequences.** Referential integrity for people belongs to the directory services;
this service enforces only what it owns, the overlap guarantee. An id that no longer
exists upstream is treated as unknown by the directory lookup.

## ADR-003 — Fail closed on directory failures

**Status:** accepted

**Decision.** 5xx, timeout, connection errors or malformed payloads raise
`DirectoryUnavailable` → `503` with `Retry-After`. Reads cache doctor facts
(profile 10 min, time-off 60 s); the write path bypasses the time-off cache.

**Consequences.** Availability can be briefly stale by design; it is never optimistic.
An outage produces errors, not wrong bookings.
