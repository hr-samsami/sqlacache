"""SQLAlchemy ORM interception hooks."""

from __future__ import annotations

import contextlib
import logging
from typing import Any, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import ORMExecuteState, loading
from sqlalchemy.util import await_only
from sqlalchemy.util.concurrency import in_greenlet

from sqlacache.invalidation import generate_tags
from sqlacache.utils.query_analysis import (
    detect_operation_type,
    extract_pks_from_fetch_result,
    has_eager_loaders,
)

logger = logging.getLogger(__name__)


def merge_cached_result(session: Any, statement: Any, frozen: Any) -> Any:
    """Merge a frozen result back into a sync or async SQLAlchemy session."""

    target_session = getattr(session, "sync_session", session)
    merged = cast("Any", loading.merge_frozen_result(target_session, statement, frozen, load=False))  # type: ignore[no-untyped-call]
    return merged()


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
            if not in_greenlet():
                return execute_state.invoke_statement()
            # Cache lookup is the only async bit we do before deciding whether
            # to invoke the statement. We keep ``invoke_statement()`` in the
            # provider greenlet (i.e. the caller's synchronous context) so the
            # dialect's own ``await_only`` calls for DB IO find the greenlet
            # intact. Awaiting the coroutine via ``await_only`` pauses the
            # provider greenlet, which invalidates any nested IO — hence the
            # split: lookup_cache is purely async (no DB IO), invoke happens
            # synchronously, cache_store is awaited afterwards.
            statement = execute_state.statement
            models = manager._extract_models(statement)
            if not models:
                return execute_state.invoke_statement()
            if has_eager_loaders(statement):
                # Eager-loaded relationships introduce dependencies on related
                # rows that sqlacache does not track. Caching the result would
                # return stale joined data when a related row changes. Bypass
                # rather than silently return wrong answers.
                logger.warning(
                    "sqlacache: bypassing cache for %s — statement uses eager loading "
                    "(selectinload/joinedload/subqueryload/immediateload), which is not "
                    "supported; see README 'Caveats'.",
                    models[0].__name__,
                )
                return execute_state.invoke_statement()
            op = detect_operation_type(statement, models)
            primary_model = models[0]
            if not manager.is_enabled(primary_model, op):
                return execute_state.invoke_statement()

            lookup = await_only(_lookup_cache(manager, statement, models))
            if lookup is not None:
                cached_frozen, _ = lookup
                return merge_cached_result(execute_state.session, statement, cached_frozen)

            # Cache miss: invoke the statement synchronously in the provider
            # greenlet so DB IO can run.
            result = execute_state.invoke_statement()
            model_config = manager.get_model_config(primary_model)
            timeout = model_config["timeout"] if model_config else manager.config["default_timeout"]
            return await_only(_store_and_merge(manager, execute_state.session, statement, result, models, timeout))
        if execute_state.is_update or execute_state.is_delete:
            result = execute_state.invoke_statement()
            # Record the bulk mutation on the session; the actual table-version
            # bump runs at after_commit time.
            models = manager._extract_models(execute_state.statement)
            for model in models:
                manager._record_bulk_mutation(execute_state.session, model)
            return result
        return execute_state.invoke_statement()

    return handler


async def _lookup_cache(manager: Any, statement: Any, models: list[type[Any]]) -> tuple[Any, str] | None:
    """Pure-cache lookup with no DB IO. Returns (frozen_result, key) or None on miss."""

    key = await manager._build_cache_key(statement, models)
    cached = await manager._transport.get(key)
    if cached is None:
        return None
    return cached, key


async def _store_and_merge(
    manager: Any,
    session: Any,
    statement: Any,
    result: Any,
    models: list[type[Any]],
    timeout: int,
) -> Any:
    """Freeze, store in cache, and return a merged result."""

    frozen = result.freeze()
    pks_by_model = extract_pks_from_fetch_result(list(frozen.data), models)
    tags: list[str] = []
    for model, pks in pks_by_model.items():
        tags.extend(generate_tags(model, pks))
    key = await manager._build_cache_key(statement, models)
    await manager._transport.set(key, frozen, expire=timeout, tags=tags)
    return merge_cached_result(session, statement, frozen)


def build_track_instance_handler(manager: Any, action: str) -> Any:
    """Build a mapper event handler that records row mutations for later invalidation.

    Runs during flush (before commit). Only records the affected primary key on
    the session; the actual cache eviction is deferred to after_commit so that
    rolled-back transactions never invalidate the cache.
    """

    del action  # retained for API symmetry and future per-action behaviour

    def handler(mapper: Any, connection: Any, target: Any) -> None:
        del connection
        # Resolve the owning Session from the instance state; mapper events
        # don't receive the session directly.
        try:
            state = sa_inspect(target)
            session = state.session
        except Exception:
            return
        if session is None:
            return
        manager._record_instance_change(session, mapper.class_, target)

    return handler


def build_post_commit_handler(manager: Any) -> Any:
    """Build the Session.after_commit handler.

    Applies all invalidations recorded during flush. Scheduling on the running
    loop (rather than awaiting inline) keeps us out of the SQLAlchemy greenlet
    stack, which turns out to be fragile if we call ``await_only`` here —
    subsequent queries in the same session can fail with ``MissingGreenlet``.

    The tradeoff is a small post-commit window where a read on the same
    session could still hit the cache before the invalidation task runs.
    ``_apply_pending`` is scheduled eagerly so this window is a single event
    loop turn; for strict read-after-write consistency, callers should use
    ``session.expunge_all()`` + a fresh session, or await
    ``cache_manager.flush_pending()`` before reading.
    """

    def handler(session: Any) -> None:
        pending = manager._pending.pop(session, None)
        if not pending:
            return
        if manager._transport is None:
            return
        manager._schedule_flush(pending)

    return handler


def build_rollback_handler(manager: Any) -> Any:
    """Build the Session.after_rollback / after_soft_rollback handler.

    Discards any pending invalidations for the session so that cache entries
    built from committed state aren't evicted because of a failed transaction.
    """

    def handler(session: Any, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        manager._drop_pending(session)

    return handler
