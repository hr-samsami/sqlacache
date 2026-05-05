from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from sqlalchemy import delete, event, select, update

import sqlacache.interceptor as interceptor_module
from sqlacache.interceptor import (
    build_post_commit_handler,
    build_rollback_handler,
    build_track_instance_handler,
)

from .conftest import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from sqlacache.manager import CacheManager


# --- build_do_orm_execute_handler ---
#
# These tests run the real handler against a real ORM session and a real
# in-memory cache. The previous version monkey-patched ``await_only``,
# ``in_greenlet``, and ``merge_cached_result`` and asserted on the call order,
# which meant they passed even if ``_lookup_cache`` / ``_store_and_merge``
# were broken.


class TestDoOrmExecuteHandler:
    async def test_select_cache_miss_then_hit(
        self, cache: CacheManager, session: AsyncSession, async_engine: AsyncEngine
    ) -> None:
        """First execute hits the DB and stores; second execute serves from cache."""

        session.add(User(id=1, name="alice"))
        await session.commit()

        select_count = 0

        def count_selects(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, executemany: Any) -> None:
            nonlocal select_count
            if "SELECT" in stmt.upper() and "users" in stmt.lower():
                select_count += 1

        event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
        try:
            stmt = select(User).where(User.id == 1)
            first = (await session.execute(stmt)).scalar_one()
            assert first.name == "alice"
            after_first = select_count

            second = (await session.execute(stmt)).scalar_one()
            assert second.name == "alice"
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

        # Second execute issued no SELECT.
        assert select_count == after_first

    async def test_skip_interceptor_option(
        self, cache: CacheManager, session: AsyncSession, async_engine: AsyncEngine
    ) -> None:
        """``sqlacache_skip_interceptor`` execution option bypasses the cache entirely."""

        session.add(User(id=2, name="bob"))
        await session.commit()

        # Prime the cache.
        await session.execute(select(User).where(User.id == 2))

        select_count = 0

        def count_selects(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, executemany: Any) -> None:
            nonlocal select_count
            if "SELECT" in stmt.upper() and "users" in stmt.lower():
                select_count += 1

        event.listen(async_engine.sync_engine, "before_cursor_execute", count_selects)
        try:
            stmt = select(User).where(User.id == 2).execution_options(sqlacache_skip_interceptor=True)
            await session.execute(stmt)
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", count_selects)

        # The skip option forced a real DB SELECT.
        assert select_count >= 1

    async def test_update_records_bulk_mutation(self, cache: CacheManager, session: AsyncSession) -> None:
        """Bulk UPDATE flowing through do_orm_execute records a bulk mutation on the session."""

        session.add(User(id=3, name="c"))
        await session.commit()

        await session.execute(update(User).where(User.id == 3).values(name="C"))
        # The interceptor records a bulk-table mutation on the underlying sync session.
        pending = cache._pending.get(session.sync_session)
        assert pending is not None
        assert User in pending["bulk_tables"]

    async def test_delete_records_bulk_mutation(self, cache: CacheManager, session: AsyncSession) -> None:
        """Bulk DELETE flowing through do_orm_execute records a bulk mutation on the session."""

        session.add(User(id=4, name="d"))
        await session.commit()

        await session.execute(delete(User).where(User.id == 4))
        pending = cache._pending.get(session.sync_session)
        assert pending is not None
        assert User in pending["bulk_tables"]


# --- build_track_instance_handler ---


class TestTrackInstanceHandler:
    def test_records_instance_change(self, cache: Any) -> None:
        """Mapper event fires with an attached instance → manager records (session, model, target)."""

        manager = cache
        calls: list[tuple[Any, type, Any]] = []
        manager._record_instance_change = lambda session, model, target: calls.append((session, model, target))

        handler = build_track_instance_handler(manager, "insert")
        mapper = MagicMock()
        mapper.class_ = User

        # Simulate an instance with a valid session attached via SQLAlchemy state.
        target = User(id=1, name="u")
        fake_session = object()
        fake_state = MagicMock()
        fake_state.session = fake_session

        orig_inspect = interceptor_module.sa_inspect
        interceptor_module.sa_inspect = lambda obj: fake_state
        try:
            handler(mapper, MagicMock(), target)
        finally:
            interceptor_module.sa_inspect = orig_inspect

        assert calls == [(fake_session, User, target)]

    def test_no_session_attached_is_noop(self, cache: Any) -> None:
        manager = cache
        calls: list[Any] = []
        manager._record_instance_change = lambda *args, **kwargs: calls.append(args)

        handler = build_track_instance_handler(manager, "insert")
        mapper = MagicMock()
        mapper.class_ = User
        target = User(id=1, name="u")

        fake_state = MagicMock()
        fake_state.session = None

        orig_inspect = interceptor_module.sa_inspect
        interceptor_module.sa_inspect = lambda obj: fake_state
        try:
            handler(mapper, MagicMock(), target)
        finally:
            interceptor_module.sa_inspect = orig_inspect

        assert calls == []


class _FakeSession:
    """Hashable, weak-ref-able stand-in for Session in manager.pending tests."""


# --- build_post_commit_handler ---


class TestPostCommitHandler:
    def test_no_pending_is_noop(self, cache: Any) -> None:
        manager = cache
        session = _FakeSession()
        # Ensure nothing pending.
        manager._pending.pop(session, None)

        handler = build_post_commit_handler(manager)
        handler(session)  # should not raise

    def test_schedules_flush_and_pops_pending(self, cache: Any) -> None:
        """Handler pops the session's pending set and schedules it for flush."""

        manager = cache
        session = _FakeSession()
        manager._pending[session] = {"rows": {User: [1, 2]}, "bulk_tables": set()}

        scheduled: list[Any] = []
        manager._schedule_flush = lambda pending: scheduled.append(pending)

        handler = build_post_commit_handler(manager)
        handler(session)

        assert len(scheduled) == 1
        assert scheduled[0]["rows"] == {User: [1, 2]}
        assert session not in manager._pending

    def test_no_transport_is_noop(self, cache: Any) -> None:
        manager = cache
        session = _FakeSession()
        manager._pending[session] = {"rows": {User: [1]}, "bulk_tables": set()}

        scheduled: list[Any] = []
        manager._schedule_flush = lambda pending: scheduled.append(pending)

        orig_transport = manager._transport
        manager._transport = None
        try:
            handler = build_post_commit_handler(manager)
            handler(session)
        finally:
            manager._transport = orig_transport

        # Pending was popped, but nothing scheduled because transport is gone.
        assert scheduled == []
        assert session not in manager._pending


# --- build_rollback_handler ---


class TestRollbackHandler:
    def test_discards_pending(self, cache: Any) -> None:
        manager = cache
        session = _FakeSession()
        manager._pending[session] = {"rows": {User: [1]}, "bulk_tables": set()}

        handler = build_rollback_handler(manager)
        handler(session)

        assert session not in manager._pending

    def test_handles_extra_args(self, cache: Any) -> None:
        """after_soft_rollback passes a previous_transaction arg; handler must accept it."""

        manager = cache
        session = _FakeSession()
        manager._pending[session] = {"rows": {}, "bulk_tables": {User}}

        handler = build_rollback_handler(manager)
        handler(session, previous_transaction=MagicMock())

        assert session not in manager._pending


# --- merge_cached_result ---


class TestMergeCachedResult:
    async def test_merge_frozen_result(self, cache: Any, session: Any) -> None:
        session.add(User(id=99, name="frozen"))
        await session.commit()

        stmt = select(User).where(User.id == 99)
        result = await cache.execute(session, stmt)
        user = result.scalar_one()
        assert user.name == "frozen"
