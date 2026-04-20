"""Row-level invalidation helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlacache.transport import CacheTransport

logger = logging.getLogger(__name__)

_TABLE_VERSION_PREFIX = "__sqlacache_tv__"


def generate_tags(model: type[Any] | str, pks: list[Any]) -> list[str]:
    """Generate dependency tags from model/table and PK values.

    ``None`` PKs are dropped with a warning. A ``None`` PK here means something
    upstream tried to invalidate an instance that was never flushed — usually a
    bug in user code, not sqlacache, but silent drops make it hard to diagnose.
    """

    table_name = model if isinstance(model, str) else model.__tablename__
    tags: list[str] = []
    dropped = 0
    for pk in pks:
        if pk is None:
            dropped += 1
            continue
        tags.append(f"{table_name}:{pk}")
    if dropped:
        logger.warning(
            "sqlacache: dropped %d None PK(s) while generating tags for %s; "
            "likely an unflushed or detached instance",
            dropped,
            table_name,
        )
    return tags


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
