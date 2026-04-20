"""Configuration helpers for sqlacache."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import import_module
from typing import Any
from urllib.parse import urlparse

from sqlacache.exceptions import ConfigError
from sqlacache.manager import CacheManager

ALL_OPS = frozenset({"get", "fetch", "count", "exists"})
VALID_OPS = ALL_OPS


def normalize_ops(ops_config: str | Iterable[str] | None) -> frozenset[str]:
    """Normalize an ops config into a validated frozenset."""

    if ops_config is None:
        return frozenset()
    if ops_config == "all":
        return ALL_OPS
    if isinstance(ops_config, str):
        raise ConfigError(f"Unknown ops value: {ops_config!r}")

    normalized = frozenset(ops_config)
    invalid_ops = normalized - VALID_OPS
    if invalid_ops:
        raise ConfigError(f"Unknown ops: {sorted(invalid_ops)!r}")
    return normalized


def _resolve_model(model_path: str) -> type[Any]:
    """Resolve a dotted import path to a Python class."""

    module_path, separator, class_name = model_path.rpartition(".")
    if not separator or not module_path or not class_name:
        raise ConfigError(f"Invalid model path: {model_path!r}")

    try:
        module = import_module(module_path)
    except ImportError as exc:  # pragma: no cover - exact import failures vary
        raise ConfigError(f"Could not import model module {module_path!r}") from exc

    try:
        model = getattr(module, class_name)
    except AttributeError as exc:  # pragma: no cover - exact import failures vary
        raise ConfigError(f"Module {module_path!r} does not define {class_name!r}") from exc

    if not isinstance(model, type):
        raise ConfigError(f"Resolved model path {model_path!r} is not a class")
    return model


def _normalize_backend(
    backend: str | Mapping[str, Any],
    *,
    serializer: str,
    prefix: str,
    compress: str | None,
) -> dict[str, Any]:
    if isinstance(backend, str):
        parsed = urlparse(backend)
        if not parsed.scheme:
            raise ConfigError(f"Invalid backend URL: {backend!r}")
        normalized_backend: dict[str, Any] = {"url": backend}
    elif isinstance(backend, Mapping):
        url = backend.get("url")
        if not isinstance(url, str) or not url:
            raise ConfigError("Backend mapping must define a non-empty 'url' string")
        parsed = urlparse(url)
        if not parsed.scheme:
            raise ConfigError(f"Invalid backend URL: {url!r}")
        normalized_backend = dict(backend)
    else:
        raise ConfigError("Backend must be a URL string or mapping")

    normalized_backend.setdefault("pickle_type", serializer)
    if compress is not None:
        normalized_backend.setdefault("compress_type", compress)
    return normalized_backend


def _normalize_models(
    models: Mapping[str, Mapping[str, Any] | None],
    *,
    default_timeout: int,
) -> tuple[dict[str, dict[str, Any] | None], dict[str, Any] | None]:
    if not models:
        raise ConfigError("models configuration must not be empty")

    wildcard_raw = models.get("*")
    wildcard_config: dict[str, Any] | None = None
    if wildcard_raw is not None:
        if not isinstance(wildcard_raw, Mapping):
            raise ConfigError("Wildcard model config must be a mapping or None")
        wildcard_timeout = wildcard_raw.get("timeout", default_timeout)
        if not isinstance(wildcard_timeout, int) or wildcard_timeout <= 0:
            raise ConfigError("Wildcard timeout must be a positive integer")
        wildcard_config = {
            "ops": normalize_ops(wildcard_raw.get("ops", "all")),
            "timeout": wildcard_timeout,
        }

    normalized_models: dict[str, dict[str, Any] | None] = {}
    for model_path, raw_config in models.items():
        if not isinstance(model_path, str) or not model_path:
            raise ConfigError("Model keys must be non-empty strings")

        if model_path != "*" and "." not in model_path:
            raise ConfigError(f"Invalid model path: {model_path!r}")

        if raw_config is None:
            normalized_models[model_path] = None
            continue

        if not isinstance(raw_config, Mapping):
            raise ConfigError(f"Model config for {model_path!r} must be a mapping or None")

        timeout = raw_config.get("timeout")
        if timeout is None:
            timeout = wildcard_config["timeout"] if wildcard_config is not None else default_timeout
        if not isinstance(timeout, int) or timeout <= 0:
            raise ConfigError(f"Timeout for {model_path!r} must be a positive integer")

        ops_default = wildcard_config["ops"] if wildcard_config is not None else "all"
        normalized_models[model_path] = {
            "ops": normalize_ops(raw_config.get("ops", ops_default)),
            "timeout": timeout,
        }

    return normalized_models, wildcard_config


def configure(
    *,
    backend: str | Mapping[str, Any],
    models: Mapping[str, Mapping[str, Any] | None],
    serializer: str = "sqlalchemy",
    prefix: str = "sqlacache",
    default_timeout: int = 3600,
    compress: str | None = None,
) -> CacheManager:
    """Create a cache manager from validated configuration."""

    if not isinstance(default_timeout, int) or default_timeout <= 0:
        raise ConfigError("default_timeout must be a positive integer")

    normalized_backend = _normalize_backend(
        backend,
        serializer=serializer,
        prefix=prefix,
        compress=compress,
    )
    normalized_models, wildcard_config = _normalize_models(
        models,
        default_timeout=default_timeout,
    )

    return CacheManager(
        config={
            "backend": normalized_backend,
            "models": normalized_models,
            "wildcard": wildcard_config,
            "serializer": serializer,
            "prefix": prefix,
            "default_timeout": default_timeout,
            "compress": compress,
        }
    )
