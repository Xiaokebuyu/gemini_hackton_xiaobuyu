"""Tests for NPC combat effect ticking."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.game_core.orchestration.hooks.status_effect import StatusEffectHook
from app.game_core.rules.handlers.status_effect import StatusEffectHandler
from app.game_core.rules.models import Command
from app.game_core.state.base import StateContainer
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.state.slices.player import PlayerSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

handler = StatusEffectHandler()


def _make_state(
    hostile_tracking: dict[str, dict[str, Any]] | None = None,
    area_id: str = "forest",
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"hp": 20, "max_hp": 20})
    state.register(player)

    areas = AreaSlice()
    area = AreaState()
    if hostile_tracking:
        area.hostile_tracking = dict(hostile_tracking)
    areas.areas[area_id] = area
    state.register(areas)
    return state


def _make_world() -> MagicMock:
    return MagicMock()


def _combat_with_effects(
    participants: list[dict[str, Any]],
    combat_active: bool = True,
) -> dict[str, Any]:
    return {
        "combat_active": combat_active,
        "status": "engaged" if combat_active else "cleared",
        "participants": participants,
    }


# ------------------------------------------------------------------
# tick_combat_effects — handler tests
# ------------------------------------------------------------------


def test_tick_combat_no_combats():
    """No hostile_tracking → success with 0 combats processed."""
    state = _make_state()
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    assert result.metadata["combats_processed"] == 0
    assert result.metadata["participants_ticked"] == 0


def test_tick_combat_decrements_duration():
    """Participant effect remaining_ticks 3 → 2."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 10,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "burn", "effect_type": "dot", "remaining_ticks": 3, "periodic": {}},
                ],
            },
        ]),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    assert result.metadata["participants_ticked"] == 1
    # Verify the state change was generated
    assert result.delta is not None
    changes = result.delta.changes
    assert len(changes) == 1
    updated_hostile = changes[0].value
    effects = updated_hostile["participants"][0]["active_effects"]
    assert len(effects) == 1
    assert effects[0]["remaining_ticks"] == 2


def test_tick_combat_expires_effect():
    """remaining_ticks 1 → effect removed."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 10,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "stun", "effect_type": "cc", "remaining_ticks": 1, "periodic": {}},
                ],
            },
        ]),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    assert result.metadata["effects_expired"] == 1
    updated_hostile = result.delta.changes[0].value
    effects = updated_hostile["participants"][0]["active_effects"]
    assert effects == []


def test_tick_combat_periodic_damage():
    """Periodic damage reduces participant HP."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 10,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "poison", "effect_type": "dot", "periodic": {"damage": 3}},
                ],
            },
        ]),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    assert result.metadata["total_hp_delta"] == -3
    updated_hostile = result.delta.changes[0].value
    assert updated_hostile["participants"][0]["hp"] == 7


def test_tick_combat_periodic_heal():
    """Periodic heal restores participant HP (capped at max_hp)."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 5,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "regen", "effect_type": "heal", "periodic": {"heal": 20}},
                ],
            },
        ]),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    updated_hostile = result.delta.changes[0].value
    assert updated_hostile["participants"][0]["hp"] == 10  # capped at max_hp


def test_tick_combat_kills_participant():
    """Periodic damage reducing HP to 0 marks participant as dead."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 2,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "fire", "effect_type": "dot", "periodic": {"damage": 5}},
                ],
            },
        ]),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    updated_hostile = result.delta.changes[0].value
    participant = updated_hostile["participants"][0]
    assert participant["hp"] == 0
    assert participant["alive"] is False


def test_tick_combat_skips_cleared():
    """Cleared combats are not processed."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects(
            [
                {
                    "monster_id": "goblin",
                    "hp": 10,
                    "max_hp": 10,
                    "alive": True,
                    "active_effects": [
                        {"effect_id": "burn", "effect_type": "dot", "remaining_ticks": 3, "periodic": {"damage": 1}},
                    ],
                },
            ],
            combat_active=False,
        ),
    })
    cmd = Command(type="tick_combat_effects", source="system")
    result = handler.compute(cmd, state, _make_world())

    assert result.success
    assert result.metadata["combats_processed"] == 0
    assert result.metadata["participants_ticked"] == 0


def test_tick_effect_list_shared_logic():
    """_tick_effect_list produces correct results for both player and combat use."""
    effects = [
        {"effect_id": "a", "remaining_ticks": 2, "periodic": {"damage": 1}},
        {"effect_id": "b", "remaining_ticks": 1, "periodic": {"heal": 3}},
        {"effect_id": "c", "periodic": {}},  # no remaining_ticks → kept forever
    ]
    next_effects, next_hp, expired, processed = handler._tick_effect_list(
        effects, hp=10, max_hp=15
    )

    assert len(next_effects) == 2  # "a" survives (2→1), "b" expired (1→0), "c" kept
    assert expired == 1
    assert processed == 3
    # HP: 10 - 1 (damage from a) + 3 (heal from b) = 12
    assert next_hp == 12


# ------------------------------------------------------------------
# Hook integration
# ------------------------------------------------------------------


def _make_settlement_context(state: StateContainer) -> MagicMock:
    from app.game_core.rules.engine import RulesEngine

    engine = RulesEngine()
    engine.register(StatusEffectHandler())

    ctx = MagicMock()
    ctx.state = state

    def real_execute(cmd: Command):
        result = engine.execute(cmd, state, MagicMock())
        if result.delta:
            state.apply(result.delta)
        return result

    ctx.execute_command = MagicMock(side_effect=real_execute)
    return ctx


def test_hook_emits_combat_effects_ticked_sse():
    """Hook emits combat_effects_ticked SSE when effects change."""
    state = _make_state(hostile_tracking={
        "encounter_1": _combat_with_effects([
            {
                "monster_id": "goblin",
                "hp": 10,
                "max_hp": 10,
                "alive": True,
                "active_effects": [
                    {"effect_id": "burn", "effect_type": "dot", "remaining_ticks": 2, "periodic": {"damage": 1}},
                ],
            },
        ]),
    })
    ctx = _make_settlement_context(state)
    result = asyncio.run(StatusEffectHook().execute(ctx))

    sse_types = [e.event_type for e in result.sse_events]
    assert "combat_effects_ticked" in sse_types


def test_hook_no_combat_tick_without_areas_slice():
    """Without areas slice, hook only ticks player effects."""
    state = StateContainer()
    player = PlayerSlice()
    player.restore({"hp": 20, "max_hp": 20})
    state.register(player)
    ctx = _make_settlement_context(state)

    result = asyncio.run(StatusEffectHook().execute(ctx))

    # Should not crash, no combat_effects_ticked SSE
    sse_types = [e.event_type for e in result.sse_events]
    assert "combat_effects_ticked" not in sse_types
