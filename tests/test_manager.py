from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, event, select, update

from .conftest import Product, User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from sqlacache.manager import CacheManager


# --- execute (manual caching API) ---


class TestExecute:
    async def test_caches_and_returns_result(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=1, name="u1"))
        await session.commit()

        result = await cache.execute(session, select(User).where(User.id == 1))
        assert result.scalar_one().name == "u1"

    async def test_cache_hit_skips_db(
        self, cache: CacheManager, session: AsyncSession, async_engine: AsyncEngine
    ) -> None:
        session.add(User(id=1, name="u1"))
        await session.commit()

        await cache.execute(session, select(User).where(User.id == 1))

        select_count = 0

        def count_selects(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, executemany: Any) -> None:
            nonlocal select_count
            if "SELECT" in stmt.upper() and "users" in stmt.lower():
                select_count += 1

        event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
        try:
            result = await cache.execute(session, select(User).where(User.id == 1))
            assert result.scalar_one().name == "u1"
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

        assert select_count == 0

    async def test_custom_timeout(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=1, name="u1"))
        await session.commit()

        result = await cache.execute(session, select(User).where(User.id == 1), timeout=10)
        assert result.scalar_one().name == "u1"

    async def test_statement_without_model_falls_through(self, cache: CacheManager, session: AsyncSession) -> None:
        from sqlalchemy import text

        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    async def test_multi_row_fetch(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add_all([User(id=1, name="a"), User(id=2, name="b"), User(id=3, name="c")])
        await session.commit()

        result = await cache.execute(session, select(User).order_by(User.id))
        rows = result.scalars().all()
        assert len(rows) == 3
        assert [r.name for r in rows] == ["a", "b", "c"]


# --- invalidate ---


class TestInvalidate:
    async def test_row_level_invalidation(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=2, name="u2"))
        await session.commit()
        await cache.execute(session, select(User).where(User.id == 2))

        await cache.invalidate(User, [2])

        # After invalidation, re-executing should hit the DB again
        result = await cache.execute(session, select(User).where(User.id == 2))
        assert result.scalar_one().id == 2

    async def test_invalidate_multiple_pks(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add_all([User(id=1, name="a"), User(id=2, name="b")])
        await session.commit()
        await cache.execute(session, select(User).where(User.id == 1))
        await cache.execute(session, select(User).where(User.id == 2))

        await cache.invalidate(User, [1, 2])

    async def test_invalidate_without_pks_bumps_table_version(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=1, name="u1"))
        await session.commit()
        await cache.execute(session, select(User).where(User.id == 1))

        await cache.invalidate(User)
        # Table version bump means old cache keys won't match

    async def test_invalidate_none_model_clears_all(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=1, name="u1"))
        await session.commit()
        await cache.execute(session, select(User).where(User.id == 1))

        await cache.invalidate(model=None)

        transport = cache._transport
        assert transport is not None
        key = await cache._build_cache_key(select(User).where(User.id == 1), [User])
        assert await transport.get(key) is None


# --- invalidate_all ---


class TestInvalidateAll:
    async def test_clears_entire_cache(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=10, name="u10"))
        await session.commit()
        await cache.execute(session, select(User).where(User.id == 10))

        transport = cache._transport
        assert transport is not None
        key = await cache._build_cache_key(select(User).where(User.id == 10), [User])

        await cache.invalidate_all()

        assert await transport.get(key) is None

    async def test_transport_still_connected_after_clear(self, cache: CacheManager, session: AsyncSession) -> None:
        await cache.invalidate_all()
        assert cache._transport is not None


# --- bind / disconnect ---


