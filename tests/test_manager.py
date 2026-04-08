from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, event, select

from tests.conftest import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


async def test_execute_caches_and_returns_results(cache, session) -> None:
    session.add(User(id=1, name="u1"))
    await session.commit()

    result = await cache.execute(session, select(User).where(User.id == 1))

    assert result.scalar_one().name == "u1"


async def test_invalidate_row_level(cache, session) -> None:
    session.add(User(id=2, name="u2"))
    await session.commit()
    await cache.execute(session, select(User).where(User.id == 2))

    await cache.invalidate(User, [2])
    result = await cache.execute(session, select(User).where(User.id == 2))

    assert result.scalar_one().id == 2


async def test_invalidate_all_clears_cache(cache, session) -> None:
    session.add(User(id=10, name="u10"))
    await session.commit()
    await cache.execute(session, select(User).where(User.id == 10))
    transport = cache._transport
    assert transport is not None
    key = await cache._build_cache_key(select(User).where(User.id == 10), [User])

    await cache.invalidate_all()

    assert cache._transport is transport
    assert await transport.get(key) is None


async def test_bind_enables_automatic_caching_for_normal_session_reads(
    cache,
    session: AsyncSession,
    async_engine: AsyncEngine,
) -> None:
    session.add(User(id=3, name="u3"))
    await session.commit()
    session.expunge_all()

    first = await session.get(User, 3)
    assert first is not None
    assert first.name == "u3"

    statement = delete(User).where(User.id == 3).execution_options(sqlacache_skip_interceptor=True)
    await session.execute(statement)
    await session.commit()
    session.expunge_all()

    select_count = 0

    def count_selects(
        conn: Any,
        cursor: Any,
        statement_text: str,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        nonlocal select_count
        normalized = " ".join(statement_text.split()).upper()
        if normalized.startswith("SELECT") and "FROM USERS" in normalized:
            select_count += 1

    event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        second = await session.get(User, 3)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

    assert second is not None
    assert second.name == "u3"
    assert select_count == 0


async def test_orm_delete_invalidates_automatic_cached_get_results(
    cache,
    session: AsyncSession,
    async_engine: AsyncEngine,
) -> None:
    session.add(User(id=4, name="u4"))
    await session.commit()
    session.expunge_all()

    warm = await session.get(User, 4)
    assert warm is not None
    assert warm.name == "u4"
    session.expunge_all()

    select_count = 0

    def count_selects(
        conn: Any,
        cursor: Any,
        statement_text: str,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        nonlocal select_count
        normalized = " ".join(statement_text.split()).upper()
        if normalized.startswith("SELECT") and "FROM USERS" in normalized:
            select_count += 1

    event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        user_to_delete = await session.get(User, 4)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

    assert user_to_delete is not None
    assert user_to_delete.name == "u4"
    assert select_count == 0

    await session.delete(user_to_delete)
    await session.commit()
    session.expunge_all()

    await asyncio.sleep(0.05)

    missing = await session.get(User, 4)
    assert missing is None
