# sqlacache — Architecture & Design Document

> **Status note**: This document is the broader architecture and roadmap reference. It includes planned and exploratory work that is not fully implemented in `0.1.0`. For current supported behavior, treat `README.md` as authoritative.

> **Purpose**: This document captures the full architectural decisions and API design for `sqlacache`, an open-source Python library that brings django-cacheops-style declarative caching with automatic row-level invalidation to SQLAlchemy. It is intended to be used as a reference/prompt for LLMs or developers to continue implementation.

---

## 1. Problem Statement

Django has `django-cacheops` — a brilliant library that provides declarative, ORM-aware caching with automatic invalidation. But outside Django (especially in the broader FastAPI, Flask, Starlette, and general SQLAlchemy ecosystems), there is **nothing comparable**. Developers are left stitching together `aiocache`, manual cache keys, and hand-rolled invalidation logic.

### Existing tools and their gaps

| Library | Stars | Async | ORM Awareness | Auto-Invalidation | Production Ready | Why Not Sufficient |
|---|---|---|---|---|---|---|
| `cashews` | ~569 | ✅ Yes | ❌ None | ❌ (manual only) | ✅ Yes | Rich async cache framework with tag invalidation, client-side caching, transactions — but zero SQLAlchemy query interception or ORM-aware invalidation |
| `dogpile.cache` | ~290 | ❌ No (open issue since 2021) | ❌ None | ❌ No | ✅ Yes | SQLAlchemy-org maintained, but sync-only, no auto-invalidation, requires manual key management |
| `aiocache` | ~1.2k | ✅ Yes | ❌ None | ❌ No | ⚠️ Moderate (slow releases) | General-purpose async cache, no ORM awareness at all |
| `ECache` | Very low | ❌ No | ⚠️ Partial (CacheMixin) | ❌ No (TTL only) | ❌ Abandoned | Closest concept to sqlacache — `User.cache.get(id)` API — but sync-only, Flask-coupled, legacy SA patterns, unmaintained |
| `cacheme` | Very low | ✅ Yes | ❌ None | ❌ No | ❌ Niche | Interesting node-based cache with invalidation graphs, but not SQLAlchemy-specific |
| `django-cacheops` | ~2.1k | ❌ No | ✅ Full (Django ORM) | ✅ Full | ✅ Yes | The gold standard — but Django-only, tightly coupled to Django ORM, cannot be used with SQLAlchemy |

### Our unique position

**ORM-aware, async-first, framework-agnostic caching with automatic row-level invalidation for SQLAlchemy**, built on top of `cashews` as the cache transport layer.

### Why `cashews` as the foundation

After evaluating the ecosystem, `cashews` is the best fit as sqlacache's underlying cache engine:

- **Async-first** — matches our async-first architecture, no need for sync→async adapters
- **Tag-based invalidation** — native tag system with Redis SETs, directly maps to our dependency tracking needs
- **Client-side caching** — built-in Redis 6+ client-side cache support (10x faster reads) — a feature we get for free
- **Transactions** — `cache.transaction()` with multiple isolation modes aligns with our DB transaction consistency goals
- **Multiple backends** — `mem://`, `redis://`, `disk://` out of the box, with DiskCache as a bonus for local dev
- **Production-proven** — actively maintained (v7.5.0, Mar 2026), ~569 stars, used by 383+ projects
- **SQLAlchemy serializer** — already has `pickle_type="sqlalchemy"` for ORM object serialization
- **Middleware system** — extensible middleware pipeline for logging, metrics, Prometheus integration

What cashews does **not** provide (and what sqlacache adds on top):
- No `do_orm_execute` query interception
- No automatic cache key generation from SQL statements
- No row-level dependency tracking (reverse index)
- No automatic invalidation on ORM write events
- No declarative model-to-ops configuration

---

## 2. Core Design Principles

1. **Django-Cacheops style declarative config** — model-to-ops mapping, not scattered decorators
2. **Row-level invalidation by default** — table-level is a fallback, not the primary strategy
3. **Async-first, sync-compatible** — internal async core with sync wrappers
4. **Cashews-powered backend** — delegate cache storage, serialization, and transport to `cashews`; sqlacache owns ORM interception + invalidation logic
5. **Zero framework coupling** — works with FastAPI, Flask, Starlette, bare SQLAlchemy, anything
6. **Minimal API surface** — `configure()`, `bind()`, `cache.execute()`, `cache.invalidate()`

---

## 3. Configuration Design

Inspired directly by django-cacheops. The configuration separates **what** to cache from **how** to cache.

### 3.1 Backend Configuration

Backend configuration uses `cashews` URL format directly. Any valid cashews setup string works.

```python
# Simple URL string — passed directly to cashews.setup() (preferred for most cases)
SQLACACHE_BACKEND = "redis://localhost:6379/1"

# URL variants (all native cashews formats)
SQLACACHE_BACKEND = "redis://:password@localhost:6379/1"
SQLACACHE_BACKEND = "mem://?size=10000"                                  # in-memory (LRU, great for dev/testing)
SQLACACHE_BACKEND = "disk://?directory=/tmp/sqlacache&shards=0"          # DiskCache (local SQLite, no Redis needed)
SQLACACHE_BACKEND = "redis://0.0.0.0/?client_side=true&secret=my_key"   # Redis with client-side caching (10x faster)
SQLACACHE_BACKEND = "rediss://0.0.0.0/"                                 # Redis over SSL

# Redis Cluster
SQLACACHE_BACKEND = "redis://0.0.0.0:6379/?cluster=true"

# Dict form (full control — kwargs passed to cashews.setup())
SQLACACHE_BACKEND = {
    "url": "redis://localhost:6379/1",
    "db": 1,
    "suppress": False,             # raise on connection errors instead of swallowing
    "secret": "my_secret",         # hash salt for pickle security
    "client_side": True,           # enable Redis 6+ client-side caching
    "client_side_prefix": "sqla:", # prefix for client-side cache keys
    "pickle_type": "sqlalchemy",   # use SA's built-in serializer for ORM objects
    "compress_type": "gzip",       # compress cached values
}

# PostgreSQL backend (UNLOGGED table — no cashews, direct implementation)
SQLACACHE_BACKEND = "postgresql+asyncpg://user:pass@localhost/cache_db"
```

> **Note:** Redis and in-memory backends are powered by `cashews`. The PostgreSQL UNLOGGED table backend is a custom implementation since `cashews` does not support PostgreSQL as a cache store.

### 3.2 Model-to-Ops Mapping