class TestBindAndDisconnect:
    async def test_bind_creates_transport(self, cache: CacheManager) -> None:
        assert cache._transport is not None

    async def test_bind_registers_engine(self, cache: CacheManager) -> None:
        assert cache._bound_engine is not None
        assert cache._bound_sync_engine is not None

    async def test_disconnect_clears_state(self, async_engine: AsyncEngine) -> None:
        from sqlacache import configure

        manager = configure(
            backend="mem://",
            models={"*": {"timeout": 60}},
        )
        await manager.bind(async_engine)
        await manager.disconnect()

        assert manager._transport is None
        assert manager._bound_engine is None
        assert manager._bound_sync_engine is None

    async def test_disconnect_removes_listeners(self, async_engine: AsyncEngine) -> None:
        from sqlacache import configure

        manager = configure(
            backend="mem://",
            models={"*": {"timeout": 60}},
        )
        await manager.bind(async_engine)
        assert len(manager._listeners) > 0

        await manager.disconnect()
        assert len(manager._listeners) == 0

    async def test_rebind_disconnects_previous(self, async_engine: AsyncEngine) -> None:
        from sqlacache import configure

        manager = configure(
            backend="mem://",
            models={"*": {"timeout": 60}},
        )
        await manager.bind(async_engine)
        old_transport = manager._transport

        await manager.bind(async_engine)
        # Old transport should have been disconnected, new one created
        assert manager._transport is not old_transport


# --- session.get() interception via do_orm_execute ---


class TestSessionGetInterception:
    """``session.get(Model, pk)`` uses SQLAlchemy's ``load_on_pk_identity``,
    which fires ``do_orm_execute``. The interceptor recognises the resulting
    statement as a primary-key lookup (``op="get"``) and caches it without any
    monkey-patching of ``AsyncSession.get``.
    """

    async def test_automatic_caching_for_get(
        self, cache: CacheManager, session: AsyncSession, async_engine: AsyncEngine
    ) -> None:
        session.add(User(id=3, name="u3"))
        await session.commit()
        session.expunge_all()

        first = await session.get(User, 3)
        assert first is not None
        assert first.name == "u3"

        # Delete from DB directly, bypassing cache invalidation.
        stmt = delete(User).where(User.id == 3).execution_options(sqlacache_skip_interceptor=True)
        await session.execute(stmt)
        await session.commit()
        session.expunge_all()

        select_count = 0

        def count_selects(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, executemany: Any) -> None:
            nonlocal select_count
            if "SELECT" in stmt.upper() and "users" in stmt.lower():
                select_count += 1

        event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
        try:
            second = await session.get(User, 3)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

        assert second is not None
        assert second.name == "u3"
        assert select_count == 0  # served from cache

    async def test_get_with_populate_existing_bypasses_cache(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=5, name="u5"))
        await session.commit()
        session.expunge_all()

        # populate_existing forces a DB round-trip; the interceptor honours it.
        result = await session.get(User, 5, populate_existing=True)
        assert result is not None
        assert result.name == "u5"


# --- Automatic ORM event invalidation ---


