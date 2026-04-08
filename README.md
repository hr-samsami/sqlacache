# sqlacache

*Django-cacheops-style declarative caching for SQLAlchemy, with automatic row-level invalidation.*

[![Tests](https://github.com/hr-samsami/sqlacache/actions/workflows/ci.yml/badge.svg)](https://github.com/hr-samsami/sqlacache/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/sqlacache)](https://pypi.org/project/sqlacache/)
[![Python](https://img.shields.io/pypi/pyversions/sqlacache)](https://pypi.org/project/sqlacache/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

**sqlacache** automatically caches your SQLAlchemy ORM reads and invalidates them — at the row level — the moment a write happens. No decorators, no manual cache keys, no stale data.

Built on top of [`cashews`](https://github.com/Krukov/cashews) for storage and tag-based dependency tracking.

**Key features:**

- **Zero code changes to reads** — `await session.get(User, 1)` is cached automatically once you call `cache.bind(engine)`
- **Row-level invalidation** — only the specific rows that changed are invalidated, not the whole table
- **Cross-process invalidation** — Redis pub/sub propagates invalidations to every worker instantly
- **Declarative config** — map models to ops and TTLs in one place, with wildcard fallback
- **Two backends** — `redis://` for production, `mem://` for dev and testing (no infrastructure needed)
- **Async-first** — built for FastAPI + async SQLAlchemy; sync support coming in v0.2.0

---

## Requirements

- Python 3.10+
- SQLAlchemy >= 1.4
- cashews >= 7.0
- Redis (for production; not needed for `mem://` backend)

---

## Installation

```bash
pip install sqlacache
```

With Redis support:

```bash
pip install "sqlacache[redis]"
```

Using `uv`:

```bash
uv add sqlacache
uv add "sqlacache[redis]"   # with Redis support
```

---

## Quick Start

### 1. Configure the cache

Call `configure()` once at startup — typically in the same place you create your engine.

```python
from sqlacache import configure

cache = configure(
    backend="redis://localhost:6379/1",
    models={
        "app.models.User":    {"ops": {"get", "fetch"}, "timeout": 900},
        "app.models.Product": {"ops": "all",            "timeout": 3600},
        "*":                  {"timeout": 3600},   # wildcard fallback for any other model
    },
)
```

### 2. Bind to your async engine

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/mydb")

await cache.bind(engine)
```

### 3. Use SQLAlchemy normally — reads are cached automatically

```python
from sqlalchemy import select

async with AsyncSession(engine) as session:
    # Cached automatically — hits the cache on the second call
    user = await session.get(User, 42)

    # Multi-row fetches are also cached
    result = await session.execute(select(User).where(User.active == True))
    users = result.scalars().all()
```

### 4. Writes invalidate the cache automatically

```python
async with AsyncSession(engine) as session:
    user = await session.get(User, 42)
    user.name = "Alice"
    await session.commit()   # cache entry for User id=42 is invalidated automatically
```

That's it. No decorators, no manual `cache.set()` or `cache.delete()`.

---

## FastAPI Example

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlacache import configure

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/mydb")
session_maker = async_sessionmaker(engine, expire_on_commit=False)

cache = configure(
    backend="redis://localhost:6379/1",
    models={
        "app.models.User": {"ops": "all", "timeout": 300},
    },
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache.bind(engine)
    yield
    await cache.disconnect()


app = FastAPI(lifespan=lifespan)


async def get_session():
    async with session_maker() as session:
        yield session


@app.get("/users/{user_id}")
async def get_user(user_id: int, session: AsyncSession = Depends(get_session)):
    return await session.get(User, user_id)  # served from cache after first request
```

---

## Configuration Reference

### `configure()`

```python
cache = configure(
    backend="redis://localhost:6379/1",  # or "mem://" for in-memory
    models={...},
    prefix="sqlacache",        # cache key prefix (default: "sqlacache")
    default_timeout=3600,      # TTL in seconds when not specified per model (default: 3600)
    serializer="sqlalchemy",   # cashews serializer (default: "sqlalchemy")
)
```

### Model configuration

```python
models={
    # Full dotted path to the model class
    "app.models.User": {
        "ops": {"get", "fetch"},   # which operations to cache
        "timeout": 900,            # TTL in seconds
    },

    # "all" is shorthand for {"get", "fetch", "count", "exists"}
    "app.models.Product": {"ops": "all", "timeout": 3600},

    # Wildcard applies to any model not explicitly listed
    "*": {"timeout": 3600},
}
```

**Supported ops:**

| Op | Description |
| --- | --- |
| `"get"` | Single-row lookup (`session.get(Model, pk)`) |
| `"fetch"` | Multi-row select (`session.execute(select(Model))`) |
| `"count"` | `select(func.count())` queries |
| `"exists"` | `select(exists(...))` queries |
| `"all"` | Shorthand for all four ops above |

### Backends

| Backend | URL | Notes |
| --- | --- | --- |
| Redis | `redis://host:port/db` | Production; required for cross-process invalidation |
| In-memory | `mem://` | Dev and testing; no infrastructure needed |

---

## Manual Cache Control

For cases where you need explicit control:

```python
# Execute and cache a statement manually (bypasses automatic interception)
result = await cache.execute(session, select(User).where(User.active == True), timeout=300)

# Invalidate specific rows by PK
await cache.invalidate(User, pks=[42, 99])

# Invalidate all rows of a model (bumps table version, all queries re-fetch)
await cache.invalidate(User)

# Flush the entire cache
await cache.invalidate_all()
```

---

## How It Works

```text
Your Code
    │
    ▼
session.get(User, 42)          ← intercepted by sqlacache
    │
    ├── cache HIT  → return cached result immediately
    │
    └── cache MISS → execute SQL → store result with tag "users:42" → return result


session.commit() with User(id=42) changed
    │
    ▼
after_update event → invalidate tag "users:42" → all queries that read row 42 are cleared
    │
    └── (Redis backend) → pub/sub message → all other workers also clear their copies
```

- **Cache keys** are a hash of the compiled SQL statement + bound parameters + a per-table version counter.
- **Tags** are formatted as `"{tablename}:{pk}"` — cashews tracks which keys depend on which tags and deletes them atomically.
- **Bulk mutations** (`UPDATE ... WHERE ...`) bump a table-level version counter so all cached queries for that table become stale.

---

## Known Limitations (v0.1.0)

- **Async only** — sync `Session` support is deferred to v0.2.0
- **Bulk operations** — `session.execute(update(Model).where(...))` triggers table-level invalidation (all cached queries for that model), not row-level
- **Eager loading** — `joinedload` / `selectinload` relationships are not tracked; if a related row changes, queries that eager-loaded it won't be invalidated
- **Raw SQL** — queries via `text(...)` or `engine.execute()` are not intercepted

---

## Development

```bash
# Clone and install with all dev dependencies
git clone https://github.com/hr-samsami/sqlacache
cd sqlacache
uv sync --extra redis --group dev

# Run unit tests (no infrastructure needed)
make test

# Run linter and formatter
make lint
make format

# Run type checker
make typecheck

# Start Redis and Postgres for integration tests
docker-compose up -d
make integration
```

---

## License

[MIT](LICENSE)
