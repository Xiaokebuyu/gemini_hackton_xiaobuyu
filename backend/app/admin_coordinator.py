"""Application-level session coordinator with cache and per-session locks."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import time
from typing import Awaitable, Callable, TypeVar

from app.game_core import GameRuntime, ManagedSession
from app.game_core.runtime import SaveResult

T = TypeVar("T")


@dataclass(slots=True)
class CachedSessionHandle:
    """One in-memory session handle."""

    key: str
    session: ManagedSession
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_accessed: float = field(default_factory=time.monotonic)


class AdminCoordinator:
    """Own per-session locking, caching, and persistence boundaries."""

    def __init__(
        self,
        runtime: GameRuntime,
        *,
        max_cached_sessions: int = 32,
    ) -> None:
        self.runtime = runtime
        self._max_cached_sessions = max_cached_sessions
        self._guard = asyncio.Lock()
        self._entries: OrderedDict[str, CachedSessionHandle] = OrderedDict()

    @staticmethod
    def _cache_key(world_id: str, session_id: str) -> str:
        return f"{world_id}:{session_id}"

    async def session_lock(self, world_id: str, session_id: str) -> asyncio.Lock:
        handle = await self._ensure_handle(world_id, session_id, session=None)
        return handle.lock

    async def create_session(self, world_id: str) -> ManagedSession:
        session = await self.runtime.create_session(world_id)
        await self._ensure_handle(world_id, session.session_id, session=session)
        return session

    async def get_session(
        self,
        world_id: str,
        session_id: str,
    ) -> ManagedSession | None:
        key = self._cache_key(world_id, session_id)
        async with self._guard:
            handle = self._entries.get(key)
            if handle is not None:
                handle.last_accessed = time.monotonic()
                self._entries.move_to_end(key)
                return handle.session

        session = await self.runtime.resume_session(world_id, session_id)
        if session is None:
            return None
        handle = await self._ensure_handle(world_id, session_id, session=session)
        return handle.session

    async def save_session(self, session: ManagedSession) -> SaveResult:
        result = await self.runtime.save_session(session)
        await self._ensure_handle(session.world_id, session.session_id, session=session)
        return result

    async def delete_session(self, world_id: str, session_id: str) -> bool:
        deleted = await self.runtime.delete_session(world_id, session_id)
        if deleted:
            await self.invalidate_session(world_id, session_id)
        return deleted

    async def invalidate_session(self, world_id: str, session_id: str) -> None:
        key = self._cache_key(world_id, session_id)
        async with self._guard:
            self._entries.pop(key, None)

    async def with_session(
        self,
        world_id: str,
        session_id: str,
        executor: Callable[[ManagedSession], Awaitable[T]],
        *,
        persist: bool = False,
    ) -> T | None:
        lock = await self.session_lock(world_id, session_id)
        async with lock:
            session = await self.get_session(world_id, session_id)
            if session is None:
                return None
            result = await executor(session)
            if persist:
                await self.save_session(session)
            return result

    async def clear(self) -> None:
        async with self._guard:
            self._entries.clear()

    async def stats(self) -> dict[str, object]:
        async with self._guard:
            return {
                "count": len(self._entries),
                "keys": list(self._entries.keys()),
                "max_cached_sessions": self._max_cached_sessions,
            }

    async def _ensure_handle(
        self,
        world_id: str,
        session_id: str,
        *,
        session: ManagedSession | None,
    ) -> CachedSessionHandle:
        key = self._cache_key(world_id, session_id)
        async with self._guard:
            handle = self._entries.get(key)
            if handle is not None:
                if session is not None:
                    handle.session = session
                handle.last_accessed = time.monotonic()
                self._entries.move_to_end(key)
                return handle

            if session is None:
                session = await self.runtime.resume_session(world_id, session_id)
                if session is None:
                    raise KeyError(key)
            handle = CachedSessionHandle(key=key, session=session)
            self._entries[key] = handle
            while len(self._entries) > self._max_cached_sessions:
                self._entries.popitem(last=False)
            return handle
