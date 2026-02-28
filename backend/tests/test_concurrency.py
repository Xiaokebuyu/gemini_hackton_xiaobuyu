"""Tests for session-level execution lock concurrency safety."""

from __future__ import annotations

import asyncio

from app.game_core import GameRuntime
from app.game_core.adapters.persistence import NullPersistencePort
from app.game_core.adapters.session_store import SaveStore


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def test_session_lock_returns_same_instance_for_same_id() -> None:
    """Same session_id yields the same Lock object."""
    runtime = _runtime()

    async def _check() -> None:
        lock_a = await runtime.session_lock("sess_1")
        lock_b = await runtime.session_lock("sess_1")
        assert lock_a is lock_b

    asyncio.run(_check())


def test_session_lock_returns_different_instance_for_different_ids() -> None:
    """Different session_ids yield independent Lock objects."""
    runtime = _runtime()

    async def _check() -> None:
        lock_a = await runtime.session_lock("sess_1")
        lock_b = await runtime.session_lock("sess_2")
        assert lock_a is not lock_b

    asyncio.run(_check())


def test_session_lock_serializes_concurrent_operations() -> None:
    """Two coroutines operating on the same session execute sequentially."""
    runtime = _runtime()
    execution_order: list[str] = []

    async def _worker(name: str, delay: float) -> None:
        lock = await runtime.session_lock("sess_shared")
        async with lock:
            execution_order.append(f"{name}_start")
            await asyncio.sleep(delay)
            execution_order.append(f"{name}_end")

    async def _run() -> None:
        await asyncio.gather(
            _worker("A", 0.05),
            _worker("B", 0.01),
        )

    asyncio.run(_run())

    # One must fully complete before the other starts
    assert execution_order[0].endswith("_start")
    assert execution_order[1].endswith("_end")
    assert execution_order[0][0] == execution_order[1][0]  # same worker
    assert execution_order[2].endswith("_start")
    assert execution_order[3].endswith("_end")
    assert execution_order[2][0] == execution_order[3][0]  # same worker
