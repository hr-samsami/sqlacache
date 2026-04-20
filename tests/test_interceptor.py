from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.orm import Session

import sqlacache.interceptor as interceptor_module
from sqlacache.interceptor import (
    build_do_orm_execute_handler,
    build_post_commit_handler,
    build_rollback_handler,
    build_track_instance_handler,
)

from .conftest import User


class DummyExecuteState:
    """Minimal stand-in for ORMExecuteState used in unit tests."""

    def __init__(
        self,
        session: Any,
        statement: Any,
        result: Any,
        *,
        is_select: bool = True,
        is_update: bool = False,
        is_delete: bool = False,
    ) -> None:
        self.session = session
        self.statement = statement
        self._result = result
        self.is_select = is_select
        self.is_update = is_update
        self.is_delete = is_delete
        self.execution_options: dict[str, Any] = {}
        self.load_options: Any = None

    def invoke_statement(self) -> Any:
        return self._result


# --- build_do_orm_execute_handler ---


class TestDoOrmExecuteHandler:
    def test_select_cache_hit_returns_merged(self, cache: Any) -> None:
        """On cache hit, handler calls await_only(_lookup_cache) and merges the frozen result."""

        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        execute_state = DummyExecuteState(session, select(User), object())

        # Simulate the two await_only calls the handler makes:
        # 1. _lookup_cache → returns (frozen, key) on hit, None on miss.
        fake_frozen = object()
        calls: list[str] = []

        def fake_await_only(coro: Any) -> Any:
            coro.close()
            calls.append("await_only")
            return (fake_frozen, "some-key")

        def fake_merge(sess: Any, stmt: Any, frozen: Any) -> str:
            assert frozen is fake_frozen
            return "merged"

        orig_await = interceptor_module.await_only
        orig_greenlet = interceptor_module.in_greenlet
        orig_merge = interceptor_module.merge_cached_result
        interceptor_module.await_only = fake_await_only
        interceptor_module.in_greenlet = lambda: True
        interceptor_module.merge_cached_result = fake_merge

        try:
            handler = build_do_orm_execute_handler(manager)
            assert handler(execute_state) == "merged"
            assert calls == ["await_only"]
        finally:
            interceptor_module.await_only = orig_await
            interceptor_module.in_greenlet = orig_greenlet
            interceptor_module.merge_cached_result = orig_merge

    def test_select_cache_miss_invokes_statement(self, cache: Any) -> None:
        """On cache miss, handler invokes the statement then stores the result."""

        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        sentinel_result = object()
        execute_state = DummyExecuteState(session, select(User), sentinel_result)

        # Track the sequence: lookup returns None (miss), invoke_statement is
        # called, then _store_and_merge is awaited.
        call_log: list[str] = []
        orig_invoke = execute_state.invoke_statement

        def logged_invoke() -> Any:
            call_log.append("invoke")
            return orig_invoke()

        execute_state.invoke_statement = logged_invoke  # type: ignore[method-assign]

        call_count = [0]

        def fake_await_only(coro: Any) -> Any:
            coro.close()
            call_count[0] += 1
            if call_count[0] == 1:
                call_log.append("lookup")
                return None  # miss
            call_log.append("store")
            return "stored-and-merged"

        orig_await = interceptor_module.await_only
        orig_greenlet = interceptor_module.in_greenlet
        interceptor_module.await_only = fake_await_only
        interceptor_module.in_greenlet = lambda: True
        try:
            handler = build_do_orm_execute_handler(manager)
            assert handler(execute_state) == "stored-and-merged"
            assert call_log == ["lookup", "invoke", "store"]
        finally:
            interceptor_module.await_only = orig_await
            interceptor_module.in_greenlet = orig_greenlet

    def test_update_records_bulk_mutation(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        recorded: list[tuple[Any, type]] = []
        manager._record_bulk_mutation = lambda session, model: recorded.append((session, model))
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel, is_select=False, is_update=True)

        handler = build_do_orm_execute_handler(manager)
        result = handler(execute_state)
        assert result is sentinel
        assert recorded == [(session, User)]

    def test_delete_records_bulk_mutation(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        recorded: list[tuple[Any, type]] = []
        manager._record_bulk_mutation = lambda session, model: recorded.append((session, model))
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel, is_select=False, is_delete=True)

        handler = build_do_orm_execute_handler(manager)
        result = handler(execute_state)
        assert result is sentinel
        assert recorded == [(session, User)]

    def test_skip_interceptor_option(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel)
        execute_state.execution_options["sqlacache_skip_interceptor"] = True

        handler = build_do_orm_execute_handler(manager)
        assert handler(execute_state) is sentinel

    def test_unmatched_session_invokes_statement(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: False  # type: ignore[method-assign]
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel)

        handler = build_do_orm_execute_handler(manager)
        assert handler(execute_state) is sentinel

    def test_non_greenlet_select_invokes_statement(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel)

        orig_greenlet = interceptor_module.in_greenlet
        interceptor_module.in_greenlet = lambda: False
        try:
            handler = build_do_orm_execute_handler(manager)
            assert handler(execute_state) is sentinel
        finally:
            interceptor_module.in_greenlet = orig_greenlet

    def test_non_select_non_update_non_delete_invokes_statement(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(
            session, select(User), sentinel, is_select=False, is_update=False, is_delete=False
        )

        handler = build_do_orm_execute_handler(manager)
        assert handler(execute_state) is sentinel


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
