"""Tests for CombatHandler (v2 SRPG start_combat and dispatcher routing)."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import BattleMapRegistry, MapRegistry, MonsterRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


def _make_world(*, battle_maps: dict[str, object] | None = None) -> WorldInstance:
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({"forest": {"id": "forest"}})
    world.register(maps)

    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {"id": "goblin", "name": "Goblin", "hp": 7, "ac": 13},
            "wolf": {"id": "wolf", "name": "Wolf", "hp": 11, "ac": 12},
        }
    )
    world.register(monsters)
    if battle_maps is not None:
        reg = BattleMapRegistry()
        reg.load(battle_maps)
        world.register(reg)

    return world


def _make_state() -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "player_1",
            "hp": 6,
            "max_hp": 12,
            "current_area": "forest",
            "stats": {
                "str": 10,
                "dex": 10,
                "con": 10,
                "int": 10,
                "wis": 10,
                "cha": 10,
            },
            "proficiency_bonus": 2,
        }
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {
                    "danger_level": 1.0,
                    "npc_locations": {},
                    "hostile_tracking": {},
                }
            }
        }
    )
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(CombatHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestCombatHandler:
    def test_default_action_dispatcher_v1_commands_not_routed(self) -> None:
        """v1 combat commands (attack, defend, flee, etc.) must NOT be in dispatcher."""
        dispatcher = build_default_action_dispatcher()

        for v1_cmd in ("attack", "defend", "disengage", "dash", "shove", "flee",
                        "use_combat_item", "offhand_attack", "stand_up"):
            result = dispatcher.dispatch(StructuredAction(action_type=v1_cmd))
            assert result is None, f"v1 command '{v1_cmd}' should not be routed"

    def test_default_action_dispatcher_v2_combat_commands_routed(self) -> None:
        """v2 SRPG combat commands must be registered in the default dispatcher."""
        dispatcher = build_default_action_dispatcher()

        for v2_cmd in ("combat_move", "combat_end_turn", "combat_disengage",
                        "combat_dash", "combat_attack", "combat_defend", "combat_npc_turn"):
            result = dispatcher.dispatch(StructuredAction(action_type=v2_cmd))
            assert result is not None, f"v2 command '{v2_cmd}' should be routed"
            assert result.type == v2_cmd

    def test_start_combat_not_in_dispatcher(self) -> None:
        """start_combat is engine-only and must NOT be in the default dispatcher."""
        dispatcher = build_default_action_dispatcher()
        start_command = dispatcher.dispatch(
            StructuredAction(action_type="start_combat", params={"monsters": ["goblin"]})
        )
        assert start_command is None

    def test_start_combat_creates_minimal_combat_payload(self) -> None:
        state = _make_state()

        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="engine",
                # stealth_total=100 forces all enemies to fail their WIS check → surprised
                params={
                    "monsters": ["goblin", "wolf"],
                    "surprise_state": "player_surprise",
                    "stealth_total": 100,
                },
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "started"
        assert result.metadata["sub_area_id"] == "_combat_forest_9"
        _apply(result, state)
        hostile = state.areas.get_hostile_state("_combat_forest_9")
        assert hostile is not None
        assert hostile["combat_active"] is True
        assert hostile["status"] == "engaged"
        # All enemies surprised → surprise round (combat_round == 0)
        assert hostile["combat_round"] == 0
        assert hostile["monster_ids"] == ["goblin", "wolf"]
        # v2 fields present
        assert hostile["version"] == 2
        assert "units" in hostile
        assert "grid" in hostile
        assert "turn_order" in hostile

    def test_start_combat_can_use_existing_hostile_monsters(self) -> None:
        state = _make_state()
        state.areas.register_hostile(
            "ambush",
            {
                "area_id": "forest",
                "status": "active",
                "cleared": False,
                "blocking": True,
                "monster_ids": ["goblin"],
            },
        )

        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="system",
                params={"sub_area_id": "ambush"},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("ambush")
        assert hostile is not None
        assert hostile["combat_active"] is True
        # v2 units built from existing monster_ids
        assert any(u["monster_id"] == "goblin" for u in hostile["units"] if u["side"] == "enemy")

    def test_start_combat_uses_map_category_from_existing_hostile(self) -> None:
        state = _make_state()
        state.areas.register_hostile(
            "ambush",
            {
                "area_id": "forest",
                "status": "active",
                "cleared": False,
                "blocking": True,
                "monster_ids": ["goblin"],
                "map_category": "town_street",
            },
        )
        world = _make_world(
            battle_maps={
                "town_street": {
                    "variants": [
                        {
                            "name": "Stone Street",
                            "size": [8, 6],
                            "terrain": [
                                "RRRRRRRR",
                                "RRRRRRRR",
                                "RRRRRRRR",
                                "RRRRRRRR",
                                "RRRRRRRR",
                                "RRRRRRRR",
                            ],
                            "player_spawn": [[0, 2], [0, 3], [1, 2]],
                            "enemy_spawn": [[7, 2], [7, 3], [6, 3]],
                            "tags": ["town_street", "urban"],
                        }
                    ]
                }
            }
        )

        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="system",
                params={"sub_area_id": "ambush"},
            ),
            state,
            world,
        )

        assert result.executed is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("ambush")
        assert hostile is not None
        assert hostile["grid"]["terrain"][0] == "RRRRRRRR"

    def test_start_combat_clears_stale_cleared_at_tick_when_reusing_hostile(self) -> None:
        state = _make_state()
        state.areas.register_hostile(
            "ambush",
            {
                "area_id": "forest",
                "status": "active",
                "cleared": False,
                "blocking": True,
                "monster_ids": ["goblin"],
                "cleared_at_tick": 4,
            },
        )

        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="system",
                params={"sub_area_id": "ambush"},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("ambush")
        assert hostile is not None
        assert "cleared_at_tick" not in hostile

    def test_start_combat_rejects_non_engine_sources(self) -> None:
        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="player",
                params={"monsters": ["goblin"]},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["start_combat is restricted to engine/system"]

    def test_start_combat_metadata_includes_v2_fields(self) -> None:
        """start_combat result metadata contains grid/units/turn_order for SSE."""
        state = _make_state()
        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="engine",
                params={"monsters": ["goblin"]},
            ),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        sub_area_id = result.metadata["sub_area_id"]
        hostile = state.areas.get_hostile_state(sub_area_id)
        assert hostile is not None
        assert hostile["version"] == 2
        assert isinstance(hostile["units"], list)
        assert isinstance(hostile["grid"], dict)
        assert isinstance(hostile["turn_order"], list)
