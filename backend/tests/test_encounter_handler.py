"""Tests for EncounterHandler."""

from __future__ import annotations

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ItemRegistry, MapRegistry, MonsterRegistry
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import EncounterHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


def _make_world(
    *,
    include_maps: bool = True,
    include_monsters: bool = True,
    include_items: bool = True,
) -> WorldInstance:
    world = WorldInstance("test_world")
    if include_maps:
        maps = MapRegistry()
        maps.load(
            {
                "forest": {"id": "forest"},
                "town": {"id": "town"},
            }
        )
        world.register(maps)
    if include_monsters:
        monsters = MonsterRegistry()
        monsters.load(
            {
                "goblin": {
                    "id": "goblin",
                    "gold_drop": 3,
                    "loot_table": [
                        {"item_id": "potion", "count": 2, "chance": 1},
                        {"item_id": "rare_drop", "count": 1, "chance": 0.5},
                    ],
                }
            }
        )
        world.register(monsters)
    if include_items:
        items = ItemRegistry()
        items.load(
            {
                "potion": {"id": "potion", "name": "Healing Potion", "rarity": "uncommon"},
            }
        )
        world.register(items)
    return world


def _make_state(
    *,
    include_player: bool = True,
    danger_level: float = 1.0,
    day: int = 1,
    slot: int = 9,
) -> StateContainer:
    state = StateContainer()

    if include_player:
        player = PlayerSlice()
        player.restore({"gold": 1})
        state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {"danger_level": danger_level, "npc_locations": {}},
                "town": {"danger_level": 0.0, "npc_locations": {}},
            }
        }
    )
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)

    return state


