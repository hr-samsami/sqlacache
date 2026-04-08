from __future__ import annotations

from sqlalchemy import func, select

from sqlacache.utils.query_analysis import (
    detect_operation_type,
    extract_model_from_statement,
    extract_pk_from_instance,
    extract_pks_from_fetch_result,
)
from tests.conftest import Product, User


def test_extract_model_from_statement_handles_single_and_multi_model() -> None:
    assert extract_model_from_statement(select(User)) is User
    extracted = extract_model_from_statement(select(User, Product))
    assert extracted == [User, Product]


def test_extract_pk_from_instance_handles_single_pk() -> None:
    assert extract_pk_from_instance(User(id=1, name="hamid")) == 1


def test_extract_pks_from_fetch_result_reads_row_mappings() -> None:
    rows = [(User(id=1, name="u1"), Product(id=2, name="p1", price=10))]

    result = extract_pks_from_fetch_result(rows, [User, Product])

    assert result == {User: [1], Product: [2]}


def test_detect_operation_type_supports_get_fetch_count() -> None:
    assert detect_operation_type(select(User).where(User.id == 1), [User]) == "get"
    assert detect_operation_type(select(User), [User]) == "fetch"
    assert detect_operation_type(select(func.count(User.id)), [User]) == "count"
