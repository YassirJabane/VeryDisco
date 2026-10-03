from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any


class LibraryScanCoordinator:
    """Serializes filesystem-heavy operations per user/library."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, str] = {}

    @asynccontextmanager
    async def operation(self, user_id: Any, name: str):
        key = str(user_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            self._active[key] = name
            try:
                yield
            finally:
                if self._active.get(key) == name:
                    self._active.pop(key, None)

    def active(self, user_id: Any) -> str | None:
        return self._active.get(str(user_id))


library_scan_coordinator = LibraryScanCoordinator()
