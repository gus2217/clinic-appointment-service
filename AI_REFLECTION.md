# AI reflection

A note on how this service was built, and where the line between my judgment and the
model's output sits.

## The setup

I work spec-first. Before any code is generated I write down what must be true: the three
tables, the exclusion constraints, the lifecycle and its legal transitions, the error
codes, the timezone rule. Those are decisions, not drafts. The schema in `alembic/`
implements that spec; it was not discovered by an agent.

From there the work runs as a loop with agents:

1. **Scaffold.** One slice at a time — an adapter, a router, a migration, a test module —
   against the written contract, in its own branch. Nothing spans two slices in one pass.
2. **Review.** I read every diff. Not a skim: the SQL, the transaction boundaries, and
   whether the tests assert behaviour or only status codes.
3. **Test, then hard-test.** `ruff`, `mypy`, `pytest` and the coverage gate run.
   Then I attack it: two `psql` sessions racing the same slot, a key replayed twice, an
   expired hold swept, the directory returning 500.
4. **Fix.** Failures go back to an agent with the failing test and the logs. I re-run the
   gates myself.
5. **Ship.** Branch, PR, green CI, merge, deploy, then hit the live URL and confirm
   `/version` reports the commit I merged.

The final pass before submission ran the same way. Reading every module turned up two
copies of the hold-expiry SQL — the duplication that had already produced one bug — dead
configuration, an unused dependency, and seven route handlers that differed only in the
operation they called. Those became one repository method, one `_mutate` helper, and less
code overall. A live smoke test against a real Uvicorn process and PostgreSQL then booked,
replayed, conflicted and cancelled twice, and checked the status codes.

The agents are fast in the middle of that loop. The first and last steps are mine, and so
is anything the loop discovers.

## 1. What I used AI for across the four sections

**Design.** As a critic, not an author. I wrote the entities, the constraint and the
lifecycle; I used the model to argue against them — what breaks if two requests hit the
same slot, what this looks like at a thousand bookings a day. Most of its alternatives
moved correctness into application code, and I rejected them.

**Implementation.** The parts that are mechanical once the design is fixed: router
signatures, Pydantic schemas, Alembic boilerplate, the HTTP adapter behind the directory
port, and the first draft of test matrices for timezone edges.

**Testing and quality.** Two concrete wins: it found the coverage measurement bug below,
and it proposed boundary cases I had not written — naive datetimes, rescheduling into a
taken slot, a key replayed after a partial failure.

**Delivery.** Drafting the CI workflow, the Render configuration and the smoke script.
Every one of those drafts had a defect that CI or a deploy caught. See below.

## 2. One suggestion that improved the work

Coverage stalled at 81% while every new test passed. I asked:

> "pytest-cov reports these lines as uncovered, but the endpoint tests call them and
> return 200. Why would coverage miss code after an `await`?"

The answer: SQLAlchemy's async engine switches greenlets, and coverage.py stops attributing
lines after the first database `await` unless `concurrency = ["greenlet", "thread"]` is
set. The measurement was wrong, not the tests. That one setting took reported coverage to
99% and stopped me writing tests to chase a number that did not exist. I would have spent
an afternoon on phantom work.

## 3. Where AI was wrong, and how I caught it

The idempotency implementation. The first version stored the response but the route was
pinned to `201 Created`, so a replay also answered 201 — as if it had created something.
The conflict error was mapped to 400 while the code path produced 409, and the agent's own
test asserted the wrong behaviour, so the suite stayed green.

I caught it by using the API the way a client would: two identical `POST`s with the same
`Idempotency-Key`, comparing status and body. `201` then `201` is not idempotent; it is
two creates that happen to share an id. That sent me back to the contract — `201` first,
`200` with the identical body on replay, `409` only when the key is reused with a
different request.

Chasing that exposed a worse defect underneath: the appointment commit and the idempotency
row were separate transactions, so a crash between them could double-book on retry. The
fix was structural — claim the key first, then commit the work and the stored response
together. The regression test fails the commit deliberately and asserts neither row
survives. That test is the point; the status code was the symptom. The suite finished at 86 tests and 96.8% coverage, with the gate still at 85%.

A smaller one: an agent-written Dockerfile ran `pip install .` before copying `app/`, so
the image could never build. CI caught it, not review.

## 4. Two decisions I made without AI

**The service owns three tables, not twelve.** Doctors and patients belong to directory
services. I kept `appointment`, `appointment_events` and `idempotency_keys`, and put
the boundary behind a port with fixture and HTTP adapters. Every suggestion to store
doctor or patient rows locally made the demo easier and the system wrong: two sources of
truth for working hours is a scheduling bug waiting for a holiday. I trusted my read of the
deployment boundary because I own it — the gateway injects identity, and the directories
already exist.

**Overlap is enforced by PostgreSQL, not by application code.** A read-then-write check
passes every sequential test and still double-books under concurrency; the exclusion
constraint holds for every writer, including someone at a `psql` prompt. I chose the
declarative constraint over advisory locks and over application-level locking because it is
the smaller invariant and it is provable: open two sessions, insert the same interval,
watch the second fail.

In both cases the model was useful as an opponent. It was not useful as an authority,
because it does not carry the failure modes I have already been burned by.

---

*This page is part of the submission: when the design or the contract changes, it changes
with them.*
