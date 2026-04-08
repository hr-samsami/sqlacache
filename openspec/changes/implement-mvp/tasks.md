# Task List: sqlacache MVP Implementation

## 1. Project Infrastructure & Setup

- [x] 1.1 Create directory structure (`src/sqlacache/`, `src/sqlacache/transport/`, `src/sqlacache/pubsub/`, `src/sqlacache/utils/`, `src/sqlacache/contrib/`, `tests/`)
- [x] 1.2 Create `pyproject.toml` with hatchling build config, core + optional dependencies, uv + ruff + mypy + ty configs
- [x] 1.3 Create `src/sqlacache/__init__.py` with `__version__ = "0.1.0"` and public API exports
- [x] 1.4 Create `src/sqlacache/py.typed` marker file (PEP 561)
- [x] 1.5 Create `src/sqlacache/exceptions.py` with `ConfigError` and core exceptions
- [x] 1.6 Create `Makefile` with targets: install (uv sync), lint (ruff check), format (ruff format), typecheck (mypy + ty), test, testcov, integration, clean
- [x] 1.7 Create `.pre-commit-config.yaml` for prek hooks (ruff, mypy, basic pre-commit-hooks)
- [x] 1.8 Create `.gitignore` excluding virtualenv, caches, build artifacts
- [x] 1.9 Create `docker-compose.yml` with Redis and PostgreSQL services
- [x] 1.10 Create `.github/workflows/ci.yml` with uv setup, Matrix (Py 3.10-3.13), ruff lint, ruff format check, mypy, ty (non-blocking), pytest
- [x] 1.11 Create `.github/workflows/release.yml` for PyPI publishing on version tag via OIDC trusted publisher
- [x] 1.12 Create `.github/workflows/integration.yml` for integration tests with docker-compose
- [x] 1.13 Create `README.md` with setup via `uv sync` and quick-start example
- [x] 1.14 Create `CHANGELOG.md`, `CONTRIBUTING.md` (mention `uv`, `make`, `prek`), `LICENSE` documentation files
- [x] 1.15 Verify `uv sync --extra redis --group dev` succeeds and creates `.venv/`
- [x] 1.16 Verify `uv run ruff check src/` works
- [x] 1.17 Verify `uv run ruff format --check src/` works
- [x] 1.18 Verify `uv run mypy src/` works
- [x] 1.19 Verify `uv run ty check src/` works (experimental, non-blocking; current ty CLI uses `check`)
- [x] 1.20 Verify `prek install` sets up git hooks without error

## 2. Configuration & Cache Manager

- [x] 2.1 Create `src/sqlacache/config.py` with `ConfigError` exception and configuration schema
- [x] 2.2 Implement `normalize_ops()` helper to convert "all" → {"get", "fetch", "count", "exists"}
- [x] 2.3 Implement `_resolve_model()` helper for dynamic model import resolution
- [x] 2.4 Implement `configure()` factory function with validation and normalization
- [x] 2.5 Create `src/sqlacache/manager.py` with `CacheManager` class skeleton
- [x] 2.6 Implement `CacheManager.__init__()` and config storage
- [x] 2.7 Implement `CacheManager.get_model_config()` with wildcard fallback
- [x] 2.8 Implement `CacheManager.is_enabled(model, op)` helper method
- [x] 2.9 Implement sync wrapper skeleton (`bind_sync`, `execute_sync`, `invalidate_sync`)
- [x] 2.10 Update `src/sqlacache/__init__.py` to export `configure`, `CacheManager`, `ConfigError`

## 3. Transport Layer & Cache Storage