def _execute(command: Command, state: StateContainer, world: WorldInstance):
    engine = RulesEngine()
    engine.register(EncounterHandler())
    return engine.execute(command, state, world)


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestEncounterHandler:
    def test_encounter_check_returns_checked_without_delta_below_threshold(self) -> None:
        result = _execute(
            Command(
                type="encounter_check",
                params={"area_id": "forest", "period": "day"},
            ),
            _make_state(danger_level=1.0),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "checked"
        assert result.metadata["triggered"] is False
        assert result.metadata["reason"] == "danger_below_threshold"

    def test_encounter_check_creates_hostile_delta_when_threshold_met(self) -> None:
        state = _make_state(danger_level=1.0, slot=9)
        result = _execute(
            Command(
                type="encounter_check",
                params={"area_id": "forest", "period": "night"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "triggered"
        assert result.metadata["triggered"] is True
        assert result.metadata["sub_area_id"] == "_encounter_forest_9"
        _apply(result, state)
        hostile = state.areas.get_hostile_state("_encounter_forest_9")
        assert hostile is not None
        assert hostile["area_id"] == "forest"
        assert hostile["status"] == "spotted"
        assert hostile["created_at_tick"] == 9
        assert hostile["blocking"] is True
        temporary = state.areas.list_temporary_sub_areas("forest")
        assert temporary[0]["id"] == "_encounter_forest_9"
        assert temporary[0]["hostile"] is True

    def test_encounter_check_force_triggered_overrides_threshold(self) -> None:
        result = _execute(
            Command(
                type="encounter_check",
                params={
                    "area_id": "forest",
                    "period": "day",
                    "force_triggered": True,
                    "sub_area_id": "forced_encounter",
                },
            ),
            _make_state(danger_level=0.1),
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["triggered"] is True
        assert result.metadata["sub_area_id"] == "forced_encounter"

    def test_encounter_check_persists_template_id_when_provided(self) -> None:
        state = _make_state(danger_level=0.1, slot=9)
        result = _execute(
            Command(
                type="encounter_check",
                params={
                    "area_id": "forest",
                    "period": "day",
                    "force_triggered": True,
                    "template_id": "forest_patrol",
                },
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["template_id"] == "forest_patrol"
        _apply(result, state)
        hostile = state.areas.get_hostile_state(result.metadata["sub_area_id"])
        assert hostile is not None
        assert hostile["template_id"] == "forest_patrol"

    def test_encounter_check_rejects_invalid_template_id(self) -> None:
        result = _execute(
            Command(
                type="encounter_check",
                params={
                    "area_id": "forest",
                    "period": "day",
                    "template_id": "",
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["template_id must be a non-empty string"]

    def test_encounter_check_rejects_invalid_period(self) -> None:
        result = _execute(
            Command(
                type="encounter_check",
                params={"area_id": "forest", "period": "noon"},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["unsupported period: noon"]

    def test_clear_hostile_is_idempotent_noop_when_missing(self) -> None:
        result = _execute(
            Command(type="clear_hostile", params={"sub_area_id": "missing"}),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "noop"
        assert result.metadata["cleared"] is False

    def test_clear_hostile_marks_existing_hostile_cleared(self) -> None:
        state = _make_state(slot=12)
        state.areas.register_hostile(
            "hostile_1",
            {
                "area_id": "forest",
                "source": "encounter",
                "status": "active",
                "cleared": False,
                "blocking": True,
            },
        )

        result = _execute(
            Command(type="clear_hostile", params={"sub_area_id": "hostile_1"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "cleared"
        _apply(result, state)
        hostile = state.areas.get_hostile_state("hostile_1")
        assert hostile is not None
        assert hostile["status"] == "cleared"
        assert hostile["cleared"] is True
        assert hostile["blocking"] is False
        assert hostile["combat_active"] is False
        assert hostile["cleared_at_tick"] == 12

    def test_clear_hostile_preserves_existing_participants(self) -> None:
        state = _make_state(slot=12)
        state.areas.register_hostile(
            "hostile_1",
            {
                "area_id": "forest",
                "source": "encounter",
                "status": "active",
                "cleared": False,
                "blocking": True,
                "participants": [
                    {
                        "monster_id": "goblin",
                        "hp": 2,
                        "alive": True,
                    }
                ],
            },
        )

        result = _execute(
            Command(type="clear_hostile", params={"sub_area_id": "hostile_1"}),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("hostile_1")
        assert hostile is not None
        assert hostile["participants"] == [
            {
                "monster_id": "goblin",
                "hp": 2,
                "alive": True,
            }
        ]

    def test_generate_loot_returns_gold_delta_and_deterministic_items(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import random as _random_mod
        monkeypatch.setattr(_random_mod, "random", lambda: 0.9)
        state = _make_state()
        result = _execute(
            Command(
                type="generate_loot",
                params={"monster_ids": ["goblin", "missing"], "area_id": "forest"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "generated"
        assert result.metadata["processed_monster_count"] == 1
        assert result.metadata["unknown_monster_count"] == 1
        loot = result.metadata["loot"]
        assert loot["gold"] == 3
        assert loot["source"] == "2 defeated enemies"
        assert loot["items"] == [
            {
                "item_id": "potion",
                "name": "Healing Potion",
                "count": 2,
                "rarity": "uncommon",
            }
        ]
        _apply(result, state)
        assert state.player.gold == 4

    def test_generate_loot_returns_empty_without_monster_registry(self) -> None:
        result = _execute(
            Command(type="generate_loot", params={"monster_ids": ["goblin"]}),
            _make_state(),
            _make_world(include_monsters=False),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "empty"
        assert result.metadata["unknown_monster_count"] == 1

    def test_generate_loot_requires_player_slice(self) -> None:
        result = _execute(
            Command(type="generate_loot", params={"monster_ids": ["goblin"]}),
            _make_state(include_player=False),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["player slice is required"]


def test_resolve_gold_dice_expressions() -> None:
    """_resolve_gold handles str gold_drop: "0", pure int str, and dice expression."""
    from app.game_core.rules.handlers.encounter import EncounterHandler

    handler = EncounterHandler()

    class FakeMonster:
        def __init__(self, gold_drop: str) -> None:
            self.gold_drop = gold_drop

    assert handler._resolve_gold(FakeMonster("0")) == 0
    assert handler._resolve_gold(FakeMonster("")) == 0
    assert handler._resolve_gold(FakeMonster("10")) == 10
    # Dice expression: result must be >= 1 (roll_damage_dice floor)
    result = handler._resolve_gold(FakeMonster("1d6"))
    assert 1 <= result <= 6
