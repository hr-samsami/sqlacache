"""SQLAlchemy ORM interception hooks."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, cast

from sqlalchemy.orm import ORMExecuteState, loading
from sqlalchemy.util import await_only
from sqlalchemy.util.concurrency import in_greenlet

from sqlacache.invalidation import generate_tags
from sqlacache.utils.query_analysis import (
    detect_operation_type,
    extract_pks_from_fetch_result,
)

logger = logging.getLogger(__name__)


def merge_cached_result(session: Any, statement: Any, frozen: Any) -> Any:
    """Merge a frozen result back into a sync or async SQLAlchemy session."""

    target_session = getattr(session, "sync_session", session)
    merged = cast("Any", loading.merge_frozen_result(target_session, statement, frozen, load=False))  # type: ignore[no-untyped-call]
    return merged()


async def cache_query_result(
    manager: Any,
    statement: Any,
    result: Any,
    models: list[type[Any]],
    timeout: int,
) -> Any:
    """Freeze and cache a SQLAlchemy result object with dependency tags."""

    frozen = result.freeze()
    pks_by_model = extract_pks_from_fetch_result(list(frozen.data), models)
    tags: list[str] = []
    for model, pks in pks_by_model.items():
        tags.extend(generate_tags(model, pks))

    key = await manager._build_cache_key(statement, models)
    await manager._transport.set(key, frozen, expire=timeout, tags=tags)
    return merge_cached_result(result.session, statement, frozen)


def build_do_orm_execute_handler(manager: Any) -> Any:
    """Build the Session.do_orm_execute event handler for a manager instance."""

    def handler(execute_state: ORMExecuteState) -> Any:
        if not manager._matches_session(execute_state.session):
            return execute_state.invoke_statement()
        if execute_state.execution_options.get("sqlacache_skip_interceptor"):
            return execute_state.invoke_statement()
        if execute_state.is_select:
            with contextlib.suppress(Exception):
                if getattr(execute_state.load_options, "_populate_existing", False):
                    return execute_state.invoke_statement()
        if execute_state.is_select:
            if in_greenlet():
                return await_only(resolve_cached_result(manager, execute_state))
            return execute_state.invoke_statement()
        if execute_state.is_update or execute_state.is_delete:
            result = execute_state.invoke_statement()
            models = manager._extract_models(execute_state.statement)
            for model in models:
                manager._schedule_table_bump(model)
            return result
        return execute_state.invoke_statement()

    return handler


def build_invalidation_handler(manager: Any, action: str) -> Any:
    """Build a mapper event handler that invalidates on row mutation."""

    def handler(mapper: Any, connection: Any, target: Any) -> None:
        del connection
        manager._schedule_invalidation(mapper.class_, target, action=action)

    return handler


def build_bulk_update_handler(manager: Any) -> Any:
    def handler(update_context: Any) -> None:
        mapper = getattr(update_context, "mapper", None)
        if mapper is not None:
            manager._schedule_table_bump(mapper.class_)

    return handler


def build_bulk_delete_handler(manager: Any) -> Any:
    def handler(delete_context: Any) -> None:
        mapper = getattr(delete_context, "mapper", None)
        if mapper is not None:
            manager._schedule_table_bump(mapper.class_)

    return handler


async def resolve_cached_result(manager: Any, execute_state: ORMExecuteState) -> Any:
    statement = execute_state.statement
    models = manager._extract_models(statement)
    if not models:
        return execute_state.invoke_statement()

    op = detect_operation_type(statement, models)
    primary_model = models[0]
    if not manager.is_enabled(primary_model, op):
        return execute_state.invoke_statement()

    key = await manager._build_cache_key(statement, models)
    cached = await manager._transport.get(key)
    if cached is not None:
        return merge_cached_result(execute_state.session, statement, cached)

    result = execute_state.invoke_statement()
    model_config = manager.get_model_config(primary_model)
    timeout = model_config["timeout"] if model_config else manager.config["default_timeout"]
    return await cache_query_result(manager, statement, result, models, timeout)


async def handle_bulk_mutation(manager: Any, execute_state: ORMExecuteState) -> Any:
    statement = execute_state.statement
    models = manager._extract_models(statement)
    result = execute_state.invoke_statement()
    for model in models:
        manager._schedule_table_bump(model)
    return result
