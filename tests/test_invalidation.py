"""Comprehensive tests for sqlacache.invalidation module."""

from __future__ import annotations

import uuid

from sqlacache.invalidation import (
    _TABLE_VERSION_PREFIX,
    _bump_table_version,
    _get_table_version,
    _table_version_key,
    generate_tags,
    invalidate_tags,
)

from .conftest import CompositeRecord, Product, User

# --- generate_tags ---


class TestGenerateTags:
    def test_scalar_pks(self) -> None:
        assert generate_tags(User, [1, 2, 3]) == ["users:1", "users:2", "users:3"]

    def test_composite_pks(self) -> None:
        assert generate_tags(CompositeRecord, [(1, 2)]) == ["composite_records:1|2"]

    def test_multiple_composite_pks(self) -> None:
        result = generate_tags(CompositeRecord, [(1, 2), (3, 4)])
        assert result == ["composite_records:1|2", "composite_records:3|4"]

    def test_ignores_none_pks(self) -> None:
        assert generate_tags(User, [None, 1, None, 2]) == ["users:1", "users:2"]

    def test_all_none_pks_returns_empty(self) -> None:
        assert generate_tags(User, [None, None]) == []

    def test_empty_pks_returns_empty(self) -> None:
        assert generate_tags(User, []) == []

    def test_uuid_pks(self) -> None:
        uid = uuid.uuid4()
        result = generate_tags(User, [uid])
        assert result == [f"users:{uid}"]

    def test_string_pks(self) -> None:
        assert generate_tags(User, ["abc", "def"]) == ["users:abc", "users:def"]

    def test_string_table_name(self) -> None:
        result = generate_tags("my_table", [1, 2])
        assert result == ["my_table:1", "my_table:2"]

    def test_different_models_produce_different_tags(self) -> None:
        user_tags = generate_tags(User, [1])
        product_tags = generate_tags(Product, [1])
        assert user_tags != product_tags
        assert user_tags == ["users:1"]
        assert product_tags == ["products:1"]


# --- _table_version_key ---


class TestTableVersionKey:
    def test_from_model_class(self) -> None:
        assert _table_version_key(User) == f"{_TABLE_VERSION_PREFIX}users"

    def test_from_string(self) -> None:
        assert _table_version_key("orders") == f"{_TABLE_VERSION_PREFIX}orders"

    def test_prefix_value(self) -> None:
        assert _TABLE_VERSION_PREFIX == "__sqlacache_tv__"


# --- _bump_table_version / _get_table_version ---


class TestTableVersion:
    async def test_initial_version_is_zero(self, memory_transport) -> None:
        version = await _get_table_version(memory_transport, User)
        assert version == 0

    async def test_bump_increments(self, memory_transport) -> None:
        v1 = await _bump_table_version(memory_transport, User)
        assert v1 == 1

        v2 = await _bump_table_version(memory_transport, User)
        assert v2 == 2

    async def test_get_after_bump(self, memory_transport) -> None:
        await _bump_table_version(memory_transport, User)
        assert await _get_table_version(memory_transport, User) == 1

    async def test_independent_per_model(self, memory_transport) -> None:
        await _bump_table_version(memory_transport, User)
        await _bump_table_version(memory_transport, User)
        await _bump_table_version(memory_transport, Product)

        assert await _get_table_version(memory_transport, User) == 2
        assert await _get_table_version(memory_transport, Product) == 1

    async def test_bump_with_string_table_name(self, memory_transport) -> None:
        v = await _bump_table_version(memory_transport, "orders")
        assert v == 1
        assert await _get_table_version(memory_transport, "orders") == 1

    async def test_multiple_bumps(self, memory_transport) -> None:
        for i in range(10):
            v = await _bump_table_version(memory_transport, User)
            assert v == i + 1


# --- invalidate_tags ---


class TestInvalidateTags:
    async def test_deletes_tagged_entries(self, memory_transport) -> None:
        await memory_transport.set("users:1", {"id": 1}, expire=60, tags=["users:1"])
        await invalidate_tags(memory_transport, "users:1")
        assert await memory_transport.get("users:1") is None

    async def test_no_tags_is_noop(self, memory_transport) -> None:
        await memory_transport.set("key", "val", expire=60, tags=["tag"])
        await invalidate_tags(memory_transport)  # no tags passed
        assert await memory_transport.get("key") == "val"

    async def test_multiple_tags(self, memory_transport) -> None:
        await memory_transport.set("a", 1, expire=60, tags=["t1"])
        await memory_transport.set("b", 2, expire=60, tags=["t2"])
        await invalidate_tags(memory_transport, "t1", "t2")
        assert await memory_transport.get("a") is None
        assert await memory_transport.get("b") is None

    async def test_invalidate_nonexistent_tag_is_noop(self, memory_transport) -> None:
        await invalidate_tags(memory_transport, "nonexistent_tag")  # should not raise
