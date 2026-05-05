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

# Exponential backoff for reconnect attempts, capped.
_BACKOFF_INITIAL = 0.5
_BACKOFF_MAX = 30.0


class RedisPubSub:
    """Listen for and publish invalidation events over Redis pub/sub.

    The listener automatically reconnects on transient Redis failures.
    Without reconnect, a single network blip silently stops invalidation
    fanout to this worker until the manager is rebound — which means stale
    reads indefinitely.
    """

    def __init__(self, redis_url: str, channel: str = "sqlacache:invalidate") -> None:
        self._redis_url = redis_url
        self._channel = channel
        self._client: Redis | None = None
        self._pubsub: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[dict[str, Any]], Awaitable[None]]] = []
        self._stopping = False
        self._healthy = False

    async def connect(self) -> None:
        await self._open_connection()

    async def _open_connection(self) -> None:
        self._client = Redis.from_url(self._redis_url)
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._healthy = True

    async def _close_connection(self) -> None:
        self._healthy = False
        if self._pubsub is not None:
            with suppress(Exception):
                await self._pubsub.unsubscribe(self._channel)
            with suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None
        if self._client is not None:
            with suppress(Exception):
                await self._client.aclose()
            self._client = None

    def add_callback(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Register a callback invoked for every received invalidation event."""

        self._callbacks.append(callback)

    async def listen(self, callback: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        """Deprecated alias for :meth:`add_callback`.

        The name is misleading — this method only registers a callback, it
        doesn't start the listener. Use :meth:`add_callback` in new code.
        Remains ``async`` for backwards compatibility with existing callers.
        """

        self.add_callback(callback)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen_loop())

    def is_healthy(self) -> bool:
        """Return whether the listener currently has a live Redis connection.

        Useful for health checks: if this is False for long, invalidations from
        other workers are being missed.
        """

        return self._healthy and not self._stopping

    async def _listen_loop(self) -> None:
        backoff = _BACKOFF_INITIAL
        while not self._stopping:
            if self._pubsub is None:
                try:
                    await self._open_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("sqlacache: pub/sub reconnect failed (%s); retrying in %.1fs", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_MAX)
                    continue
                else:
                    logger.info("sqlacache: pub/sub connected")
                    backoff = _BACKOFF_INITIAL

            assert self._pubsub is not None
            try:
                async for message in self._pubsub.listen():
                    if self._stopping:
                        break
                    if message.get("type") != "message":
                        continue
                    raw_data = message.get("data")
                    try:
                        if isinstance(raw_data, bytes):
                            raw_data = raw_data.decode("utf-8")
                        event = json.loads(raw_data)
                    except (TypeError, json.JSONDecodeError):
                        logger.warning("sqlacache: failed to parse pub/sub message: %r", message)
                        continue

                    for callback in list(self._callbacks):
                        try:
                            await callback(event)
                        except Exception:
                            logger.exception("sqlacache: error in pub/sub callback")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sqlacache: pub/sub listener crashed, reconnecting")
                await self._close_connection()
                # Loop around and reconnect with backoff on next iteration.
                continue
            # Generator exited cleanly (no exception). That means either the
            # user stopped us, or the mock in a test drained its messages.
            # Either way, don't reconnect — exit the outer loop.
            return

    async def publish(self, event: dict[str, Any]) -> int:
        if self._client is None:
            raise RuntimeError("RedisPubSub is not connected")
        message = json.dumps(event, default=str, sort_keys=True)
        try:
            return int(await self._client.publish(self._channel, message))
        except Exception:
            # Don't let a transient Redis failure break the commit path. The
            # listener will reconnect, but missed publishes are lost — this is
            # an at-most-once channel by design.
            logger.exception("sqlacache: failed to publish invalidation event")
            self._healthy = False
            return 0

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_connection()

    async def disconnect(self) -> None:
        await self.stop()
