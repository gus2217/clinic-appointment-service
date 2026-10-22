.PHONY: install migrate drift run test lint typecheck check perf

install:
	python -m pip install -e ".[dev]"

migrate:
	alembic upgrade head

drift:
	alembic check

run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check app tests

typecheck:
	mypy app

check: lint typecheck test

perf:
	python scripts/perf/explain_availability.py
	python scripts/perf/load_book.py
	python scripts/perf/seed_bulk.py