- [x] 3.1 Create `src/sqlacache/transport/__init__.py` with `CacheTransport` protocol
- [x] 3.2 Create `src/sqlacache/transport/cashews.py` with `CashewsTransport` class
- [x] 3.3 Implement `CashewsTransport.connect()` to initialize cashews.Cache via URL
- [x] 3.4 Implement `CashewsTransport.get()` for cache retrieval
- [x] 3.5 Implement `CashewsTransport.set()` with TTL and tag support
- [x] 3.6 Implement `CashewsTransport.delete()` and `delete_tags()` for invalidation
- [x] 3.7 Implement `CashewsTransport.is_available()` for health checks
- [x] 3.8 Implement `CashewsTransport.disconnect()` for cleanup
- [x] 3.9 Add error handling with `suppress` flag for connection errors
- [x] 3.10 Create `tests/transport/conftest.py` with transport fixtures (Redis and in-memory)
- [x] 3.11 Create `tests/transport/test_cashews.py` with basic get/set/tag/disconnect tests

## 4. Utility Functions & Query Analysis

- [x] 4.1 Create `src/sqlacache/utils/key_generation.py` with cache key generation functions
- [x] 4.2 Implement `statement_to_sql()` to compile SQLAlchemy statements to normalized SQL strings
- [x] 4.3 Implement `sql_to_cache_key()` to hash SQL strings to cache keys
- [x] 4.4 Implement `generate_cache_key(stmt, prefix)` as public API
- [x] 4.5 Create unit tests in `tests/test_key_generation.py` for determinism, uniqueness, parameter sensitivity
- [x] 4.6 Create `src/sqlacache/utils/query_analysis.py` for query introspection
- [x] 4.7 Implement `extract_model_from_statement()` to extract models from FROM clause
- [x] 4.8 Implement `extract_pk_from_instance()` to get PK from ORM instance
- [x] 4.9 Implement `extract_pks_from_fetch_result()` to extract PKs from multi-row results
- [x] 4.10 Implement operation type detection (`get`, `fetch`, `count`, `exists`) helper
- [x] 4.11 Create unit tests in `tests/test_query_analysis.py`

## 5. Invalidation Engine & Tag Management

- [x] 5.1 Create `src/sqlacache/invalidation.py` with tag generation and invalidation logic
- [x] 5.2 Implement `generate_tags(model, pks)` to create cache dependency tags
- [x] 5.3 Implement `invalidate_tags(*tags)` wrapper for transport.delete_tags()
- [x] 5.4 Implement table-version workaround for bulk operations (`_bump_table_version()`, `_get_table_version()`)
- [x] 5.5 Create `tests/test_invalidation.py` with tag generation and invalidation tests
- [x] 5.6 Add tests for composite PKs and edge cases (NULL PKs, UUID PKs)

## 6. Cache Interceptor & Event Hooks