```python
SQLACACHE_MODELS = {
    # Auto-cache get and fetch queries on User for 15 minutes
    "myapp.models.User": {"ops": {"get", "fetch"}, "timeout": 60 * 15},

    # Cache all query types on Product for 1 hour
    "myapp.models.Product": {"ops": "all", "timeout": 60 * 60},

    # Only allow manual caching on Order (no auto-cache, but invalidation still works)
    "myapp.models.Order": {"ops": (), "timeout": 60 * 30},

    # Wildcard — default for all unspecified models
    "*": {"timeout": 60 * 60},

    # Explicitly disable caching (even manual) for a model
    "myapp.models.AuditLog": None,
}
```

### 3.3 Ops Definitions

| Op | SQLAlchemy equivalent |
|---|---|
| `"get"` | `session.get(Model, pk)`, `.filter_by(id=X).first()`, `.first()`, `.one()` |
| `"fetch"` | `.all()`, `.scalars().all()`, any query returning multiple rows |
| `"count"` | `.count()`, `select(func.count(...))` |
| `"exists"` | `.exists()` |
| `"all"` | Shorthand alias for `{"get", "fetch", "count", "exists"}` |

### 3.4 Additional Configuration Options

```python
# Serialization format (maps to cashews pickle_type)
SQLACACHE_SERIALIZER = "sqlalchemy"  # "sqlalchemy" (default) | "pickle" | "dill" | "json"

# Invalidation strategy (row-level is default and recommended)
SQLACACHE_INVALIDATION = "row"  # "row" | "table"

# Key prefix (for shared Redis instances — passed to cashews as prefix)
SQLACACHE_PREFIX = "sqlacache"

# Default TTL if not specified per-model
SQLACACHE_DEFAULT_TIMEOUT = 3600

# Compression (passed to cashews compress_type)
SQLACACHE_COMPRESS = None  # None | "gzip" | "zlib"

# Enable cashews client-side caching (Redis 6+ server-assisted tracking)
SQLACACHE_CLIENT_SIDE = False  # True for 10x faster reads in multi-get scenarios
```

---

## 4. Public API Design

### 4.1 Initialization

```python
from sqlacache import CacheManager, configure

# Option A: configure() factory (recommended)
cache = configure(
    backend="redis://localhost:6379/1",  # any valid cashews URL
    models={
        "app.models.User": {"ops": {"get", "fetch"}, "timeout": 900},
        "app.models.Product": {"ops": "all", "timeout": 3600},
        "*": {"timeout": 3600},
    },
    serializer="sqlalchemy",  # cashews pickle_type (default)
    invalidation="row",
)

# Option B: configure with cashews-specific options
cache = configure(
    backend={
        "url": "redis://localhost:6379/1",
        "client_side": True,           # enable 10x faster client-side caching
        "secret": "my_secret",         # pickle hash salt
        "compress_type": "gzip",       # compress cached values
    },
    models={...},
)

# Option C: configure with in-memory backend (great for dev/testing)
cache = configure(
    backend="mem://?size=10000",
    models={...},
)

# Option D: CacheManager class directly
cache = CacheManager(config=my_config_dict)

# Bind to SQLAlchemy engine — this hooks all ORM events
cache.bind(async_engine)   # for async
cache.bind(sync_engine)    # for sync
```

### 4.2 Automatic Caching (Zero Code Changes)

Once `cache.bind(engine)` is called, queries matching the configured ops are **automatically cached and invalidated**:

```python
# Async — auto-cached because User has "get" in ops
async def get_user(session: AsyncSession, user_id: int):
    return await session.get(User, user_id)

# Async — auto-cached because Product has "fetch" in ops
async def list_products(session: AsyncSession):
    result = await session.execute(select(Product).where(Product.active == True))
    return result.scalars().all()

# Sync — same behavior, no code difference
def get_user_sync(session: Session, user_id: int):
    return session.get(User, user_id)
```

### 4.3 Manual Caching

For models with `ops=()` or for custom queries:

```python
# Explicit cache call — similar to cacheops' .cache() on querysets
async def get_recent_orders(session: AsyncSession):
    stmt = select(Order).order_by(Order.created_at.desc()).limit(10)
    result = await cache.execute(session, stmt, timeout=300)
    return result.scalars().all()
```

### 4.4 Manual Invalidation

For edge cases, raw SQL, or external data changes:

```python
# Invalidate specific rows
await cache.invalidate(model=User, pks=[42, 55])

# Invalidate entire table
await cache.invalidate(model=Product)

# Invalidate everything
await cache.invalidate_all()
```

---

## 5. Architecture

### 5.1 Component Overview

```
┌─────────────────────────────────────────────┐
│              Application Code               │
│         (FastAPI, Flask, scripts)            │
└──────────┬──────────┬──────────┬────────────┘
           │          │          │
     ┌─────▼──┐  ┌────▼────┐  ┌─▼──────────────┐
     │ Config │  │  Cache   │  │    Query        │
     │ Module │  │ Manager  │  │  Interceptor    │
     └────────┘  └────┬─────┘  └──┬──────────────┘
                      │           │
              ┌───────▼───────────▼──────┐
              │   Invalidation Engine    │
              │  (row-level tracking +   │
              │   reverse index)         │
              └───────────┬──────────────┘
                          │
              ┌───────────▼──────────────┐
              │   Cashews Adapter        │
              │  (tag-based deps,        │
              │   transactions,          │
              │   client-side cache)     │
              └───────────┬──────────────┘
                          │
              ┌───────────▼──────────────┐
              │   cashews.Cache          │
              │  (transport layer)       │
              └─────┬─────────────┬──────┘
                    │             │
             ┌──────▼──┐   ┌─────▼────────┐
             │  Redis  │   │  In-Memory /  │
             │         │   │  DiskCache    │
             └─────────┘   └──────────────┘
                    │
         ┌──────────▼─────────────┐
         │  Cross-Process Pubsub  │
         │  (Redis pub/sub or     │
         │   PG LISTEN/NOTIFY)    │
         └────────────────────────┘
```

> **Key insight:** sqlacache does NOT implement cache storage, serialization, or connection management. It delegates all of that to `cashews` and focuses entirely on the ORM-aware layer: query interception, dependency tracking, and automatic invalidation.

### 5.2 Query Interceptor

Hooks into SQLAlchemy's `do_orm_execute` event (available since SQLAlchemy 1.4). This single event covers **both sync and async sessions**.

