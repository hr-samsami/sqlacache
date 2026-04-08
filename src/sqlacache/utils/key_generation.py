"""Helpers for deterministic cache key generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.dialects import sqlite


def statement_to_sql(statement: Any, params: dict[str, Any] | None = None) -> str:
    """Compile a SQLAlchemy statement into a deterministic SQL string."""

    compiled = statement.compile(
        dialect=sqlite.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql = " ".join(str(compiled).split())
    if params:
        params_json = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
        return f"{sql}|{params_json}"
    return sql


def sql_to_cache_key(sql: str, prefix: str = "sqlacache") -> str:
    """Hash a normalized SQL string into a compact cache key."""

    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def generate_cache_key(
    statement: Any,
    params: dict[str, Any] | None = None,
    prefix: str = "sqlacache",
) -> str:
    """Generate a deterministic cache key from a statement and parameters."""

    return sql_to_cache_key(statement_to_sql(statement, params=params), prefix=prefix)
