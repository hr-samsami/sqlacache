## Why

sqlacache has a fully designed architecture (see `sqlacache-architecture.md`) but zero implementation. v0.1.0 is the MVP that delivers the core value proposition — automatic, row-level cache invalidation for SQLAlchemy — along with the project infrastructure needed to publish to PyPI.

## What Changes

- **New**: Full project scaffold — `pyproject.toml`, `src/` layout, `uv.lock`, `Makefile`, GitHub Actions CI/CD, `docker-compose.yml`, `prek` hooks, `CHANGELOG.md`, `CONTRIBUTING.md`, `LICENSE`, `README.md`
- **New**: `configure()` factory and `CacheManager` public API
- **New**: `do_orm_execute` query interceptor (async SQLAlchemy sessions)
- **New**: Cacheops-style declarative model-to-ops configuration with wildcard matching
- **New**: Automatic cache key generation from SQL statement + bound parameters
- **New**: Row-level invalidation engine using cashews' native tag system
- **New**: `CashewsTransport` wrapping `cashews.Cache` (Redis and in-memory backends)
- **New**: `after_insert` / `after_update` / `after_delete` ORM event hooks for automatic invalidation
- **New**: Basic cross-process invalidation via Redis pub/sub
- **New**: `cache.invalidate(model, pks)` and `cache.invalidate_all()` manual invalidation API
- **New**: `py.typed` marker (PEP 561)
- **Async only** — sync session support is deferred to v0.2.0

## Capabilities

### New Capabilities

- `project-infrastructure`: Full project scaffold (pyproject.toml, build system, tooling, CI/CD, Docker Compose, docs files)
- `cache-configuration`: `configure()` factory, `CacheManager`, model-to-ops mapping, wildcard matching, backend URL parsing
- `query-interception`: `do_orm_execute` event hook, op-type detection (get/fetch/count/exists), model extraction from statements
- `cache-key-generation`: Deterministic key hashing from SQL statement + bound parameters
- `row-level-invalidation`: ORM write event hooks, PK extraction from results, cashews tag-based dependency tracking, `delete_tags()` invalidation
- `cashews-transport`: `CashewsTransport` adapter wrapping `cashews.Cache` for Redis and `mem://` backends
- `cross-process-invalidation`: Redis pub/sub publish on write, subscribe listener for multi-worker deployments

### Modified Capabilities

<!-- None — this is the initial implementation, no existing specs to modify -->

## Impact

- **New package**: `sqlacache` published to PyPI
- **Dependencies**: `sqlalchemy>=1.4`, `cashews>=7.0`; optional `cashews[redis]`
- **Python**: 3.10–3.13
- **Infrastructure**: Requires Redis for production use; `mem://` for dev/testing
- **No breaking changes** (initial release)
