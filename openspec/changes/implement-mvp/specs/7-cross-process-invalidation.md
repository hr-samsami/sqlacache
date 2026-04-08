# Spec: Cross-Process Invalidation

Implement Redis pub/sub listener for multi-worker deployments, ensuring cache consistency across worker processes.

## Requirements

### R1: Problem Statement

In production, multiple workers (gunicorn, uvicorn, etc.) run the same application code:

```
Worker A (FastAPI)  ──┐
Worker B (FastAPI)  ──┼─→ Shared Redis Cache
Worker C (FastAPI)  ──┘
```

When a write happens in Worker A:

1. ORM event hook fires: `after_insert`, `after_update`, `after_delete`
2. Tag-based invalidation deletes keys from Redis
3. **Problem**: Worker B and C still have stale data in their **local in-process caches** (if using cashews `client_side=True`)

**Solution**: Publish invalidation events via Redis pub/sub so all workers clear their local caches.

### R2: Pub/Sub Architecture

```
Worker A (on write):
  ├─> SQLAlchemy event fires
  ├─> delete_tags() invalidates Redis keys
  └─> publish("sqlacache:invalidate", json({"table": "users", "pks": [42]}))

Worker B (listening):
  └─> subscribe("sqlacache:invalidate")
      └─> callback: clear local client-side cache for "users:42"

Worker C (listening):
  └─> subscribe("sqlacache:invalidate")
      └─> callback: same
```

### R3: Redis Pub/Sub Implementation

Create `src/sqlacache/pubsub/redis.py`:

```python
import asyncio
import json
from typing import Callable
import redis.asyncio as redis

class RedisPubSub:
    """Redis pub/sub listener for cross-process invalidation."""

    def __init__(self, redis_client: redis.Redis):
        self._client = redis_client
        self._pubsub = None
        self._task = None
        self._callbacks = []

    async def connect(self):
        """Initialize pub/sub."""
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe("sqlacache:invalidate")

    async def listen(self, callback: Callable):
        """
        Start listening for invalidation messages.

        Callback signature: async def on_invalidate(event: dict) -> None

        event = {
            "table": "users",
            "pks": [42, 55],
            "action": "update"
        }
        """
        self._callbacks.append(callback)

    async def start(self):
        """Start listening loop (background task)."""
        self._task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self):
        """Background listener loop."""
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    event = json.loads(message["data"])
                    for callback in self._callbacks:
                        await callback(event)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse pub/sub message: {message}")
                except Exception as e:
                    logger.exception(f"Error in pub/sub callback: {e}")

    async def publish(self, event: dict) -> int:
        """
        Publish invalidation event.

        Returns: Number of subscribers that received the message
        """
        message = json.dumps(event)
        return await self._client.publish("sqlacache:invalidate", message)

    async def stop(self):
        """Stop listening."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()

    async def disconnect(self):
        """Cleanup."""
        await self.stop()
```

### R4: Integration with CacheManager

On `cache.bind()`:

1. If backend is Redis, instantiate `RedisPubSub` with the Redis client
2. Register callback that clears client-side cache tags
3. Start background listener task

```python
class CacheManager:
    async def bind(self, engine):
        # ... other setup ...

        # If Redis backend with client_side=True, setup pub/sub
        if self._config.get("backend", "").startswith("redis") or \
           isinstance(self._config.get("backend"), dict) and \
           self._config["backend"].get("client_side", False):

            redis_client = self._transport._get_redis_client()
            self._pubsub = RedisPubSub(redis_client)
            await self._pubsub.connect()

            async def on_invalidate(event):
                # Extract tags from event and clear local cache
                table = event["table"]
                pks = event["pks"]
                tags = [f"{table}:{pk}" for pk in pks]
                # Clear local client-side cache
                for tag in tags:
                    await self._transport._clear_client_cache(tag)

            await self._pubsub.listen(on_invalidate)
            await self._pubsub.start()
```

### R5: Publishing Invalidation Events

When writing to cache (in `after_insert`, `after_update`, `after_delete` hooks):

```python
@event.listens_for(Mapper, "after_update", propagate=True)
async def invalidate_after_update(mapper, connection, target):
    model = mapper.class_
    pk = extract_pk_from_instance(target)

    # 1. Invalidate Redis keys (via delete_tags)
    tags = generate_tags(model, [pk])
    await cache_manager._transport.delete_tags(*tags)

    # 2. Publish invalidation event for other workers
    event = {
        "table": model.__tablename__,
        "pks": [pk],
        "action": "update"
    }
    if hasattr(cache_manager, "_pubsub"):
        await cache_manager._pubsub.publish(event)
```

