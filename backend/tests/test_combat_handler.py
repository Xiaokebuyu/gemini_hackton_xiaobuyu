"""Tests for CombatHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ItemRegistry, MapRegistry, MonsterRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


def _make_world(*, include_items: bool = True) -> WorldInstance:
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

    if include_items:
        items = ItemRegistry()
        items.load(
            {
                "potion": {"id": "potion", "heal_amount": 5},
                "bomb": {"id": "bomb"},
            }
        )
        world.register(items)

    return world


def _make_state(
    *,
    hp: int = 6,
    strength: int = 10,
    dexterity: int = 10,
    proficiency_bonus: int = 2,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "player_1",
            "hp": hp,
            "max_hp": 12,
            "current_area": "forest",
            "stats": {
                "str": strength,
                "dex": dexterity,
                "con": 10,
                "int": 10,
                "wis": 10,
                "cha": 10,
            },
            "proficiency_bonus": proficiency_bonus,
            "inventory": [{"item_id": "potion", "count": 2}, {"item_id": "bomb", "count": 1}],
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


def _active_combat_state(
    *,
    blocking: bool = True,
    flags: dict[str, bool] | None = None,
    strength: int = 10,
    proficiency_bonus: int = 2,
    participant_hp: int = 7,
    participant_ac: int = 13,
    participant_alive: bool = True,
) -> StateContainer:
    participant_max_hp = max(1, participant_hp)
    state = _make_state(
        strength=strength,
        proficiency_bonus=proficiency_bonus,
    )
    state.areas.register_hostile(
        "combat_1",
        {
            "area_id": "forest",
            "status": "engaged",
            "cleared": False,
            "blocking": blocking,
            "combat_active": True,
            "combat_round": 1,
            "surprise_state": "none",
            "monster_ids": ["goblin"],
            "participants": [
                {
                    "monster_id": "goblin",
                    "name": "Goblin",
                    "hp": participant_hp,
                    "max_hp": participant_max_hp,
                    "ac": participant_ac,
                    "alive": participant_alive,
                }
            ],
            "player_flags": flags or {
                "defending": False,
                "disengaged": False,
                "dashed": False,
            },
        },
    )
    return state


class TestCombatHandler:
    def test_default_action_dispatcher_routes_player_combat_actions_only(self) -> None:
        dispatcher = build_default_action_dispatcher()

        attack_command = dispatcher.dispatch(
            StructuredAction(action_type="attack", params={"target": "goblin"})
        )
        defend_command = dispatcher.dispatch(StructuredAction(action_type="defend"))
        start_command = dispatcher.dispatch(
            StructuredAction(action_type="start_combat", params={"monsters": ["goblin"]})
        )

        assert attack_command is not None and attack_command.type == "attack"
        assert defend_command is not None and defend_command.type == "defend"
        assert start_command is None

    def test_start_combat_creates_minimal_combat_payload(self) -> None:
        state = _make_state()

        result = _make_engine().execute(
            Command(
                type="start_combat",
                source="engine",
                params={"monsters": ["goblin", "wolf"], "surprise_state": "player_surprise"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "started"
        assert result.metadata["sub_area_id"] == "_combat_forest_9"
        _apply(result, state)
        hostile = state.areas.get_hostile_state("_combat_forest_9")
        assert hostile is not None
        assert hostile["combat_active"] is True
        assert hostile["status"] == "engaged"
        assert hostile["combat_round"] == 0
        assert hostile["monster_ids"] == ["goblin", "wolf"]
        assert len(hostile["participants"]) == 2

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

        assert result.success is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("ambush")
        assert hostile is not None
        assert hostile["combat_active"] is True
        assert hostile["participants"][0]["monster_id"] == "goblin"

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

        assert result.success is True
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

        assert result.success is False
        assert result.errors == ["start_combat is restricted to engine/system"]

    def test_defend_disengage_and_dash_update_player_flags(self) -> None:
        state = _active_combat_state()
        engine = _make_engine()

        defend = engine.execute(Command(type="defend"), state, _make_world())
        _apply(defend, state)
        assert state.areas.get_hostile_state("combat_1")["player_flags"]["defending"] is True

        disengage = engine.execute(Command(type="disengage"), state, _make_world())
        _apply(disengage, state)
        assert state.areas.get_hostile_state("combat_1")["player_flags"]["disengaged"] is True

        dash = engine.execute(Command(type="dash"), state, _make_world())
        _apply(dash, state)
        assert state.areas.get_hostile_state("combat_1")["player_flags"]["dashed"] is True

    def test_flee_can_fail_and_then_succeed_with_mobility_flags(self) -> None:
        state = _active_combat_state(blocking=True, proficiency_bonus=1)
        engine = _make_engine()

        failed = engine.execute(Command(type="flee"), state, _make_world())
        assert failed.success is True
        assert failed.delta is None
        assert failed.metadata["status"] == "failed"

        state.areas.register_hostile(
            "combat_1",
            {
                **state.areas.get_hostile_state("combat_1"),
                "player_flags": {
                    "defending": False,
                    "disengaged": True,
                    "dashed": True,
                },
            },
        )

        succeeded = engine.execute(Command(type="flee"), state, _make_world())
        assert succeeded.success is True
        assert succeeded.metadata["status"] == "fled"
        _apply(succeeded, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["combat_active"] is False
        assert hostile["status"] == "active"

    def test_use_combat_item_heals_and_consumes_inventory(self) -> None:
        state = _active_combat_state()

        result = _make_engine().execute(
            Command(type="use_combat_item", params={"item_id": "potion"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "used"
        _apply(result, state)
        assert state.player.hp == 11
        assert state.player.get_item_count("potion") == 1

    def test_use_combat_item_returns_no_effect_for_unsupported_items(self) -> None:
        state = _active_combat_state()

        result = _make_engine().execute(
            Command(type="use_combat_item", params={"item_id": "bomb"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "no_effect"

    def test_attack_hits_and_consumes_player_flags(self) -> None:
        engine = _make_engine()
        state = _active_combat_state(
            strength=12,
            flags={"defending": True, "disengaged": True, "dashed": True},
        )

        result = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "hit"
        assert result.metadata["damage"] == 3
        assert result.metadata["attack_total"] == 13
        assert result.metadata["target_hp"] == 4
        assert result.metadata["target_alive"] is True
        assert result.metadata["combat_active"] is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["hp"] == 4
        assert hostile["player_flags"] == {
            "defending": False,
            "disengaged": False,
            "dashed": False,
        }

    def test_attack_can_clear_the_last_enemy(self) -> None:
        state = _active_combat_state(
            strength=12,
            participant_hp=3,
        )

        result = _make_engine().execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "hit"
        assert result.metadata["target_defeated"] is True
        assert result.metadata["combat_active"] is False
        assert result.metadata["combat_cleared"] is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["hp"] == 0
        assert hostile["participants"][0]["alive"] is False
        assert hostile["cleared"] is True
        assert hostile["combat_active"] is False
        assert hostile["blocking"] is False
        assert hostile["status"] == "cleared"
        assert hostile["cleared_at_tick"] == 9

    def test_attack_miss_still_returns_delta_and_keeps_target_hp(self) -> None:
        state = _active_combat_state(
            flags={"defending": True, "disengaged": False, "dashed": False},
        )

        result = _make_engine().execute(
            Command(type="attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        assert result.metadata["status"] == "miss"
        assert result.metadata["damage"] == 0
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["hp"] == 7
        assert hostile["player_flags"] == {
            "defending": False,
            "disengaged": False,
            "dashed": False,
        }

    def test_offhand_attack_deals_one_damage_on_hit(self) -> None:
        state = _active_combat_state(strength=12)

        result = _make_engine().execute(
            Command(type="offhand_attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "hit"
        assert result.metadata["damage"] == 1
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["hp"] == 6

    def test_shove_can_open_escape_window(self) -> None:
        state = _active_combat_state(
            strength=14,
            flags={"defending": False, "disengaged": True, "dashed": True},
        )

        result = _make_engine().execute(
            Command(type="shove", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "shoved"
        assert result.metadata["passed"] is True
        assert result.metadata["blocking"] is False
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["blocking"] is False
        assert hostile["player_flags"] == {
            "defending": False,
            "disengaged": False,
            "dashed": False,
        }

    def test_shove_can_fail_and_keep_blocking(self) -> None:
        state = _active_combat_state(
            flags={"defending": False, "disengaged": False, "dashed": True},
        )

        result = _make_engine().execute(
            Command(type="shove", params={"target": "goblin"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        assert result.metadata["status"] == "resisted"
        assert result.metadata["passed"] is False
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["blocking"] is True
        assert hostile["player_flags"] == {
            "defending": False,
            "disengaged": False,
            "dashed": False,
        }

    def test_direct_resolution_validates_target_and_active_combat(self) -> None:
        engine = _make_engine()
        world = _make_world()

        missing_target = engine.execute(
            Command(type="attack", params={"target": "ogre"}),
            _active_combat_state(),
            world,
        )
        assert missing_target.success is False
        assert missing_target.errors == ["unknown combat target: ogre"]

        defeated_target = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            _active_combat_state(participant_hp=0, participant_alive=False),
            world,
        )
        assert defeated_target.success is False
        assert defeated_target.errors == ["target is not alive: goblin"]

        no_combat = engine.execute(
            Command(type="attack", params={"target": "goblin"}),
            _make_state(),
            world,
        )
        assert no_combat.success is False
        assert no_combat.errors == ["active combat sub_area_id is required"]
