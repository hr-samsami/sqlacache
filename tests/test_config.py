from __future__ import annotations

import pytest

from sqlacache import CacheManager, ConfigError, configure
from sqlacache.config import (
    ALL_OPS,
    VALID_OPS,
    _normalize_backend,
    _normalize_models,
    _resolve_model,
    normalize_ops,
)

from .conftest import User

# --- normalize_ops ---


class TestNormalizeOps:
    def test_all_returns_full_set(self) -> None:
        assert normalize_ops("all") == frozenset({"get", "fetch", "count", "exists"})

    def test_none_returns_empty(self) -> None:
        assert normalize_ops(None) == frozenset()

    def test_list_of_valid_ops(self) -> None:
        result = normalize_ops(["get", "fetch"])
        assert result == frozenset({"get", "fetch"})

    def test_set_of_valid_ops(self) -> None:
        result = normalize_ops({"count", "exists"})
        assert result == frozenset({"count", "exists"})

    def test_tuple_of_valid_ops(self) -> None:
        result = normalize_ops(("get",))
        assert result == frozenset({"get"})

    def test_single_op_list(self) -> None:
        assert normalize_ops(["get"]) == frozenset({"get"})

    def test_empty_iterable_returns_empty(self) -> None:
        assert normalize_ops([]) == frozenset()

    def test_unknown_string_raises(self) -> None:
        with pytest.raises(ConfigError, match="Unknown ops value"):
            normalize_ops("invalid")

    def test_unknown_op_in_iterable_raises(self) -> None:
        with pytest.raises(ConfigError, match="Unknown ops"):
            normalize_ops(["get", "invalid_op"])

    def test_mixed_valid_and_invalid_raises(self) -> None:
        with pytest.raises(ConfigError, match="Unknown ops"):
            normalize_ops(["get", "fetch", "bad"])

    def test_all_ops_equals_valid_ops(self) -> None:
        assert ALL_OPS == VALID_OPS

    def test_deduplication(self) -> None:
        result = normalize_ops(["get", "get", "fetch"])
        assert result == frozenset({"get", "fetch"})


# --- _resolve_model ---


class TestResolveModel:
    def test_resolves_existing_model(self) -> None:
        path = f"{User.__module__}.{User.__name__}"
        assert _resolve_model(path) is User

    def test_invalid_path_no_dot(self) -> None:
        with pytest.raises(ConfigError, match="Invalid model path"):
            _resolve_model("NoDots")

    def test_invalid_path_empty_string(self) -> None:
        with pytest.raises(ConfigError, match="Invalid model path"):
            _resolve_model("")

    def test_invalid_path_trailing_dot(self) -> None:
        with pytest.raises(ConfigError, match="Invalid model path"):
            _resolve_model("module.")

    def test_invalid_path_leading_dot(self) -> None:
        with pytest.raises(ConfigError, match="Invalid model path"):
            _resolve_model(".ClassName")


# --- _normalize_backend ---


