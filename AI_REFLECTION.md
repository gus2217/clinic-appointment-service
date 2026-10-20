# AI reflection

The brief asks four questions. These are the honest answers for this submission.

## 1. What I used AI for

- **Scaffolding:** the first pass of the FastAPI routers, Pydantic schemas, and the
  SQLAlchemy models was drafted with an assistant, then edited heavily.
- **Test ideas:** edge cases I had not considered (naive datetimes, idempotency-key
  reuse, reschedule into a taken slot).
- **Review pass:** a second model read the diff and the deployment setup and listed
  risks. Some of those became real fixes.
- **Documentation:** drafting README sections, then correcting them against the code.

AI did not choose the database constraints, the lifecycle states, or the decision to
enforce overlap in PostgreSQL rather than in application code.

## 2. One suggestion that improved the work

Coverage stalled around 82% while every new test passed. The prompt was roughly:

> "pytest-cov reports these lines as uncovered, but the endpoint tests call them and
> return 200. Why would coverage miss code after an await?"

The answer was that SQLAlchemy's async engine switches **greenlets**, and coverage.py
without `concurrency = ["greenlet", "thread"]` stops attributing lines after the first
database await. That was not a testing gap at all — the measurement was wrong. Adding
that setting took the reported coverage to ~99% and, more importantly, stopped me from
writing pointless tests to chase a phantom number.

## 3. One case where AI was wrong

When I questioned why the schema still contained `doctor` and `patient` tables, the
assistant argued from the assignment text that those entities had to be owned locally,
and defended the existing design. That was wrong at the level that mattered: the
`app/directories` package was supposed to be the seam for external directory services,
but it was implemented as a three-line database getter returning ORM models.

I caught it by reading the module instead of the folder name. The fix was a real port
and two adapters (local fixtures, HTTP services) plus a migration dropping the local
foreign keys.

A smaller example: an assistant-written Dockerfile ran `pip install .` before copying
`app/`, so the image could never build. CI caught it, not review.

## 4. Two decisions made without AI

- **Overlap enforced by PostgreSQL exclusion constraints**, not by a read-then-write
  check. A pre-check passes every sequential test and still double-books under
  concurrency. I chose the declarative constraint because it holds for every writer,
  including manual SQL.
- **Availability computed from working hours at request time**, instead of
  materialising slot rows. No stale rows, no nightly job, and cancellation is a single
  status update. The cost is a heavier read query, which is why the partial indexes
  exist and why the query plan is checked in CI.

Both are choices where I distrusted a plausible-sounding alternative and preferred the
simpler invariant.
