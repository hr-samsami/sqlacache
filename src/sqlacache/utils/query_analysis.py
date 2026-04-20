"""Query analysis helpers used by cache interception and invalidation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import inspect
from sqlalchemy.sql import functions
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.sql.selectable import Exists

if TYPE_CHECKING:
    from collections.abc import Iterable


def extract_model_from_statement(statement: Any) -> type[Any] | list[type[Any]] | None:
    """Extract ORM model classes referenced by a select or DML statement."""

    models: list[type[Any]] = []
    for description in getattr(statement, "column_descriptions", []):
        entity = description.get("entity")
        if isinstance(entity, type) and entity not in models:
            models.append(entity)

    if not models:
        entity_desc = getattr(statement, "entity_description", None)
        if entity_desc is not None:
            entity = entity_desc.get("entity")
            if isinstance(entity, type):
                models.append(entity)

    if not models:
        return None
    return models[0] if len(models) == 1 else models


def extract_pk_from_instance(instance: Any) -> Any:
    """Extract the primary key value for a mapped ORM instance."""

    mapper = inspect(instance).mapper
    values = tuple(getattr(instance, column.key) for column in mapper.primary_key)
    return values[0] if len(values) == 1 else values


def extract_pks_from_fetch_result(
    results: Iterable[Any],
    models: list[type[Any]],
) -> dict[type[Any], list[Any]]:
    """Extract primary keys grouped by model from ORM results."""

    pks_by_model: dict[type[Any], list[Any]] = {}
    for row in results:
        for model in models:
            value = _extract_model_instance(row, model)
            if value is None:
                continue
            pks_by_model.setdefault(model, []).append(extract_pk_from_instance(value))
    return pks_by_model


def detect_operation_type(statement: Any, models: list[type[Any]] | None = None) -> str:
    """Infer the cache operation kind for a SELECT statement.

    Inspects the SQLAlchemy AST rather than stringifying the statement and
    searching for ``"COUNT("`` / ``"EXISTS"`` substrings — the substring approach
    has false positives from user-supplied literals (e.g. a WHERE clause like
    ``Model.description.like("%count(%")``) and depends on dialect-specific
    stringification.
    """

    if isinstance(statement, Exists):
        return "exists"

    raw_columns = getattr(statement, "_raw_columns", None)
    if raw_columns:
        for col in raw_columns:
            if isinstance(col, Exists):
                return "exists"
            # sqlalchemy.sql.functions.count is the generic aggregate function.
            if isinstance(col, functions.count):
                return "count"

    if models and len(models) == 1 and _is_primary_key_lookup(statement, models[0]):
        return "get"
    return "fetch"


def _extract_model_instance(row: Any, model: type[Any]) -> Any | None:
    if isinstance(row, model):
        return row
    if isinstance(row, tuple):
        for value in row:
            if isinstance(value, model):
                return value

    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        for value in mapping.values():
            if isinstance(value, model):
                return value
    return None


def _is_primary_key_lookup(statement: Any, model: type[Any]) -> bool:
    mapper = inspect(model)
    pk_keys = {column.key for column in mapper.primary_key}
    criteria = getattr(statement, "_where_criteria", ())
    if len(criteria) != len(pk_keys):
        return False

    matched_keys: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, BinaryExpression):
            return False
        left = getattr(criterion, "left", None)
        column_name = getattr(left, "key", None)
        if column_name not in pk_keys:
            return False
        operator_name = getattr(getattr(criterion, "operator", None), "__name__", "")
        if operator_name != "eq":
            return False
        matched_keys.add(str(column_name))
    return matched_keys == pk_keys
