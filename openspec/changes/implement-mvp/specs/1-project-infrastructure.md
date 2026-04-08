# Spec: Project Infrastructure

Implement the complete project scaffold, build system, tooling, and publishing infrastructure for sqlacache v0.1.0.

## Requirements

### R1: Repository Structure (src/ layout)

Create the following directory structure:

```
src/sqlacache/           # Package source
├── __init__.py          # version, public exports
├── config.py
├── manager.py
├── interceptor.py
├── invalidation.py
├── exceptions.py
├── py.typed             # PEP 561 marker
├── transport/
│   ├── __init__.py
│   └── cashews.py
├── serializers/
│   ├── __init__.py
│   └── json.py
├── pubsub/
│   ├── __init__.py
│   └── redis.py
├── utils/
│   ├── __init__.py
│   ├── key_generation.py
│   ├── query_analysis.py
│   └── sync_wrapper.py
└── contrib/
    ├── __init__.py
    ├── fastapi.py
    └── prometheus.py

tests/                   # Test suite
├── conftest.py
├── test_config.py
├── test_interceptor.py
├── test_invalidation.py
├── test_manager.py
├── test_key_generation.py
├── test_query_analysis.py
├── test_serializers.py
├── transport/
│   ├── conftest.py
│   └── test_cashews.py
├── pubsub/
│   └── test_redis.py
└── integration/
    └── conftest.py
```

### R2: pyproject.toml

Create `pyproject.toml` with:

- **Build system**: `hatchling` backend
- **Build config**: Dynamic version from `src/sqlacache/__init__.py`
- **Package config**:
  - Name: "sqlacache"
  - Description: "Django-cacheops-style declarative caching with automatic row-level invalidation for SQLAlchemy"
  - License: MIT
  - Python: ">=3.10"
  - Authors and repository metadata
- **Core dependencies**: `sqlalchemy >= 1.4`, `cashews >= 7.0`
- **Optional dependencies** (extras):
  - `[redis]`: `cashews[redis]`
  - `[diskcache]`: `cashews[diskcache]`
  - `[dill]`: `cashews[dill]`
  - `[speedup]`: `cashews[speedup]`
  - `[postgresql]`: `asyncpg` (v0.3+ placeholder, not implemented in v0.1.0)
  - `[all]`: all of the above
- **Dev dependencies group**: pytest, pytest-asyncio, pytest-cov, mypy, ty (experimental), ruff, prek
- **Package manager**: `uv` (not in pyproject.toml, installed globally or per-project)
- **Tool configs**:
  - `[tool.hatch.version]` pointing to `src/sqlacache/__init__.py`
  - `[tool.hatch.build.targets.wheel]` packages = ["src/sqlacache"]
  - `[tool.ruff]` config (target py310, line-length 120, src layout, comprehensive rule set)
  - `[tool.ruff.lint]` rules: E, W, F, I (isort), UP (pyupgrade), B, SIM, TCH, RUF
  - `[tool.ruff.lint.isort]` known-first-party = ["sqlacache"]
  - `[tool.mypy]` config (strict mode, Python 3.10, ignore cashews, exclude tests)
  - `[tool.ty]` basic config (optional, experimental Rust-based type checker)
  - `[tool.pytest.ini_options]` (testpaths, asyncio_mode="auto", markers)

### R3: Version Management

- Store version in `src/sqlacache/__init__.py` as `__version__ = "0.1.0"`
- Version is read dynamically by hatchling from this file
- Exported from public API (users can `import sqlacache; sqlacache.__version__`)

### R4: Dependency Management (`uv`)

- **Package manager**: `uv` (Rust-based, drop-in replacement for pip + pipenv + virtualenv)
- Create `uv.lock` (committed to VCS) — deterministic dependency resolution
- All dependencies managed through `pyproject.toml` only
- No `requirements.txt` or `setup.py`
- Supported workflows:
  - `uv sync` — install all deps from lockfile into .venv
  - `uv sync --extra redis --group dev` — install specific extras + dev group
  - `uv run pytest` — run command in project venv
  - `uv lockfile --python 3.10` — update lockfile with specific Python version

### R5: Linting & Formatting (`ruff`)

Setup `ruff` config in `pyproject.toml`:

- **Linter**: `uv run ruff check src/ tests/ --fix`
  - Target: Python 3.10
  - Line length: 120
  - Rules: E, W, F, I (isort), UP (pyupgrade), B (bugbear), SIM, TCH, RUF
  - isort config: known-first-party = ["sqlacache"]
  - Extend-ignore: None (strict)
