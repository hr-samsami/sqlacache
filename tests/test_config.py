from __future__ import annotations

import pytest

from sqlacache import ConfigError, configure
from sqlacache.config import _resolve_model, normalize_ops
from tests.conftest import User


def test_normalize_ops_supports_all() -> None:
    assert normalize_ops("all") == frozenset({"get", "fetch", "count", "exists"})


def test_configure_requires_models() -> None:
    with pytest.raises(ConfigError):
        configure(backend="mem://", models={})


def test_configure_rejects_invalid_backend() -> None:
    with pytest.raises(ConfigError):
        configure(backend="not-a-url", models={"*": {"timeout": 60}})


def test_resolve_model_imports_existing_model() -> None:
    path = f"{User.__module__}.{User.__name__}"
    assert _resolve_model(path) is User
