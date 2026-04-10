from __future__ import annotations

import pytest

from sqlacache.pubsub.redis import RedisPubSub


@pytest.mark.integration
async def test_cross_process_invalidation(redis_client) -> None:
    events: list[dict[str, object]] = []

    pubsub = RedisPubSub("redis://localhost:6379/1")
    await pubsub.connect()
    await pubsub.listen(lambda event: _collect_event(events, event))
    await pubsub.start()
    try:
        await pubsub.publish({"table": "users", "pks": [1], "action": "update", "version": 1})
    finally:
        await pubsub.disconnect()


async def _collect_event(events, event):
    events.append(event)
