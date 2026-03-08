"""Tests for SkillCheckHandler."""

from __future__ import annotations

from typing import Iterable

import pytest

from app.game_core.content import WorldInstance
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import SkillCheckHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice


def _make_state() -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "stats": {
                "str": 14,
                "dex": 12,
                "con": 14,
                "int": 10,
                "wis": 10,
                "cha": 10,
            },
            "proficiency_bonus": 2,
            "save_proficiencies": ["con", "wis"],
        }
    )
    state.register(player)
    return state


def _make_engine(handler: SkillCheckHandler) -> RulesEngine:
    engine = RulesEngine()
    engine.register(handler)
    return engine


def _patch_rolls(
    monkeypatch: pytest.MonkeyPatch,
    values: Iterable[int],
) -> None:
    iterator = iter(values)
    monkeypatch.setattr(
        "app.game_core.rules.handler_utils.roll_d20", lambda: next(iterator)
    )


class TestSkillCheckHandler:
    def test_default_action_dispatcher_routes_skill_check(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(
            StructuredAction(
                action_type="skill_check",
                params={"skill": "athletics", "dc": 10},
            )
        )

        assert command is not None
        assert command.type == "skill_check"

    def test_skill_check_passes_and_costs_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [10])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 13}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.executed is True
        assert result.executed is True
        assert result.time_cost == pytest.approx(1.0 / 6.0)
        assert result.metadata["passed"] is True
        assert result.metadata["outcome"] == {
            "category": "check",
            "passed": True,
            "margin": 1,
        }
        assert result.metadata["total"] == 14
        assert result.delta is None

    def test_skill_check_can_fail_without_becoming_execution_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [5])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 12}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.executed is True
        assert result.metadata["passed"] is False
        assert result.metadata["outcome"] == {
            "category": "check",
            "passed": False,
            "margin": -3,
        }
        assert result.metadata["total"] == 9
        assert result.errors == []

    def test_advantage_uses_higher_roll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [3, 17])

        result = _make_engine(handler).execute(
            Command(
                type="skill_check",
                params={"skill": "athletics", "dc": 20, "advantage": True},
            ),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.metadata["selected_roll"] == 17
        assert result.metadata["all_rolls"] == [3, 17]
        assert result.rolls[0].dice == "2d20kh1"

    def test_disadvantage_uses_lower_roll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [18, 4])

        result = _make_engine(handler).execute(
            Command(
                type="skill_check",
                params={"skill": "athletics", "dc": 10, "disadvantage": True},
            ),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.metadata["selected_roll"] == 4
        assert result.metadata["all_rolls"] == [18, 4]
        assert result.rolls[0].dice == "2d20kl1"

    def test_advantage_and_disadvantage_cancel_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [11])

        result = _make_engine(handler).execute(
            Command(
                type="skill_check",
                params={
                    "skill": "athletics",
                    "dc": 10,
                    "advantage": True,
                    "disadvantage": True,
                },
            ),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.metadata["all_rolls"] == [11]
        assert result.rolls[0].dice == "1d20"

    def test_saving_throw_returns_metadata_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [9])

        result = _make_engine(handler).execute(
            Command(type="saving_throw", params={"ability": "con", "dc": 13}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.executed is True
        assert result.time_cost == 0.0
        assert result.metadata["passed"] is True
        assert result.metadata["total"] == 13
        assert result.rolls[0].purpose == "saving_throw"

    def test_contest_supports_explicit_non_player_target_bonus(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [10, 8])

        result = _make_engine(handler).execute(
            Command(
                type="contest",
                params={
                    "actor_skill": "athletics",
                    "target_skill": "acrobatics",
                    "target": "npc:guard",
                    "target_bonus": 1,
                },
            ),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.executed is True
        assert result.metadata["winner"] == "actor"
        assert result.metadata["outcome"] == {
            "category": "contest",
            "winner": "actor",
            "passed": True,
            "margin": 5,
        }
        assert result.metadata["actor_total"] == 14
        assert result.metadata["target_total"] == 9
        assert len(result.rolls) == 2

    def test_contest_rejects_non_player_target_without_bonus(self) -> None:
        handler = SkillCheckHandler()

        result = _make_engine(handler).execute(
            Command(
                type="contest",
                params={
                    "actor_skill": "athletics",
                    "target_skill": "acrobatics",
                    "target": "npc:guard",
                },
            ),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.executed is False
        assert result.errors == ["target_bonus is required for non-player targets"]

    @pytest.mark.parametrize(
        ("roll", "expected_hint"),
        [
            (20, "extraordinary success"),
            (1, "disastrous failure"),
        ],
    )
    def test_critical_rolls_add_narrative_hints(
        self,
        monkeypatch: pytest.MonkeyPatch,
        roll: int,
        expected_hint: str,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [roll])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 10}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.narrative_hints == [expected_hint]


class TestInvestigate:
    @staticmethod
    def _make_investigate_state(
        *,
        search_targets: dict | None = None,
        search_dc: int | None = None,
        discoveries: dict | None = None,
    ) -> StateContainer:
        state = _make_state()
        areas = AreaSlice()
        props: dict = {}
        if search_targets is not None:
            props["search_targets"] = search_targets
        if search_dc is not None:
            props["search_dc"] = search_dc
        if discoveries is not None:
            props["discoveries"] = discoveries
        state.player.current_area = "forest"
        areas.restore({
            "areas": {
                "forest": {
                    "properties": props,
                }
            }
        })
        state.register(areas)
        return state

    def test_investigate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15])
        state = self._make_investigate_state(
            search_targets={"hidden_chest": {"dc": 12, "description": "A hidden chest"}},
        )
        result = _make_engine(handler).execute(
            Command(type="investigate"),
            state,
            WorldInstance("test_world"),
        )
        assert result.executed is True
        assert result.metadata["status"] == "discovered"
        assert "hidden_chest" in result.metadata["found"]
        assert result.delta is not None
        state.apply(result.delta)
        area = state.areas.areas["forest"]
        assert area.properties.get("discoveries", {}).get("hidden_chest") is True

    def test_investigate_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [3])
        state = self._make_investigate_state(
            search_targets={"hidden_chest": {"dc": 15}},
        )
        result = _make_engine(handler).execute(
            Command(type="investigate"),
            state,
            WorldInstance("test_world"),
        )
        assert result.executed is True
        assert result.metadata["status"] == "found_nothing"
        assert result.delta is None

    def test_investigate_no_targets(self) -> None:
        handler = SkillCheckHandler()
        state = self._make_investigate_state()
        result = _make_engine(handler).execute(
            Command(type="investigate"),
            state,
            WorldInstance("test_world"),
        )
        assert result.executed is True
        assert result.metadata["status"] == "nothing_to_find"
        assert result.delta is None

    def test_investigate_default_skill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [10])
        state = self._make_investigate_state(
            search_targets={"note": {"dc": 10}},
        )
        result = _make_engine(handler).execute(
            Command(type="investigate"),
            state,
            WorldInstance("test_world"),
        )
        assert result.executed is True


