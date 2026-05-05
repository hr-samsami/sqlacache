from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence


class CacheTransport(Protocol):
    """Protocol for cache storage backends."""

    async def connect(self) -> None:
        """Initialize the transport."""

    async def disconnect(self) -> None:
        """Release backend resources."""

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached value."""

    async def set(
        self,
        key: str,
        value: Any,
        expire: int,
        tags: Sequence[str] | None = None,
    ) -> None:
        """Store a cached value."""

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys."""

    async def delete_tags(self, *tags: str) -> None:
        """Invalidate keys associated with tags."""

    async def clear(self) -> None:
        """Delete all cached entries."""

    async def incr(self, key: str) -> int:
        """Atomically increment an integer counter and return the new value."""

    async def is_available(self) -> bool:
        """Return whether the backend is usable."""