**Flow:**
1. Intercept the query via `do_orm_execute`
2. Extract the target model(s) from the statement's `FROM` clause
3. Determine the operation type (`get`, `fetch`, `count`, `exists`)
4. Check if auto-caching is enabled for this model + op combo
5. Generate a cache key from the query (statement hash + bound parameters)
6. Check cache → if hit, return cached result (skip DB execution)
7. If miss → execute query, cache the result, record PK dependencies

```python
from sqlalchemy import event

@event.listens_for(Session, "do_orm_execute")
def intercept_query(execute_state):
    # 1. Extract model from statement
    model = extract_model_from_statement(execute_state.statement)

    # 2. Check config
    config = get_model_config(model)
    if config is None or op_type not in config["ops"]:
        return  # not cached, execute normally

    # 3. Check cache (via cashews)
    cache_key = generate_cache_key(execute_state.statement, execute_state.parameters)
    cached = await cache_store.get(cache_key)
    if cached:
        return deserialize(cached)  # cache hit

    # 4. Execute + cache + track dependencies via cashews tags
    result = execute_state.invoke_statement()
    pks = extract_pks(result, model)
    tags = [f"{model.__tablename__}:{pk}" for pk in pks]
    await cache_store.set(
        cache_key,
        serialize(result),
        expire=config["timeout"],
        tags=tags,  # cashews native tag system for dependency tracking
    )
    return result
```

### 5.3 Invalidation Engine (Row-Level)

This is the core differentiator. When a write happens, we invalidate **only the cache entries that contained the affected rows**.

#### Leveraging Cashews Tags

Instead of manually maintaining Redis SETs for dependency tracking, sqlacache uses cashews' **native tag system**. Tags are stored in separate Redis SETs automatically by cashews, and `cache.delete_tags()` provides atomic invalidation of all keys associated with a tag.

```
# How cashews stores it internally:
#
# Forward mapping (managed by cashews):
# sqlacache:cache:{query_hash} → serialized bytes (with TTL)
#
# Tag sets (managed by cashews):
# tag:users:42 → SET of cache keys containing this row
# tag:users:55 → SET of cache keys containing this row
#
# Example flow:
# cache.set("sqlacache:cache:a3f8b2c1", data, tags=["users:42", "users:55"])
#   → cashews auto-creates tag:users:42 → {"sqlacache:cache:a3f8b2c1"}
#   → cashews auto-creates tag:users:55 → {"sqlacache:cache:a3f8b2c1"}
#
# cache.delete_tags("users:42")
#   → cashews deletes all cache keys in that tag set → done
```

#### Recording Dependencies (on cache write)

Dependencies are recorded implicitly via cashews tags — no manual Redis pipeline code needed:

```python
async def cache_query_result(cache_store, cache_key: str, result, models_pks: dict, ttl: int):
    """Cache a query result with automatic dependency tracking via tags."""
    tags = []
    for model, pks in models_pks.items():
        table_name = model.__tablename__
        for pk in pks:
            tags.append(f"{table_name}:{pk}")

    await cache_store.set(cache_key, serialize(result), expire=ttl, tags=tags)
```

#### Invalidation (on write events)

```python
from sqlalchemy import event

@event.listens_for(SomeModel, "after_insert")
@event.listens_for(SomeModel, "after_update")
@event.listens_for(SomeModel, "after_delete")
async def invalidate_on_write(mapper, connection, target):
    table_name = target.__tablename__
    pk = inspect(target).identity
    tag = f"{table_name}:{pk}"

    # Single call — cashews handles finding + deleting all affected cache keys
    await cache_store.delete_tags(tag)

    # Publish invalidation event for cross-process (if client-side caching is enabled)
    await cache_store.publish("sqlacache:invalidate", f"{table_name}:{pk}")
```

> **Advantage over raw Redis:** The previous design required manual `SADD`/`SMEMBERS`/`DELETE` pipelines. Cashews tags encapsulate this entirely — we write tags on `set()`, call `delete_tags()` on invalidation, and cashews handles the Redis SET operations internally. This also means tag-based invalidation works with cashews' client-side cache, so local in-process caches are invalidated too.

### 5.4 SQLAlchemy Query Support Matrix

| Query Pattern | Row-Level Invalidation | Notes |
|---|---|---|
| `session.get(User, 42)` | ✅ Full support | PK known from args |
| `select(User).where(User.id == 42)` | ✅ Full support | PK extracted from result |
| `select(User).where(User.active == True).all()` | ✅ Full support | PKs extracted from result rows |
| `select(User.name, User.email).where(...)` | ✅ With PK injection | Library auto-adds PK column to query, strips from result |
| `select(User, Order).join(Order)` | ✅ Multi-table tracking | Track PKs for BOTH User and Order |
| `session.execute(text("SELECT ..."))` | ❌ Not supported | Fall back to manual invalidation |

### 5.5 PK Extraction Strategy

```python
def extract_pks(result, models: list[type]) -> dict[type, list]:
    """Extract primary keys from query results, grouped by model."""
    pks = {}
    for row in result:
        for model in models:
            if isinstance(row, model):
                pk = inspect(row).identity
                pks.setdefault(model, []).append(pk)
            elif hasattr(row, '_mapping'):
                # Column-level query — look for PK column
                pk_col = inspect(model).primary_key[0].name
                if pk_col in row._mapping:
                    pks.setdefault(model, []).append(row._mapping[pk_col])
    return pks
```

For **column-level queries** (case 3 in the support matrix), the library will inject the PK column into the SELECT:

```python
# User writes:
stmt = select(User.name, User.email).where(User.active == True)

# Library rewrites to:
stmt = select(User.id, User.name, User.email).where(User.active == True)

# After execution, strip PK from returned rows before caching result
```

---

## 6. Backend Abstraction

### 6.1 Cashews as the Primary Backend

Instead of implementing our own cache backends, sqlacache wraps `cashews.Cache` and exposes a thin adapter for the features cashews doesn't provide (cross-process invalidation messaging, PostgreSQL UNLOGGED table backend).

