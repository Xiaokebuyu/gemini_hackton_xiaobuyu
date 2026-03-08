"""Tests for GrowthHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ClassRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import GrowthHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    classes = ClassRegistry()
    classes.load(
        {
            "classes": {
                "fighter": {
                    "id": "fighter",
                    "hit_die": 10,
                    "hp_per_level": 6,
                    "subclass_level": 6,
                    "starting_gold": 10,
                    "level_features": {
                        "1": ["Second Wind"],
                        "2": ["Action Surge"],
                    },
                }
            },
            "subclasses": {
                "champion": {
                    "id": "champion",
                    "class_id": "fighter",
                    "features": ["Improved Critical"],
                    "level_features": {
                        "6": ["Remarkable Athlete"],
                    },
                }
            },
            "races": {
                "human": {
                    "id": "human",
                    "stat_bonuses": {"str": 1},
                    "racial_traits": ["Human Versatility"],
                }
            },
            "backgrounds": {
                "soldier": {
                    "id": "soldier",
                    "feature": "Military Rank",
                    "gold_bonus": 20,
                }
            },
            "xp_curve": [0, 1000, 2000, 3000, 4000, 5000, 6000],
        }
    )
    world.register(classes)
    return world


def _make_state(
    *,
    character_id: str = "pc_1",
    character_class: str = "fighter",
    level: int = 1,
    xp: int = 0,
    subclass: str | None = None,
    class_features: list[str] | None = None,
    stats: dict[str, int] | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": character_id,
            "character_class": character_class,
            "level": level,
            "xp": xp,
            "hp": 12,
            "max_hp": 12,
            "proficiency_bonus": 2,
            "subclass": subclass,
            "class_features": class_features or ["Second Wind"],
            "stats": stats
            or {
                "str": 12,
                "dex": 12,
                "con": 12,
                "int": 10,
                "wis": 10,
                "cha": 10,
            },
        }
    )
    state.register(player)
    return state


def _make_blank_state() -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore({})
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(GrowthHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestGrowthHandler:
    def test_default_action_dispatcher_routes_growth_actions(self) -> None:
        dispatcher = build_default_action_dispatcher()

        add_xp = dispatcher.dispatch(
            StructuredAction(action_type="add_xp", params={"amount": 100})
        )
        create_character = dispatcher.dispatch(
            StructuredAction(action_type="create_character", params={"character_id": "pc_2"})
        )

        assert add_xp is not None
        assert add_xp.type == "add_xp"
        assert create_character is not None
        assert create_character.type == "create_character"

    def test_create_character_remains_compatible(self) -> None:
        state = _make_blank_state()
        result = _make_engine().execute(
            Command(
                type="create_character",
                params={
                    "character_id": "pc_1",
                    "name": "Hero",
                    "race_id": "human",
                    "class_id": "fighter",
                    "background_id": "soldier",
                    "ability_scores": {
                        "str": 12,
                        "dex": 10,
                        "con": 12,
                        "int": 10,
                        "wis": 10,
                        "cha": 10,
                    },
                },
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "created"
        _apply(result, state)
        assert state.player.character_id == "pc_1"
        assert state.player.character_name == "Hero"
        assert state.player.character_class == "fighter"
        assert state.player.stats["str"] == 13
        assert "Second Wind" in state.player.class_features

    def test_add_xp_increases_xp_without_changing_level(self) -> None:
        state = _make_state(level=1, xp=0)
        result = _make_engine().execute(
            Command(type="add_xp", params={"amount": 1000}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["new_xp"] == 1000
        assert result.metadata["current_level"] == 1
        assert result.metadata["available_level"] == 2
        assert result.metadata["level_up_available"] is True
        _apply(result, state)
        assert state.player.xp == 1000
        assert state.player.level == 1

    def test_level_up_defaults_to_next_level_and_updates_stats(self) -> None:
        state = _make_state(level=1, xp=1000)
        result = _make_engine().execute(
            Command(type="level_up"),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["from_level"] == 1
        assert result.metadata["to_level"] == 2
        assert result.metadata["hp_gain"] == 7
        assert result.metadata["added_features"] == ["Action Surge"]
        assert result.metadata["new_proficiency_bonus"] == 2
        _apply(result, state)
        assert state.player.level == 2
        assert state.player.max_hp == 19
        assert state.player.hp == 19
        assert "Action Surge" in state.player.class_features

    def test_level_up_rejects_target_above_available_level(self) -> None:
        result = _make_engine().execute(
            Command(type="level_up", params={"target_level": 3}),
            _make_state(level=1, xp=1000),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["target level exceeds available level: 3 > 2"]

    def test_apply_asi_updates_stat_at_valid_level(self) -> None:
        state = _make_state(level=4)
        result = _make_engine().execute(
            Command(type="apply_asi", params={"stat": "str", "bonus": 2}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["from_value"] == 12
        assert result.metadata["to_value"] == 14
        _apply(result, state)
        assert state.player.stats["str"] == 14

    def test_apply_asi_rejects_invalid_level(self) -> None:
        result = _make_engine().execute(
            Command(type="apply_asi", params={"stat": "str", "bonus": 1}),
            _make_state(level=3),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["ASI can only be applied at levels 4/8/12/16/19"]

    def test_choose_subclass_rejects_when_level_too_low(self) -> None:
        result = _make_engine().execute(
            Command(type="choose_subclass", params={"subclass_id": "champion"}),
            _make_state(level=5),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["subclass requires level 6"]

    def test_choose_subclass_sets_subclass_and_adds_features(self) -> None:
        state = _make_state(level=6, class_features=["Second Wind", "Action Surge"])
        result = _make_engine().execute(
            Command(type="choose_subclass", params={"subclass_id": "champion"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "chosen"
        assert result.metadata["added_features"] == [
            "Improved Critical",
            "Remarkable Athlete",
        ]
        _apply(result, state)
        assert state.player.subclass == "champion"
        assert "Improved Critical" in state.player.class_features
        assert "Remarkable Athlete" in state.player.class_features
