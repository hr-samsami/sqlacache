from __future__ import annotations

import uuid

import pytest

from sqlacache.invalidation import _bump_table_version, _get_table_version, generate_tags, invalidate_tags
from tests.conftest import CompositeRecord, User


def test_generate_tags_supports_scalar_and_composite_pks() -> None:
    assert generate_tags(User, [1, 2]) == ["users:1", "users:2"]
    assert generate_tags(CompositeRecord, [(1, 2)]) == ["composite_records:(1, 2)"]


def test_generate_tags_ignores_none_pks() -> None:
    assert generate_tags(User, [None, 1]) == ["users:1"]


@pytest.mark.asyncio
async def test_table_version_bump_and_get(memory_transport) -> None:
    before = await _get_table_version(memory_transport, User)
    after = await _bump_table_version(memory_transport, User)

    assert after == before + 1
    assert await _get_table_version(memory_transport, User) == after


@pytest.mark.asyncio
async def test_invalidate_tags_delegates_to_transport(memory_transport) -> None:
    await memory_transport.set("users:1", {"id": 1}, expire=60, tags=["users:1"])

    await invalidate_tags(memory_transport, "users:1")

    assert await memory_transport.get("users:1") is None


def test_uuid_pk_tags_stringify_cleanly() -> None:
    value = uuid.uuid4()
    assert generate_tags(User, [value]) == [f"users:{value}"]
