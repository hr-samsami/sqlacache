"""Helpers for deterministic cache key generation."""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Mapping
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

    Bind parameters baked into the statement are embedded via
    ``literal_binds=True``. Parameters supplied separately at execution time
    (``Session.get`` does this — the ``_get_clause`` is shared and the PK is
    passed at execute) must be folded in by the caller via ``_params_to_str``
    when building a cache key.
    """

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=sa_exc.SAWarning)
        compiled = statement.compile(
            dialect=StrCompileDialect(),
            compile_kwargs={"literal_binds": True},
        )
    return " ".join(str(compiled).split())


def _params_to_str(parameters: Any) -> str:
    """Render execute-time parameters into a stable string.

    ``Session.get`` and similar paths use a shared ``_get_clause`` and pass
    PK values through ``state.parameters``. Two ``session.get(User, 1)`` and
    ``session.get(User, 2)`` calls compile to the *same* SQL string, so the
    parameter dict is the only thing that distinguishes their cache keys.
    """

    if not parameters:
        return ""
    if isinstance(parameters, Mapping):
        return ";".join(f"{k}={parameters[k]!r}" for k in sorted(parameters))
    if isinstance(parameters, (list, tuple)):
        return ";".join(_params_to_str(p) for p in parameters)
    return repr(parameters)


def sql_to_cache_key(sql: str, prefix: str = "sqlacache") -> str:
    """Hash a normalized SQL string into a compact cache key."""

    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def generate_cache_key(
    statement: Any,
    prefix: str = "sqlacache",
    parameters: Any = None,
) -> str:
    """Generate a deterministic cache key from a statement.

    ``parameters`` is the ``ORMExecuteState.parameters`` dict (or list of
    dicts for executemany). When non-empty it is appended to the SQL before
    hashing, so two ``session.get`` calls for different PKs that share a
    compiled SQL string produce different cache keys.
    """

    sql = statement_to_sql(statement)
    if parameters:
        sql = f"{sql}|params={_params_to_str(parameters)}"
    return sql_to_cache_key(sql, prefix=prefix)
