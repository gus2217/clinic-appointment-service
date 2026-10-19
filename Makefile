.PHONY: install migrate seed run test lint typecheck check

install:
	python -m pip install -e . --group dev 2>/dev/null || pip install -e .

migrate:
	alembic upgrade head

seed:
	python scripts/seed.py

run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check app tests

typecheck:
	mypy app

check: lint typecheck test
