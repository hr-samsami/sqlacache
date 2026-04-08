from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from sqlacache.exceptions import ConfigError, TransportError
from sqlacache.transport.cashews import CashewsTransport


@pytest.mark.asyncio
async def test_memory_transport_roundtrip(memory_transport: CashewsTransport) -> None:
    await memory_transport.set("user:1", {"id": 1}, expire=60)

    value = await memory_transport.get("user:1")

    assert value == {"id": 1}
    assert await memory_transport.is_available() is True


@pytest.mark.asyncio
async def test_delete_tags_invalidates_tagged_entries(memory_transport: CashewsTransport) -> None:
    await memory_transport.set("users:1", {"id": 1}, expire=60, tags=["users:1"])
    await memory_transport.set("users:2", {"id": 2}, expire=60, tags=["users:2"])

    await memory_transport.delete_tags("users:1")

    assert await memory_transport.get("users:1") is None
    assert await memory_transport.get("users:2") == {"id": 2}


@pytest.mark.asyncio
async def test_delete_removes_keys(memory_transport: CashewsTransport) -> None:
    await memory_transport.set("users:1", {"id": 1}, expire=60)
    await memory_transport.set("users:2", {"id": 2}, expire=60)

    await memory_transport.delete("users:1", "users:2")

    assert await memory_transport.get("users:1") is None
    assert await memory_transport.get("users:2") is None


@pytest.mark.asyncio
async def test_disconnect_marks_transport_unavailable(memory_transport: CashewsTransport) -> None:
    await memory_transport.disconnect()

    assert await memory_transport.is_available() is False


def test_from_config_uses_backend_mapping() -> None:
    transport = CashewsTransport.from_config(
        {
            "backend": {
                "url": "mem://",
                "prefix": "sqlacache",
            },
            "serializer": "sqlalchemy",
        }
    )

    assert transport._url == "mem://"
    assert transport._kwargs["prefix"] == "sqlacache"
    assert transport._kwargs["pickle_type"] == "sqlalchemy"


def test_from_config_rejects_invalid_backend_mapping() -> None:
    with pytest.raises(ConfigError):
        CashewsTransport.from_config({"backend": {"url": None}})


@pytest.mark.asyncio
async def test_suppress_returns_none_on_get_failure() -> None:
    transport = CashewsTransport("mem://", suppress=True)
    transport._connected = True

    with patch.object(transport._cache, "get", new=AsyncMock(side_effect=RuntimeError("boom"))):
        value = await transport.get("users:1")

    assert value is None


@pytest.mark.asyncio
async def test_unsuppressed_errors_raise_transport_error() -> None:
    transport = CashewsTransport("mem://")

    with (
        patch.object(
            transport._cache,
            "get",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        pytest.raises(TransportError),
    ):
        await transport.get("users:1")


@pytest.mark.asyncio
async def test_connect_raises_transport_error_when_unsuppressed() -> None:
    transport = CashewsTransport("mem://")

    with patch.object(transport._cache, "setup", side_effect=RuntimeError("boom")), pytest.raises(TransportError):
        await transport.connect()
