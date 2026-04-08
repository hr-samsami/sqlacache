from __future__ import annotations

from sqlalchemy import select

from sqlacache.utils.key_generation import generate_cache_key, sql_to_cache_key, statement_to_sql
from tests.conftest import User


def test_statement_to_sql_is_deterministic() -> None:
    stmt = select(User).where(User.id == 42)

    assert statement_to_sql(stmt) == statement_to_sql(stmt)


def test_generate_cache_key_differs_for_parameters() -> None:
    key1 = generate_cache_key(select(User).where(User.id == 1))
    key2 = generate_cache_key(select(User).where(User.id == 2))

    assert key1 != key2


def test_sql_to_cache_key_applies_prefix() -> None:
    key = sql_to_cache_key("SELECT 1", prefix="custom")

    assert key.startswith("custom:")
    assert len(key.split(":")[1]) == 32
