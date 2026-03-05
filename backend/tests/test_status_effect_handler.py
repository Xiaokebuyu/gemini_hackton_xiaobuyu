"""Tests for StatusEffectHandler."""

from __future__ import annotations

import pytest

from app.game_core.content import WorldInstance
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import StatusEffectHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


def _make_state(*, active_effects: list[dict] | None = None, hp: int = 10) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "hp": hp,
            "max_hp": 12,
            "active_effects": active_effects or [],
        }
    )
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(StatusEffectHandler())
    return engine


def _execute(command: Command, state: StateContainer):
    return _make_engine().execute(command, state, WorldInstance("test_world"))


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestStatusEffectHandler:
    def test_apply_effect_replaces_same_effect_id(self) -> None:
        state = _make_state(
            active_effects=[
                {
                    "effect_id": "burn",
                    "effect_type": "damage_over_time",
                    "remaining_ticks": 1,
                    "duration_ticks": 1,
                }
            ]
        )

        result = _execute(
            Command(
                type="apply_effect",
                params={
                    "effect_id": "burn",
                    "effect_type": "damage_over_time",
                    "duration_ticks": 3,
                    "periodic": {"damage": 2},
                    "tags": ["fire", "debuff"],
                },
            ),
            state,
        )

        assert result.success is True
        assert result.metadata["removed_count"] == 1
        _apply(result, state)
        assert len(state.player.active_effects) == 1
        effect = state.player.active_effects[0]
        assert effect["remaining_ticks"] == 3
        assert effect["duration_ticks"] == 3
        assert effect["periodic"] == {"damage": 2, "heal": 0}
        assert effect["tags"] == ["fire", "debuff"]

    def test_remove_effect_removes_matching_effect_id(self) -> None:
        state = _make_state(
            active_effects=[
                {"effect_id": "burn", "effect_type": "fire"},
                {"effect_id": "regen", "effect_type": "heal"},
            ]
        )

        result = _execute(
            Command(type="remove_effect", params={"effect_id": "burn"}),
            state,
        )

        assert result.success is True
        assert result.metadata["removed_count"] == 1
        _apply(result, state)
        assert [effect["effect_id"] for effect in state.player.active_effects] == ["regen"]

    def test_remove_effect_by_type_accepts_alias_and_removes_all_matches(self) -> None:
        state = _make_state(
            active_effects=[
                {"effect_id": "poison_1", "effect_type": "poison"},
                {"effect_id": "poison_2", "effect_type": "poison"},
                {"effect_id": "bless", "effect_type": "buff"},
            ]
        )

        result = _execute(
            Command(type="remove_effect_by_type", params={"effect_id": "poison"}),
            state,
        )

        assert result.success is True
        assert result.metadata["removed_count"] == 2
        _apply(result, state)
        assert [effect["effect_id"] for effect in state.player.active_effects] == ["bless"]

    def test_tick_effects_applies_hp_delta_and_expires_finished_effects(self) -> None:
        state = _make_state(
            active_effects=[
                {
                    "effect_id": "burn",
                    "effect_type": "damage_over_time",
                    "remaining_ticks": 1,
                    "duration_ticks": 1,
                    "periodic": {"damage": 3},
                },
                {
                    "effect_id": "regen",
                    "effect_type": "heal_over_time",
                    "remaining_ticks": 2,
                    "duration_ticks": 2,
                    "periodic": {"heal": 1},
                },
            ],
            hp=10,
        )

        result = _execute(Command(type="tick_effects"), state)

        assert result.success is True
        assert result.metadata["applied_count"] == 2
        assert result.metadata["expired_count"] == 1
        assert result.metadata["hp_delta"] == -2
        _apply(result, state)
        assert state.player.hp == 8
        assert [effect["effect_id"] for effect in state.player.active_effects] == ["regen"]
        assert state.player.active_effects[0]["remaining_ticks"] == 1

    def test_apply_effect_requires_player_slice(self) -> None:
        result = _make_engine().execute(
            Command(
                type="apply_effect",
                params={
                    "effect_id": "burn",
                    "effect_type": "damage_over_time",
                    "duration_ticks": 1,
                },
            ),
            StateContainer(),
            WorldInstance("test_world"),
        )

        assert result.success is False
        assert result.errors == ["player slice is required"]


class TestSaveEndOfTurn:
    """Tests for Phase 3: save_end_of_turn in _compute_tick_effects."""

    @staticmethod
    def _make_state_with_save_effect(
        *,
        save_ability: str = "con",
        save_dc: int = 12,
        con: int = 10,
        save_proficiencies: list[str] | None = None,
    ) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "hp": 10,
            "max_hp": 12,
            "stats": {"str": 10, "dex": 10, "con": con, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
            "save_proficiencies": save_proficiencies or [],
            "active_effects": [{
                "effect_id": "paralyzed", "effect_type": "condition",
                "remaining_ticks": 5,
                "duration_ticks": 5,
                "remaining_duration": 5,
                "modifiers": {}, "periodic": {}, "tags": [],
                "save_end_of_turn": save_ability,
                "save_dc": save_dc,
            }],
        })
        state.register(player)
        return state

    def test_save_end_of_turn_removes_effect_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Successful save_end_of_turn removes the effect immediately."""
        state = self._make_state_with_save_effect(save_dc=12, con=10)  # con mod=0
        # patch resolve_roll → roll=12; 12+0 >= 12 → save succeeds
        monkeypatch.setattr(
            "app.game_core.rules.handler_utils.roll_d20", lambda: 12
        )
        result = _execute(Command(type="tick_effects"), state)
        assert result.success is True
        state.apply(result.delta)
        assert len(state.player.active_effects) == 0
        assert result.metadata["expired_count"] == 1

    def test_save_end_of_turn_keeps_effect_on_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failed save_end_of_turn keeps the effect (duration countdown still ticks)."""
        state = self._make_state_with_save_effect(save_dc=20, con=10)  # very high DC
        # patch resolve_roll → roll=1; 1+0 < 20 → save fails
        monkeypatch.setattr(
            "app.game_core.rules.handler_utils.roll_d20", lambda: 1
        )
        result = _execute(Command(type="tick_effects"), state)
        assert result.success is True
        state.apply(result.delta)
        # Effect still present (ticked once: remaining_ticks = 5-1 = 4)
        assert len(state.player.active_effects) == 1
        assert state.player.active_effects[0]["remaining_ticks"] == 4