- [x] 6.1 Create `src/sqlacache/interceptor.py` for SQLAlchemy event registration
- [x] 6.2 Implement `cache_query_result()` to cache query results with tags
- [x] 6.3 Implement `do_orm_execute` hook skeleton for query interception (async-only for v0.1.0)
- [x] 6.4 Implement model extraction and cacheability checking in hook
- [x] 6.5 Implement cache lookup flow (check, miss, set with tags)
- [x] 6.6 Handle `do_orm_execute` async/sync context detection
- [x] 6.7 Register `after_insert`, `after_update`, `after_delete` invalidation hooks
- [x] 6.8 Hook into bulk update/delete with table-version bumping
- [x] 6.9 Implement error handling (cache errors don't propagate to app)
- [x] 6.10 Create `tests/test_interceptor.py` with mock ORM query tests
- [x] 6.11 Create `CacheManager.bind()` to register all hooks on engine
- [x] 6.12 Implement `CacheManager.disconnect()` to unregister hooks

## 7. Cache Control APIs

- [x] 7.1 Implement `CacheManager.execute(session, stmt, timeout)` for manual query caching
- [x] 7.2 Implement `CacheManager.invalidate(model, pks)` for manual row-level invalidation
- [x] 7.3 Implement `CacheManager.invalidate_all()` for full cache flush
- [x] 7.4 Create `tests/test_manager.py` with execute, invalidate, invalidate_all tests

## 8. Cross-Process Invalidation (Redis Pub/Sub)

- [x] 8.1 Create `src/sqlacache/pubsub/__init__.py`
- [x] 8.2 Create `src/sqlacache/pubsub/redis.py` with `RedisPubSub` class
- [x] 8.3 Implement `RedisPubSub.connect()` for pub/sub setup
- [x] 8.4 Implement `RedisPubSub.listen()` callback registration
- [x] 8.5 Implement `RedisPubSub._listen_loop()` background listener task
- [x] 8.6 Implement `RedisPubSub.publish()` to send invalidation events
- [x] 8.7 Implement `RedisPubSub.stop()` and `disconnect()` for cleanup
- [x] 8.8 Integrate pub/sub into `CacheManager.bind()` (conditionally for Redis backend)
- [x] 8.9 Hook pub/sub publish into invalidation events
- [x] 8.10 Create `tests/pubsub/test_redis.py` for unit tests (without real Redis)
- [x] 8.11 Create integration test in `tests/integration/` to verify multi-worker invalidation

## 9. Serialization & Utilities

- [x] 9.1 Create `src/sqlacache/serializers/__init__.py`
- [x] 9.2 Create `src/sqlacache/serializers/json.py` with model-aware JSON serializer (optional, for future)
- [x] 9.3 Create `src/sqlacache/utils/sync_wrapper.py` with sync adapter skeleton
- [x] 9.4 Create `src/sqlacache/contrib/__init__.py` (empty stubs for future integrations)
- [x] 9.5 Create `src/sqlacache/contrib/fastapi.py` (empty stub)
- [x] 9.6 Create `src/sqlacache/contrib/prometheus.py` (empty stub)

## 10. Testing Infrastructure

- [x] 10.1 Create `tests/conftest.py` with fixtures: `async_engine`, `cache`, `session` (async), `models` (sample SQLAlchemy models)
- [x] 10.2 Create sample test models (User, Product) with proper PKs for testing
- [x] 10.3 Create `tests/integration/conftest.py` with real Redis and PostgreSQL fixtures
- [x] 10.4 Create `tests/test_config.py` for configuration validation and error cases
- [x] 10.5 Create comprehensive tests covering:
     - Cache HIT/MISS scenarios
     - Row-level invalidation on INSERT/UPDATE/DELETE
     - Multi-model queries and PKs extraction
     - Bulk operations (table-version fallback)
     - Composite PKs
     - Error handling (cache connection errors)
     - Async session support only (sync denied in v0.1.0)

## 11. Documentation & Final Polish

- [ ] 11.1 Write README.md with quick-start example using `configure()` and `cache.bind()`
- [ ] 11.2 Document caching ops (`get`, `fetch`, `count`, `exists`, `all`)
- [ ] 11.3 Document configuration schema and wildcard matching
- [ ] 11.4 Document known limitation: bulk operations, raw SQL, sync sessions (v0.2.0)
- [ ] 11.5 Document backend options (Redis, in-memory, DiskCache in v0.2.0)
- [ ] 11.6 Add CHANGELOG.md v0.1.0 entry with features and known limitations
- [ ] 11.7 Verify pyproject.toml classifiers and metadata
- [ ] 11.8 Run `make lint`, `make format`, `make typecheck`, `make test` locally and verify all pass
- [ ] 11.9 Test `uv sync` and `uv run pytest` work without errors
- [ ] 11.10 Ensure GitHub Actions workflows validate and publish correctly

## 12. Pre-Release Verification

- [ ] 12.1 Run full unit test suite (`make test`)
- [ ] 12.2 Run linter and formatter (`make lint`, `make format`)
- [ ] 12.3 Run type checker (`make typecheck`)
- [ ] 12.4 Run integration tests locally (`docker-compose up`, `make integration`)
- [ ] 12.5 Verify package builds correctly (`python -m build`)
- [ ] 12.6 Verify PyPI metadata (`twine check dist/...`)
- [ ] 12.7 Manual smoke test: `pip install sqlacache`, `import sqlacache`, confirm version
- [ ] 12.8 Update CHANGELOG.md with release notes and final TODOs for v0.2.0
