"""Tests for dialogue/private-chat integration with TickCoordinator lifecycle."""

from __future__ import annotations

import asyncio

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult
from app.game_core.state.slices.scene import SceneEntry


class _CaptureActionLogHook(NoOpSettlementHook):
    HOOK_PRIORITY = 10
    HOOK_NAME = "capture_action_log"

    def __init__(self) -> None:
        super().__init__()
        self.seen_logs: list[list[dict[str, object]]] = []

    async def execute(self, context):
        self.seen_logs.append([dict(entry) for entry in context.action_log])
        return HookResult()


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


def test_finalize_external_turn_exposes_dialogue_turn_to_settlement_hooks() -> None:
    world = build_default_world("test_world", world_data={})
    runtime = build_runtime_for_world(world)
    runtime.state.time.restore({
        "day": 1,
        "slot": 8,
        "period": "day",
        "action_count": 5,
        "accumulated": 5 / 6,
    })
    capture = _CaptureActionLogHook()
    runtime.tick_coordinator.register_settlement_hook(capture)

    asyncio.run(
        runtime.tick_coordinator.finalize_external_turn(
            time_cost=1 / 6,
            turn_action_record={
                "type": "dialogue_turn",
                "actor": "player",
                "params": {"npc_id": "guild_girl", "intent": "talk"},
                "executed": True,
            },
        )
    )

    assert capture.seen_logs == [[
        {
            "type": "dialogue_turn",
            "actor": "player",
            "params": {"npc_id": "guild_girl", "intent": "talk"},
            "executed": True,
            "time_cost": 1 / 6,
            "source": "external_turn",
        }
    ]]


def test_finalize_external_turn_exposes_private_chat_turn_to_settlement_hooks() -> None:
    world = build_default_world("test_world", world_data={})
    runtime = build_runtime_for_world(world)
    runtime.state.time.restore({
        "day": 1,
        "slot": 8,
        "period": "day",
        "action_count": 5,
        "accumulated": 5 / 6,
    })
    capture = _CaptureActionLogHook()
    runtime.tick_coordinator.register_settlement_hook(capture)

    asyncio.run(
        runtime.tick_coordinator.finalize_external_turn(
            time_cost=1 / 6,
            turn_action_record={
                "type": "private_chat_turn",
                "actor": "player",
                "params": {"npc_id": "cow_girl", "intent": "private_chat"},
                "executed": True,
            },
        )
    )

    assert capture.seen_logs == [[
        {
            "type": "private_chat_turn",
            "actor": "player",
            "params": {"npc_id": "cow_girl", "intent": "private_chat"},
            "executed": True,
            "time_cost": 1 / 6,
            "source": "external_turn",
        }
    ]]
