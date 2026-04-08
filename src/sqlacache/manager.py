"""Cache manager primitives."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

from sqlacache.interceptor import (
    build_bulk_delete_handler,
    build_bulk_update_handler,
    build_do_orm_execute_handler,
    build_invalidation_handler,
    handle_bulk_mutation,
    merge_cached_result,
    resolve_cached_result,
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
        if not model_config:
            return False
        return op in model_config["ops"]

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
        for model, pks in pks_by_model.items():
            tags.extend(generate_tags(model, pks))
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
        for task in list(self._pending_tasks):
            task.cancel()
        self._pending_tasks.clear()
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
        if isinstance(extracted, list):
            return extracted
        return [extracted]

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
                listener = build_invalidation_handler(self, action)
                self._listeners.append((model, identifier, listener))
                event.listen(model, identifier, listener, propagate=True)

        bulk_update_listener = build_bulk_update_handler(self)
        self._listeners.append((Session, "after_bulk_update", bulk_update_listener))
        event.listen(Session, "after_bulk_update", bulk_update_listener)

        bulk_delete_listener = build_bulk_delete_handler(self)
        self._listeners.append((Session, "after_bulk_delete", bulk_delete_listener))
        event.listen(Session, "after_bulk_delete", bulk_delete_listener)

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

    def _schedule_invalidation(self, model: type[Any], target: Any, action: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        pk = extract_pk_from_instance(target)
        task = asyncio.create_task(self.invalidate(model=model, pks=[pk]))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _schedule_table_bump(self, model: type[Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        transport = self._transport
        if transport is None:
            return
        task = loop.create_task(_bump_table_version(transport, model))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _handle_select(self, execute_state: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._run_sync(resolve_cached_result(self, execute_state))
        else:
            return execute_state.invoke_statement()

    def _handle_bulk_mutation(self, execute_state: Any) -> Any:
        return self._run_sync(handle_bulk_mutation(self, execute_state))

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise RuntimeError("Sync wrappers cannot be used while an event loop is already running")

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
        statement = select(entity).where(*[column == value for column, value in zip(pk_columns, values, strict=False)])
        if execution_options:
            statement = statement.execution_options(**dict(execution_options))
        return statement
