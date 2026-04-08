from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlacache.interceptor import build_do_orm_execute_handler
from tests.conftest import User


class DummyExecuteState:
    def __init__(self, session, statement, result, *, is_select=True, is_update=False, is_delete=False):
        self.session = session
        self.statement = statement
        self._result = result
        self.is_select = is_select
        self.is_update = is_update
        self.is_delete = is_delete
        self.execution_options = {}

    def invoke_statement(self):
        return self._result


def test_interceptor_handler_routes_selects(cache) -> None:
    manager = cache
    manager._matches_session = lambda session: True  # type: ignore[method-assign]
    session = Session()
    session._is_asyncio = True  # type: ignore[attr-defined]
    execute_state = DummyExecuteState(session, select(User), object())

    def fake_await_only(value):
        value.close()
        return "handled-select"

    import sqlacache.interceptor as interceptor_module

    original = interceptor_module.await_only
    interceptor_module.await_only = fake_await_only

    try:
        handler = build_do_orm_execute_handler(manager)
        assert handler(execute_state) == "handled-select"
    finally:
        interceptor_module.await_only = original


def test_interceptor_handler_routes_bulk_mutations(cache) -> None:
    manager = cache
    manager._matches_session = lambda session: True  # type: ignore[method-assign]
    session = Session()
    session._is_asyncio = True  # type: ignore[attr-defined]
    execute_state = DummyExecuteState(session, select(User), object(), is_select=False, is_update=True)

    def fake_await_only(value):
        value.close()
        return "handled-bulk"

    import sqlacache.interceptor as interceptor_module

    original = interceptor_module.await_only
    interceptor_module.await_only = fake_await_only

    try:
        handler = build_do_orm_execute_handler(manager)
        assert handler(execute_state) == "handled-bulk"
    finally:
        interceptor_module.await_only = original