```python
from cashews import Cache
from typing import Protocol, Optional, Callable

class CacheTransport(Protocol):
    """Minimal protocol for the cache transport layer."""

    # Core cache operations (delegated to cashews)
    async def get(self, key: str) -> Optional[bytes]: ...
    async def set(self, key: str, value: bytes, expire: int, tags: list[str] = None) -> None: ...
    async def delete(self, *keys: str) -> None: ...
    async def delete_tags(self, *tags: str) -> None: ...

    # Cross-process invalidation (sqlacache-specific)
    async def publish(self, channel: str, message: str) -> None: ...
    async def subscribe(self, channel: str, callback: Callable) -> None: ...

    # Lifecycle
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...


class CashewsTransport:
    """Primary transport — wraps cashews.Cache for all storage operations."""

    def __init__(self, url: str, **kwargs):
        self._cache = Cache()
        self._url = url
        self._kwargs = kwargs

    async def connect(self):
        self._cache.setup(self._url, **self._kwargs)

    async def get(self, key: str) -> Optional[bytes]:
        return await self._cache.get(key)

    async def set(self, key: str, value: bytes, expire: int, tags: list[str] = None):
        await self._cache.set(key, value, expire=expire, tags=tags)

    async def delete(self, *keys: str):
        for key in keys:
            await self._cache.delete(key)

    async def delete_tags(self, *tags: str):
        await self._cache.delete_tags(*tags)

    async def disconnect(self):
        await self._cache.close()
```

### 6.2 Redis Backend (via cashews)

Primary backend. All Redis operations are handled by cashews internally.

