# Spec: Cashews Transport Adapter

Implement `CashewsTransport` — a thin wrapper around `cashews.Cache` for Redis and in-memory backends, with support for dependency tracking via tags and cross-process invalidation hooks.

## Requirements

### R1: Transport Protocol

Define the minimal interface for a cache transport:

```python
from typing import Protocol, Optional, Callable

class CacheTransport(Protocol):
    """Minimal protocol for cache storage backends."""

    async def connect(self) -> None:
        """Initialize and connect transport."""

    async def disconnect(self) -> None:
        """Close connection and cleanup."""

    async def get(self, key: str) -> Optional[bytes]:
        """Get a value by key."""

    async def set(
        self,
        key: str,
        value: bytes,
        expire: int,
        tags: list[str] = None,
    ) -> None:
        """Set a value with TTL and optional dependency tags."""

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys."""

    async def delete_tags(self, *tags: str) -> None:
        """Delete all keys associated with given tags."""

    async def is_available(self) -> bool:
        """Check if transport is connected and working."""
```

### R2: CashewsTransport Implementation

Wrap `cashews.Cache` to implement the transport protocol:

```python
from cashews import Cache

class CashewsTransport:
    """Wraps cashews.Cache for cache storage operations."""

    def __init__(self, url: str, **kwargs):
        """
        Initialize transport with backend URL.

        Args:
            url: cashews URL string (e.g., "redis://localhost:6379/1", "mem://")
            **kwargs: Additional options passed to cashews.setup()
                - pickle_type: "sqlalchemy", "pickle", "dill", "json" (default: "sqlalchemy")
                - compress_type: "gzip", "zlib", None (default: None)
                - secret: Pickle hash salt (optional)
                - suppress: Suppress connection errors (default: False)
        """
        self._url = url
        self._kwargs = kwargs
        self._cache = Cache()

    async def connect(self) -> None:
        """Connect to backend via cashews.setup()."""
        await self._cache.setup(self._url, **self._kwargs)

    async def disconnect(self) -> None:
        """Close connection."""
        await self._cache.close()

    async def get(self, key: str) -> Optional[bytes]:
        """Retrieve value from cache."""
        return await self._cache.get(key)

    async def set(
        self,
        key: str,
        value: bytes,
        expire: int,
        tags: list[str] = None,
    ) -> None:
        """Store value with TTL and tags."""
        await self._cache.set(key, value, expire=expire, tags=tags or [])

    async def delete(self, *keys: str) -> None:
        """Delete keys."""
        for key in keys:
            await self._cache.delete(key)

    async def delete_tags(self, *tags: str) -> None:
        """Delete all keys with given tags."""
        await self._cache.delete_tags(*tags)

    async def is_available(self) -> bool:
        """Check connection status."""
        try:
            await self._cache.get("__ping__")
            return True
        except Exception:
            return False
```

### R3: Serialization

Cashews handles serialization internally. Ensure proper setup:

```python
# In configure(), set default pickle_type when initializing transport
transport = CashewsTransport(
    url=backend_url,
    pickle_type=serializer,  # Default: "sqlalchemy"
    compress_type=compress,
    secret=secret_key,
)
```

For v0.1.0, use `pickle_type="sqlalchemy"` (cashews built-in) which handles ORM object serialization correctly.

### R4: Redis Backend

Redis support is provided by cashews automatically. Configure via URL:

```
redis://[username[:password]]@host[:port]/[db]

Examples:
- redis://localhost:6379/1
- redis://:mypass@localhost:6379/0
- redis://localhost:6380 (SSL: rediss://)
- redis://0.0.0.0:6379/?cluster=true (Cluster mode)
```

Additional options via kwargs:

```python
transport = CashewsTransport(
    url="redis://localhost:6379/1",
    pickle_type="sqlalchemy",
    client_side=True,           # Enable Redis 6+ server-assisted cache (10x faster)
    client_side_prefix="sqla:",
    compress_type="gzip",
    secret="my_secret",
    suppress=False,             # Raise on connection errors
)
```

### R5: In-Memory Backend

