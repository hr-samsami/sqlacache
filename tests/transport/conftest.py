"""Transport test fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlacache.transport.cashews import CashewsTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def memory_transport() -> AsyncIterator[CashewsTransport]:
    transport = CashewsTransport("mem://")
    await transport.connect()
    try:
        yield transport
    finally:
        await transport.disconnect()


@pytest_asyncio.fixture
async def redis_transport() -> AsyncIterator[CashewsTransport]:
    redis_url = os.getenv("SQLACACHE_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("SQLACACHE_TEST_REDIS_URL is not set")

    transport = CashewsTransport(redis_url, pickle_type="sqlalchemy")
    await transport.connect()
    try:
        yield transport
    finally:
        await transport.disconnect()
