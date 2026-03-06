from __future__ import annotations

import asyncio

from app.admin_coordinator import AdminCoordinator
from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def test_admin_coordinator_reuses_cached_session_handle() -> None:
    runtime = _runtime()
    coordinator = AdminCoordinator(runtime)

    created = asyncio.run(
        coordinator.create_session("goblin_slayer")
    )
    loaded_a = asyncio.run(
        coordinator.get_session("goblin_slayer", created.session_id)
    )
    loaded_b = asyncio.run(
        coordinator.get_session("goblin_slayer", created.session_id)
    )

    assert loaded_a is created
    assert loaded_b is created


def test_admin_coordinator_delete_invalidates_cache() -> None:
    runtime = _runtime()
    coordinator = AdminCoordinator(runtime)

    created = asyncio.run(
        coordinator.create_session("goblin_slayer")
    )
    stats_before = asyncio.run(coordinator.stats())
    deleted = asyncio.run(
        coordinator.delete_session("goblin_slayer", created.session_id)
    )
    stats_after = asyncio.run(coordinator.stats())

    assert deleted is True
    assert stats_before["count"] == 1
    assert stats_after["count"] == 0
    assert asyncio.run(
        coordinator.get_session("goblin_slayer", created.session_id)
    ) is None


def test_admin_coordinator_evicts_lru_when_cache_is_full() -> None:
    runtime = _runtime()
    coordinator = AdminCoordinator(runtime, max_cached_sessions=1)

    first = asyncio.run(
        coordinator.create_session("goblin_slayer")
    )
    second = asyncio.run(
        coordinator.create_session("goblin_slayer")
    )
    stats = asyncio.run(coordinator.stats())

    assert stats["count"] == 1
    assert stats["keys"] == [f"goblin_slayer:{second.session_id}"]
    reloaded_first = asyncio.run(
        coordinator.get_session("goblin_slayer", first.session_id)
    )
    assert reloaded_first is not None
    assert reloaded_first.session_id == first.session_id