For development and testing, use cashews' in-memory backend:

```
mem://?size=10000&check_interval=10

Parameters:
- size: Max number of entries (LRU eviction)
- check_interval: Seconds between expiry checks
```

```python
transport = CashewsTransport(url="mem://")
```

**Advantages**:
- Zero infrastructure (no Redis needed)
- Full tag support (tags stored as in-memory dicts)
- Supports all serialize types

**Limitation**: Single-process only (no cross-process invalidation).

### R6: Error Handling

When cache operations fail:

1. **Connection error**: If `suppress=False`, raise; if `suppress=True`, log and return None/skip set
2. **Deserialization error**: Log warning, treat as cache miss
3. **Tag operation error**: Log warning, fall back to full cache clear

```python
async def get(self, key: str) -> Optional[bytes]:
    try:
        return await self._cache.get(key)
    except Exception as e:
        if not self._suppress:
            raise
        logger.warning(f"Cache get failed: {e}")
        return None
```

### R7: Status Monitoring

Expose cache status via `is_available()`:

```python
# Used by health checks or to decide whether to apply cache
if await cache_transport.is_available():
    # Use cache
else:
    # Bypass cache, go to DB directly
```

### R8: Cross-Process Invalidation Hooks

For Redis backend, add methods for pub/sub (implemented in separate `pubsub/` module, but transport exposes connection):

```python
class CashewsTransport:
    async def publish(self, channel: str, message: str) -> int:
        """Publish message to Redis channel."""
        # Access underlying Redis client from cashews
        # cashews exposes client via self._cache._backend._client
        # (may require version-specific access)

    async def subscribe(self, channel: str, callback: Callable) -> None:
        """Subscribe to Redis channel with callback."""
        # Similar access pattern
```

**Note**: This is a bridge to the Redis client. Cashews may not expose the raw client directly; we may need to:
1. Access cashews internals (fragile)
2. Maintain a separate Redis client for pub/sub
3. Use cashews' own pub/sub if available (check cashews v7+ API)

For v0.1.0: **Defer pub/sub to separate `pubsub/redis.py` module**; transport only provides cache operations.

### R9: Configuration from Dict

Support initializing transport from a dict (for backward compat with config-driven setup):

```python
def from_config(config: dict) -> CashewsTransport:
    """Create transport from config dict."""
    backend_config = config.get("backend", {})

    if isinstance(backend_config, str):
        url = backend_config
        kwargs = {}
    else:
        url = backend_config.get("url", "mem://")
        kwargs = {k: v for k, v in backend_config.items() if k != "url"}

    # Add default serializer if not specified
    if "pickle_type" not in kwargs:
        kwargs["pickle_type"] = config.get("serializer", "sqlalchemy")

    return CashewsTransport(url=url, **kwargs)
```

### R10: Testing Backend

For unit tests and development:

```python
# Fixture in conftest.py
@pytest.fixture
async def cache_transport():
    transport = CashewsTransport(url="mem://?size=10000")
    await transport.connect()
    yield transport
    await transport.disconnect()
```

Tests can use this fixture without any real Redis/PostgreSQL.

## Implementation Notes

- Cashews handles all serialization details; we don't need custom serializers for v0.1.0
- Tag-based invalidation is provided by cashews automatically (we just call `delete_tags()`)
- For error suppression, use a config flag (suppress=True/False) to decide whether to raise or swallow errors
- Redis client access for pub/sub may require inspecting cashews source code or accessing private attributes (fragile)

## Acceptance Criteria

- ✓ `CashewsTransport` implements `CacheTransport` protocol
- ✓ `connect()` successfully initializes Redis and in-memory backends
- ✓ `set()` and `get()` work with and without tags
- ✓ `delete_tags()` removes all keys with matching tags
- ✓ In-memory backend works in tests without Redis
- ✓ Serialization defaults to "sqlalchemy" for ORM object support
- ✓ Error handling doesn't raise exceptions when `suppress=True`
- ✓ `is_available()` returns True/False on connection status
- ✓ Configuration from dict works correctly
