"""Redis pub/sub adapter for invalidation events."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class RedisPubSub:
    """Listen for and publish invalidation events over Redis pub/sub."""

    def __init__(self, redis_url: str, channel: str = "sqlacache:invalidate") -> None:
        self._redis_url = redis_url
        self._channel = channel
        self._client: Redis | None = None
        self._pubsub: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[dict[str, Any]], Awaitable[None]]] = []

    async def connect(self) -> None:
        self._client = Redis.from_url(self._redis_url)
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(self._channel)

    async def listen(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        if self._pubsub is None:
            return
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw_data = message.get("data")
                try:
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")
                    event = json.loads(raw_data)
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Failed to parse pub/sub message: %r", message)
                    continue

                for callback in list(self._callbacks):
                    try:
                        await callback(event)
                    except Exception:
                        logger.exception("Error in pub/sub callback")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pub/sub listener crashed")

    async def publish(self, event: dict[str, Any]) -> int:
        if self._client is None:
            raise RuntimeError("RedisPubSub is not connected")
        message = json.dumps(event, default=str, sort_keys=True)
        return int(await self._client.publish(self._channel, message))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(self._channel)
            await self._pubsub.aclose()
            self._pubsub = None

    async def disconnect(self) -> None:
        await self.stop()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
