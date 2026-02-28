"""Tests for StatusEffectHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks import StatusEffectHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import StatusEffectHandler
from app.game_core.state import StateContainer, StateDelta
from app.game_core.state.slices import PlayerSlice, SceneSlice


def _make_context(
    *,
    include_player: bool = True,
    active_effects: list[dict] | None = None,
    hp: int = 10,
) -> SettlementContext:
    state = StateContainer()
    if include_player:
        player = PlayerSlice()
        player.restore(
            {
                "hp": hp,
                "max_hp": 12,
                "active_effects": active_effects or [],
            }
        )
        state.register(player)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    rules_engine.register(StatusEffectHandler())

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class TestStatusEffectHook:
    def test_missing_player_is_noop(self) -> None:
        result = asyncio.run(StatusEffectHook().execute(_make_context(include_player=False)))

        assert result.metadata == {"status": "noop", "reason": "missing_player"}
        assert result.sse_events == []

    def test_tick_emits_sse_when_effect_changes(self) -> None:
        context = _make_context(
            active_effects=[
                {
                    "effect_id": "regen",
                    "effect_type": "heal_over_time",
                    "remaining_ticks": 1,
                    "duration_ticks": 1,
                    "periodic": {"heal": 2},
                }
            ],
            hp=5,
        )

        result = asyncio.run(StatusEffectHook().execute(context))

        assert result.metadata["status"] == "ticked"
        assert result.metadata["effect_result"]["expired_count"] == 1
        assert result.metadata["effect_result"]["hp_delta"] == 2
        assert context.state.player.hp == 7
        assert context.state.player.active_effects == []
        assert len(result.sse_events) == 1
        assert result.sse_events[0].event_type == "status_effects_ticked"
        assert result.sse_events[0].payload["expired_count"] == 1

    def test_no_active_effects_returns_noop_without_sse(self) -> None:
        context = _make_context(active_effects=[])

        result = asyncio.run(StatusEffectHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["effect_result"]["applied_count"] == 0
        assert result.sse_events == []