### R6: Event Message Format

Standardize the invalidation event format:

```python
{
    "table": "users",      # Table name (string)
    "pks": [42, 55],       # List of affected PKs
    "action": "update",    # "insert", "update", or "delete"
    "version": 1,          # Message format version (for future compatibility)
}
```

### R7: Client-Side Cache Clearing

For deployments using cashews `client_side=True` (Redis 6+ server-assisted caching):

```python
async def _clear_client_cache(self, tag: str) -> None:
    """
    Clear client-side cache entries for a tag.

    When using Redis 6+ client-side caching, the local cache is managed
    by the Redis client via server-assisted tracking. Clearing requires
    special handling.

    For cashews, this may require:
    1. Invalidating via cache.delete_tags() (already happened on the Redis side)
    2. Manually removing from local dict if cashews exposes it
    """
    # This is backend-specific; defer to cashews internals or v0.2.0
    pass
```

### R8: Error Handling

If pub/sub fails:

1. **Connection error**: Log and continue (cache still works, just no cross-process invalidation)
2. **Parse error**: Log and skip message
3. **Callback error**: Log but don't stop listener

```python
async def _listen_loop(self):
    try:
        async for message in self._pubsub.listen():
            if message["type"] == "message":
                try:
                    event = json.loads(message["data"])
                    for callback in self._callbacks:
                        await callback(event)
                except Exception as e:
                    logger.exception(f"Error processing invalidation event: {e}")
    except Exception as e:
        logger.exception(f"Pub/sub listener crashed: {e}")
        # Attempt to reconnect after delay (optional v0.2.0)
```

### R9: Testing

For unit tests (in-memory backend), pub/sub is not needed — tag-based invalidation is in-process.

For integration tests with Redis, test pub/sub with multiple simulated workers:

```python
@pytest.mark.integration
async def test_cross_process_invalidation():
    """Test that pub/sub propagates invalidation to other workers."""

    # Simulate two workers with shared Redis
    redis_url = "redis://localhost:6379/1"

    worker1 = CacheManager({"backend": redis_url, "models": {...}})
    worker2 = CacheManager({"backend": redis_url, "models": {...}})

    await worker1.bind(shared_engine)
    await worker2.bind(shared_engine)

    # Worker 1 writes
    await worker1_session.execute(insert(User).values(id=42, name="Alice"))

    # Worker 2's cache should be invalidated
    # (verify via checking cache.get() returns None)

    await worker1.disconnect()
    await worker2.disconnect()
```

### R10: Disabling Pub/Sub

For deployments where cross-process invalidation is not needed (single worker, or external cache invalidation):

```python
# Config option
config = {
    "backend": "redis://localhost:6379/1",
    "pubsub_enabled": False,  # Disable pub/sub
    "models": {...},
}
```

### R11: Graceful Shutdown

On `cache.disconnect()` or application shutdown:

```python
async def disconnect(self):
    if self._pubsub:
        await self._pubsub.stop()
    # ... other cleanup ...
```

Ensure pub/sub listener is cleanly stopped and doesn't prevent graceful shutdown.

## Implementation Notes

- Redis pub/sub is fire-and-forget; if a worker is not listening at the moment of publish, it misses the event (acceptable for cache invalidation — eventually consistent)
- For in-memory backend, pub/sub is not applicable (single process)
- For DiskCache backend (v0.2.0), pub/sub is not applicable (no Redis)
- Consider connection pooling for pub/sub client (separate from main Redis client)
- Test that background listener doesn't block FastAPI request handling

## Acceptance Criteria

- ✓ `RedisPubSub` class implements pub/sub protocol
- ✓ `connect()` and `listen()` setup without error
- ✓ Background listener task runs and receives messages
- ✓ Messages are published from invalidation hooks
- ✓ Event parsing handles JSON correctly
- ✓ Errors in callback don't stop listener
- ✓ `stop()` cleanly closes connection
- ✓ Integration test with two workers propagates invalidation
- ✓ Graceful shutdown doesn't hang (pub/sub task cancells cleanly)
- ✓ In-memory backend tests don't attempt pub/sub setup
