"""Row-level invalidation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlacache.transport import CacheTransport

_TABLE_VERSION_PREFIX = "__sqlacache_tv__"


def generate_tags(model: type[Any], pks: list[Any]) -> list[str]:
    """Generate dependency tags from model/table and PK values."""

    table_name = model.__tablename__
    return [f"{table_name}:{pk}" for pk in pks if pk is not None]


async def invalidate_tags(transport: CacheTransport, *tags: str) -> None:
    """Invalidate transport entries for the provided tags."""

    if not tags:
        return
    await transport.delete_tags(*tags)


def _table_version_key(model: type[Any] | str) -> str:
    table_name = model if isinstance(model, str) else model.__tablename__
    return f"{_TABLE_VERSION_PREFIX}{table_name}"


async def _bump_table_version(transport: CacheTransport, model: type[Any] | str) -> int:
    """Increment the table-version counter in the shared cache and return the new value."""

    key = _table_version_key(model)
    return await transport.incr(key)


async def _get_table_version(transport: CacheTransport, model: type[Any] | str) -> int:
    """Return the current table-version counter from the shared cache."""

    key = _table_version_key(model)
    value = await transport.get(key)
    return int(value) if value is not None else 0