class TestNormalizeBackend:
    def test_string_url(self) -> None:
        result = _normalize_backend("redis://localhost:6379/0", serializer="sqlalchemy", prefix="sc", compress=None)
        assert result["url"] == "redis://localhost:6379/0"
        assert result["pickle_type"] == "sqlalchemy"

    def test_mem_url(self) -> None:
        result = _normalize_backend("mem://", serializer="sqlalchemy", prefix="sc", compress=None)
        assert result["url"] == "mem://"

    def test_mapping_backend(self) -> None:
        result = _normalize_backend(
            {"url": "redis://localhost:6379/0", "max_connections": 10},
            serializer="sqlalchemy",
            prefix="sc",
            compress=None,
        )
        assert result["url"] == "redis://localhost:6379/0"
        assert result["max_connections"] == 10

    def test_compress_added_when_specified(self) -> None:
        result = _normalize_backend("mem://", serializer="sqlalchemy", prefix="sc", compress="gzip")
        assert result["compress_type"] == "gzip"

    def test_compress_not_added_when_none(self) -> None:
        result = _normalize_backend("mem://", serializer="sqlalchemy", prefix="sc", compress=None)
        assert "compress_type" not in result

    def test_mapping_preserves_existing_pickle_type(self) -> None:
        result = _normalize_backend(
            {"url": "mem://", "pickle_type": "json"},
            serializer="sqlalchemy",
            prefix="sc",
            compress=None,
        )
        assert result["pickle_type"] == "json"

    def test_invalid_url_no_scheme(self) -> None:
        with pytest.raises(ConfigError, match="Invalid backend URL"):
            _normalize_backend("/just/a/path", serializer="sqlalchemy", prefix="sc", compress=None)

    def test_mapping_missing_url(self) -> None:
        with pytest.raises(ConfigError, match="non-empty 'url' string"):
            _normalize_backend({"url": ""}, serializer="sqlalchemy", prefix="sc", compress=None)

    def test_mapping_none_url(self) -> None:
        with pytest.raises(ConfigError, match="non-empty 'url' string"):
            _normalize_backend({"url": None}, serializer="sqlalchemy", prefix="sc", compress=None)

    def test_mapping_no_url_key(self) -> None:
        with pytest.raises(ConfigError, match="non-empty 'url' string"):
            _normalize_backend({}, serializer="sqlalchemy", prefix="sc", compress=None)

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ConfigError, match="Backend must be a URL string or mapping"):
            _normalize_backend(42, serializer="sqlalchemy", prefix="sc", compress=None)  # type: ignore[arg-type]


# --- _normalize_models ---


