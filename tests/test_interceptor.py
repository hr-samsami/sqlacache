from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import select
from sqlalchemy.orm import Session

import sqlacache.interceptor as interceptor_module
from sqlacache.interceptor import (
    build_bulk_delete_handler,
    build_bulk_update_handler,
    build_do_orm_execute_handler,
    build_invalidation_handler,
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
    def test_routes_selects_to_async_path(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        session = Session()
        execute_state = DummyExecuteState(session, select(User), object())

        def fake_await_only(value: Any) -> str:
            value.close()
            return "handled-select"

        orig_await = interceptor_module.await_only
        orig_greenlet = interceptor_module.in_greenlet
        interceptor_module.await_only = fake_await_only
        interceptor_module.in_greenlet = lambda: True

        try:
            handler = build_do_orm_execute_handler(manager)
            assert handler(execute_state) == "handled-select"
        finally:
            interceptor_module.await_only = orig_await
            interceptor_module.in_greenlet = orig_greenlet

    def test_routes_update_schedules_table_bump(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel, is_select=False, is_update=True)

        handler = build_do_orm_execute_handler(manager)
        result = handler(execute_state)
        assert result is sentinel
        assert bumped == [User]

    def test_routes_delete_schedules_table_bump(self, cache: Any) -> None:
        manager = cache
        manager._matches_session = lambda session: True  # type: ignore[method-assign]
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)
        session = Session()
        sentinel = object()
        execute_state = DummyExecuteState(session, select(User), sentinel, is_select=False, is_delete=True)

        handler = build_do_orm_execute_handler(manager)
        result = handler(execute_state)
        assert result is sentinel
        assert bumped == [User]

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


# --- build_invalidation_handler ---


class TestInvalidationHandler:
    def test_schedules_invalidation(self, cache: Any) -> None:
        manager = cache
        calls: list[tuple[Any, Any, str]] = []
        manager._schedule_invalidation = lambda model, target, action: calls.append((model, target, action))

        handler = build_invalidation_handler(manager, "insert")
        mapper = MagicMock()
        mapper.class_ = User
        target = User(id=1, name="u")

        handler(mapper, MagicMock(), target)

        assert len(calls) == 1
        assert calls[0] == (User, target, "insert")

    def test_different_actions(self, cache: Any) -> None:
        manager = cache
        calls: list[str] = []
        manager._schedule_invalidation = lambda model, target, action: calls.append(action)

        for action in ("insert", "update", "delete"):
            handler = build_invalidation_handler(manager, action)
            handler(MagicMock(class_=User), MagicMock(), User(id=1, name="u"))

        assert calls == ["insert", "update", "delete"]


# --- build_bulk_update_handler / build_bulk_delete_handler ---


class TestBulkHandlers:
    def test_bulk_update_schedules_table_bump(self, cache: Any) -> None:
        manager = cache
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)

        handler = build_bulk_update_handler(manager)
        ctx = MagicMock()
        ctx.mapper.class_ = User

        handler(ctx)
        assert bumped == [User]

    def test_bulk_delete_schedules_table_bump(self, cache: Any) -> None:
        manager = cache
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)

        handler = build_bulk_delete_handler(manager)
        ctx = MagicMock()
        ctx.mapper.class_ = User

        handler(ctx)
        assert bumped == [User]

    def test_bulk_update_no_mapper_is_noop(self, cache: Any) -> None:
        manager = cache
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)

        handler = build_bulk_update_handler(manager)
        ctx = MagicMock(spec=[])  # no mapper attribute

        handler(ctx)
        assert bumped == []

    def test_bulk_delete_no_mapper_is_noop(self, cache: Any) -> None:
        manager = cache
        bumped: list[type] = []
        manager._schedule_table_bump = lambda model: bumped.append(model)

        handler = build_bulk_delete_handler(manager)
        ctx = MagicMock(spec=[])

        handler(ctx)
        assert bumped == []


# --- merge_cached_result ---


class TestMergeCachedResult:
    async def test_merge_frozen_result(self, cache: Any, session: Any) -> None:
        session.add(User(id=99, name="frozen"))
        await session.commit()

        stmt = select(User).where(User.id == 99)
        result = await cache.execute(session, stmt)
        user = result.scalar_one()
        assert user.name == "frozen"
