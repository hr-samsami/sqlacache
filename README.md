# sqlacache

A slick library that adds automatic queryset caching and row-level invalidation to SQLAlchemy async sessions.

[![Tests](https://github.com/hr-samsami/sqlacache/actions/workflows/ci.yml/badge.svg)](https://github.com/hr-samsami/sqlacache/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/sqlacache)](https://pypi.org/project/sqlacache/)
[![Python](https://img.shields.io/pypi/pyversions/sqlacache)](https://pypi.org/project/sqlacache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

`session.get(User, 42)` is cached. `session.commit()` invalidates it. That's the whole idea.

Built on top of [`cashews`](https://github.com/Krukov/cashews) for storage and tag-based dependency tracking.

- Zero changes to your session usage — reads are intercepted automatically
- Row-level invalidation — only the rows that changed are evicted, not the whole table
- Cross-process invalidation via Redis pub/sub — all workers stay in sync
- Declarative config — map models to ops and TTLs in one place, with wildcard fallback
- Two backends: `redis://` for production, `mem://` for dev and testing

---

## Requirements

- Python 3.10+
- SQLAlchemy >= 1.4
- cashews >= 7.0
- Redis (for production; not needed for `mem://`)

---

## Installation

```bash
pip install sqlacache
pip install "sqlacache[redis]"   # with Redis support
```

Using `uv`:

```bash
uv add "sqlacache[redis]"
```

---

## Setup

Call `configure()` once at startup — alongside your engine setup.

```python
from sqlalchemy.ext.asyncio import create_async_engine
from sqlacache import configure

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/mydb")

sqlacache = configure(
    backend="redis://localhost:6379/1",
    models={
        "app.models.User":    {"ops": {"get", "fetch"}, "timeout": 900},
        "app.models.Product": {"ops": "all",            "timeout": 3600},
        "*":                  {"timeout": 3600},
    },
)
await sqlacache.bind(engine)
```

That's all the wiring needed. From here, your sessions work as normal.

---

## Usage

### Reads are cached automatically

```python
async with AsyncSession(engine) as session:
    user = await session.get(User, 42)           # cache miss → fetches from DB, stores result
    user = await session.get(User, 42)           # cache hit → returned instantly

    result = await session.execute(select(User).where(User.active == True))
    users = result.scalars().all()               # multi-row fetch, also cached
```

### Writes invalidate the cache automatically

```python
async with AsyncSession(engine) as session:
    user = await session.get(User, 42)
    user.name = "Alice"
    await session.commit()     # evicts all cached reads that touched User id=42
```

No decorators. No manual cache keys. No changes to how you write queries.

---

## FastAPI Example

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlacache import configure

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/mydb")
session_maker = async_sessionmaker(engine, expire_on_commit=False)

sqlacache = configure(
    backend="redis://localhost:6379/1",
    models={"app.models.User": {"ops": "all", "timeout": 300}},
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await sqlacache.bind(engine)
    yield
    await sqlacache.disconnect()


app = FastAPI(lifespan=lifespan)


async def get_session():
    async with session_maker() as session:
        yield session


@app.get("/users/{user_id}")
async def get_user(user_id: int, session: AsyncSession = Depends(get_session)):
    return await session.get(User, user_id)   # cached after the first request
```

---

## Configuration Reference

### `configure()`

```python
sqlacache = configure(
    backend="redis://localhost:6379/1",   # or "mem://" for in-memory
    models={...},
    prefix="sqlacache",       # key prefix (default: "sqlacache")
    default_timeout=3600,     # TTL when not specified per model (default: 3600)
    serializer="sqlalchemy",  # cashews serializer (default: "sqlalchemy")
)
await sqlacache.bind(engine)
```

### Model config

```python
models={
    "app.models.User": {
        "ops": {"get", "fetch"},   # operations to cache
        "timeout": 900,            # TTL in seconds
    },
    "app.models.Product": {"ops": "all", "timeout": 3600},
    "*": {"timeout": 3600},        # wildcard fallback
}
```

**Ops:**

| Op | What it covers |
| --- | --- |
| `"get"` | `session.get(Model, pk)` |
| `"fetch"` | `session.execute(select(Model))` |
| `"count"` | `select(func.count())` queries |
| `"exists"` | `select(exists(...))` queries |
| `"all"` | All four above |

### Backends

| Backend | URL | Notes |
| --- | --- | --- |
| Redis | `redis://host:port/db` | Production; enables cross-process invalidation |
| In-memory | `mem://` | Dev and testing; no infrastructure needed |

---

## Manual Control

You rarely need these — sqlacache handles everything through the session automatically. They're available for edge cases:

```python
# Cache a statement explicitly
result = await sqlacache.execute(session, select(User).where(User.active == True), timeout=300)

# Invalidate specific rows
await sqlacache.invalidate(User, pks=[42, 99])

# Invalidate all cached reads for a model
await sqlacache.invalidate(User)

# Flush everything
await sqlacache.invalidate_all()
```

---

## How It Works

```text
session.get(User, 42)
    │
    ├── cache HIT  → return immediately
    └── cache MISS → execute SQL → tag result as "users:42" → return

session.commit()  [User id=42 changed]
    │
    ├── during flush: mapper events record pending invalidation on the session
    │                 (transaction might still roll back, so nothing is evicted yet)
    │
    └── after_commit event → delete tag "users:42" → all reads that touched that row are evicted
          └── (Redis) → pub/sub → other workers evict their copies too

session.rollback()
    │
    └── after_rollback event → discard pending invalidations (cache stays intact)
```

- Cache keys are a hash of the compiled SQL + bound parameters + a per-table version counter.
- Tags (`"{tablename}:{pk}"`) let cashews atomically evict all keys that depended on a row.
- Bulk `UPDATE ... WHERE ...` bumps a table-level version so all cached queries for that model go stale.
- Invalidation is deferred to commit time: rolled-back transactions never evict the cache.

---

## Caveats

- **Async only.** Sync `Session` is not supported yet (planned for v0.2.0).
- **Bulk writes use table-level invalidation.** `session.execute(update(Model).where(...))` evicts all cached reads for that model, not just the affected rows.
- **Eager-loaded relationships bypass the cache.** Statements using `selectinload` / `joinedload` / `subqueryload` / `immediateload` are not cached at all — they go straight to the database. sqlacache does not track related rows as dependencies, so caching a joined result could silently return stale data when a related row changes. Bypassing is safer. You'll see a `WARNING` on the `sqlacache.interceptor` logger each time this happens. If you want to cache, load the related data in a separate query.
- **Invalidation runs after commit.** Mapper events during flush only *record* pending invalidations; the cache is evicted in `after_commit`. This means rolled-back transactions correctly leave the cache untouched, but also that invalidation is scheduled on the event loop rather than awaited inline. For strict read-after-write on the same session (e.g. in tests), call `await cache_manager.flush_pending()` after `session.commit()`.
- **Raw SQL is not intercepted.** `text(...)` and `engine.execute()` bypass sqlacache entirely.

---

## Development

```bash
git clone https://github.com/hr-samsami/sqlacache
cd sqlacache
uv sync --extra redis --group dev

make test          # unit tests (no infrastructure needed)
make lint          # ruff
make format        # ruff format
make typecheck     # mypy

docker-compose up -d
make integration   # Redis + Postgres integration tests
```

---

## License

[MIT](LICENSE)
