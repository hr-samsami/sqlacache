## Context

sqlacache has a complete architecture document (`sqlacache-architecture.md`) that specifies every design decision, API contract, component interface, and backend strategy. This design doc summarizes the key technical decisions and their rationale for implementers, without repeating the full architecture.

Current state: empty repository (only `sqlacache-architecture.md` and `CLAUDE.md`). Everything needs to be built from scratch.

Constraints:
- `cashews >= 7.0` as the cache storage layer — we own ORM interception + invalidation, cashews owns storage + serialization + tags
- SQLAlchemy `>= 1.4` for `do_orm_execute` event
- Async-first (v0.1.0 is async-only; sync wrapper deferred to v0.2.0)
- Python 3.10+

## Goals / Non-Goals

**Goals:**
- Implement the `src/` layout project scaffold with all tooling (uv, hatchling, ruff, mypy, pytest, prek, Makefile, GitHub Actions, docker-compose)
- Implement `configure()` + `CacheManager` public API
- Implement `do_orm_execute` query interception for async sessions
- Implement cacheops-style model-to-ops config with wildcard matching
- Implement cache key generation (statement hash + bound params)
- Implement row-level invalidation via cashews tags (`after_insert/update/delete` hooks)
- Implement `CashewsTransport` for Redis and `mem://` backends
- Implement basic Redis pub/sub for cross-process invalidation
- Publish-ready package (pyproject.toml, py.typed, version, classifiers)

**Non-Goals (deferred):**
- Sync session support (v0.2.0)
- DiskCache backend (v0.2.0)
- Client-side caching / `client_side=True` (v0.2.0)
- Custom JSON serializer (v0.2.0)
- PK injection for column-level queries (v0.2.0)
- PostgreSQL UNLOGGED table backend (v0.3.0)
- `cache.execute()` manual caching with full invalidation (v0.3.0)
- Join queries / multi-table PK tracking (v0.3.0)
- Flask/Starlette contrib helpers (v0.4.0)
- Prometheus metrics contrib (v0.4.0)

## Decisions

### D1: `src/` layout

**Decision:** Place package source under `src/sqlacache/`, not `sqlacache/` at root.

**Rationale:** Prevents Python from accidentally importing the local uninstalled package during development or tests. Enforced by PyPA, default for `uv init --lib`, and used by the packages surveyed. Hatchling handles `src/` layout natively via `[tool.hatch.build.targets.wheel] packages = ["src/sqlacache"]`.

### D2: `hatchling` as build backend

**Decision:** Use `hatchling` as the build backend (not `uv_build`, `flit`, `setuptools`, or `pdm-backend`).

**Rationale:** Most widely adopted for pure-Python libraries (pydantic, httpx). Stable, well-documented, supports dynamic versioning from source file, supports `src/` layout, and has no unnecessary complexity for a library of this scope.

**Alternative considered:** `uv_build` — newer, from Astral, but still maturing. `pdm-backend` — used by FastAPI but less ecosystem traction. `setuptools` — legacy, more config surface area.

### D3: Version from `__init__.py`

**Decision:** Store version as `__version__ = "0.1.0"` in `src/sqlacache/__init__.py`. Hatchling reads it via `[tool.hatch.version] path = "src/sqlacache/__init__.py"`.

**Rationale:** Single source of truth accessible at runtime (`import sqlacache; sqlacache.__version__`). Same pattern as pydantic and httpx.

### D4: `do_orm_execute` as the single interception point

**Decision:** Hook all query interception via SQLAlchemy's `do_orm_execute` session event.

**Rationale:** Single event covers all ORM query types (get, select, count, exists) for both sync and async sessions. Available since SQLAlchemy 1.4. Alternative (`before_cursor_execute` / `after_cursor_execute`) operates at the Core level and loses ORM model context needed for cache key generation.

### D5: cashews tags for dependency tracking (no manual Redis pipelines)

**Decision:** Use cashews' native tag system for recording and invalidating cache dependencies.

