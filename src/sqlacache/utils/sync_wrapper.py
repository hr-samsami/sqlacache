"""Sync wrappers around async cache APIs."""

from __future__ import annotations

import asyncio
from typing import Any


def run_sync(coro: Any) -> Any:
    """Run a coroutine from synchronous code."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Synchronous wrappers are not supported while an event loop is already running")