class TestNormalizeModels:
    def test_empty_models_raises(self) -> None:
        with pytest.raises(ConfigError, match="must not be empty"):
            _normalize_models({}, default_timeout=60)

    def test_basic_model_config(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, wildcard = _normalize_models(
            {user_path: {"ops": "all", "timeout": 120}},
            default_timeout=60,
        )
        assert models[user_path]["ops"] == ALL_OPS
        assert models[user_path]["timeout"] == 120
        assert wildcard is None

    def test_wildcard_config(self) -> None:
        _models, wildcard = _normalize_models(
            {"*": {"ops": "all", "timeout": 300}},
            default_timeout=60,
        )
        assert wildcard is not None
        assert wildcard["ops"] == ALL_OPS
        assert wildcard["timeout"] == 300

    def test_model_inherits_wildcard_timeout(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, _ = _normalize_models(
            {
                "*": {"timeout": 500},
                user_path: {"ops": ["get"]},
            },
            default_timeout=60,
        )
        assert models[user_path]["timeout"] == 500

    def test_model_inherits_wildcard_ops(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, _ = _normalize_models(
            {
                "*": {"ops": ["get", "fetch"]},
                user_path: {"timeout": 120},
            },
            default_timeout=60,
        )
        assert models[user_path]["ops"] == frozenset({"get", "fetch"})

    def test_model_overrides_wildcard(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, _ = _normalize_models(
            {
                "*": {"ops": "all", "timeout": 300},
                user_path: {"ops": ["get"], "timeout": 120},
            },
            default_timeout=60,
        )
        assert models[user_path]["ops"] == frozenset({"get"})
        assert models[user_path]["timeout"] == 120

    def test_none_config_disables_model(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, _ = _normalize_models(
            {user_path: None, "*": {"timeout": 60}},
            default_timeout=60,
        )
        assert models[user_path] is None

    def test_invalid_model_path_no_dot(self) -> None:
        with pytest.raises(ConfigError, match="Invalid model path"):
            _normalize_models({"NoDots": {"ops": "all"}}, default_timeout=60)

    def test_non_string_key_raises(self) -> None:
        with pytest.raises(ConfigError, match="non-empty strings"):
            _normalize_models({123: {"ops": "all"}}, default_timeout=60)  # type: ignore[dict-item]

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ConfigError, match="non-empty strings"):
            _normalize_models({"": {"ops": "all"}}, default_timeout=60)

    def test_invalid_timeout_zero(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        with pytest.raises(ConfigError, match="positive integer"):
            _normalize_models({user_path: {"timeout": 0}}, default_timeout=60)

    def test_invalid_timeout_negative(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        with pytest.raises(ConfigError, match="positive integer"):
            _normalize_models({user_path: {"timeout": -1}}, default_timeout=60)

    def test_invalid_timeout_string(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        with pytest.raises(ConfigError, match="positive integer"):
            _normalize_models({user_path: {"timeout": "60"}}, default_timeout=60)  # type: ignore[dict-item]

    def test_wildcard_invalid_timeout(self) -> None:
        with pytest.raises(ConfigError, match="Wildcard timeout must be a positive integer"):
            _normalize_models({"*": {"timeout": -1}}, default_timeout=60)

    def test_wildcard_non_mapping_raises(self) -> None:
        with pytest.raises(ConfigError, match="Wildcard model config must be a mapping"):
            _normalize_models({"*": "invalid"}, default_timeout=60)  # type: ignore[dict-item]

    def test_model_config_non_mapping_raises(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        with pytest.raises(ConfigError, match="must be a mapping or None"):
            _normalize_models({user_path: "invalid"}, default_timeout=60)  # type: ignore[dict-item]

    def test_default_timeout_used_without_wildcard(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        models, _ = _normalize_models(
            {user_path: {"ops": "all"}},
            default_timeout=999,
        )
        assert models[user_path]["timeout"] == 999


# --- configure ---


class TestConfigure:
    def test_returns_cache_manager(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}})
        assert isinstance(manager, CacheManager)

    def test_config_is_accessible(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}})
        assert manager.config["prefix"] == "sqlacache"
        assert manager.config["invalidation"] == "row"
        assert manager.config["default_timeout"] == 3600
        assert manager.config["serializer"] == "sqlalchemy"

    def test_custom_prefix(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}}, prefix="myapp")
        assert manager.config["prefix"] == "myapp"

    def test_custom_invalidation_mode(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}}, invalidation="table")
        assert manager.config["invalidation"] == "table"

    def test_invalid_invalidation_mode(self) -> None:
        with pytest.raises(ConfigError, match="Unsupported invalidation mode"):
            configure(backend="mem://", models={"*": {"timeout": 60}}, invalidation="invalid")

    def test_invalid_default_timeout_zero(self) -> None:
        with pytest.raises(ConfigError, match="default_timeout must be a positive integer"):
            configure(backend="mem://", models={"*": {"timeout": 60}}, default_timeout=0)

    def test_invalid_default_timeout_negative(self) -> None:
        with pytest.raises(ConfigError, match="default_timeout must be a positive integer"):
            configure(backend="mem://", models={"*": {"timeout": 60}}, default_timeout=-5)

    def test_invalid_default_timeout_string(self) -> None:
        with pytest.raises(ConfigError, match="default_timeout must be a positive integer"):
            configure(backend="mem://", models={"*": {"timeout": 60}}, default_timeout="60")  # type: ignore[arg-type]

    def test_empty_models_raises(self) -> None:
        with pytest.raises(ConfigError):
            configure(backend="mem://", models={})

    def test_invalid_backend_raises(self) -> None:
        with pytest.raises(ConfigError):
            configure(backend="not-a-url", models={"*": {"timeout": 60}})

    def test_compress_option_passed_through(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}}, compress="gzip")
        assert manager.config["compress"] == "gzip"
        assert manager.config["backend"]["compress_type"] == "gzip"

    def test_custom_serializer(self) -> None:
        manager = configure(backend="mem://", models={"*": {"timeout": 60}}, serializer="json")
        assert manager.config["serializer"] == "json"

    def test_backend_mapping(self) -> None:
        manager = configure(
            backend={"url": "mem://", "max_connections": 10},
            models={"*": {"timeout": 60}},
        )
        assert manager.config["backend"]["url"] == "mem://"
        assert manager.config["backend"]["max_connections"] == 10

    def test_multiple_models(self) -> None:
        user_path = f"{User.__module__}.{User.__name__}"
        manager = configure(
            backend="mem://",
            models={
                user_path: {"ops": ["get"], "timeout": 30},
                "*": {"ops": "all", "timeout": 60},
            },
        )
        assert manager.config["models"][user_path]["ops"] == frozenset({"get"})
        assert manager.config["wildcard"]["ops"] == ALL_OPS