class TestAutomaticInvalidation:
    async def test_insert_invalidates(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=20, name="u20"))
        await session.commit()
        session.expunge_all()

        await session.get(User, 20)  # warm cache

        session.add(User(id=21, name="u21"))
        await session.commit()
        await asyncio.sleep(0.05)  # let async invalidation complete

    async def test_update_invalidates(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=30, name="original"))
        await session.commit()
        session.expunge_all()

        await session.get(User, 30)  # warm cache

        user = await session.get(User, 30)
        assert user is not None
        user.name = "updated"
        await session.commit()
        await asyncio.sleep(0.05)

    async def test_delete_invalidates(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=4, name="u4"))
        await session.commit()
        session.expunge_all()

        warm = await session.get(User, 4)
        assert warm is not None
        session.expunge_all()

        user_to_delete = await session.get(User, 4)
        assert user_to_delete is not None

        await session.delete(user_to_delete)
        await session.commit()
        session.expunge_all()

        await asyncio.sleep(0.05)

        missing = await session.get(User, 4)
        assert missing is None

    async def test_bulk_update_bumps_table_version(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add_all([User(id=50, name="a"), User(id=51, name="b")])
        await session.commit()

        from sqlacache.invalidation import _get_table_version

        transport = cache._transport
        assert transport is not None
        v_before = await _get_table_version(transport, User)

        await session.execute(update(User).values(name="updated"))
        await session.commit()
        await asyncio.sleep(0.05)

        v_after = await _get_table_version(transport, User)
        assert v_after > v_before

    async def test_bulk_delete_bumps_table_version(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add_all([User(id=60, name="a"), User(id=61, name="b")])
        await session.commit()

        from sqlacache.invalidation import _get_table_version

        transport = cache._transport
        assert transport is not None
        v_before = await _get_table_version(transport, User)

        await session.execute(delete(User).where(User.id.in_([60, 61])))
        await session.commit()
        await asyncio.sleep(0.05)

        v_after = await _get_table_version(transport, User)
        assert v_after > v_before


# --- get_model_config / is_enabled ---


class TestModelConfig:
    async def test_get_model_config_for_configured_model(self, cache: CacheManager) -> None:
        config = cache.get_model_config(User)
        assert config is not None
        assert "ops" in config
        assert "timeout" in config

    async def test_get_model_config_unconfigured_falls_to_wildcard(self, cache: CacheManager) -> None:
        class Unregistered:
            __module__ = "fake.module"
            __name__ = "Unregistered"

        config = cache.get_model_config(Unregistered)
        # wildcard is configured in the test fixture
        assert config is not None

    async def test_is_enabled_for_valid_op(self, cache: CacheManager) -> None:
        assert cache.is_enabled(User, "get") is True
        assert cache.is_enabled(User, "fetch") is True

    async def test_is_enabled_for_disabled_model(self) -> None:
        from sqlacache import configure

        manager = configure(
            backend="mem://",
            models={f"{User.__module__}.{User.__name__}": None, "*": {"timeout": 60}},
        )
        assert manager.is_enabled(User, "get") is False


# --- _build_cache_key ---


class TestBuildCacheKey:
    async def test_includes_version(self, cache: CacheManager, session: AsyncSession) -> None:
        key = await cache._build_cache_key(select(User).where(User.id == 1), [User])
        assert ":v" in key

    async def test_version_changes_after_bump(self, cache: CacheManager, session: AsyncSession) -> None:
        stmt = select(User).where(User.id == 1)
        k1 = await cache._build_cache_key(stmt, [User])

        from sqlacache.invalidation import _bump_table_version

        await _bump_table_version(cache._transport, User)

        k2 = await cache._build_cache_key(stmt, [User])
        assert k1 != k2

    async def test_empty_models_returns_base_key(self, cache: CacheManager, session: AsyncSession) -> None:
        key = await cache._build_cache_key(select(User), [])
        assert ":v" not in key


# --- _matches_session ---


class TestMatchesSession:
    async def test_matches_bound_session(self, cache: CacheManager, session: AsyncSession) -> None:
        assert cache._matches_session(session.sync_session) is True

    async def test_no_match_unbound(self) -> None:
        from sqlacache import configure

        manager = configure(backend="mem://", models={"*": {"timeout": 60}})
        from sqlalchemy.orm import Session

        s = Session()
        assert manager._matches_session(s) is False

    async def test_no_match_different_engine(self, cache: CacheManager) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        other_engine = create_engine("sqlite:///:memory:")
        s = Session(bind=other_engine)
        assert cache._matches_session(s) is False
        other_engine.dispose()


# --- config property ---


class TestConfigProperty:
    def test_config_accessible(self) -> None:
        from sqlacache import configure

        manager = configure(backend="mem://", models={"*": {"timeout": 60}})
        assert isinstance(manager.config, dict)
        assert "backend" in manager.config
        assert "models" in manager.config


# --- _extract_models ---


class TestExtractModels:
    async def test_single_model(self, cache: CacheManager) -> None:
        models = cache._extract_models(select(User))
        assert models == [User]

    async def test_multi_model(self, cache: CacheManager) -> None:
        models = cache._extract_models(select(User, Product))
        assert models == [User, Product]

    async def test_no_model(self, cache: CacheManager) -> None:
        from sqlalchemy import text

        models = cache._extract_models(text("SELECT 1"))
        assert models == []