- **Formatter**: `uv run ruff format src/ tests/`
  - Same line length (120)
  - Auto-formats code to match linter expectations
- Both are configured in one tool (ruff replaces separate black + isort + flake8)
- Pre-commit hook executes: `ruff check --fix` then `ruff format`

### R6: Type Checking (`mypy` + `ty` experimental)

**Primary type checker: `mypy`**

Setup `mypy` config in `pyproject.toml`:

- Python version: 3.10
- Strict mode enabled
- Ignore missing imports for `cashews.*`
- Exclude: tests/ (but run separately in isolated mode)
- Command: `uv run mypy src/`

**Secondary (experimental): `ty`**

- Astral's Rust-based type checker (~10-100x faster than mypy)
- Config: `[tool.ty]` basic setup (follows mypy-compatible format)
- Command: `uv run ty src/` (optional, for checking speed)
- Status in v0.1.0: **experimental** — run alongside mypy, compare results
- FastAPI already uses ty in CI alongside mypy (proven pattern)

### R7: Testing Framework

Configure pytest in `pyproject.toml`:

- Test path: `tests/`
- `asyncio_mode = "auto"` (no need to decorate every async test with `@pytest.mark.asyncio`)
- Markers:
  - `integration`: Requires real Redis/PostgreSQL (skip with `-m "not integration"`)
  - `slow`: Long-running tests (skip with `-m "not slow"`)
- Filter warnings as errors
- Execution: `uv run pytest` (ensures virtualenv and dependencies)

Create `tests/conftest.py` with shared fixtures:

- **`async_engine`**: AsyncEngine with in-memory SQLite
- **`cache`**: CacheManager with `mem://` in-memory backend
- **`session`** (async): AsyncSession bound to async_engine
- **Sample models** (User, Product) for testing

Run tests:

- Unit tests: `uv run pytest -m "not integration"` (no infrastructure needed)
- Integration tests: `docker-compose up` then `uv run pytest -m integration`
- Coverage: `uv run pytest --cov=sqlacache --cov-report=term-missing`

Use `prek` (Rust-based pre-commit replacement) instead of `pre-commit`:

- **Advantages**: Single binary, no Python runtime dependency, ~50% less disk, same `.pre-commit-config.yaml` format
- **Installation**: `brew install prek` or `uv tool install prek`
- **Activation**: `prek install` (instead of `pre-commit install`)
- **Config file**: `.pre-commit-config.yaml` (100% compatible with pre-commit format)

Create `.pre-commit-config.yaml` with:

- `pre-commit-hooks` (v5.0.0+):
  - `trailing-whitespace`
  - `end-of-file-fixer`
  - `check-yaml`
  - `check-toml`
  - `check-added-large-files`
- **Local hooks** via `uv run`:
  - `ruff check --fix` (linter + auto-fix)
  - `ruff format` (formatter)
  - `mypy src/` (type checker)
  - Optional: `ty src/` (experimental Rust type checker, non-blocking)

Hooks run automatically on `git commit` (or `git push` with additional config).

### R9: Task Runner (`Makefile`)

Create `Makefile` with targets using `uv run`:

- `make install` — `uv sync --extra redis --group dev` (install all with Redis + dev deps)
- `make lint` — `uv run ruff check src/ tests/` (run linter)
- `make format` — `uv run ruff format src/ tests/` (format code)
- `make typecheck` — `uv run mypy src/` && `uv run ty src/ || true` (both mypy and ty, ty is optional)
- `make test` — `uv run pytest -x -m "not integration"` (unit tests only)
- `make testcov` — `uv run pytest --cov=sqlacache --cov-report=term-missing -m "not integration"`
- `make integration` — `uv run pytest -m integration` (requires docker-compose up)
- `make clean` — Remove build artifacts, caches, .venv
- `make help` — Show all targets

All tasks use `uv run`, ensuring the correct virtualenv and Python are used.

### R10: GitHub Actions CI/CD

Create `.github/workflows/` with `uv` as the package manager:

- **`ci.yml`** — Triggered on PR:
  - Matrix: Python 3.10, 3.11, 3.12, 3.13
  - Setup: `uv` via `astral-sh/setup-uv@v1` or manual install
  - Steps:
    - `uv sync --extra redis --group dev` (install deps)
    - `uv run ruff check src/ tests/` (lint)
    - `uv run ruff format --check src/ tests/` (format check)
    - `uv run mypy src/` (type check)
    - `uv run pytest -m "not integration"` (unit tests)
    - Optional: `uv run ty src/ || true` (experimental type checker, non-blocking)

