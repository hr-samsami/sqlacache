from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def integration_redis_url() -> str:
    return os.getenv("SQLACACHE_TEST_REDIS_URL", "redis://localhost:6379/1")


@pytest.fixture
def integration_postgres_url() -> str:
    return os.getenv("SQLACACHE_TEST_POSTGRES_URL", "postgresql+asyncpg://sqlacache:sqlacache@localhost:5432/sqlacache")


@pytest_asyncio.fixture
async def redis_client(integration_redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(integration_redis_url)
    try:
        await cast("Any", client).ping()
    except Exception as exc:
        pytest.skip(f"Redis integration service unavailable: {exc}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def postgres_engine(integration_postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(integration_postgres_url)
    try:
        async with engine.begin():
            pass
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL integration service unavailable: {exc}")
    try:
        yield engine
    finally:
        await engine.dispose()
