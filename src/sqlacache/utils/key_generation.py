"""Helpers for deterministic cache key generation."""

from __future__ import annotations

import hashlib
import warnings
from typing import Any

from sqlalchemy import exc as sa_exc
from sqlalchemy.engine.default import StrCompileDialect


def statement_to_sql(statement: Any) -> str:
    """Compile a SQLAlchemy statement into a deterministic SQL string.

    Uses ``StrCompileDialect`` (SQLAlchemy's dialect-agnostic compiler) rather
    than any specific dialect. That matters for two reasons:

    1. **Correctness across dialects.** A Postgres-specific construct
       (``ILIKE``, ``JSONB`` operators, ``ON CONFLICT``) compiled against the
       sqlite dialect can raise or silently degrade, and two semantically
       different statements can collapse to the same sqlite rendering.
    2. **Stability.** ``StrCompileDialect`` is the compiler SQLAlchemy uses
       internally for ``str(stmt)`` and is explicitly designed for this kind
       of cross-dialect representation.

    Bind parameters are embedded via ``literal_binds=True`` so the hash
    reflects the actual parameter values, not just the SQL shape.
    """

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=sa_exc.SAWarning)
        compiled = statement.compile(
            dialect=StrCompileDialect(),
            compile_kwargs={"literal_binds": True},
        )
    return " ".join(str(compiled).split())


def sql_to_cache_key(sql: str, prefix: str = "sqlacache") -> str:
    """Hash a normalized SQL string into a compact cache key."""

    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def generate_cache_key(
    statement: Any,
    prefix: str = "sqlacache",
) -> str:
    """Generate a deterministic cache key from a statement."""

    return sql_to_cache_key(statement_to_sql(statement), prefix=prefix)
