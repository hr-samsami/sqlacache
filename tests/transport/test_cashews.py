"""Comprehensive tests for CashewsTransport."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sqlacache.exceptions import ConfigError, TransportError
from sqlacache.transport.cashews import CashewsTransport

# --- Roundtrip / basic operations ---


class TestMemoryTransportBasics:
    async def test_set_and_get(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("k1", {"id": 1}, expire=60)
        assert await memory_transport.get("k1") == {"id": 1}

    async def test_get_missing_key_returns_none(self, memory_transport: CashewsTransport) -> None:
        assert await memory_transport.get("nonexistent") is None

    async def test_set_overwrites_existing(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("k1", "v1", expire=60)
        await memory_transport.set("k1", "v2", expire=60)
        assert await memory_transport.get("k1") == "v2"

    async def test_is_available_when_connected(self, memory_transport: CashewsTransport) -> None:
        assert await memory_transport.is_available() is True

    async def test_stores_various_types(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("str", "hello", expire=60)
        await memory_transport.set("int", 42, expire=60)
        await memory_transport.set("list", [1, 2, 3], expire=60)
        await memory_transport.set("dict", {"nested": {"key": "val"}}, expire=60)

        assert await memory_transport.get("str") == "hello"
        assert await memory_transport.get("int") == 42
        assert await memory_transport.get("list") == [1, 2, 3]
        assert await memory_transport.get("dict") == {"nested": {"key": "val"}}


# --- Delete ---


class TestDelete:
    async def test_delete_single_key(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("k1", "v1", expire=60)
        await memory_transport.delete("k1")
        assert await memory_transport.get("k1") is None

    async def test_delete_multiple_keys(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("k1", "v1", expire=60)
        await memory_transport.set("k2", "v2", expire=60)
        await memory_transport.delete("k1", "k2")
        assert await memory_transport.get("k1") is None
        assert await memory_transport.get("k2") is None

    async def test_delete_nonexistent_key_is_noop(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.delete("nonexistent")  # should not raise


# --- Tags ---


class TestTags:
    async def test_delete_tags_removes_tagged_entries(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("users:1", {"id": 1}, expire=60, tags=["users:1"])
        await memory_transport.set("users:2", {"id": 2}, expire=60, tags=["users:2"])

        await memory_transport.delete_tags("users:1")

        assert await memory_transport.get("users:1") is None
        assert await memory_transport.get("users:2") == {"id": 2}

    async def test_delete_tags_multiple(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("a", 1, expire=60, tags=["tag_a"])
        await memory_transport.set("b", 2, expire=60, tags=["tag_b"])

        await memory_transport.delete_tags("tag_a", "tag_b")

        assert await memory_transport.get("a") is None
        assert await memory_transport.get("b") is None

    async def test_entry_with_multiple_tags(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("multi", "val", expire=60, tags=["t1", "t2"])

        await memory_transport.delete_tags("t1")
        assert await memory_transport.get("multi") is None

    async def test_set_without_tags(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("no_tags", "val", expire=60)
        assert await memory_transport.get("no_tags") == "val"


# --- Clear ---


class TestClear:
    async def test_clear_removes_all(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.set("a", 1, expire=60)
        await memory_transport.set("b", 2, expire=60)

        await memory_transport.clear()

        assert await memory_transport.get("a") is None
        assert await memory_transport.get("b") is None


# --- Incr ---


class TestIncr:
    async def test_incr_new_key(self, memory_transport: CashewsTransport) -> None:
        result = await memory_transport.incr("counter")
        assert result == 1

    async def test_incr_existing_key(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.incr("counter")
        result = await memory_transport.incr("counter")
        assert result == 2

    async def test_incr_multiple_times(self, memory_transport: CashewsTransport) -> None:
        for _ in range(5):
            await memory_transport.incr("counter")
        result = await memory_transport.incr("counter")
        assert result == 6


# --- Disconnect ---


class TestDisconnect:
    async def test_disconnect_marks_unavailable(self, memory_transport: CashewsTransport) -> None:
        await memory_transport.disconnect()
        assert await memory_transport.is_available() is False

    async def test_is_available_before_connect(self) -> None:
        transport = CashewsTransport("mem://")
        assert await transport.is_available() is False


# --- from_config ---


class TestFromConfig:
    def test_string_backend(self) -> None:
        transport = CashewsTransport.from_config({"backend": "mem://", "serializer": "sqlalchemy"})
        assert transport._url == "mem://"
        assert transport._kwargs["pickle_type"] == "sqlalchemy"

    def test_mapping_backend(self) -> None:
        transport = CashewsTransport.from_config(
            {
                "backend": {"url": "mem://", "prefix": "sqlacache"},
                "serializer": "sqlalchemy",
            }
        )
        assert transport._url == "mem://"
        assert transport._kwargs["prefix"] == "sqlacache"
        assert transport._kwargs["pickle_type"] == "sqlalchemy"

    def test_mapping_preserves_custom_pickle_type(self) -> None:
        transport = CashewsTransport.from_config(
            {
                "backend": {"url": "mem://", "pickle_type": "json"},
                "serializer": "sqlalchemy",
            }
        )
        assert transport._kwargs["pickle_type"] == "json"

    def test_missing_backend_defaults_to_mem(self) -> None:
        transport = CashewsTransport.from_config({"backend": {}})
        assert transport._url == "mem://"

    def test_invalid_backend_mapping_url_none(self) -> None:
        with pytest.raises(ConfigError):
            CashewsTransport.from_config({"backend": {"url": None}})

    def test_invalid_backend_mapping_url_empty(self) -> None:
        with pytest.raises(ConfigError):
            CashewsTransport.from_config({"backend": {"url": ""}})

    def test_invalid_backend_type(self) -> None:
        with pytest.raises(ConfigError):
            CashewsTransport.from_config({"backend": 42})

    def test_default_serializer_when_missing(self) -> None:
        transport = CashewsTransport.from_config({"backend": "mem://"})
        assert transport._kwargs["pickle_type"] == "sqlalchemy"

    def test_url_key_excluded_from_kwargs(self) -> None:
        transport = CashewsTransport.from_config(
            {
                "backend": {"url": "mem://", "extra": "val"},
            }
        )
        assert "url" not in transport._kwargs
        assert transport._kwargs["extra"] == "val"


# --- Suppress mode ---


class TestSuppressMode:
    async def test_get_returns_none_on_failure(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "get", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert await transport.get("key") is None

    async def test_set_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "set", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await transport.set("key", "val", expire=60)  # should not raise

    async def test_delete_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "delete", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await transport.delete("key")  # should not raise

    async def test_delete_tags_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "delete_tags", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await transport.delete_tags("tag")  # should not raise

    async def test_clear_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "clear", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await transport.clear()  # should not raise

    async def test_incr_returns_zero_on_failure(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "incr", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert await transport.incr("key") == 0

    async def test_connect_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        with patch.object(transport._cache, "setup", side_effect=RuntimeError("boom")):
            await transport.connect()  # should not raise
            assert transport._connected is False

    async def test_disconnect_swallows_error(self) -> None:
        transport = CashewsTransport("mem://", suppress=True)
        transport._connected = True
        with patch.object(transport._cache, "close", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await transport.disconnect()  # should not raise
            assert not transport._connected


# --- Error propagation (unsuppressed) ---


class TestErrorPropagation:
    async def test_get_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "get", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache get failed"),
        ):
            await transport.get("key")

    async def test_set_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "set", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache set failed"),
        ):
            await transport.set("key", "val", expire=60)

    async def test_delete_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "delete", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache delete failed"),
        ):
            await transport.delete("key")

    async def test_delete_tags_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "delete_tags", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache delete_tags failed"),
        ):
            await transport.delete_tags("tag")

    async def test_clear_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "clear", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache clear failed"),
        ):
            await transport.clear()

    async def test_incr_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "incr", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache incr failed"),
        ):
            await transport.incr("key")

    async def test_connect_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        with (
            patch.object(transport._cache, "setup", side_effect=RuntimeError("boom")),
            pytest.raises(TransportError, match="Cache connect failed"),
        ):
            await transport.connect()

    async def test_disconnect_raises_transport_error(self) -> None:
        transport = CashewsTransport("mem://")
        transport._connected = True
        with (
            patch.object(transport._cache, "close", new=AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(TransportError, match="Cache disconnect failed"),
        ):
            await transport.disconnect()


# --- is_available edge cases ---


class TestIsAvailable:
    async def test_available_after_connect(self) -> None:
        transport = CashewsTransport("mem://")
        await transport.connect()
        assert await transport.is_available() is True
        await transport.disconnect()

    async def test_unavailable_when_healthcheck_fails(self) -> None:
        transport = CashewsTransport("mem://")
        transport._connected = True
        with patch.object(transport._cache, "get", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert await transport.is_available() is False
