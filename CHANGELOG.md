# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Invalidation correctness.** Row mutations no longer invalidate the cache from `after_insert`/`after_update`/`after_delete` mapper events (which fire during flush, before commit). A rolled-back transaction was evicting cache entries that were still valid, leaving other workers to repopulate from the pre-rollback state. Mapper events now only record pending invalidations on the session; eviction runs in `after_commit` and is discarded on `after_rollback`/`after_soft_rollback`.
- **`session.execute(select(...))` on cache miss**. Previously raised `MissingGreenlet` for any uncached SELECT statement that reached `do_orm_execute` — the `await_only(resolve_cached_result(...))` pattern paused the provider greenlet before `invoke_statement()` could do DB IO. The SELECT path is now split: cache lookup is awaited, statement invocation runs synchronously in the provider greenlet, cache storage is awaited afterwards.
- **Operation-type detection.** `detect_operation_type` previously stringified the SQL and searched for `"COUNT("` / `"EXISTS"` substrings, which false-positive on literals like `Model.name.like("%count(%")`. Now uses AST inspection of `statement._raw_columns`.
- **Cache key dialect.** Cache keys were compiled against the sqlite dialect, which can raise or silently collapse distinct Postgres constructs (ILIKE, JSONB operators, ON CONFLICT). Switched to `StrCompileDialect`, SQLAlchemy's dialect-agnostic compiler.
- **`pyproject.toml` URLs.** Homepage/Repository/Documentation/Issues pointed at `github.com/persix/sqlacache`, which doesn't exist. Now point at `hr-samsami/sqlacache`.
- **Redis pub/sub resilience.** The listen loop previously died silently on any exception, stopping cross-process invalidation until the manager was rebound. Now reconnects with exponential backoff (0.5s → 30s). `publish()` swallows transient Redis failures rather than breaking the commit path.

### Added

- `cache_manager.flush_pending()` — await in-flight post-commit invalidations. Useful in tests or when the same session commits and immediately re-reads.
- `ModelJSONEncoder` — new `encode`/`decode` API that doesn't imply round-trip symmetry. `ModelJSONSerializer` kept as a backwards-compatible alias.
- `RedisPubSub.is_healthy()` — exposes listener connection state for health checks.
- `RedisPubSub.add_callback()` — replaces the misleading `listen()` name (kept as a deprecated alias).
- Warning log when `generate_tags` drops `None` PKs (previously silent).

### Changed

- **Eager-loaded relationships bypass the cache** with a warning log instead of being silently cached with untracked dependencies. Statements using `selectinload` / `joinedload` / `subqueryload` / `immediateload` go straight to the database.
- Pub/sub payload version is now checked on receive (`_PUBSUB_PROTOCOL_VERSION`); events with a mismatched version are skipped rather than mis-applied.
- Architecture design doc moved from repo root to `docs/architecture-and-roadmap.md`.

### Removed

- `configure(invalidation=...)` parameter — it was validated and stored but never read.
- `params` kwarg on `statement_to_sql` / `generate_cache_key` — unreachable code path.
- Legacy `after_bulk_update` / `after_bulk_delete` listeners — don't fire for 2.x ORM-enabled `update()`/`delete()` (handled by the `is_update`/`is_delete` branch of `do_orm_execute`).
- Dead code: `_handle_select`, `_handle_bulk_mutation`, `resolve_cached_result`, `cache_query_result`.
- Empty placeholder modules `contrib/fastapi.py` and `contrib/prometheus.py`.

## [0.1.1] - 2026-04-09

### Changed

- Updated package description for clarity.

## [0.1.0] - 2026-04-09

### Added

- First alpha release.
- Automatic caching for async SQLAlchemy reads — no decorators or query changes required.
- Row-level cache invalidation when records are created, updated, or deleted.
- Declarative config to map models to cache rules and TTLs in one place.
- Redis and in-memory backends supported out of the box.
- All workers stay in sync — cache invalidation propagates across processes via Redis.
- Manual override APIs for edge cases where automatic behavior isn't enough.
