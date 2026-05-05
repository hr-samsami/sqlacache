from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from cashews import Cache

from sqlacache.exceptions import ConfigError, TransportError

logger = logging.getLogger(__name__)


class CashewsTransport:
    """Thin async wrapper around ``cashews.Cache``."""

    def __init__(self, url: str, **kwargs: Any) -> None:
        self._url = url
        self._kwargs = dict(kwargs)
        self._suppress = bool(self._kwargs.get("suppress", False))
        self._cache = Cache()
        self._connected = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> CashewsTransport:
        """Build a transport instance from normalized cache config."""

        backend_config = config.get("backend", {})
        if isinstance(backend_config, str):
            url = backend_config
            kwargs: dict[str, Any] = {}
        elif isinstance(backend_config, Mapping):
            url = backend_config.get("url", "mem://")
            if not isinstance(url, str) or not url:
                raise ConfigError("Backend mapping must define a non-empty 'url' string")
            kwargs = {key: value for key, value in backend_config.items() if key != "url"}
        else:
            raise ConfigError("Backend must be configured as a URL string or mapping")

        if "pickle_type" not in kwargs:
            kwargs["pickle_type"] = config.get("serializer", "sqlalchemy")

        return cls(url=url, **kwargs)

    async def connect(self) -> None:
        try:
            self._cache.setup(self._url, **self._kwargs)
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache connect failed for %s: %s", self._url, exc)
                return
            raise TransportError(f"Cache connect failed for {self._url!r}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        try:
            await self._cache.close()
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache disconnect failed for %s: %s", self._url, exc)
                return
            raise TransportError("Cache disconnect failed") from exc
        finally:
            self._connected = False

    async def get(self, key: str) -> Any | None:
        try:
            return await self._cache.get(key)
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache get failed for key %s: %s", key, exc)
                return None
            raise TransportError(f"Cache get failed for key {key!r}") from exc

    async def set(
        self,
        key: str,
        value: Any,
        expire: int,
        tags: Sequence[str] | None = None,
    ) -> None:
        try:
            await self._cache.set(key, value, expire=expire, tags=tags or ())
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache set failed for key %s: %s", key, exc)
                return
            raise TransportError(f"Cache set failed for key {key!r}") from exc

    async def delete(self, *keys: str) -> None:
        try:
            for key in keys:
                await self._cache.delete(key)
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache delete failed for keys %s: %s", keys, exc)
                return
            raise TransportError(f"Cache delete failed for keys {keys!r}") from exc

    async def delete_tags(self, *tags: str) -> None:
        try:
            await self._cache.delete_tags(*tags)
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache delete_tags failed for tags %s: %s", tags, exc)
                return
            raise TransportError(f"Cache delete_tags failed for tags {tags!r}") from exc

    async def clear(self) -> None:
        try:
            await self._cache.clear()
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache clear failed for %s: %s", self._url, exc)
                return
            raise TransportError("Cache clear failed") from exc

    async def incr(self, key: str) -> int:
        try:
            return int(await self._cache.incr(key))
        except Exception as exc:
            if self._suppress:
                logger.warning("Cache incr failed for key %s: %s", key, exc)
                return 0
            raise TransportError(f"Cache incr failed for key {key!r}") from exc

    async def is_available(self) -> bool:
        if not self._connected:
            return False
        try:
            # Use cashews' native ping rather than reading a sentinel key.
            # Reading a key would route through the configured serializer,
            # so a poisoned/unrelated value at that key would fail the
            # healthcheck even when the backend itself is fine.
            await self._cache.ping()
        except Exception:
            return False
        return True
