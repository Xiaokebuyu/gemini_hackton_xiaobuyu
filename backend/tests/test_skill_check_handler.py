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
from app.game_core.state.slices import PlayerSlice


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
    handler: SkillCheckHandler,
    values: Iterable[int],
) -> None:
    iterator = iter(values)
    monkeypatch.setattr(handler, "_roll_d20", lambda: next(iterator))


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
        _patch_rolls(monkeypatch, handler, [10])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 13}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.success is True
        assert result.time_cost == pytest.approx(1.0 / 6.0)
        assert result.metadata["passed"] is True
        assert result.metadata["total"] == 14
        assert result.delta is None

    def test_skill_check_can_fail_without_becoming_execution_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, handler, [5])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 12}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.success is True
        assert result.metadata["passed"] is False
        assert result.metadata["total"] == 9
        assert result.errors == []

    def test_advantage_uses_higher_roll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, handler, [3, 17])

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
        _patch_rolls(monkeypatch, handler, [18, 4])

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
        _patch_rolls(monkeypatch, handler, [11])

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
        _patch_rolls(monkeypatch, handler, [9])

        result = _make_engine(handler).execute(
            Command(type="saving_throw", params={"ability": "con", "dc": 13}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.success is True
        assert result.time_cost == 0.0
        assert result.metadata["passed"] is True
        assert result.metadata["total"] == 13
        assert result.rolls[0].purpose == "saving_throw"

    def test_contest_supports_explicit_non_player_target_bonus(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = SkillCheckHandler()
        _patch_rolls(monkeypatch, handler, [10, 8])

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

        assert result.success is True
        assert result.metadata["winner"] == "actor"
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

        assert result.success is False
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
        _patch_rolls(monkeypatch, handler, [roll])

        result = _make_engine(handler).execute(
            Command(type="skill_check", params={"skill": "athletics", "dc": 10}),
            _make_state(),
            WorldInstance("test_world"),
        )

        assert result.narrative_hints == [expected_hint]
