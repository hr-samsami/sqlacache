from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session

from sqlacache.interceptor import (
    build_do_orm_execute_handler,
    build_post_commit_handler,
    build_rollback_handler,
    build_track_instance_handler,
    merge_cached_result,
)
from sqlacache.invalidation import (
    _bump_table_version,
    _encode_composite_pk,
    _get_table_version,
    generate_tags,
    invalidate_tags,
)
from sqlacache.transport.cashews import CashewsTransport
from sqlacache.utils.key_generation import generate_cache_key
from sqlacache.utils.query_analysis import (
    extract_model_from_statement,
    extract_pk_from_instance,
    probe_eager_loader_detection,
)

if TYPE_CHECKING:
    from sqlacache.pubsub.redis import RedisPubSub
    from sqlacache.transport import CacheTransport

logger = logging.getLogger(__name__)

# Pub/sub invalidation payload schema version. Bump when the payload format
# changes in a non-backward-compatible way; workers with a different version
# will skip events rather than mis-apply them.
_PUBSUB_PROTOCOL_VERSION = 1

# Eager-loader detection runs once per process: if SA internals drift it would
# log a warning every bind, which is noisy in test suites that bind/unbind a
# manager dozens of times.
_eager_loader_probe_done = False


class CacheManager:
    """Manage normalized cache configuration and runtime bindings."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._transport: CacheTransport | None = None
        self._model_config: dict[str, dict[str, Any] | None] = dict(config.get("models", {}))
        self._wildcard_config: dict[str, Any] | None = config.get("wildcard")
        self._bound_engine: Any | None = None
        self._bound_sync_engine: Any | None = None
        self._pubsub_task: asyncio.Task[Any] | None = None
        self._pubsub: RedisPubSub | None = None
        self._listeners: list[tuple[Any, str, Any]] = []
        # Per-session pending invalidations, accumulated during flush and applied
        # in after_commit. WeakKeyDictionary so abandoned sessions don't leak.
        self._pending: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()
        # Background tasks spawned for post-commit invalidation; tracked so we can
        # await/cancel them on disconnect().
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        # Unique origin id for cross-process invalidation events; the listener
        # filters out events it published itself to avoid double-applying them
        # locally (most notably, double-bumping the table-version counter on
        # bulk DML).
        self._origin_id = uuid.uuid4().hex

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    async def bind(self, engine: Any) -> None:
        if self._transport is not None:
            await self.disconnect()
        self._bound_engine = engine
        self._bound_sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        await self._ensure_transport()
        self._register_listeners()
        await self._maybe_setup_pubsub()

    def get_model_config(self, model: type[Any]) -> dict[str, Any] | None:
        model_path = f"{model.__module__}.{model.__name__}"
        if model_path in self._model_config:
            return self._model_config[model_path]
        return self._wildcard_config

    def is_enabled(self, model: type[Any], op: str) -> bool:
        model_config = self.get_model_config(model)
        return op in model_config["ops"] if model_config else False

    async def execute(self, session: Any, statement: Any, timeout: int | None = None) -> Any:
        await self._ensure_transport()
        models = self._extract_models(statement)
        execution_statement = statement.execution_options(sqlacache_skip_interceptor=True)
        if not models:
            return await session.execute(execution_statement)

        key = await self._build_cache_key(statement, models)
        transport = self._transport
        assert transport is not None
        cached = await transport.get(key)
        if cached is not None:
            return merge_cached_result(session, statement, cached)

        result = await session.execute(execution_statement)
        frozen = result.freeze()
        primary_model = models[0]
        model_config = self.get_model_config(primary_model)
        expire = timeout or (model_config["timeout"] if model_config else self._config["default_timeout"])
        from sqlacache.utils.query_analysis import extract_pks_from_fetch_result

        pks_by_model = extract_pks_from_fetch_result(list(frozen.data), models)
        tags: list[str] = []
        for tag_model, tag_pks in pks_by_model.items():
            tags.extend(generate_tags(tag_model, tag_pks))
        await transport.set(key, frozen, expire=expire, tags=tags)
        return merge_cached_result(session, statement, frozen)

    async def invalidate(self, model: type[Any] | None = None, pks: list[Any] | None = None) -> None:
        await self._ensure_transport()
        if model is None:
            await self.invalidate_all()
            return
        transport = self._transport
        assert transport is not None
        if pks:
            await invalidate_tags(transport, *generate_tags(model, pks))
            await self._publish_invalidation(model, pks, action="manual")
            return
        await _bump_table_version(transport, model)
        await self._publish_invalidation(model, [], action="table")

    async def invalidate_all(self) -> None:
        await self._ensure_transport()
        assert self._transport is not None
        await self._transport.clear()

    async def disconnect(self) -> None:
        for target, identifier, listener in self._listeners:
            with suppress(Exception):
                event.remove(target, identifier, listener)
        self._listeners.clear()
        if self._pubsub_task is not None:
            self._pubsub_task.cancel()
            self._pubsub_task = None
        if self._pubsub is not None:
            await self._pubsub.disconnect()
            self._pubsub = None
        # Wait briefly for in-flight post-commit invalidations to finish so we
        # don't drop cache evictions on shutdown.
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()
        self._pending.clear()
        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None
        self._bound_engine = None
        self._bound_sync_engine = None

    def _matches_session(self, session: Session) -> bool:
        if self._bound_sync_engine is None:
            return False
        try:
            bind = session.get_bind()
        except Exception:
            return False
        return bind is self._bound_sync_engine

    def _extract_models(self, statement: Any) -> list[type[Any]]:
        extracted = extract_model_from_statement(statement)
        if extracted is None:
            return []
        return extracted if isinstance(extracted, list) else [extracted]

    async def _build_cache_key(self, statement: Any, models: list[type[Any]], parameters: Any = None) -> str:
        prefix = self._config["prefix"]
        base_key = generate_cache_key(statement, prefix=prefix, parameters=parameters)
        if not models:
            return base_key
        transport = self._transport
        assert transport is not None
        versions = [str(await _get_table_version(transport, model)) for model in models]
        return f"{base_key}:v{'.'.join(versions)}"

    def _register_listeners(self) -> None:
        global _eager_loader_probe_done

        from sqlacache.config import _resolve_model

        if not _eager_loader_probe_done:
            _eager_loader_probe_done = True
            try:
                detected = probe_eager_loader_detection()
            except Exception:  # pragma: no cover - exact failures depend on SA internals
                logger.warning(
                    "sqlacache: eager-loader detection probe raised — has_eager_loaders may "
                    "be looking at SQLAlchemy internals that have moved. Eager-loaded "
                    "queries could be silently cached and return stale joined data; "
                    "verify your SQLAlchemy version is supported.",
                    exc_info=True,
                )
            else:
                if not detected:
                    logger.warning(
                        "sqlacache: eager-loader detection probe failed — selectinload was "
                        "not detected as an eager loader. Eager-loaded queries may be "
                        "silently cached and return stale joined data; verify your "
                        "SQLAlchemy version is supported."
                    )

        select_listener = build_do_orm_execute_handler(self)
        self._listeners.append((Session, "do_orm_execute", select_listener))
        event.listen(Session, "do_orm_execute", select_listener, retval=True)

        # Mapper-level events fire during flush (before commit). They only record
        # pending invalidations on the session; we defer the actual cache eviction
        # to after_commit so rolled-back transactions never invalidate the cache.
        for model_path in self._model_config:
            if model_path == "*":
                continue
            try:
                model = _resolve_model(model_path)
            except Exception:
                # User explicitly listed this model — failing silently means
                # writes to it never invalidate the cache and the user finds
                # out by debugging stale reads later. Log loudly instead.
                logger.warning(
                    "sqlacache: could not resolve configured model %r; mutations to it "
                    "will not invalidate the cache. Check the dotted import path.",
                    model_path,
                    exc_info=True,
                )
                continue
            for identifier, action in (
                ("after_insert", "insert"),
                ("after_update", "update"),
                ("after_delete", "delete"),
            ):
                listener = build_track_instance_handler(self, action)
                self._listeners.append((model, identifier, listener))
                event.listen(model, identifier, listener, propagate=True)

        # Commit-time invalidation: apply everything recorded during flush.
        commit_listener = build_post_commit_handler(self)
        self._listeners.append((Session, "after_commit", commit_listener))
        event.listen(Session, "after_commit", commit_listener)

        # Rollback: drop everything recorded during flush so we don't evict
        # based on writes that never made it to the database.
        rollback_listener = build_rollback_handler(self)
        self._listeners.append((Session, "after_rollback", rollback_listener))
        event.listen(Session, "after_rollback", rollback_listener)
        # after_soft_rollback covers nested savepoint rollbacks too.
        self._listeners.append((Session, "after_soft_rollback", rollback_listener))
        event.listen(Session, "after_soft_rollback", rollback_listener)

    async def _maybe_setup_pubsub(self) -> None:
        """Subscribe to cross-process invalidation events on Redis backends.

        With ``redis://`` the cache is shared across workers, so a single
        ``delete_tags()`` evicts entries that every worker reads from. Pub/sub
        is *only* needed to invalidate per-worker derived state — today that
        means the in-process table-version counter cached as part of cache key
        construction (a future client-side caching mode would also live here).
        Without pub/sub, after a bulk DML each worker would happily keep
        building cache keys with the old table version until the next miss
        forced a refresh.

        For ``mem://`` (single-process) and any other non-Redis backend, the
        cache is per-process anyway and there is no fan-out to do, so we skip
        pub/sub entirely.
        """

        from sqlacache.pubsub.redis import RedisPubSub

        backend = self._config["backend"]
        redis_url = backend["url"]
        if not isinstance(redis_url, str) or not redis_url.startswith("redis"):
            return
        self._pubsub = RedisPubSub(redis_url)
        await self._pubsub.connect()

        async def on_invalidate(event_payload: dict[str, Any]) -> None:
            # Ignore events we published ourselves: the local invalidation
            # path already applied them. Without this filter, a bulk DML
            # would double-bump the table-version counter (once locally,
            # once when the listener loops back the same publish).
            if event_payload.get("origin") == self._origin_id:
                return
            # Ignore events we don't know how to handle rather than applying
            # them with potentially wrong semantics. If a future version of
            # sqlacache changes the payload schema, older workers in a mixed
            # deployment will just skip the event — still safe, since stale
            # entries will TTL out eventually.
            event_version = event_payload.get("version", 1)
            if event_version != _PUBSUB_PROTOCOL_VERSION:
                logger.debug(
                    "sqlacache: skipping pub/sub event with version %r (expected %r)",
                    event_version,
                    _PUBSUB_PROTOCOL_VERSION,
                )
                return
            table = event_payload.get("table")
            pks = event_payload.get("pks", [])
            if not table:
                return
            transport = self._transport
            assert transport is not None
            if pks:
                await invalidate_tags(transport, *(f"{table}:{pk}" for pk in pks))
            else:
                await _bump_table_version(transport, table)

        self._pubsub.add_callback(on_invalidate)
        await self._pubsub.start()

    async def _ensure_transport(self) -> None:
        if self._transport is None:
            self._transport = CashewsTransport.from_config(self._config)
            await self._transport.connect()

    async def _publish_invalidation(self, model: type[Any], pks: list[Any], action: str) -> None:
        if self._pubsub is None:
            return
        # Encode composite-PK tuples deterministically so the receiver builds
        # the same tag string the publisher used. (JSON would otherwise
        # serialize tuples as lists, and ``f"{table}:{[1, 2]}"`` would not
        # match ``"{table}:1|2"`` from generate_tags.)
        encoded_pks: list[Any] = [_encode_composite_pk(pk) if isinstance(pk, tuple) else pk for pk in pks]
        await self._pubsub.publish(
            {
                "table": model.__tablename__,
                "pks": encoded_pks,
                "action": action,
                "version": _PUBSUB_PROTOCOL_VERSION,
                "origin": self._origin_id,
            }
        )

    def _record_instance_change(self, session: Any, model: type[Any], target: Any) -> None:
        """Record a row mutation for invalidation on commit.

        Called from mapper-level ``after_insert``/``after_update``/``after_delete``
        events, which fire during flush (before the transaction commits). We
        extract the PK now (while the instance is still attached) but defer the
        actual cache eviction until ``after_commit``.
        """

        try:
            pk = extract_pk_from_instance(target)
        except Exception:
            return
        if pk is None:
            return
        pending = self._pending.setdefault(session, {"rows": {}, "bulk_tables": set()})
        pending["rows"].setdefault(model, []).append(pk)

    def _record_bulk_mutation(self, session: Any, model: type[Any]) -> None:
        """Record a bulk UPDATE/DELETE for table-level invalidation on commit."""

        pending = self._pending.setdefault(session, {"rows": {}, "bulk_tables": set()})
        pending["bulk_tables"].add(model)

    def _drop_pending(self, session: Any) -> None:
        """Discard recorded mutations for a session (e.g. after rollback)."""

        self._pending.pop(session, None)

    async def flush_pending(self) -> None:
        """Wait for any in-flight post-commit invalidations to complete.

        For ``AsyncSession``, this is a no-op in the common case: the
        ``after_commit`` handler now evicts inline via ``await_only`` before
        ``await session.commit()`` returns. The method is kept because the
        sync ``Session`` path under a running loop still schedules eviction
        as a task — call this if you mix sync ``Session``s with async cache
        invalidation and need strict read-after-write consistency.
        """

        if not self._pending_tasks:
            return
        await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)

    def _schedule_flush(self, pending: dict[str, Any]) -> None:
        """Schedule invalidation application on the current event loop.

        Used by the sync ``Session`` path under a running loop. The
        ``AsyncSession`` path runs ``_apply_pending`` inline via ``await_only``
        in the ``after_commit`` handler so eviction completes before
        ``await session.commit()`` returns.
        """

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._transport is None:
            return
        task = loop.create_task(self._apply_pending(pending))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _apply_pending(self, pending: dict[str, Any]) -> None:
        transport = self._transport
        if transport is None:
            return
        rows: dict[type[Any], list[Any]] = pending.get("rows", {})
        bulk_tables: set[type[Any]] = pending.get("bulk_tables", set())
        # Bulk mutations invalidate the entire table; no need to also evict
        # per-row tags for models in bulk_tables.
        for model, pks in rows.items():
            if model in bulk_tables:
                continue
            deduped = list(dict.fromkeys(pks))
            tags = generate_tags(model, deduped)
            if tags:
                await invalidate_tags(transport, *tags)
            await self._publish_invalidation(model, deduped, action="row")
        for model in bulk_tables:
            await _bump_table_version(transport, model)
            await self._publish_invalidation(model, [], action="table")