**Rationale:** `cache.set(key, value, tags=["users:42"])` automatically creates a Redis SET mapping the tag to the cache key. `cache.delete_tags("users:42")` atomically deletes all keys in that set. Zero custom Redis pipeline code. Also works with `mem://` backend (in-memory sets), enabling unit tests with no infrastructure.

### D6: ORM write events for invalidation triggering

**Decision:** Use SQLAlchemy `after_insert`, `after_update`, `after_delete` mapper events to trigger invalidation.

**Rationale:** These events fire after flush, providing the final PK of the affected row. They cover the standard ORM write path. **Known limitation:** bulk operations (`update(Model).where(...)`) bypass these events — this is documented in Section 14 of the architecture doc as "The Silent Killer" and is deferred to a later milestone.

### D7: `pickle_type="sqlalchemy"` as default serializer

**Decision:** Pass `pickle_type="sqlalchemy"` to cashews setup as the default serializer.

**Rationale:** SQLAlchemy's `sqlalchemy.ext.serializer` handles ORM instances, relationships, and detached objects correctly. cashews has this built-in. No custom serialization code needed for v0.1.0.

### D8: Redis pub/sub for cross-process invalidation

**Decision:** On invalidation, publish a message to a Redis channel so all workers can clear their local caches.

**Rationale:** Required for multi-worker deployments. cashews' `delete_tags()` clears Redis keys, but if a worker has a local in-process cache (future: `client_side=True`), it won't know about the invalidation. Pub/sub bridges this. In v0.1.0, the listener is a background task spawned on `cache.bind()`.

### D9: `prek` instead of `pre-commit`

**Decision:** Use `prek` (Rust-based drop-in replacement) instead of `pre-commit`.

**Rationale:** Single binary, no Python runtime dependency, ~50% less disk, same `.pre-commit-config.yaml` format. Already adopted by CPython, FastAPI. Zero migration cost since format is identical.

### D10: Unit tests use `mem://` backend

**Decision:** Unit tests use cashews' in-memory backend (`mem://`) with no real Redis/PostgreSQL.

**Rationale:** cashews `mem://` supports full tag invalidation in-process. Zero infrastructure for CI runs and local development. Integration tests (marked `@pytest.mark.integration`) use real Redis via `docker-compose.yml`.

## Risks / Trade-offs

- **Bulk operation invalidation gap** → cashews `after_update`/`after_delete` only fires for ORM-loaded rows. Bulk `UPDATE`/`DELETE` bypasses events. *Mitigation:* Document clearly. Provide `cache.invalidate(model)` table-level API for users who do bulk ops. Full fix in v0.2.0 via `after_bulk_update`/`after_bulk_delete` hooks.

- **Eager loading PK tracking** → `joinedload` / `selectinload` results won't automatically tag the related table's PKs in v0.1.0. Cache entries for `User` with eager-loaded `Addresses` won't be invalidated when an `Address` row changes. *Mitigation:* Document limitation. Full fix in v0.3.0.

- **`do_orm_execute` async bridging** → The event fires synchronously even for async sessions. cashews operations are async. The interceptor must schedule async cache operations carefully (using `asyncio.ensure_future` or similar) to avoid blocking. *Mitigation:* The architecture doc addresses this; implement async scheduling in the interceptor.

- **cashews version pinning** → cashews tag API must be stable across the `>= 7.0` range. *Mitigation:* Pin to `cashews >= 7.0, < 8.0` initially to avoid unexpected breaking changes.

## Open Questions

- Resolved in architecture doc: op-type detection logic, cache key hashing strategy, tag naming convention (`"{table}:{pk}"`), wildcard config matching order.
- Outstanding: Should `bind()` be idempotent (safe to call multiple times)? → Yes, deregister existing hooks before registering new ones.
- Outstanding: Should `configure()` return a singleton or a new instance each call? → New instance each call; users manage the instance themselves. Singleton pattern is an application concern.
