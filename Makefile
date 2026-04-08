.PHONY: help install lint format typecheck test testcov integration clean

help:
	@printf "Available targets:\n"
	@printf "  install      Install project dependencies with uv\n"
	@printf "  lint         Run ruff checks\n"
	@printf "  format       Format code with ruff\n"
	@printf "  typecheck    Run mypy and ty\n"
	@printf "  test         Run unit tests\n"
	@printf "  testcov      Run unit tests with coverage\n"
	@printf "  integration  Run integration tests\n"
	@printf "  clean        Remove caches, build artifacts, and virtualenv\n"

install:
	uv sync --extra redis --group dev

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/
	uv run ty check src/ || true

test:
	uv run pytest -x -m "not integration"

testcov:
	uv run pytest --cov=sqlacache --cov-report=term-missing -m "not integration"

integration:
	uv run pytest -m integration

clean:
	rm -rf .venv .mypy_cache .pytest_cache .ruff_cache dist build src/*.egg-info .coverage htmlcov
