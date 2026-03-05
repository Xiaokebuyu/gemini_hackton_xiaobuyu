"""Tests for dialogue/private-chat integration with TickCoordinator lifecycle."""

from __future__ import annotations

import asyncio

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.state.slices.scene import SceneEntry


def test_finalize_external_turn_triggers_settlement_and_resets_scene_bus() -> None:
    """A pure dialogue turn should still advance the tick lifecycle."""
    world = build_default_world("test_world", world_data={})
    runtime = build_runtime_for_world(world)

    runtime.state.time.restore({
        "day": 1,
        "slot": 8,
        "period": "day",
        "action_count": 5,
        "accumulated": 5 / 6,
    })
    runtime.state.scene.add_entry(SceneEntry(
        source="player",
        content="One more line of dialogue",
        visibility="public",
        tags=["speech"],
    ))

    events = asyncio.run(
        runtime.tick_coordinator.finalize_external_turn(time_cost=1 / 6)
    )

    event_types = [event.event_type for event in events]
    assert "time_advanced" in event_types
    assert runtime.state.time.accumulated == 0.0
    assert runtime.state.scene.snapshot()["entries"] == []
