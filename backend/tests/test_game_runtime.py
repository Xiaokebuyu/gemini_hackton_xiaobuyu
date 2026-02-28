from __future__ import annotations

import asyncio

from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def test_game_runtime_get_world_caches_and_force_reload_rebuilds() -> None:
    runtime = _runtime()

    world_a = runtime.get_world("test_world", world_data={})
    world_b = runtime.get_world("test_world")
    world_c = runtime.get_world("test_world", world_data={}, force_reload=True)

    assert world_a is world_b
    assert world_c is not world_a
    assert runtime.has_world("test_world") is True


def test_game_runtime_can_create_list_and_delete_sessions_with_in_memory_store() -> None:
    runtime = _runtime()

    session = asyncio.run(
        runtime.create_session(
            "goblin_slayer",
            world_data={},
            session_id="sess_alpha",
        )
    )
    listed = asyncio.run(runtime.list_sessions("goblin_slayer"))

    assert session.session_id == "sess_alpha"
    assert [item.session_id for item in listed] == ["sess_alpha"]
    assert asyncio.run(runtime.delete_session("other_world", "sess_alpha")) is False
    assert asyncio.run(runtime.delete_session("goblin_slayer", "sess_alpha")) is True
    assert asyncio.run(runtime.list_sessions("goblin_slayer")) == []
