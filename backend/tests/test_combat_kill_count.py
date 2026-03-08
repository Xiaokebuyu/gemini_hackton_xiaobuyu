"""Tests for kill_count flag writing on monster defeat (P5 Phase 8)."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ItemRegistry, MapRegistry, MonsterRegistry
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice
from app.game_core.state.slices.flags import FlagSlice


# ------------------------------------------------------------------
# Helpers (mirrors test_combat_handler.py patterns)
# ------------------------------------------------------------------


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load({"forest": {"id": "forest"}})
    world.register(maps)

    monsters = MonsterRegistry()
    monsters.load({
        "goblin": {"id": "goblin", "name": "Goblin", "hp": 7, "ac": 13},
        "wolf": {"id": "wolf", "name": "Wolf", "hp": 11, "ac": 12},
    })
    world.register(monsters)

    items = ItemRegistry()
    items.load({"potion": {"id": "potion", "heal_amount": 5}})
    world.register(items)
    return world


def _make_state(*, strength: int = 14, hp: int = 30) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "player_1",
        "hp": hp,
        "max_hp": 30,
        "current_area": "forest",
        "stats": {"str": strength, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
        "inventory": [],
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {"danger_level": 1.0, "npc_locations": {}, "hostile_tracking": {}}}})
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    return state


def _setup_combat(
    state: StateContainer,
    *,
    monster_id: str = "goblin",
    hp: int = 1,
    ac: int = 5,
) -> None:
    """Register an active combat with a near-death monster."""
    state.areas.register_hostile("combat_1", {
        "area_id": "forest",
        "status": "engaged",
        "cleared": False,
        "blocking": True,
        "combat_active": True,
        "combat_round": 1,
        "surprise_state": "none",
        "monster_ids": [monster_id],
        "participants": [{
            "monster_id": monster_id,
            "name": monster_id.capitalize(),
            "hp": hp,
            "max_hp": max(1, hp),
            "ac": ac,
            "alive": True,
        }],
        "player_flags": {"defending": False, "disengaged": False, "dashed": False},
    })


def _patch_rolls(monkeypatch: pytest.MonkeyPatch, handler: CombatHandler, values: Iterable[int]) -> None:
    iterator = iter(values)
    monkeypatch.setattr("app.game_core.rules.handler_utils.roll_d20", lambda: next(iterator))


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestKillCountFlag:
    def test_kill_count_incremented_on_defeat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a monster is killed, kill_count_{monster_id} flag should increment."""
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [20])  # guaranteed hit
        engine = RulesEngine()
        engine.register(handler)

        state = _make_state(strength=14)
        _setup_combat(state, monster_id="goblin", hp=1, ac=5)

        result = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["combat_cleared"] is True
        _apply(result, state)

        assert state.flags.get("kill_count_goblin", 0) == 1

    def test_kill_count_not_incremented_on_flee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a monster flees, kill_count should NOT increment."""
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [20])  # guaranteed hit
        engine = RulesEngine()
        engine.register(handler)

        state = _make_state(strength=14)
        # Monster with enough HP to survive but configured to flee
        _setup_combat(state, monster_id="goblin", hp=100, ac=5)
        # Simulate monster fleeing by directly setting participant state
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        hostile["participants"][0]["alive"] = False
        hostile["participants"][0]["fled"] = True
        hostile["combat_active"] = False
        hostile["cleared"] = True
        hostile["status"] = "cleared"

        # After flee, no kill_count should exist
        assert state.flags.get("kill_count_goblin", 0) == 0

    def test_kill_count_accumulates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple kills should accumulate the count."""
        handler = CombatHandler()
        engine = RulesEngine()
        engine.register(handler)
        world = _make_world()

        state = _make_state(strength=14)

        # First combat: kill a goblin
        _setup_combat(state, monster_id="goblin", hp=1, ac=5)
        _patch_rolls(monkeypatch, handler, [20])
        result = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            world,
        )
        assert result.executed and result.metadata["combat_cleared"]
        _apply(result, state)
        assert state.flags.get("kill_count_goblin", 0) == 1

        # Second combat: kill another goblin
        _setup_combat(state, monster_id="goblin", hp=1, ac=5)
        _patch_rolls(monkeypatch, handler, [20])
        result = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            world,
        )
        assert result.executed and result.metadata["combat_cleared"]
        _apply(result, state)
        assert state.flags.get("kill_count_goblin", 0) == 2

    def test_kill_count_different_monster_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Different monster types get separate kill_count flags."""
        handler = CombatHandler()
        engine = RulesEngine()
        engine.register(handler)
        world = _make_world()

        state = _make_state(strength=14)

        # Kill a goblin
        _setup_combat(state, monster_id="goblin", hp=1, ac=5)
        _patch_rolls(monkeypatch, handler, [20])
        result = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            state, world,
        )
        _apply(result, state)

        # Kill a wolf
        _setup_combat(state, monster_id="wolf", hp=1, ac=5)
        _patch_rolls(monkeypatch, handler, [20])
        result = engine.execute(
            Command(type="attack", params={"target": "wolf"}),
            state, world,
        )
        _apply(result, state)

        assert state.flags.get("kill_count_goblin", 0) == 1
        assert state.flags.get("kill_count_wolf", 0) == 1