class TestAutoDisadvantage:
    """Tests for B3: SkillCheckHandler auto-disadvantage from active effects."""

    @staticmethod
    def _state_with_effect(dis_checks: list[str]) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
            "save_proficiencies": ["con"],
            "active_effects": [{
                "effect_id": "restrained", "effect_type": "condition",
                "remaining_ticks": 3, "modifiers": {}, "periodic": {}, "tags": [],
                "disadvantage_checks": dis_checks,
            }],
        })
        state.register(player)
        return state

    def test_auto_disadvantage_specific_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effect with disadvantage_checks=["perception"] auto-applies disadvantage to perception."""
        state = self._state_with_effect(["perception"])
        handler = SkillCheckHandler()
        # two rolls: [15, 5] → disadvantage takes min → selected=5
        _patch_rolls(monkeypatch, [15, 5])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "perception", "dc": 10}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 5
        assert result.metadata["auto_disadvantage"] is True

    def test_auto_disadvantage_does_not_affect_other_skills(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effect with disadvantage_checks=["perception"] does NOT affect stealth."""
        state = self._state_with_effect(["perception"])
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "stealth", "dc": 10}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 15
        assert result.metadata["auto_disadvantage"] is False

    def test_auto_disadvantage_all_affects_any_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effect with disadvantage_checks=["all"] applies disadvantage to any skill."""
        state = self._state_with_effect(["all"])
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15, 5])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 10}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 5
        assert result.metadata["auto_disadvantage"] is True

    def test_explicit_disadvantage_not_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit disadvantage=True in params still works without any effects."""
        state = _make_state()  # no active_effects
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15, 5])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "perception", "dc": 10, "disadvantage": True}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 5
        assert result.metadata["auto_disadvantage"] is False  # explicit, not auto

    def test_auto_disadvantage_saving_throw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effect with disadvantage_checks=["con"] auto-applies disadvantage to con saving throw."""
        state = self._state_with_effect(["con"])
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15, 5])

        result = _make_engine(handler).execute(
            Command(type="saving_throw", params={"ability": "con", "dc": 10}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 5
        assert result.metadata["auto_disadvantage"] is True

    def test_auto_disadvantage_saving_throw_unmatched_ability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effect with disadvantage_checks=["con"] does NOT affect str saving throw."""
        state = self._state_with_effect(["con"])
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, [15])

        result = _make_engine(handler).execute(
            Command(type="saving_throw", params={"ability": "str", "dc": 10}),
            state, WorldInstance("test_world"),
        )
        assert result.metadata["raw_roll"] == 15
        assert result.metadata["auto_disadvantage"] is False
