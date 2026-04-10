"""Comprehensive tests for sqlacache.pubsub.redis.RedisPubSub."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlacache.pubsub.redis import RedisPubSub

# --- listen / callbacks ---


class TestListenCallbacks:
    async def test_registers_single_callback(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        callback = AsyncMock()
        await pubsub.listen(callback)
        assert pubsub._callbacks == [callback]

    async def test_registers_multiple_callbacks(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        await pubsub.listen(cb1)
        await pubsub.listen(cb2)
        assert pubsub._callbacks == [cb1, cb2]


# --- publish ---


class TestPublish:
    async def test_publishes_json_to_channel(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._client = AsyncMock()
        pubsub._client.publish.return_value = 2

        count = await pubsub.publish({"table": "users", "pks": [1], "action": "update", "version": 1})

        assert count == 2
        pubsub._client.publish.assert_called_once()
        call_args = pubsub._client.publish.call_args
        channel = call_args[0][0]
        message = call_args[0][1]
        assert channel == "sqlacache:invalidate"
        parsed = json.loads(message)
        assert parsed["table"] == "users"
        assert parsed["pks"] == [1]

    async def test_publish_sorts_keys(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._client = AsyncMock()
        pubsub._client.publish.return_value = 1

        await pubsub.publish({"z": 1, "a": 2})

        message = pubsub._client.publish.call_args[0][1]
        assert message == json.dumps({"a": 2, "z": 1}, sort_keys=True)

    async def test_publish_without_connection_raises(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        with pytest.raises(RuntimeError, match="not connected"):
            await pubsub.publish({"table": "users"})

    async def test_publish_uses_default_str_for_non_serializable(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._client = AsyncMock()
        pubsub._client.publish.return_value = 1

        import uuid

        uid = uuid.uuid4()
        await pubsub.publish({"id": uid})

        message = pubsub._client.publish.call_args[0][1]
        parsed = json.loads(message)
        assert parsed["id"] == str(uid)


# --- custom channel ---


class TestCustomChannel:
    def test_default_channel(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        assert pubsub._channel == "sqlacache:invalidate"

    def test_custom_channel(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1", channel="custom:channel")
        assert pubsub._channel == "custom:channel"


# --- _listen_loop ---


class TestListenLoop:
    async def test_ignores_non_message_types(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        callback = AsyncMock()
        await pubsub.listen(callback)

        async def messages():
            yield {"type": "subscribe", "data": 1}
            yield {"type": "pong", "data": None}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()
        callback.assert_not_called()

    async def test_ignores_invalid_json(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        callback = AsyncMock()
        await pubsub.listen(callback)

        async def messages():
            yield {"type": "message", "data": b"not-json"}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()
        callback.assert_not_called()

    async def test_dispatches_valid_json_to_callbacks(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        events: list[dict] = []
        callback = AsyncMock(side_effect=lambda e: events.append(e))
        await pubsub.listen(callback)

        payload = json.dumps({"table": "users", "pks": [1]}).encode()

        async def messages():
            yield {"type": "message", "data": payload}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()

        assert len(events) == 1
        assert events[0]["table"] == "users"

    async def test_decodes_bytes_data(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        events: list[dict] = []
        callback = AsyncMock(side_effect=lambda e: events.append(e))
        await pubsub.listen(callback)

        async def messages():
            yield {"type": "message", "data": b'{"key": "value"}'}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()
        assert events[0] == {"key": "value"}

    async def test_handles_string_data(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        events: list[dict] = []
        callback = AsyncMock(side_effect=lambda e: events.append(e))
        await pubsub.listen(callback)

        async def messages():
            yield {"type": "message", "data": '{"key": "value"}'}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()
        assert events[0] == {"key": "value"}

    async def test_callback_exception_does_not_stop_loop(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        call_count = 0

        async def failing_callback(event: dict) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("boom")

        await pubsub.listen(failing_callback)

        msg1 = json.dumps({"msg": 1}).encode()
        msg2 = json.dumps({"msg": 2}).encode()

        async def messages():
            yield {"type": "message", "data": msg1}
            yield {"type": "message", "data": msg2}

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        await pubsub._listen_loop()
        assert call_count == 2

    async def test_no_pubsub_returns_immediately(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._pubsub = None
        await pubsub._listen_loop()  # should not raise

    async def test_cancelled_error_propagates(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")

        async def messages():
            yield {"type": "message", "data": b'{"k":"v"}'}
            raise asyncio.CancelledError()

        class StubPubSub:
            def listen(self):
                return messages()

        pubsub._pubsub = StubPubSub()
        callback = AsyncMock()
        await pubsub.listen(callback)

        with pytest.raises(asyncio.CancelledError):
            await pubsub._listen_loop()


# --- start / stop ---


class TestStartStop:
    async def test_start_creates_task(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._pubsub = MagicMock()

        # Mock _listen_loop to avoid real execution
        async def noop_loop() -> None:
            await asyncio.sleep(10)

        with patch.object(pubsub, "_listen_loop", noop_loop):
            await pubsub.start()
            assert pubsub._task is not None
            pubsub._task.cancel()
            with suppress(asyncio.CancelledError):
                await pubsub._task

    async def test_start_idempotent(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")

        async def noop_loop() -> None:
            await asyncio.sleep(10)

        with patch.object(pubsub, "_listen_loop", noop_loop):
            await pubsub.start()
            first_task = pubsub._task
            await pubsub.start()
            assert pubsub._task is first_task
            pubsub._task.cancel()
            with suppress(asyncio.CancelledError):
                await pubsub._task

    async def test_stop_cancels_task(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")

        async def noop_loop() -> None:
            await asyncio.sleep(10)

        stub_pubsub = AsyncMock()
        pubsub._pubsub = stub_pubsub

        with patch.object(pubsub, "_listen_loop", noop_loop):
            await pubsub.start()
            assert pubsub._task is not None

            await pubsub.stop()
            assert pubsub._task is None

    async def test_stop_without_start_is_noop(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        await pubsub.stop()  # should not raise


# --- disconnect ---


class TestDisconnect:
    async def test_disconnect_stops_and_closes_client(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        pubsub._client = AsyncMock()
        pubsub._pubsub = AsyncMock()

        await pubsub.disconnect()

        assert pubsub._client is None
        assert pubsub._pubsub is None

    async def test_disconnect_without_connect_is_noop(self) -> None:
        pubsub = RedisPubSub("redis://localhost:6379/1")
        await pubsub.disconnect()  # should not raise


# --- connect ---


class TestConnect:
    async def test_connect_creates_client_and_subscribes(self) -> None:
        mock_pubsub_obj = AsyncMock()
        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub_obj

        with patch("sqlacache.pubsub.redis.Redis") as MockRedis:
            MockRedis.from_url.return_value = mock_client

            pubsub = RedisPubSub("redis://localhost:6379/1")
            await pubsub.connect()

            MockRedis.from_url.assert_called_once_with("redis://localhost:6379/1")
            mock_client.pubsub.assert_called_once()
            mock_pubsub_obj.subscribe.assert_called_once_with("sqlacache:invalidate")
            assert pubsub._client is mock_client

    async def test_connect_with_custom_channel(self) -> None:
        mock_pubsub_obj = AsyncMock()
        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub_obj

        with patch("sqlacache.pubsub.redis.Redis") as MockRedis:
            MockRedis.from_url.return_value = mock_client

            pubsub = RedisPubSub("redis://localhost:6379/1", channel="my:channel")
            await pubsub.connect()

            mock_pubsub_obj.subscribe.assert_called_once_with("my:channel")
