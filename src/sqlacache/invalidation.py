from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlacache.transport import CacheTransport

logger = logging.getLogger(__name__)

_TABLE_VERSION_PREFIX = "__sqlacache_tv__"


def generate_tags(model: type[Any] | str, pks: list[Any]) -> list[str]:
    """Generate dependency tags from model/table and PK values.

    Single-column PK ``v`` becomes ``"{table}:{v}"``. Composite PKs (tuples)
    become ``"{table}:{v1}|{v2}|..."`` — a deterministic, whitespace-free
    encoding so two workers agree on the tag for the same composite PK
    regardless of how Python's ``repr(tuple)`` renders it. ``None`` PKs are
    dropped with a warning (a ``None`` PK means something upstream tried to
    invalidate an unflushed instance — usually a user-code bug, but silent
    drops make it hard to diagnose).
    """

    table_name = model if isinstance(model, str) else model.__tablename__
    tags: list[str] = []
    dropped = 0
    for pk in pks:
        if pk is None:
            dropped += 1
            continue
        if isinstance(pk, tuple):
            tags.append(f"{table_name}:{_encode_composite_pk(pk)}")
        else:
            tags.append(f"{table_name}:{pk}")
    if dropped:
        logger.warning(
            "sqlacache: dropped %d None PK(s) while generating tags for %s; likely an unflushed or detached instance",
            dropped,
            table_name,
        )
    return tags


def _encode_composite_pk(pk: tuple[Any, ...]) -> str:
    """Encode a composite PK tuple into a stable, whitespace-free string."""

    return "|".join(str(part) for part in pk)


async def invalidate_tags(transport: CacheTransport, *tags: str) -> None:
    """Invalidate transport entries for the provided tags."""

    if not tags:
        return
    await transport.delete_tags(*tags)


def _table_version_key(model: type[Any] | str) -> str:
    table_name = model if isinstance(model, str) else model.__tablename__
    return f"{_TABLE_VERSION_PREFIX}{table_name}"


async def _bump_table_version(transport: CacheTransport, model: type[Any] | str) -> int:
    """Increment the table-version counter in the shared cache and return the new value.

    A successful ``incr`` always returns a positive integer (cashews seeds
    missing keys to 0 then increments). A 0 return therefore means the
    underlying transport swallowed a failure under ``suppress=True`` — and
    that means table-level invalidation just silently failed, which is a
    correctness issue the user should know about.
    """

    key = _table_version_key(model)
    new_version = await transport.incr(key)
    if new_version == 0:
        table_name = model if isinstance(model, str) else model.__tablename__
        logger.warning(
            "sqlacache: failed to bump table version for %r — cache may serve stale rows "
            "for this table until TTL expiry. Check transport configuration.",
            table_name,
        )
    return new_version


async def _get_table_version(transport: CacheTransport, model: type[Any] | str) -> int:
    """Return the current table-version counter from the shared cache."""

    key = _table_version_key(model)
    value = await transport.get(key)
    return int(value) if value is not None else 0