- Cache storage: cashews `get`/`set` with TTL
- Dependency tracking: cashews **tag system** (Redis SETs under the hood)
- Client-side caching: cashews `client_side=True` (Redis 6+ tracking, 10x faster reads)
- Serialization: cashews `pickle_type="sqlalchemy"` for ORM objects, or custom serializers
- Compression: cashews `compress_type="gzip"` or `"zlib"`
- Supports: standalone, sentinel (`redis-py` sentinel), cluster (`cluster=True`)
- Connection error handling: cashews `suppress=True/False`
- Security: cashews `secret` param for pickle hash salting
- Cross-process invalidation: Redis `PUBLISH`/`SUBSCRIBE` (thin layer on top of cashews' Redis client)

```python
# Example: full-featured Redis setup via cashews
cache_transport = CashewsTransport(
    url="redis://localhost:6379/1",
    client_side=True,              # enable client-side cache
    client_side_prefix="sqla:",
    pickle_type="sqlalchemy",      # SA-aware serialization
    secret="my_secret_key",        # hash salt
    compress_type="gzip",          # compress values
    suppress=False,                # raise on connection errors
)
```

### 6.3 In-Memory Backend (via cashews)

Uses cashews' built-in LRU in-memory cache. Useful for development, testing, and single-process deployments.

```python
cache_transport = CashewsTransport(url="mem://?size=10000&check_interval=10")
```

- Fixed-size LRU with TTL expiration
- Tags supported (in-memory sets)
- No cross-process invalidation (single process only)
- Great for unit tests with zero infrastructure

### 6.4 DiskCache Backend (via cashews)

Uses cashews' DiskCache backend (local SQLite with sharding). Good for local development or when Redis isn't available.

```python
cache_transport = CashewsTransport(url="disk://?directory=/tmp/sqlacache&shards=0")
```

- Persistent across restarts
- No cross-process invalidation
- `cache.scan` and `cache.get_match` only work with shards disabled

### 6.5 PostgreSQL Backend (custom implementation — v0.3+)

> **Status:** Deferred to v0.3.0. The tag invalidation layer must be re-implemented before this backend is viable. Do not let this backend influence core architecture decisions.

Uses an `UNLOGGED` table for cache storage (no WAL = faster writes, acceptable durability tradeoff for cache data). This is the **only backend not powered by cashews**, since cashews has no PostgreSQL support.

```sql
CREATE UNLOGGED TABLE sqlacache_store (
    key TEXT PRIMARY KEY,
    value BYTEA NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    model TEXT,           -- for dependency lookups
    pks TEXT[]            -- array of PKs this entry depends on
);

-- Tag-to-key reverse index (replaces cashews' Redis SET-based tag system)
CREATE UNLOGGED TABLE sqlacache_tags (
    tag TEXT NOT NULL,
    cache_key TEXT NOT NULL REFERENCES sqlacache_store(key) ON DELETE CASCADE,
    PRIMARY KEY (tag, cache_key)
);

CREATE INDEX idx_sqlacache_tag ON sqlacache_tags (tag);
CREATE INDEX idx_sqlacache_expires ON sqlacache_store (expires_at);
```

- Cross-process invalidation: PostgreSQL `LISTEN`/`NOTIFY`
- Cleanup: periodic `DELETE FROM sqlacache_store WHERE expires_at < NOW()` (requires background job or pg_cron — no native TTL like Redis)
- Advantage: no extra infrastructure if you already run PostgreSQL

**Known tradeoffs vs Redis:**

| Concern | Impact |
| --- | --- |
| Tag invalidation | Must re-implement cashews' Redis SET logic manually in SQL — `sqlacache_tags` table acts as the reverse index |
| Connection pool contention | Cache reads/writes share the app's Postgres connection pool; cache load competes with real queries |
| Latency | ~1–5ms per cache op (SQL round-trip) vs ~0.1–0.5ms for Redis; full query planner overhead on every get |
| TTL management | No native key expiry — requires a periodic cleanup job; forgotten cleanup = unbounded table growth |
| Bulk invalidation | `DELETE FROM sqlacache_tags WHERE tag = $1` + cascade, but no equivalent of cashews' atomic `delete_tags()` pipeline |
| Cross-process tag safety | Concurrent `DELETE` + `INSERT` on `sqlacache_tags` requires careful locking to avoid partial invalidation |

**When to choose this backend:** Deployments that run PostgreSQL and explicitly cannot add Redis infrastructure, and where cache latency and throughput are not primary concerns (e.g., low-traffic internal tooling).

---

## 7. Cross-Process Invalidation

In production, multiple workers handle requests. A write in Worker A must invalidate cache in Worker B.

### Redis (pub/sub + cashews client-side cache)

When using cashews with `client_side=True`, each worker maintains a local in-process cache that mirrors Redis. Cross-process invalidation must clear both the Redis cache and all workers' local caches.

```python
# On invalidation (after ORM write event):
# 1. cashews.delete_tags() clears Redis keys (handled automatically)
# 2. Publish event so other workers clear their client-side caches
await cache_store.publish("sqlacache:invalidate", json.dumps({
    "table": "users",
    "pks": [42],
    "action": "update"
}))

# Every worker subscribes on startup:
async def listen_for_invalidations():
    pubsub = redis.pubsub()
    await pubsub.subscribe("sqlacache:invalidate")
    async for message in pubsub.listen():
        if message["type"] == "message":
            event = json.loads(message["data"])
            # Invalidate local client-side cache
            tags = [f"{event['table']}:{pk}" for pk in event["pks"]]
            await cache_store.delete_tags(*tags)
```

> **Note:** If cashews' `client_side=True` is enabled, Redis 6+ server-assisted client caching handles most of this automatically via the Redis RESP3 invalidation protocol. The pub/sub layer is a fallback for cases where server-assisted tracking doesn't cover all keys (e.g., tag sets).

### PostgreSQL (LISTEN/NOTIFY) — v0.3+

```python
await connection.execute("LISTEN sqlacache_invalidate")
# On notification:
await connection.execute(
    f"NOTIFY sqlacache_invalidate, '{json.dumps(event)}'"
)
```

---

## 8. Sync + Async Strategy

The library is **async-first internally**, aligning with cashews' async-native design. A sync wrapper layer is provided for traditional SQLAlchemy usage.

```python
# Internal: all core logic is async, delegating to cashews (which is also async-first)
class AsyncCacheManager:
    def __init__(self, transport: CashewsTransport):
        self._transport = transport

    async def get(self, key: str) -> Optional[Any]:
        return await self._transport.get(key)

    async def set(self, key: str, value: Any, expire: int, tags: list[str] = None):
        await self._transport.set(key, value, expire=expire, tags=tags)

    async def invalidate(self, model: type, pks: list):
        tags = [f"{model.__tablename__}:{pk}" for pk in pks]
        await self._transport.delete_tags(*tags)

# Sync wrapper: runs async code in an event loop
class SyncCacheManager:
    def __init__(self, async_manager: AsyncCacheManager):
        self._async = async_manager
        self._loop = asyncio.new_event_loop()

    def get(self, key: str) -> Optional[Any]:
        return self._loop.run_until_complete(self._async.get(key))

# Auto-detection on bind():
def bind(self, engine):
    if isinstance(engine, AsyncEngine):
        # use async event hooks
    else:
        # use sync event hooks with sync wrapper
```

This mirrors the approach used by `httpx` (async core, sync client wrapping it). Since cashews itself is async-first, the sync wrapper only needs to bridge the SQLAlchemy event hooks — cashews handles the async Redis/storage calls internally.

---

## 9. Serialization

SQLAlchemy model instances are not trivially serializable. The library leverages cashews' serialization system where possible and provides a custom model-aware layer on top.

### Cashews Built-in Serialization

Cashews natively supports several serialization modes via `pickle_type`:

- `pickle_type="pickle"` (default) — fastest, supports most Python types
- `pickle_type="dill"` — handles more complex types (lambdas, closures), slower
- `pickle_type="sqlalchemy"` — uses SQLAlchemy's own `sqlalchemy.ext.serializer` for ORM objects
- `pickle_type="json"` — limited but human-readable

For most use cases, `pickle_type="sqlalchemy"` is the recommended setting as it handles ORM instances, relationships, and detached objects correctly using SQLAlchemy's built-in serializer.

### Custom Model-Aware Serialization (for JSON mode)

When JSON serialization is needed (e.g., for debugging, cross-language cache reads, or security policies that forbid pickle), sqlacache provides a model-aware JSON serializer:

```python
class ModelSerializer:
    """Serialize/deserialize SQLAlchemy model instances to/from JSON."""

    def serialize(self, obj, model_class):
        if isinstance(obj, list):
            return json.dumps({
                "type": "list",
                "model": f"{model_class.__module__}.{model_class.__name__}",
                "items": [self._instance_to_dict(item) for item in obj]
            })
        return json.dumps({
            "type": "instance",
            "model": f"{model_class.__module__}.{model_class.__name__}",
            "data": self._instance_to_dict(obj)
        })

    def deserialize(self, data: str, model_registry: dict):
        parsed = json.loads(data)
        model_class = model_registry[parsed["model"]]
        if parsed["type"] == "list":
            return [self._dict_to_instance(item, model_class) for item in parsed["items"]]
        return self._dict_to_instance(parsed["data"], model_class)

    def _instance_to_dict(self, instance):
        return {c.key: getattr(instance, c.key) for c in inspect(instance).mapper.column_attrs}

    def _dict_to_instance(self, data: dict, model_class):
        instance = model_class.__new__(model_class)
        for key, value in data.items():
            setattr(instance, key, value)
        # Mark as detached (not associated with any session)
        return instance
```

### Serializer Selection Guide

| Serializer | Speed | Safety | Debuggable | Complex Types | Recommended For |
|---|---|---|---|---|---|
| `pickle_type="sqlalchemy"` | Fast | Medium | No | ✅ ORM objects | **Default — most use cases** |
| `pickle_type="pickle"` | Fastest | Low | No | ✅ Most Python types | Performance-critical, trusted environments |
| `pickle_type="dill"` | Slow | Low | No | ✅ Everything | Complex objects (lambdas, generators) |
| Custom JSON (sqlacache) | Slow | High | ✅ Yes | ⚠️ Column attrs only | Debugging, cross-language, security-sensitive |

> **Note:** cashews also supports `compress_type="gzip"` and `compress_type="zlib"` which can be combined with any serializer to reduce memory usage in Redis.

---

## 10. Package Structure

```
sqlacache/
├── __init__.py              # Public API: configure(), CacheManager
├── config.py                # Configuration parsing, validation, model-to-ops mapping
├── manager.py               # CacheManager (async + sync)
├── interceptor.py           # SQLAlchemy event hooks (do_orm_execute, after_insert, etc.)
├── invalidation.py          # Invalidation engine, tag generation, PK extraction
├── transport/
│   ├── __init__.py          # CacheTransport protocol
│   ├── cashews.py           # CashewsTransport — wraps cashews.Cache (primary)
│   └── postgresql.py        # PostgreSQL UNLOGGED table backend (custom, no cashews) — v0.3+
├── serializers/
│   ├── __init__.py
│   └── json.py              # Custom model-aware JSON serializer (optional, for non-pickle use)
├── pubsub/
│   ├── __init__.py
│   ├── redis.py             # Redis pub/sub for cross-process invalidation
│   └── postgresql.py        # PG LISTEN/NOTIFY — v0.3+
├── utils/
│   ├── __init__.py
│   ├── key_generation.py    # Cache key hashing from statements + params
│   ├── query_analysis.py    # Extract models, PKs, op type from SA statements
│   └── sync_wrapper.py      # Async-to-sync bridge
├── contrib/
│   ├── __init__.py
│   ├── fastapi.py           # FastAPI dependency injection helpers
│   └── prometheus.py        # Prometheus metrics (wraps cashews' prometheus middleware)
├── py.typed                 # PEP 561 marker
└── exceptions.py            # Custom exceptions
```

> **Note:** Compared to the pre-cashews design, the `backends/` directory has been replaced by `transport/` with only two implementations: `cashews.py` (handles Redis, mem, disk) and `postgresql.py` (custom). The `serializers/` directory is slimmer because cashews handles pickle/dill/sqlalchemy serialization internally — only the custom JSON serializer lives here.

---

## 11. Dependencies

### Required
- `sqlalchemy >= 1.4` (for `do_orm_execute` event, async support)
- `cashews >= 7.0` (cache transport layer — handles Redis, in-memory, DiskCache, serialization, tags)

### Backend-specific (extras)
- `cashews[redis]` — Redis backend via cashews (`pip install sqlacache[redis]`)
- `cashews[diskcache]` — DiskCache backend via cashews (`pip install sqlacache[diskcache]`)
- `cashews[dill]` — dill serializer for complex types (`pip install sqlacache[dill]`)
- `cashews[speedup]` — xxhash + bloom filters (`pip install sqlacache[speedup]`)
- `asyncpg` or `psycopg[binary]` — for PostgreSQL UNLOGGED table backend (`pip install sqlacache[postgresql]`)

### Install variants
```bash
pip install sqlacache                       # Core + in-memory backend (dev/testing)
pip install sqlacache[redis]                # Redis backend (recommended for production)
pip install sqlacache[redis,speedup]        # Redis + performance optimizations
pip install sqlacache[postgresql]           # PostgreSQL UNLOGGED table backend (v0.3+)
pip install sqlacache[redis,diskcache]      # Redis + local disk fallback
pip install sqlacache[all]                  # Everything
```

> **What cashews gives us for free:** Redis connection management, client-side caching, tag-based invalidation sets, pickle/dill/sqlalchemy/json serialization, gzip/zlib compression, Prometheus metrics middleware, transaction support, bloom filters, error suppression, and battle-tested async Redis client handling. This is code we do NOT need to write or maintain.

---

## 12. Roadmap / Milestones

### v0.1.0 — MVP
- Cashews-powered Redis backend (`cashews[redis]`)
- Async support only (AsyncSession)
- Row-level invalidation for ORM queries (session.get, select(Model).where)
- Dependency tracking via cashews tag system
- Cacheops-style configuration with model-to-ops mapping
- Wildcard model matching
- Serialization via `pickle_type="sqlalchemy"` (cashews built-in)
- Basic cross-process invalidation via Redis pub/sub
- In-memory backend for development/testing (`mem://`)

### v0.2.0 — Sync + Extras
- Sync session support (sync wrapper layer)
- DiskCache backend via cashews (`disk://`)
- Client-side caching support (`client_side=True` — 10x faster reads)
- Custom JSON serializer for model-aware debugging
- PK injection for column-level queries
- Cashews transaction integration (cache consistency with DB transactions)

### v0.3.0 — Advanced
- PostgreSQL UNLOGGED table backend (custom, non-cashews)
- PG LISTEN/NOTIFY for cross-process invalidation
- Redis Sentinel and Cluster support (via cashews)
- Join query support (multi-table PK tracking)
- Cache statistics and monitoring (hit rate, miss rate, invalidation count)
- Integration with cashews' Prometheus middleware for observability
- `cache.execute()` for manual caching with full invalidation support

### v0.4.0 — Ecosystem
- Flask-SQLAlchemy integration helpers
- FastAPI dependency injection helpers
- Django-style management commands (cache stats, flush, warmup)
- Stale-while-revalidate via cashews' `@cache.early()` / `@cache.soft()` strategies
- Documentation site

---

## 13. Key Design Decisions (Summary)

| Decision | Choice | Rationale |
|---|---|---|
| Cache transport layer | `cashews` library | Async-first, production-proven, tag system maps to dependency tracking, client-side caching, Redis/mem/disk out of the box — avoids reinventing cache infrastructure |
| Invalidation granularity | Row-level (default) | Table-level is useless for frequently-updated tables |
| Internal architecture | Async-first with sync wrapper | Matches modern Python ecosystem direction; aligns with cashews' async core |
| Event hook | `do_orm_execute` | Single event covers all query types, both sync and async |
| Cache key generation | Hash of statement SQL + bound params | Deterministic, handles parameterized queries |
| Dependency tracking | Cashews tags (Redis SETs internally) | Native cashews feature, O(1) lookup per PK, atomic `delete_tags()` |
| Serialization default | `pickle_type="sqlalchemy"` via cashews | SA's built-in serializer handles ORM objects correctly, no custom code needed |
| PG cache table | UNLOGGED (custom, non-cashews) — v0.3+ | No WAL overhead; niche fallback for Postgres-only deployments. Requires custom tag reverse-index table (`sqlacache_tags`), background TTL cleanup job, and careful locking. Higher latency than Redis (~1–5ms vs ~0.1–0.5ms). DiskCache (`disk://`) is the preferred "no Redis" alternative for most use cases. |
| Cross-process invalidation | Redis pub/sub + cashews client-side cache | Real-time propagation; client-side cache gives 10x read speedup |
| Raw SQL support | Not supported for auto-invalidation | Documented limitation, manual invalidation API available |
| Config style | Cacheops-inspired dict mapping | Proven UX, developers instantly understand it |

---

## 14. Known Edge Cases & Complexities

Based on architectural review, the following edge cases require careful handling during implementation:

- **Bulk Operations (The Silent Killer)**: SQLAlchemy ORM events like `after_update` fire when modifying objects loaded into the session. However, bulk operations like `session.execute(update(User).where(...).values(...))` bypass these individual row-level events. You will need to hook into `after_bulk_update` and `after_bulk_delete`. In these cases, because you might not know the exact Primary Keys modified, you may have to elegantly fall back to **table-level invalidation** for that specific operation to prevent serving stale data.
- **Complex Joins**: As noted in the support matrix, `select(User, Order).join(Order)` will require extracting PKs for both tables. This is solvable, but parsing the SQLAlchemy AST to reliably extract all involved tables and their specific PKs from a complex result set will likely be the most complex part of the `extract_pks` function.
- **Eager Loading**: Pay attention to how `joinedload` or `selectinload` behaves. If a user queries `User` but eagerly loads `Addresses`, the cache key and tags must ideally depend on the `Address` table as well, so updating an address invalidates the user query.

## 15. Project Tooling & Development Infrastructure

This section defines the development tooling, project layout, CI/CD, and packaging strategy for sqlacache. Decisions are based on patterns from top Python packages (pydantic, httpx, fastapi, ruff, polars) as of 2026.

### 15.1 Repository Root Layout

```
sqlacache/                          # ← repository root
├── src/
│   └── sqlacache/                  # ← package source (src layout)
│       ├── __init__.py             # Public API: configure(), CacheManager, __version__
│       ├── config.py
│       ├── manager.py
│       ├── interceptor.py
│       ├── invalidation.py
│       ├── transport/
│       │   ├── __init__.py
│       │   ├── cashews.py
│       │   └── postgresql.py
│       ├── serializers/
│       │   ├── __init__.py
│       │   └── json.py
│       ├── pubsub/
│       │   ├── __init__.py
│       │   ├── redis.py
│       │   └── postgresql.py
│       ├── utils/
│       │   ├── __init__.py
│       │   ├── key_generation.py
│       │   ├── query_analysis.py
│       │   └── sync_wrapper.py
│       ├── contrib/
│       │   ├── __init__.py
│       │   ├── fastapi.py
│       │   └── prometheus.py
│       ├── py.typed                # PEP 561 marker
│       └── exceptions.py
├── tests/
│   ├── conftest.py                 # Shared fixtures: async engine, session, mem:// cache
│   ├── test_config.py
│   ├── test_interceptor.py
│   ├── test_invalidation.py
│   ├── test_manager.py
│   ├── test_key_generation.py
│   ├── test_query_analysis.py
│   ├── test_serializers.py
│   ├── transport/
│   │   ├── conftest.py             # Transport-specific fixtures
│   │   ├── test_cashews.py
│   │   └── test_postgresql.py
│   ├── pubsub/
│   │   ├── test_redis.py
│   │   └── test_postgresql.py
│   └── integration/
│       ├── conftest.py             # Real Redis/PG fixtures, skip markers
│       ├── test_redis_e2e.py
│       └── test_invalidation_e2e.py
├── pyproject.toml                  # Single source of truth for build, deps, tools
├── uv.lock                         # Lockfile (committed to VCS)
├── Makefile                        # Task runner: lint, format, test, typecheck
├── .pre-commit-config.yaml         # Git hooks via pre-k
├── .github/
│   └── workflows/
│       ├── ci.yml                  # Lint + test + typecheck on PR
│       ├── release.yml             # Publish to PyPI on tag
│       └── integration.yml         # Integration tests (Redis, PG) — scheduled/manual
├── docker-compose.yml              # Redis + PostgreSQL for local dev & integration tests
├── Dockerfile                      # NOT included — sqlacache is a library, not a service
├── .gitignore
├── LICENSE
├── README.md
├── CHANGELOG.md
├── CONTRIBUTING.md
└── CLAUDE.md
```

**Key decisions:**

- **`src/` layout** — prevents accidental imports of the local package during development. Used by `uv init --lib` default and recommended by PyPA.
- **No `Dockerfile`** — sqlacache is a library, not a deployable service. Docker images are for application code that depends on sqlacache, not for sqlacache itself.
- **`docker-compose.yml`** — provides Redis and PostgreSQL for local development and integration tests only. Not shipped to users.
- **No `requirements.txt`** — all dependencies (core, optional, dev) live in `pyproject.toml`. This is the modern standard; `requirements.txt` is for applications, not libraries.

### 15.2 Build System & Packaging

**Build backend: `hatchling`** — the most widely adopted backend for pure-Python libraries (used by pydantic, httpx, and many others).

```toml
# pyproject.toml

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "sqlacache"
dynamic = ["version"]
description = "Django-cacheops-style declarative caching with automatic row-level invalidation for SQLAlchemy"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{ name = "...", email = "..." }]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Framework :: AsyncIO",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Database",
    "Topic :: Software Development :: Libraries",
    "Typing :: Typed",
]
keywords = ["sqlalchemy", "cache", "redis", "invalidation", "orm"]

dependencies = [
    "sqlalchemy>=1.4",
    "cashews>=7.0",
]

[project.optional-dependencies]
redis = ["cashews[redis]"]
diskcache = ["cashews[diskcache]"]
dill = ["cashews[dill]"]
speedup = ["cashews[speedup]"]
postgresql = ["asyncpg"]
all = ["sqlacache[redis,diskcache,dill,speedup,postgresql]"]

[project.urls]
Homepage = "https://github.com/<owner>/sqlacache"
Documentation = "https://github.com/<owner>/sqlacache"
Repository = "https://github.com/<owner>/sqlacache"
Changelog = "https://github.com/<owner>/sqlacache/blob/main/CHANGELOG.md"

[tool.hatch.version]
path = "src/sqlacache/__init__.py"     # reads __version__ = "0.1.0"

[tool.hatch.build.targets.wheel]
packages = ["src/sqlacache"]
```

**Versioning:** dynamic from `src/sqlacache/__init__.py` via `__version__ = "X.Y.Z"`. Same pattern as pydantic and httpx.

### 15.3 Dependency Management: `uv`

**`uv`** is the package manager. It replaces pip, pip-tools, virtualenv, and poetry in a single Rust-based tool.

- `uv.lock` is committed to version control (deterministic builds)
- `uv run` executes commands in the project's virtualenv
- `uv sync` installs all deps from lockfile
- `uv sync --extra redis --group dev` installs specific extras + dev deps

```toml
# pyproject.toml — dev dependency group

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov",
    "pytest-xdist",           # parallel test execution
    "mypy>=1.10",
    "ruff>=0.9",
    "pre-k>=0.5",             # pre-commit replacement
]
```

### 15.4 Linting & Formatting: `ruff`

**`ruff`** for both linting and formatting — universal across all major Python projects.

```toml
# pyproject.toml

[tool.ruff]
target-version = "py310"
line-length = 120
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes
    "I",    # isort
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "SIM",  # flake8-simplify
    "TCH",  # flake8-type-checking
    "RUF",  # ruff-specific rules
]

[tool.ruff.lint.isort]
known-first-party = ["sqlacache"]
```

### 15.5 Type Checking: `mypy` (primary) + `ty` (experimental)

**`mypy`** as the primary type checker — stable, well-understood, used by 5/6 major packages surveyed.

**`ty`** (Astral's Rust-based type checker) as a secondary/experimental check. FastAPI already uses ty alongside mypy in pre-commit. ty is in beta (v0.0.29) but 10-100x faster than mypy.

```toml
# pyproject.toml

[tool.mypy]
python_version = "3.10"
strict = true
plugins = []
exclude = ["tests/"]

[[tool.mypy.overrides]]
module = "cashews.*"
ignore_missing_imports = true
```

### 15.6 Testing: `pytest`

```toml
# pyproject.toml

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: requires real Redis or PostgreSQL (deselect with -m 'not integration')",
    "slow: slow tests (deselect with -m 'not slow')",
]
filterwarnings = ["error"]
```

**Test strategy:**

- Unit tests use `mem://` backend (cashews in-memory) — zero infrastructure, fast, full tag support
- Integration tests use real Redis/PostgreSQL via `docker-compose.yml` — marked with `@pytest.mark.integration`
- `conftest.py` at each level provides scoped fixtures
- `pytest-asyncio` with `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio` on every test
- `pytest-xdist` for parallel execution: `uv run pytest -n auto`

### 15.7 Git Hooks: `pre-k`

**[`pre-k`](https://github.com/j178/pre-k)** — a Rust-based drop-in replacement for `pre-commit`. Single binary, no runtime dependencies, much faster, fully compatible with `.pre-commit-config.yaml` format. Already adopted by CPython, Apache Airflow, and FastAPI.

```yaml
# .pre-commit-config.yaml

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files

  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check
        entry: uv run ruff check --fix
        language: system
        types: [python]

      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types: [python]

      - id: mypy
        name: mypy
        entry: uv run mypy src/
        language: system
        types: [python]
        pass_filenames: false
```

Install hooks: `pre-k install` (instead of `pre-commit install`).

### 15.8 Task Runner: `Makefile`

A `Makefile` keeps common commands discoverable and short. Used by pydantic, polars, and cashews.

```makefile
.DEFAULT_GOAL := help

.PHONY: install lint format typecheck test testcov integration clean help

install:                         ## Install all dependencies
	uv sync --extra redis --group dev

lint:                            ## Run linter
	uv run ruff check src/ tests/

format:                          ## Format code
	uv run ruff format src/ tests/

typecheck:                       ## Run type checkers
	uv run mypy src/

test:                            ## Run unit tests
	uv run pytest -x -m "not integration"

testcov:                         ## Run tests with coverage
	uv run pytest --cov=sqlacache --cov-report=term-missing -m "not integration"

integration:                     ## Run integration tests (requires docker-compose up)
	uv run pytest -m integration

clean:                           ## Remove build artifacts
	rm -rf dist/ build/ .mypy_cache/ .pytest_cache/ .ruff_cache/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

help:                            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
```

### 15.9 CI/CD: GitHub Actions

**Three workflows:**

1. **`ci.yml`** — runs on every PR and push to main:
   - Matrix: Python 3.10, 3.11, 3.12, 3.13
   - Steps: `uv sync` → `ruff check` → `ruff format --check` → `mypy` → `pytest -m "not integration"`

2. **`release.yml`** — triggered by version tag (`v*`):
   - Build with `uv build`
   - Publish to PyPI via trusted publisher (OIDC, no API tokens)

3. **`integration.yml`** — scheduled nightly + manual trigger:
   - Spins up Redis and PostgreSQL services
   - Runs `pytest -m integration`

### 15.10 Docker Compose (Local Dev Only)

```yaml
# docker-compose.yml — for local development and integration tests only

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: sqlacache
      POSTGRES_PASSWORD: sqlacache
      POSTGRES_DB: sqlacache_test
```

This is **not shipped** with the library. It exists purely for developers contributing to sqlacache.

### 15.11 Publishing to PyPI

- **Build:** `uv build` (produces `.tar.gz` + `.whl` in `dist/`)
- **Publish:** GitHub Actions trusted publisher (OIDC) — no API tokens stored in secrets
- **Versioning flow:** bump `__version__` in `src/sqlacache/__init__.py` → commit → `git tag v0.1.0` → push tag → `release.yml` triggers → published to PyPI

### 15.12 Tooling Summary

| Concern | Tool | Notes |
| --- | --- | --- |
| Package manager | `uv` | Replaces pip, virtualenv, pip-tools |
| Build backend | `hatchling` | Most popular for pure-Python libs |
| Linter + formatter | `ruff` | Universal in 2026 Python ecosystem |
| Type checker | `mypy` (primary), `ty` (experimental) | ty is 10-100x faster, in beta |
| Test runner | `pytest` + `pytest-asyncio` | `asyncio_mode = "auto"` |
| Git hooks | `pre-k` | Rust-based pre-commit replacement |
| Task runner | `Makefile` | Simple, no extra deps |
| CI/CD | GitHub Actions | 3 workflows: ci, release, integration |
| Local services | `docker-compose` | Redis + PG for dev/integration tests |
| Config | `pyproject.toml` only | No setup.py, setup.cfg, requirements.txt |

---

## 16. Open Questions

- **Library name**: `sqlacache`? `alchemycache`? `cacheops-sa`? `ormcache`?
- **Should we support SQLModel?** (It's SQLAlchemy under the hood, so likely yes with zero extra code)
- **Cache warming**: Should we provide a mechanism to pre-populate cache on startup?
- ~~**Stale-while-revalidate**: Should we support serving stale cache while refreshing in background?~~ → **Resolved:** cashews provides `@cache.early()` and `@cache.soft()` strategies that handle this natively. We can expose these for manual caching via `cache.execute()`.
- **Bulk invalidation optimization**: When a bulk UPDATE affects 1000 rows, how do we avoid 1000 separate `delete_tags()` calls? (Batch tag deletion? Lua script? cashews pipeline?)
- ~~**Testing strategy**: How to test invalidation logic without a real Redis/PG?~~ → **Partially resolved:** cashews `mem://` backend provides a zero-infrastructure in-memory cache with full tag support, making unit testing straightforward.
- **Cashews version pinning**: Should we pin to `cashews >= 7.0` or `>= 7.4`? The tag system API needs to be stable.
- **Cashews middleware integration**: Should we register a custom cashews middleware for sqlacache-specific logging/metrics, or use cashews' built-in Prometheus middleware directly?
- **DiskCache tag limitations**: cashews' DiskCache backend has limited `scan`/`get_match` support with sharding enabled — does this affect tag-based invalidation? Needs testing.
- **Client-side cache consistency**: When using `client_side=True`, how do we ensure that tag SETs used for dependency tracking are also invalidated in the local cache? Need to verify cashews' behavior here.
