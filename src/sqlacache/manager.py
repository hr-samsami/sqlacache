"""Cache manager primitives."""

from __future__ import annotations

import asyncio
import weakref
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from sqlacache.interceptor import (
    build_do_orm_execute_handler,
    build_post_commit_handler,
    build_rollback_handler,
    build_track_instance_handler,
    merge_cached_result,
)
from sqlacache.invalidation import _bump_table_version, _get_table_version, generate_tags, invalidate_tags
from sqlacache.transport.cashews import CashewsTransport
from sqlacache.utils.key_generation import generate_cache_key
from sqlacache.utils.query_analysis import extract_model_from_statement, extract_pk_from_instance

if TYPE_CHECKING:
    from sqlacache.pubsub.redis import RedisPubSub
    from sqlacache.transport import CacheTransport


class CacheManager:
    """Manage normalized cache configuration and runtime bindings."""

    _async_get_patched = False
    _original_async_get: Any = None
    _engine_registry: ClassVar[dict[int, CacheManager]] = {}

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

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    async def bind(self, engine: Any) -> None:
        if self._transport is not None:
            await self.disconnect()
        self._bound_engine = engine
        self._bound_sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
        if self._bound_sync_engine is not None:
            self._engine_registry[id(self._bound_sync_engine)] = self
        await self._ensure_transport()
        self._patch_async_get()
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
        if self._bound_sync_engine is not None:
            self._engine_registry.pop(id(self._bound_sync_engine), None)
        if not self._engine_registry:
            self._restore_async_get()
        self._bound_engine = None
        self._bound_sync_engine = None

    def bind_sync(self, engine: Any) -> None:
        raise NotImplementedError("Sync session support is deferred to v0.2.0")

    def execute_sync(self, session: Any, statement: Any, timeout: int | None = None) -> Any:
        return self._run_sync(self.execute(session, statement, timeout=timeout))

    def invalidate_sync(self, model: type[Any] | None = None, pks: list[Any] | None = None) -> None:
        self._run_sync(self.invalidate(model=model, pks=pks))

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

    async def _build_cache_key(self, statement: Any, models: list[type[Any]]) -> str:
        prefix = self._config["prefix"]
        base_key = generate_cache_key(statement, prefix=prefix)
        if not models:
            return base_key
        transport = self._transport
        assert transport is not None
        versions = [str(await _get_table_version(transport, model)) for model in models]
        return f"{base_key}:v{'.'.join(versions)}"

    def _register_listeners(self) -> None:
        from sqlacache.config import _resolve_model

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
        from sqlacache.pubsub.redis import RedisPubSub

        backend = self._config["backend"]
        redis_url = backend["url"]
        if not isinstance(redis_url, str) or not redis_url.startswith("redis"):
            return
        self._pubsub = RedisPubSub(redis_url)
        await self._pubsub.connect()

        async def on_invalidate(event_payload: dict[str, Any]) -> None:
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

        await self._pubsub.listen(on_invalidate)
        await self._pubsub.start()

    async def _ensure_transport(self) -> None:
        if self._transport is None:
            self._transport = CashewsTransport.from_config(self._config)
            await self._transport.connect()

    async def _publish_invalidation(self, model: type[Any], pks: list[Any], action: str) -> None:
        if self._pubsub is None:
            return
        await self._pubsub.publish(
            {
                "table": model.__tablename__,
                "pks": pks,
                "action": action,
                "version": 1,
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

        Call this after ``session.commit()`` when you need strict read-after-write
        consistency against the cache on the same session (e.g. in tests, or when
        a request commits and then immediately re-reads the same row). Normal
        request-per-session patterns don't need this because the invalidation
        task completes within one event loop turn.
        """

        if not self._pending_tasks:
            return
        await asyncio.gather(*list(self._pending_tasks), return_exceptions=True)

    def _schedule_flush(self, pending: dict[str, Any]) -> None:
        """Schedule invalidation application on the current event loop (sync Session path).

        For AsyncSession, the ``after_commit`` handler drives the coroutine
        synchronously via ``await_only`` instead of going through this method.
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

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("Sync wrappers cannot be used while an event loop is already running")

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

    @classmethod
    def _patch_async_get(cls) -> None:
        if cls._async_get_patched:
            return

        cls._original_async_get = AsyncSession.get

        async def patched_get(self: AsyncSession, entity: Any, ident: Any, **kwargs: Any) -> Any:
            bind = self.sync_session.get_bind(mapper=entity)
            manager = cls._engine_registry.get(id(bind))
            if manager is None or not manager.is_enabled(entity, "get"):
                return await cls._original_async_get(self, entity, ident, **kwargs)

            unsupported = (
                kwargs.get("options"),
                kwargs.get("populate_existing"),
                kwargs.get("with_for_update"),
                kwargs.get("identity_token"),
            )
            if any(unsupported):
                return await cls._original_async_get(self, entity, ident, **kwargs)

            statement = manager._build_get_statement(
                entity,
                ident,
                execution_options=kwargs.get("execution_options"),
            )
            result = await manager.execute(self, statement)
            return result.scalar_one_or_none()

        setattr(AsyncSession, "get", patched_get)  # noqa: B010
        cls._async_get_patched = True

    @classmethod
    def _restore_async_get(cls) -> None:
        if cls._async_get_patched and cls._original_async_get is not None:
            setattr(AsyncSession, "get", cls._original_async_get)  # noqa: B010
        cls._async_get_patched = False
        cls._original_async_get = None

    @staticmethod
    def _build_get_statement(entity: type[Any], ident: Any, execution_options: Any = None) -> Any:
        mapper = entity.__mapper__
        pk_columns = list(mapper.primary_key)
        values = [ident] if len(pk_columns) == 1 and not isinstance(ident, tuple) else list(ident)
        if len(pk_columns) != len(values):
            raise ValueError(f"Expected {len(pk_columns)} primary key values for {entity.__name__}, got {len(values)}")
        statement = select(entity).where(*[column == value for column, value in zip(pk_columns, values, strict=True)])
        if execution_options:
            statement = statement.execution_options(**dict(execution_options))
        return statement
