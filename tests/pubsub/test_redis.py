from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sqlacache.pubsub.redis import RedisPubSub


@pytest.mark.asyncio
async def test_listen_registers_callbacks() -> None:
    pubsub = RedisPubSub("redis://localhost:6379/1")
    callback = AsyncMock()

    await pubsub.listen(callback)

    assert pubsub._callbacks == [callback]


@pytest.mark.asyncio
async def test_publish_uses_redis_client() -> None:
    pubsub = RedisPubSub("redis://localhost:6379/1")
    pubsub._client = AsyncMock()
    pubsub._client.publish.return_value = 2

    published = await pubsub.publish({"table": "users", "pks": [1], "action": "update", "version": 1})

    assert published == 2


@pytest.mark.asyncio
async def test_listen_loop_ignores_invalid_json() -> None:
    pubsub = RedisPubSub("redis://localhost:6379/1")

    async def messages():
        yield {"type": "message", "data": b"not-json"}

    class StubPubSub:
        def listen(self):
            return messages()

    pubsub._pubsub = StubPubSub()

    await pubsub._listen_loop()
