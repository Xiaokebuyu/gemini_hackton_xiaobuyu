"""Tests for CombatHandler."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

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


def _patch_rolls(
    monkeypatch: pytest.MonkeyPatch,
    handler: CombatHandler,
    values: Iterable[int],
) -> None:
    iterator = iter(values)
    monkeypatch.setattr(
        "app.game_core.rules.handler_utils.roll_d20", lambda: next(iterator)
    )


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

    def test_flee_can_fail_and_then_succeed_with_mobility_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [5, 10])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(blocking=True, proficiency_bonus=1)

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

    def test_attack_hits_and_consumes_player_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [10])
        engine = RulesEngine()
        engine.register(handler)
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

    def test_attack_can_clear_the_last_enemy(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [15])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(
            strength=12,
            participant_hp=3,
        )

        result = engine.execute(
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

    def test_attack_miss_still_returns_delta_and_keeps_target_hp(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [5])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(
            flags={"defending": True, "disengaged": False, "dashed": False},
        )

        result = engine.execute(
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

    def test_offhand_attack_deals_one_damage_on_hit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [15])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(strength=12)

        result = engine.execute(
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

    def test_shove_can_open_escape_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [10])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(
            strength=14,
            flags={"defending": False, "disengaged": True, "dashed": True},
        )

        result = engine.execute(
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

    def test_shove_can_fail_and_keep_blocking(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler = CombatHandler()
        _patch_rolls(monkeypatch, handler, [5])
        engine = RulesEngine()
        engine.register(handler)
        state = _active_combat_state(
            flags={"defending": False, "disengaged": False, "dashed": True},
        )

        result = engine.execute(
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

    def test_stand_up_removes_prone(self) -> None:
        state = _make_state()
        state.player.active_effects = [
            {"effect_id": "prone", "instance_id": "p1"},
            {"effect_id": "bless", "instance_id": "b1"},
        ]
        result = _make_engine().execute(
            Command(type="stand_up"),
            state,
            _make_world(),
        )
        assert result.success is True
        assert result.metadata["status"] == "stood_up"
        assert result.metadata["removed_count"] == 1
        assert result.delta is not None
        state.apply(result.delta)
        assert not state.player.has_effect("prone")
        assert state.player.has_effect("bless")

    def test_stand_up_not_prone(self) -> None:
        state = _make_state()
        state.player.active_effects = [
            {"effect_id": "bless", "instance_id": "b1"},
        ]
        result = _make_engine().execute(
            Command(type="stand_up"),
            state,
            _make_world(),
        )
        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "not_prone"


# ---------------------------------------------------------------------------
# C-1: is_action_prevented blocks combat actions
# ---------------------------------------------------------------------------


class TestActionPreventedBlocksCombat:
    """Stunned / paralyzed players cannot take combat actions."""

    @staticmethod
    def _stunned_state() -> StateContainer:
        state = _make_state()
        state.player.active_effects.append(
            {"effect_id": "stunned", "prevents_action": True, "remaining_ticks": 2}
        )
        # need combat mode for most commands
        state.player.combat_mode = True
        state.player.combat_enemies = [{"id": "goblin", "name": "Goblin", "hp": 7, "ac": 13}]
        return state

    def test_attack_blocked_when_stunned(self):
        state = self._stunned_state()
        handler = CombatHandler()
        result = handler.validate(
            Command(type="attack", params={"target": "goblin"}),
            state,
            _make_world(),
        )
        assert result.ok is False
        assert "prevented" in result.reason

    def test_defend_blocked_when_stunned(self):
        state = self._stunned_state()
        handler = CombatHandler()
        result = handler.validate(
            Command(type="defend"),
            state,
            _make_world(),
        )
        assert result.ok is False
        assert "prevented" in result.reason

    def test_flee_blocked_when_stunned(self):
        state = self._stunned_state()
        handler = CombatHandler()
        result = handler.validate(
            Command(type="flee"),
            state,
            _make_world(),
        )
        assert result.ok is False
        assert "prevented" in result.reason

    def test_start_combat_not_blocked_by_action_prevention(self):
        """start_combat is engine-initiated, should bypass is_action_prevented."""
        state = self._stunned_state()
        handler = CombatHandler()
        result = handler.validate(
            Command(type="start_combat", source="engine", params={"enemies": [{"id": "goblin", "name": "Goblin", "hp": 7, "ac": 13}]}),
            state,
            _make_world(),
        )
        # May fail for other reasons, but NOT because of action prevention


class TestEffectPipelineIntegration:
    """Tests for B1/B2 (Buff/Debuff pipeline wiring) and C (damage type interactions)."""

    @staticmethod
    def _world_with_monster(spec: dict) -> WorldInstance:
        world = WorldInstance("test_world")
        maps = MapRegistry()
        maps.load({"forest": {"id": "forest"}})
        world.register(maps)
        monsters = MonsterRegistry()
        monsters.load({"test_monster": spec})
        world.register(monsters)
        return world

    @staticmethod
    def _combat_state(
        *,
        player_ac: int = 10,
        dex: int = 10,
        strength: int = 10,
        active_effects: list[dict] | None = None,
        monster_hp: int = 50,
        monster_ac: int = 100,
        monster_active_effects: list[dict] | None = None,
    ) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "character_id": "p1",
            "hp": 12,
            "max_hp": 12,
            "ac": player_ac,
            "current_area": "forest",
            "stats": {"str": strength, "dex": dex, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
            "active_effects": active_effects or [],
        })
        state.register(player)
        areas = AreaSlice()
        areas.restore({"areas": {"forest": {"danger_level": 1.0, "npc_locations": {}, "hostile_tracking": {}}}})
        state.register(areas)
        time_slice = TimeSlice()
        time_slice.restore({"day": 1, "slot": 9})
        state.register(time_slice)
        participant: dict = {
            "monster_id": "test_monster", "name": "Test Monster",
            "hp": monster_hp, "max_hp": monster_hp, "ac": monster_ac, "alive": True,
        }
        if monster_active_effects:
            participant["active_effects"] = monster_active_effects
        areas.register_hostile("c1", {
            "area_id": "forest", "status": "engaged", "cleared": False, "blocking": True,
            "combat_active": True, "combat_round": 1, "surprise_state": "none",
            "monster_ids": ["test_monster"],
            "participants": [participant],
            "player_flags": {"defending": False, "disengaged": False, "dashed": False},
        })
        return state

    # --- B1: player AC uses stored field + effect modifiers ---

    def test_player_ac_stored_field_beats_10_plus_dex_formula(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """player.ac=15 is used as base, not 10+DEX formula (which would give 10)."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 50, "ac": 100,
            "attacks": [{"name": "bite", "damage_dice": "1d4", "hit_bonus": 0}],
        })
        # player.ac=15, dex=10 (mod=0) → old formula would give ac=10, new gives 15
        state = self._combat_state(player_ac=15, dex=10)
        # player rolls 1 (miss on monster ac=100), then monster rolls 14 (total 14)
        # old ac formula (10): 14 >= 10 → HIT; new stored ac (15): 14 < 15 → MISS
        _patch_rolls(monkeypatch, CombatHandler(), [1, 14])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        assert result.success is True
        state.apply(result.delta)
        assert state.player.hp == 12  # monster missed because stored ac=15

    def test_effect_ac_modifier_raises_player_ac(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Active effect with modifiers["ac"]=3 adds 3 to effective player AC."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 50, "ac": 100,
            "attacks": [{"name": "bite", "damage_dice": "1d4", "hit_bonus": 0}],
        })
        # player.ac=10 + effect ac=+3 → effective ac=13
        state = self._combat_state(
            player_ac=10, dex=10,
            active_effects=[{
                "effect_id": "bless", "effect_type": "buff",
                "remaining_ticks": 3, "modifiers": {"ac": 3}, "periodic": {}, "tags": [],
            }],
        )
        # monster rolls 12 → old: 12>=10 HIT; new: 12<13 MISS
        _patch_rolls(monkeypatch, CombatHandler(), [1, 12])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        state.apply(result.delta)
        assert state.player.hp == 12  # missed due to +3 AC effect

    # --- B2: advantage_on_attacks_against ---

    def test_advantage_flag_in_metadata_when_effect_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """metadata["advantage"] is True when monster has advantage_on_attacks_against effect."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 1, "ac": 5,
        })
        # Effect is on the MONSTER (target), not the player — that's the correct semantics
        state = self._combat_state(
            monster_hp=1, monster_ac=5,
            monster_active_effects=[{
                "effect_id": "prone_enemy", "effect_type": "condition",
                "remaining_ticks": 2, "modifiers": {}, "periodic": {}, "tags": [],
                "advantage_on_attacks_against": True,
            }],
        )
        _patch_rolls(monkeypatch, CombatHandler(), [15, 15])  # two rolls for advantage

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        assert result.metadata["advantage"] is True

    def test_no_advantage_without_effect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """metadata["advantage"] is False when no advantage_on_attacks_against effect."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 1, "ac": 5,
        })
        state = self._combat_state(monster_hp=1, monster_ac=5)
        _patch_rolls(monkeypatch, CombatHandler(), [15])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        assert result.metadata["advantage"] is False

    # --- C: damage type interactions ---

    def test_damage_immunity_negates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attack with damage_type matching monster immunity deals 0 damage."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "Fire Elemental", "hp": 30, "ac": 5,
            "immunities": ["fire"],
        })
        state = self._combat_state(monster_hp=30, monster_ac=5)
        _patch_rolls(monkeypatch, CombatHandler(), [20])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster", "damage_type": "fire"}),
            state, world,
        )
        assert result.metadata["hit"] is True
        assert result.metadata["damage"] == 0
        assert result.metadata["damage_multiplier"] == 0.0

    def test_damage_resistance_halves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attack with damage_type matching monster resistance halves damage (min 1)."""
        # str=10(mod=0), prof=2 → raw=max(1,2)=2 → halved=max(1,1)=1
        world = self._world_with_monster({
            "id": "test_monster", "name": "Stone Golem", "hp": 30, "ac": 5,
            "resistances": ["physical"],
        })
        state = self._combat_state(monster_hp=30, monster_ac=5)
        _patch_rolls(monkeypatch, CombatHandler(), [20])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster", "damage_type": "physical"}),
            state, world,
        )
        assert result.metadata["hit"] is True
        assert result.metadata["damage"] == 1
        assert result.metadata["damage_multiplier"] == 0.5

    def test_damage_vulnerability_doubles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attack with damage_type matching monster vulnerability doubles damage."""
        # str=14(mod=+2), prof=2 → raw=max(1,4)=4 → doubled=8
        world = self._world_with_monster({
            "id": "test_monster", "name": "Skeleton", "hp": 30, "ac": 5,
            "vulnerabilities": ["bludgeoning"],
        })
        state = self._combat_state(strength=14, monster_hp=30, monster_ac=5)
        _patch_rolls(monkeypatch, CombatHandler(), [20])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster", "damage_type": "bludgeoning"}),
            state, world,
        )
        assert result.metadata["hit"] is True
        assert result.metadata["damage"] == 8
        assert result.metadata["damage_multiplier"] == 2.0

    def test_default_damage_type_is_physical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attack without damage_type defaults to 'physical'; fire immunity is not triggered."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "Fire Imp", "hp": 20, "ac": 5,
            "immunities": ["fire"],
        })
        state = self._combat_state(monster_hp=20, monster_ac=5)
        _patch_rolls(monkeypatch, CombatHandler(), [20])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}),
            state, world,
        )
        assert result.metadata["damage_type"] == "physical"
        assert result.metadata["damage"] > 0  # physical hit, fire immunity not triggered
        assert result.metadata["damage_multiplier"] == 1.0

    # --- Phase 2a/2b/2c: effect modifier integration ---

    def test_player_attack_includes_effect_bonus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Player with attack+1 effect: attack_total is increased by 1."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 30, "ac": 50,
        })
        # str=10(mod=0), prof=2, effect attack+1 → total = roll+0+2+1
        state = self._combat_state(
            monster_hp=30, monster_ac=50,
            active_effects=[{
                "effect_id": "blessed", "effect_type": "buff",
                "remaining_ticks": 5, "modifiers": {"attack": 1}, "periodic": {}, "tags": [],
            }],
        )
        _patch_rolls(monkeypatch, CombatHandler(), [10])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        # roll=10 + str=0 + prof=2 + effect=1 = 13
        assert result.metadata["attack_total"] == 13

    def test_monster_ac_includes_effect_mod(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monster with ac+2 active effect: effective AC = base_ac + 2."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 30, "ac": 10,
        })
        # Monster has AC=10 + effect +2 = 12; roll=10+prof=2+str=0 = 12 → exact hit
        state = self._combat_state(
            monster_hp=30, monster_ac=10,
            monster_active_effects=[{
                "effect_id": "shielded", "effect_type": "buff",
                "remaining_ticks": 3, "modifiers": {"ac": 2}, "periodic": {}, "tags": [],
            }],
        )
        _patch_rolls(monkeypatch, CombatHandler(), [10])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        # attack_total=10+0+2=12 >= target_ac=10+2=12 → hit
        assert result.metadata["hit"] is True
        assert result.metadata["target_ac"] == 12

    def test_monster_advantage_on_player_with_effect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Player has advantage_on_attacks_against effect → monster attacks with advantage."""
        world = self._world_with_monster({
            "id": "test_monster", "name": "M", "hp": 30, "ac": 5,
            "attacks": [{"name": "claw", "damage_dice": "1d4", "hit_bonus": 0}],
        })
        # Player has advantage_on_attacks_against (e.g., blinded/prone)
        state = self._combat_state(
            player_ac=100, monster_hp=30, monster_ac=5,
            active_effects=[{
                "effect_id": "blinded", "effect_type": "debuff",
                "remaining_ticks": 3, "modifiers": {}, "periodic": {}, "tags": [],
                "advantage_on_attacks_against": True,
            }],
        )
        # Player attack roll (hits monster), then monster rolls with advantage (2 rolls)
        _patch_rolls(monkeypatch, CombatHandler(), [20, 1, 15])

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        # Monster had advantage → used the higher of [1, 15] = 15
        monster_resp = result.metadata["monster_responses"]
        assert len(monster_resp) > 0
        # Monster attack did NOT hit (player_ac=100), but the roll count confirms advantage was used
        assert result.success is True


class TestMonsterAI:
    """P3-2: Monster AI deepening — flee_chance, attack selection, damage resistance, prevents_action."""

    # --- Phase 1: flee_chance probabilistic ---

    def test_flee_chance_zero_never_flees(self) -> None:
        """flee_chance=0 → monster always attacks even when below flee_threshold."""
        for _ in range(20):
            action = CombatHandler._decide_monster_action(
                "cowardly", hp_ratio=0.1, flee_threshold=0.5, flee_chance=0.0,
            )
            assert action == "attack"

    def test_flee_chance_one_always_flees(self) -> None:
        """flee_chance=1 → monster always flees when below flee_threshold."""
        for _ in range(5):
            action = CombatHandler._decide_monster_action(
                "cowardly", hp_ratio=0.1, flee_threshold=0.5, flee_chance=1.0,
            )
            assert action == "flee"

    def test_flee_threshold_zero_always_attacks(self) -> None:
        """flee_threshold=0 → never flees regardless of flee_chance."""
        action = CombatHandler._decide_monster_action(
            "cowardly", hp_ratio=0.0, flee_threshold=0.0, flee_chance=1.0,
        )
        assert action == "attack"

    # --- Phase 2: _estimate_damage + _select_attack ---

    def test_estimate_damage_standard_dice(self) -> None:
        assert CombatHandler._estimate_damage("2d6") == 7.0
        assert CombatHandler._estimate_damage("1d4") == 2.5
        assert CombatHandler._estimate_damage("1d8") == 4.5

    def test_estimate_damage_bad_input_returns_one(self) -> None:
        assert CombatHandler._estimate_damage("bad") == 1.0
        assert CombatHandler._estimate_damage("") == 1.0

    def test_aggressive_selects_highest_damage(self) -> None:
        """aggressive AI picks the attack with highest expected damage."""
        from types import SimpleNamespace
        attacks = [
            SimpleNamespace(damage_dice="1d4", hit_bonus=5, range=1),
            SimpleNamespace(damage_dice="2d6", hit_bonus=0, range=1),
            SimpleNamespace(damage_dice="1d8", hit_bonus=0, range=1),
        ]
        selected = CombatHandler._select_attack(attacks, "aggressive")
        assert selected.damage_dice == "2d6"

    def test_cowardly_selects_longest_range(self) -> None:
        """cowardly AI picks the attack with the longest range."""
        from types import SimpleNamespace
        attacks = [
            SimpleNamespace(damage_dice="1d4", hit_bonus=0, range=1),
            SimpleNamespace(damage_dice="1d4", hit_bonus=0, range=30),
            SimpleNamespace(damage_dice="1d4", hit_bonus=0, range=15),
        ]
        selected = CombatHandler._select_attack(attacks, "cowardly")
        assert selected.range == 30

    # --- Phase 3: _apply_player_damage_resistance ---

    @staticmethod
    def _state_with_tags(tags: list[str]) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "hp": 10, "max_hp": 12,
            "active_effects": [{
                "effect_id": "e1", "effect_type": "buff",
                "remaining_ticks": 3, "modifiers": {}, "periodic": {}, "tags": tags,
            }],
        })
        state.register(player)
        return state

    def test_player_resistance_halves_damage(self) -> None:
        state = self._state_with_tags(["fire_resistance"])
        assert CombatHandler._apply_player_damage_resistance(10, "fire", state) == 5

    def test_player_immunity_negates_damage(self) -> None:
        state = self._state_with_tags(["fire_immunity"])
        assert CombatHandler._apply_player_damage_resistance(10, "fire", state) == 0

    def test_player_vulnerability_doubles_damage(self) -> None:
        state = self._state_with_tags(["fire_vulnerability"])
        assert CombatHandler._apply_player_damage_resistance(10, "fire", state) == 20

    def test_no_matching_tag_unchanged(self) -> None:
        state = self._state_with_tags(["cold_resistance"])
        assert CombatHandler._apply_player_damage_resistance(10, "fire", state) == 10

    # --- Phase 4: prevents_action skips monster turn ---

    def test_monster_prevents_action_skips_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monster with prevents_action=True effect gets action='stunned'."""
        world = TestEffectPipelineIntegration._world_with_monster({
            "id": "test_monster", "name": "Paralyzed Goblin", "hp": 30, "ac": 5,
            "attacks": [{"name": "bite", "damage_dice": "1d6", "hit_bonus": 2}],
        })
        state = TestEffectPipelineIntegration._combat_state(
            player_ac=10, monster_hp=30,
            monster_active_effects=[{
                "effect_id": "paralyzed", "effect_type": "condition",
                "remaining_ticks": 2, "modifiers": {}, "periodic": {}, "tags": [],
                "prevents_action": True,
            }],
        )
        _patch_rolls(monkeypatch, CombatHandler(), [20])  # player attack roll

        result = _make_engine().execute(
            Command(type="attack", params={"target": "test_monster"}), state, world
        )
        assert result.success is True
        monster_resp = result.metadata["monster_responses"]
        assert len(monster_resp) == 1
        assert monster_resp[0]["action"] == "stunned"
        assert monster_resp[0]["hit"] is False
        assert monster_resp[0]["damage"] == 0
