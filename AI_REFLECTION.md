# AI Reflection (Section 4)

**My AI setup.** A coding agent wrote most of the code. Claude, in a chat window, was what I used to think through design decisions, write the build spec, and review what the agent produced. I supplied the talking points for this file; AI wrote the raw text, and I edited it so it says what happened.

**How I worked.** I wrote down what had to be true — the tables, constraints, lifecycle, error codes, the timezone rule — and gave the agent one slice at a time on its own branch: a router, a migration, a test module. I read every diff, with most of my attention on the SQL, the transaction boundaries, and whether the tests checked behaviour or only status codes. Then I ran ruff, mypy and pytest behind an 85% coverage gate and tried to break the thing by hand. Failures went back to the agent with the failing test and the logs. After each merge I hit the live URL and checked that /version reported the commit I had just merged.

What follows is where AI helped, where it was wrong, and what I decided on my own.

## 1. What I used AI for across the four sections

- **Section 1, system design.** AI was a sounding board. I framed the service as a booking microservice and asked Claude to find holes in it: what breaks if two requests hit the same slot, what this looks like at a thousand bookings a day. It helped me compare lifecycle designs and how real hospital systems model appointment states. I decided what the service owns.

- **Section 2, API implementation.** The agent wrote most of the code from my written contract: router signatures, Pydantic schemas, Alembic boilerplate, the HTTP adapter behind the directory port, and first drafts of the timezone test matrices. It also proposed boundary cases I had not written down — naive datetimes, rescheduling into a taken slot, a key replayed after a partial failure.

- **Section 3, deployment and CI/CD.** The agent drafted the GitHub Actions workflow, the Render configuration and the smoke script. Every one of those drafts had a defect that CI or a real deploy caught.

- **Section 4, this reflection.** AI wrote the raw text from my talking points. I edited it so it says what happened.

## 2. One example where an AI suggestion improved my work

**Coverage stuck at 81% while every test passed.**

The endpoint tests hit the database-backed routes and returned 200, but pytest-cov kept reporting the lines after each database await as uncovered. I asked:

> "pytest-cov reports these lines as uncovered, but the endpoint tests call them and return 200. Why would coverage miss code after an await?"

The answer was a measurement artifact, not a test gap. SQLAlchemy's async engine uses greenlets internally to bridge async/await with its synchronous ORM internals. coverage.py does not follow execution into greenlets unless you tell it to, so after the first database await it stopped attributing lines to the test. The tests were fine. The measurement was wrong.

The fix was one setting:

```toml
[tool.coverage.run]
concurrency = ["greenlet", "thread"]
```

Reported coverage went from 81% into the high 90s. The suite now has 86 tests at 96.8%, with the gate still at 85%. Without that setting I would have spent an afternoon writing tests for code that was already covered.

## 3. One example where AI output was wrong or incomplete, and how I caught it

**Idempotency that was not idempotent.**

The first version stored the response, but the route was hard-coded to 201 Created, so a replay also answered 201 — as if it had created something. The conflict case returned 400 where the contract said 409. The agent's own test asserted the wrong behaviour, so the suite stayed green.

I caught it by using the API the way a client would: two identical POSTs with the same Idempotency-Key, then compared status and body. 201 then 201 is two creates that happen to share an id. I went back to the contract: 201 on the first call, 200 with the identical body on replay, and 409 only when the key is reused with a different request.

The deeper problem sat underneath the status code. The appointment and the idempotency row were committed separately. The exclusion constraint still stops a double booking, but a crash between the two commits could leave a stored response pointing at an appointment that was never written, or a booking with no key, so a retry would get a confusing 409. The fix was to claim the key first, then commit the work and the stored response in one transaction. The regression test makes the commit fail on purpose and asserts that neither row survives.

The lesson: a test written by the same agent that wrote the code shares its blind spots. The status code was the symptom. The transaction boundary was the real defect.

Smaller things, same pattern. The agent's Dockerfile ran pip install . before copying app/, so the image could not build — CI caught that one, not my review. My final read-through found two copies of the hold-expiry SQL that had already caused one bug, plus dead configuration, an unused dependency, and seven route handlers that differed only in the operation they called. I folded those into one repository method and one \_mutate helper, and the code got smaller.

## 4. Two decisions I made without AI, and why I trusted my own judgment

### Decision 1: the service owns appointments and nothing else

An earlier iteration of mine owned doctor and patient tables; migration 0005 removes them. Doctors and patients now live behind a port, and this service stores only their IDs. The brief does not say who owns doctor data. I chose to treat this as a booking service that extends an existing platform. Claude helped with the shape of the port, not with where the boundary goes.

Two copies of working hours is a scheduling bug waiting for the first holiday, and I could name the cost of my choice: the service now depends on the directories being available, so it fails closed with a 503 rather than offering slots it cannot verify. The README says so.

### Decision 2: authentication is off by default, behind a flag

The brief does not ask for authentication, and whoever evaluates this has to be able to call the endpoints directly. In production the gateway authenticates and sends identity headers; this service reads them and enforces roles when CLINIC_AUTH_REQUIRED is on. By default it is off, so the public demo has no access control — and the README says that plainly.

This was a trade-off about my reader, and I know who the reader is. I did not need a model to tell me that a reviewer who cannot call the API cannot grade it.
