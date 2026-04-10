from __future__ import annotations

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.sql.selectable import Exists

from sqlacache.utils.query_analysis import (
    _extract_model_instance,
    _is_primary_key_lookup,
    detect_operation_type,
    extract_model_from_statement,
    extract_pk_from_instance,
    extract_pks_from_fetch_result,
)

from .conftest import CompositeRecord, Product, User

# --- extract_model_from_statement ---


class TestExtractModelFromStatement:
    def test_single_model(self) -> None:
        assert extract_model_from_statement(select(User)) is User

    def test_multi_model(self) -> None:
        result = extract_model_from_statement(select(User, Product))
        assert result == [User, Product]

    def test_no_model_returns_none(self) -> None:
        stmt = select(func.count())
        assert extract_model_from_statement(stmt) is None

    def test_no_column_descriptions(self) -> None:
        class FakeStatement:
            pass

        assert extract_model_from_statement(FakeStatement()) is None

    def test_deduplicates_models(self) -> None:
        stmt = select(User.id, User.name)
        result = extract_model_from_statement(stmt)
        assert result is User

    def test_entity_not_a_type_is_skipped(self) -> None:
        stmt = select(User.id)
        result = extract_model_from_statement(stmt)
        # User.id has entity=User which is a type, so this still extracts User
        assert result is User

    def test_update_statement(self) -> None:
        stmt = update(User).values(name="new")
        result = extract_model_from_statement(stmt)
        assert result is User

    def test_delete_statement(self) -> None:
        stmt = delete(User).where(User.id == 1)
        result = extract_model_from_statement(stmt)
        assert result is User


# --- extract_pk_from_instance ---


class TestExtractPkFromInstance:
    def test_single_pk(self) -> None:
        user = User(id=42, name="test")
        assert extract_pk_from_instance(user) == 42

    def test_composite_pk(self) -> None:
        record = CompositeRecord(tenant_id=1, user_id=2, label="x")
        assert extract_pk_from_instance(record) == (1, 2)

    def test_different_values(self) -> None:
        u1 = User(id=1, name="a")
        u2 = User(id=2, name="b")
        assert extract_pk_from_instance(u1) != extract_pk_from_instance(u2)


# --- extract_pks_from_fetch_result ---


class TestExtractPksFromFetchResult:
    def test_single_model_rows(self) -> None:
        rows = [User(id=1, name="u1"), User(id=2, name="u2")]
        result = extract_pks_from_fetch_result(rows, [User])
        assert result == {User: [1, 2]}

    def test_multi_model_tuples(self) -> None:
        rows = [(User(id=1, name="u1"), Product(id=10, name="p1", price=5))]
        result = extract_pks_from_fetch_result(rows, [User, Product])
        assert result == {User: [1], Product: [10]}

    def test_empty_results(self) -> None:
        result = extract_pks_from_fetch_result([], [User])
        assert result == {}

    def test_model_not_in_row_is_skipped(self) -> None:
        rows = [User(id=1, name="u1")]
        result = extract_pks_from_fetch_result(rows, [User, Product])
        assert User in result
        assert Product not in result

    def test_composite_pk_model(self) -> None:
        rows = [CompositeRecord(tenant_id=1, user_id=2, label="x")]
        result = extract_pks_from_fetch_result(rows, [CompositeRecord])
        assert result == {CompositeRecord: [(1, 2)]}


# --- _extract_model_instance ---


class TestExtractModelInstance:
    def test_direct_instance(self) -> None:
        user = User(id=1, name="u")
        assert _extract_model_instance(user, User) is user

    def test_instance_in_tuple(self) -> None:
        user = User(id=1, name="u")
        product = Product(id=1, name="p", price=5)
        assert _extract_model_instance((user, product), User) is user
        assert _extract_model_instance((user, product), Product) is product

    def test_wrong_type_returns_none(self) -> None:
        user = User(id=1, name="u")
        assert _extract_model_instance(user, Product) is None

    def test_empty_tuple_returns_none(self) -> None:
        assert _extract_model_instance((), User) is None

    def test_non_model_value_returns_none(self) -> None:
        assert _extract_model_instance("not a model", User) is None
        assert _extract_model_instance(42, User) is None


# --- _is_primary_key_lookup ---


class TestIsPrimaryKeyLookup:
    def test_single_pk_equality(self) -> None:
        stmt = select(User).where(User.id == 1)
        assert _is_primary_key_lookup(stmt, User) is True

    def test_no_where_clause(self) -> None:
        stmt = select(User)
        assert _is_primary_key_lookup(stmt, User) is False

    def test_non_pk_column(self) -> None:
        stmt = select(User).where(User.name == "test")
        assert _is_primary_key_lookup(stmt, User) is False

    def test_composite_pk_full_match(self) -> None:
        stmt = select(CompositeRecord).where(
            CompositeRecord.tenant_id == 1,
            CompositeRecord.user_id == 2,
        )
        assert _is_primary_key_lookup(stmt, CompositeRecord) is True

    def test_composite_pk_partial_match(self) -> None:
        stmt = select(CompositeRecord).where(CompositeRecord.tenant_id == 1)
        assert _is_primary_key_lookup(stmt, CompositeRecord) is False

    def test_extra_criteria_not_pk(self) -> None:
        stmt = select(User).where(User.id == 1, User.name == "test")
        assert _is_primary_key_lookup(stmt, User) is False

    def test_non_equality_operator(self) -> None:
        stmt = select(User).where(User.id > 1)
        assert _is_primary_key_lookup(stmt, User) is False

    def test_in_operator_not_equality(self) -> None:
        stmt = select(User).where(User.id.in_([1, 2, 3]))
        assert _is_primary_key_lookup(stmt, User) is False


# --- detect_operation_type ---


class TestDetectOperationType:
    def test_pk_lookup_is_get(self) -> None:
        stmt = select(User).where(User.id == 1)
        assert detect_operation_type(stmt, [User]) == "get"

    def test_select_all_is_fetch(self) -> None:
        stmt = select(User)
        assert detect_operation_type(stmt, [User]) == "fetch"

    def test_count_function(self) -> None:
        stmt = select(func.count(User.id))
        assert detect_operation_type(stmt, [User]) == "count"

    def test_exists_instance(self) -> None:
        stmt = exists(select(User).where(User.id == 1))
        assert isinstance(stmt, Exists)
        assert detect_operation_type(stmt, [User]) == "exists"

    def test_multi_model_is_fetch(self) -> None:
        stmt = select(User, Product)
        assert detect_operation_type(stmt, [User, Product]) == "fetch"

    def test_none_models_is_fetch(self) -> None:
        stmt = select(User)
        assert detect_operation_type(stmt, None) == "fetch"

    def test_filtered_non_pk_is_fetch(self) -> None:
        stmt = select(User).where(User.name == "test")
        assert detect_operation_type(stmt, [User]) == "fetch"
