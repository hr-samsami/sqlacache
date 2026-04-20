from __future__ import annotations

from sqlalchemy import select

from sqlacache.utils.key_generation import generate_cache_key, sql_to_cache_key, statement_to_sql

from .conftest import Product, User

# --- statement_to_sql ---


class TestStatementToSql:
    def test_deterministic(self) -> None:
        stmt = select(User).where(User.id == 42)
        assert statement_to_sql(stmt) == statement_to_sql(stmt)

    def test_different_params_produce_different_sql(self) -> None:
        s1 = statement_to_sql(select(User).where(User.id == 1))
        s2 = statement_to_sql(select(User).where(User.id == 2))
        assert s1 != s2

    def test_whitespace_normalized(self) -> None:
        stmt = select(User)
        result = statement_to_sql(stmt)
        assert "  " not in result

    def test_different_models_produce_different_sql(self) -> None:
        s1 = statement_to_sql(select(User))
        s2 = statement_to_sql(select(Product))
        assert s1 != s2


# --- sql_to_cache_key ---


class TestSqlToCacheKey:
    def test_applies_prefix(self) -> None:
        key = sql_to_cache_key("SELECT 1", prefix="custom")
        assert key.startswith("custom:")

    def test_default_prefix(self) -> None:
        key = sql_to_cache_key("SELECT 1")
        assert key.startswith("sqlacache:")

    def test_hash_length_is_32(self) -> None:
        key = sql_to_cache_key("SELECT 1")
        digest = key.split(":")[1]
        assert len(digest) == 32

    def test_deterministic(self) -> None:
        assert sql_to_cache_key("SELECT 1") == sql_to_cache_key("SELECT 1")

    def test_different_sql_different_key(self) -> None:
        assert sql_to_cache_key("SELECT 1") != sql_to_cache_key("SELECT 2")

    def test_hash_is_hex(self) -> None:
        key = sql_to_cache_key("SELECT 1")
        digest = key.split(":")[1]
        int(digest, 16)  # raises if not hex


# --- generate_cache_key ---


class TestGenerateCacheKey:
    def test_deterministic(self) -> None:
        stmt = select(User).where(User.id == 1)
        assert generate_cache_key(stmt) == generate_cache_key(stmt)

    def test_different_where_clause(self) -> None:
        k1 = generate_cache_key(select(User).where(User.id == 1))
        k2 = generate_cache_key(select(User).where(User.id == 2))
        assert k1 != k2

    def test_custom_prefix(self) -> None:
        key = generate_cache_key(select(User), prefix="myapp")
        assert key.startswith("myapp:")

    def test_different_models(self) -> None:
        k1 = generate_cache_key(select(User))
        k2 = generate_cache_key(select(Product))
        assert k1 != k2

    def test_format_is_prefix_colon_hash(self) -> None:
        key = generate_cache_key(select(User), prefix="sc")
        parts = key.split(":")
        assert len(parts) == 2
        assert parts[0] == "sc"
        assert len(parts[1]) == 32