- **`release.yml`** — Triggered on version tag (e.g., `v0.1.0`):
  - Setup: `uv`, Python 3.10
  - Build: `uv run build` (via hatchling backend)
  - Publish to PyPI using trusted publisher (OIDC)
  - No manual secrets needed

- **`integration.yml`** — Manual/scheduled (optional in v0.1.0):
  - Setup: `uv`, Docker
  - Docker: `docker-compose up` (Redis + PostgreSQL)
  - Steps: `uv run pytest -m integration`
  - Can skip in v0.1.0 if infrastructure not ready

### R11: Docker Compose (Dev only)

Create `docker-compose.yml` with:

- **Redis 7.x** service (port 6379 for unit/integration tests, no volume)
- **PostgreSQL 15+** service (port 5432, for v0.3+ integration tests, scaffolded but optional in v0.1.0)

Usage:

- `docker-compose up` — Start services
- Run tests: `uv run pytest -m integration`
- `docker-compose down` — Stop services

Not shipped to users — dev/CI only.

### R12: Documentation Files

Create at repo root:

- **`README.md`**: Project summary, quick-start using `configure()` + `cache.bind()`, link to architecture doc
- **`CHANGELOG.md`**: Initially empty with v0.1.0 placeholder
- **`CONTRIBUTING.md`**: How to contribute, setup instructions (`uv sync`, `make lint`, `make test`), PRs must pass CI
- **`LICENSE`**: MIT license text
- **`CLAUDE.md`**: Already exists (project guidance for LLMs, links to architecture)
- **`.gitignore`**: Exclude `.venv/`, `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `dist/`, `build/`, `.ruff_cache/`, `*.egg-info`

### R13: PEP 561 Type Marker

Create `src/sqlacache/py.typed` marker file (empty file) to indicate the package is typed. This enables:

- Type checkers to recognize the package as providing type information
- IDEs to show inline type hints for users of the library
- Enables PEP 561 compliance for IDE support

## Development Workflow (with all tools)

Quick reference for developers:

```bash
# Initial setup
uv sync --extra redis --group dev    # Install all deps
prek install                         # Setup git hooks

# Before commit (run prek or manually)
uv run ruff check --fix src/ tests/  # Lint + auto-fix
uv run ruff format src/ tests/       # Format
uv run mypy src/                     # Type check (primary)
uv run ty src/ || true               # Type check (experimental, non-blocking)
uv run pytest                        # Tests

# Or use Makefile
make install
make lint
make format
make typecheck
make test
```

## Implementation Notes

- Use `uv init --lib` as a reference point (automatically scaffolds `src/` layout with uv)
- All configuration centralized in `pyproject.toml` — do not split across multiple files
- `prek install` must be run after `.pre-commit-config.yaml` is in place
- `docker-compose.yml` can scaffold PostgreSQL for v0.3 without breaking v0.1.0
- `ty` is **experimental** — run it non-blocking in CI (with `|| true`) so failures don't block releases
- Ensure `Makefile` targets all use `uv run` for consistency (virtualenv + deps)
- GitHub Actions should use `astral-sh/setup-uv` action (official uv setup) or manual install
- All developers should use `uv` (not pip/virtualenv/poetry directly)
- Pre-release version should be `0.1.0-alpha` or `0.1.0a1` (**TODO: Clarify with team**)

## Acceptance Criteria

- ✓ All directories created under `src/sqlacache/` and `tests/`
- ✓ `pyproject.toml` parses without error
- ✓ `uv sync` succeeds and creates `.venv/` with all deps
- ✓ `uv run pytest --collect-only` discovers all test paths
- ✓ `uv run ruff check src/` and `uv run ruff format --check src/` pass (or can fix with `--fix`)
- ✓ `uv run mypy src/` runs without unhandled errors
- ✓ `uv run ty src/` runs successfully (experimental, may warn but doesn't error)
- ✓ `make install` executes without error
- ✓ `make lint`, `make format`, `make typecheck`, `make test` all work (even if tests fail/pass)
- ✓ `prek install` sets up git hooks successfully
- ✓ `.pre-commit-config.yaml` is valid YAML and compatible with prek
- ✓ GitHub Actions workflows (ci.yml, release.yml) are valid YAML and trigger correctly
- ✓ `docker-compose up` starts Redis and PostgreSQL without error
- ✓ `.gitignore` excludes `.venv/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`, etc.
- ✓ `src/sqlacache/py.typed` exists (empty file)
- ✓ `README.md` mentions setup via `uv sync` and `make` targets for common tasks
