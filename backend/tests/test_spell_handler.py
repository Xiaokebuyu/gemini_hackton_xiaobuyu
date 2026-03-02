"""Tests for SpellHandler."""

from __future__ import annotations

from typing import Iterable

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ClassRegistry, MonsterRegistry, SkillRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import SpellHandler
from app.game_core.rules.handlers.spell_resolver import resolve_spell_dc
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice


def _make_world(*, include_classes: bool = True) -> WorldInstance:
    world = WorldInstance("test_world")

    skills = SkillRegistry()
    skills.load(
        {
            "healing_touch": {
                "id": "healing_touch",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "heal", "heal_amount": 4},
                "cost": {"action_type": "action"},
            },
            "healing_wave": {
                "id": "healing_wave",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "heal", "dice": "1d4", "upcast_dice": "1d4"},
                "cost": {"action_type": "action"},
            },
            "mage_armor": {
                "id": "mage_armor",
                "category": "spell",
                "spell_level": 1,
                "effect": {
                    "type": "buff",
                    "applies_status": "mage_armor",
                    "duration_ticks": 6,
                    "modifiers": {"ac": 3},
                    "concentration": True,
                },
                "cost": {"action_type": "action"},
            },
            "mystery_blast": {
                "id": "mystery_blast",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "damage", "dice": "1d8"},
                "cost": {"action_type": "action"},
            },
            "arc_bolt": {
                "id": "arc_bolt",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "damage", "damage": 5},
                "cost": {"action_type": "action"},
            },
            "arc_burst": {
                "id": "arc_burst",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "damage", "dice": "1d4", "upcast_dice": "1d4"},
                "cost": {"action_type": "action"},
            },
            "hold_creature": {
                "id": "hold_creature",
                "category": "spell",
                "spell_level": 1,
                "effect": {
                    "type": "control",
                    "applies_status": "held",
                    "duration_ticks": 3,
                    "concentration": True,
                },
                "cost": {"action_type": "action"},
            },
        }
    )
    world.register(skills)

    if include_classes:
        classes = ClassRegistry()
        classes.load(
            {
                "classes": {
                    "wizard": {
                        "id": "wizard",
                        "spellcasting_ability": "int",
                        "prepared_formula": "int_mod + level",
                    },
                    "bard": {
                        "id": "bard",
                        "spellcasting_ability": "cha",
                    },
                    "cleric": {
                        "id": "cleric",
                        "spellcasting_ability": "wis",
                        "prepared_limit": 2,
                    },
                }
            }
        )
        world.register(classes)

    return world


def _make_state(*, character_class: str = "wizard", hp: int = 5) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {
            "character_id": "player_1",
            "level": 3,
            "hp": hp,
            "max_hp": 12,
            "stats": {
                "str": 10,
                "dex": 12,
                "con": 12,
                "int": 16,
                "wis": 12,
                "cha": 10,
            },
            "proficiency_bonus": 2,
            "character_class": character_class,
            "current_area": "forest",
            "spell_slots": {
                1: {"current": 2, "max": 2},
                2: {"current": 1, "max": 1},
            },
            "known_spells": [
                "healing_touch",
                "healing_wave",
                "mage_armor",
                "mystery_blast",
                "arc_bolt",
                "arc_burst",
                "hold_creature",
            ],
            "prepared_spells": [
                "healing_touch",
                "healing_wave",
                "mage_armor",
                "mystery_blast",
                "arc_bolt",
                "arc_burst",
                "hold_creature",
            ],
            "active_effects": [],
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
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(SpellHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


def _patch_rolls(
    monkeypatch: pytest.MonkeyPatch,
    values: Iterable[int],
) -> None:
    iterator = iter(values)
    monkeypatch.setattr(SpellHandler, "_roll_dice_expression", lambda self, expr: next(iterator))


def _active_combat_state(
    *,
    hp: int = 5,
    participant_hp: int = 7,
    participant_alive: bool = True,
    participant_effects: list[dict[str, object]] | None = None,
) -> StateContainer:
    state = _make_state(hp=hp)
    participant_max_hp = max(1, participant_hp)
    participant_payload = {
        "monster_id": "goblin",
        "name": "Goblin",
        "hp": participant_hp,
        "max_hp": participant_max_hp,
        "ac": 13,
        "alive": participant_alive,
    }
    if participant_effects:
        participant_payload["active_effects"] = [dict(effect) for effect in participant_effects]
    state.areas.register_hostile(
        "combat_1",
        {
            "area_id": "forest",
            "status": "engaged",
            "cleared": False,
            "blocking": True,
            "combat_active": True,
            "combat_round": 1,
            "participants": [participant_payload],
        },
    )
    return state


class TestSpellHandler:
    def test_default_action_dispatcher_routes_spell_actions(self) -> None:
        dispatcher = build_default_action_dispatcher()

        cast_command = dispatcher.dispatch(
            StructuredAction(action_type="cast_spell", params={"spell_id": "healing_touch"})
        )
        prepare_command = dispatcher.dispatch(
            StructuredAction(
                action_type="prepare_spells",
                params={"spell_ids": ["healing_touch"]},
            )
        )
        break_command = dispatcher.dispatch(
            StructuredAction(action_type="break_concentration")
        )

        assert cast_command is not None and cast_command.type == "cast_spell"
        assert prepare_command is not None and prepare_command.type == "prepare_spells"
        assert break_command is not None and break_command.type == "break_concentration"

    def test_break_concentration_clears_state_and_removes_applied_effects(self) -> None:
        state = _make_state()
        state.player.set_concentration(
            {
                "spell_id": "mage_armor",
                "applied_effects": ["old_effect"],
            }
        )
        state.player.active_effects = [
            {
                "effect_id": "mage_armor",
                "instance_id": "old_effect",
                "from_concentration": True,
                "source_spell_id": "mage_armor",
            },
            {"effect_id": "other", "instance_id": "keep"},
        ]

        result = _make_engine().execute(
            Command(type="break_concentration", params={"character": "player"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "broken"
        _apply(result, state)
        assert state.player.concentration is None
        assert [effect["instance_id"] for effect in state.player.active_effects] == ["keep"]

    def test_break_concentration_is_noop_without_active_concentration(self) -> None:
        result = _make_engine().execute(
            Command(type="break_concentration"),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "noop"

    def test_prepare_spells_supports_formula_based_limit(self) -> None:
        result = _make_engine().execute(
            Command(
                type="prepare_spells",
                params={"spell_ids": ["healing_touch", "healing_wave"]},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "prepared"
        assert result.metadata["max_prepared"] == 6

    def test_prepare_spells_returns_not_applicable_for_non_prepared_caster(self) -> None:
        result = _make_engine().execute(
            Command(
                type="prepare_spells",
                params={"spell_ids": ["healing_touch"]},
            ),
            _make_state(character_class="bard"),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "not_applicable"

    def test_prepare_spells_uses_fallback_limit_without_classes_registry(self) -> None:
        result = _make_engine().execute(
            Command(
                type="prepare_spells",
                params={"spell_ids": ["healing_touch", "healing_wave"]},
            ),
            _make_state(),
            _make_world(include_classes=False),
        )

        assert result.success is True
        assert result.metadata["status"] == "prepared_fallback"
        assert result.metadata["used_fallback_limit"] is True

    def test_cast_spell_heals_self_and_consumes_spell_slot(self) -> None:
        state = _make_state(hp=3)

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "healing_touch"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["consumed_slot"] is True
        _apply(result, state)
        assert state.player.hp == 10
        assert state.player.spell_slots[1]["current"] == 1

    def test_cast_spell_supports_upcast_healing_rolls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = _make_state(hp=2)
        _patch_rolls(monkeypatch, [3, 2])

        result = _make_engine().execute(
            Command(
                type="cast_spell",
                params={"spell_id": "healing_wave", "slot_level": 2},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert len(result.rolls) == 2
        _apply(result, state)
        assert state.player.hp == 10
        assert state.player.spell_slots[2]["current"] == 0

    def test_cast_spell_replaces_existing_concentration(self) -> None:
        state = _make_state()
        state.player.set_concentration(
            {
                "spell_id": "old_spell",
                "applied_effects": ["old_effect"],
            }
        )
        state.player.active_effects = [
            {
                "effect_id": "old_spell",
                "instance_id": "old_effect",
                "from_concentration": True,
                "source_spell_id": "old_spell",
            }
        ]

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "mage_armor"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["broke_previous_concentration"] is True
        assert result.metadata["concentration"] is True
        _apply(result, state)
        assert state.player.concentration is not None
        assert state.player.concentration["spell_id"] == "mage_armor"
        assert len(state.player.active_effects) == 1
        assert state.player.active_effects[0]["effect_id"] == "mage_armor"

    def test_break_concentration_clears_combat_target_effects(self) -> None:
        state = _active_combat_state(
            participant_effects=[
                {
                    "effect_id": "held",
                    "instance_id": "hold_1",
                    "from_concentration": True,
                    "source_spell_id": "hold_creature",
                }
            ]
        )
        state.player.set_concentration(
            {
                "spell_id": "hold_creature",
                "applied_effects": [],
                "target_refs": [
                    {
                        "kind": "combat_participant",
                        "sub_area_id": "combat_1",
                        "participant_monster_id": "goblin",
                        "effect_ids": ["hold_1"],
                    }
                ],
            }
        )

        result = _make_engine().execute(
            Command(type="break_concentration"),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "broken"
        assert result.metadata["removed_effect_count"] == 1
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0].get("active_effects") is None
        assert state.player.concentration is None

    def test_cast_spell_returns_unsupported_effect_for_nonself_heal_targets(self) -> None:
        state = _make_state()

        result = _make_engine().execute(
            Command(
                type="cast_spell",
                params={"spell_id": "healing_touch", "targets": ["enemy_1"]},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "unsupported_effect"
        assert state.player.spell_slots[1]["current"] == 2

    def test_cast_spell_returns_unsupported_effect_for_unmodeled_damage_spell(self) -> None:
        state = _make_state()

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "mystery_blast"}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is None
        assert result.metadata["status"] == "unsupported_effect"
        assert state.player.spell_slots[1]["current"] == 2

    def test_cast_spell_can_damage_combat_target_and_consume_slot(self) -> None:
        state = _active_combat_state()

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "arc_bolt", "targets": ["goblin"]}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["target_mode"] == "combat"
        assert result.metadata["damage_total"] == 5
        assert result.metadata["target_hp"] == 2
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["hp"] == 2
        assert state.player.spell_slots[1]["current"] == 1

    def test_cast_spell_damage_can_clear_active_combat(self) -> None:
        state = _active_combat_state(participant_hp=4)

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "arc_bolt", "targets": ["goblin"]}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["target_defeated"] is True
        assert result.metadata["combat_cleared"] is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"][0]["alive"] is False
        assert hostile["cleared"] is True
        assert hostile["status"] == "cleared"
        assert hostile["combat_active"] is False
        assert hostile["cleared_at_tick"] is None

    def test_cast_spell_supports_upcast_damage_rolls_on_combat_targets(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = _active_combat_state()
        _patch_rolls(monkeypatch, [3, 2])

        result = _make_engine().execute(
            Command(
                type="cast_spell",
                params={"spell_id": "arc_burst", "targets": ["goblin"], "slot_level": 2},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert len(result.rolls) == 2
        assert result.metadata["damage_total"] == 5
        _apply(result, state)
        assert state.areas.get_hostile_state("combat_1")["participants"][0]["hp"] == 2
        assert state.player.spell_slots[2]["current"] == 0

    def test_cast_spell_can_apply_control_to_combat_target_with_concentration(self) -> None:
        state = _active_combat_state()

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "hold_creature", "targets": ["goblin"]}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["target_mode"] == "combat"
        assert result.metadata["concentration"] is True
        assert result.metadata["target_effect_applied"] is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        target_effects = hostile["participants"][0]["active_effects"]
        assert len(target_effects) == 1
        assert target_effects[0]["source_spell_id"] == "hold_creature"
        assert state.player.concentration is not None
        assert state.player.concentration["spell_id"] == "hold_creature"
        assert state.player.concentration["target_refs"][0]["participant_monster_id"] == "goblin"

    def test_cast_spell_control_replaces_existing_combat_concentration(self) -> None:
        state = _active_combat_state(
            participant_effects=[
                {
                    "effect_id": "old_hold",
                    "instance_id": "old_hold_1",
                    "from_concentration": True,
                    "source_spell_id": "old_spell",
                }
            ]
        )
        state.player.set_concentration(
            {
                "spell_id": "old_spell",
                "applied_effects": [],
                "target_refs": [
                    {
                        "kind": "combat_participant",
                        "sub_area_id": "combat_1",
                        "participant_monster_id": "goblin",
                        "effect_ids": ["old_hold_1"],
                    }
                ],
            }
        )

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "hold_creature", "targets": ["goblin"]}),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["broke_previous_concentration"] is True
        _apply(result, state)
        hostile = state.areas.get_hostile_state("combat_1")
        assert hostile is not None
        target_effects = hostile["participants"][0]["active_effects"]
        assert len(target_effects) == 1
        assert target_effects[0]["source_spell_id"] == "hold_creature"
        assert target_effects[0]["instance_id"] != "old_hold_1"

    def test_cast_spell_multi_target_partial_success_skips_missing_targets(self) -> None:
        # 多目标施法：goblin 在战斗中被命中，wolf 不存在被跳过，法术整体成功
        state = _active_combat_state()

        result = _make_engine().execute(
            Command(
                type="cast_spell",
                params={"spell_id": "arc_bolt", "targets": ["goblin", "wolf"]},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        assert result.metadata["status"] == "cast"
        # arc_bolt 固定 5 点伤害，goblin 被命中
        assert result.metadata["damage_total"] == 5
        _apply(result, state)
        assert state.player.spell_slots[1]["current"] == 1

    def test_cast_spell_requires_a_live_combat_target_for_combat_spells(self) -> None:
        engine = _make_engine()
        world = _make_world()

        no_combat = engine.execute(
            Command(type="cast_spell", params={"spell_id": "arc_bolt", "targets": ["goblin"]}),
            _make_state(),
            world,
        )
        assert no_combat.success is True
        assert no_combat.delta is None
        assert no_combat.metadata["status"] == "unsupported_target"

        missing_target = engine.execute(
            Command(type="cast_spell", params={"spell_id": "arc_bolt", "targets": ["wolf"]}),
            _active_combat_state(),
            world,
        )
        assert missing_target.success is True
        assert missing_target.delta is None
        assert missing_target.metadata["status"] == "unsupported_target"

        dead_target = engine.execute(
            Command(type="cast_spell", params={"spell_id": "arc_bolt", "targets": ["goblin"]}),
            _active_combat_state(participant_hp=0, participant_alive=False),
            world,
        )
        assert dead_target.success is True
        assert dead_target.delta is None
        assert dead_target.metadata["status"] == "unsupported_target"


# ---------------------------------------------------------------------------
# Helper functions for F-B saving throw / multi-target tests
# ---------------------------------------------------------------------------

def _two_target_combat_state(*, participant_hp: int = 10) -> StateContainer:
    """Combat state with two participants (goblin + orc) in the same combat."""
    state = _make_state()
    state.areas.register_hostile(
        "combat_1",
        {
            "area_id": "forest",
            "status": "engaged",
            "cleared": False,
            "blocking": True,
            "combat_active": True,
            "combat_round": 1,
            "participants": [
                {
                    "monster_id": "goblin",
                    "name": "Goblin",
                    "hp": participant_hp,
                    "max_hp": participant_hp,
                    "ac": 13,
                    "alive": True,
                },
                {
                    "monster_id": "orc",
                    "name": "Orc",
                    "hp": participant_hp,
                    "max_hp": participant_hp,
                    "ac": 14,
                    "alive": True,
                },
            ],
        },
    )
    return state


def _make_save_world(*, goblin_dex: int = 10) -> WorldInstance:
    """World with save spells and goblin monster with given dex.

    Spells:
    - fireball: damage 2d6, save=dex, half_on_save=True
    - ice_lance: damage 2d6, save=dex, half_on_save defaults to False
    """
    world = WorldInstance("test_save_world")
    skills = SkillRegistry()
    skills.load(
        {
            "fireball": {
                "id": "fireball",
                "category": "spell",
                "spell_level": 1,
                "effect": {
                    "type": "damage",
                    "dice": "2d6",
                    "save": "dex",
                    "half_on_save": True,
                },
                "cost": {"action_type": "action"},
            },
            "ice_lance": {
                "id": "ice_lance",
                "category": "spell",
                "spell_level": 1,
                "effect": {"type": "damage", "dice": "2d6", "save": "dex"},
                "cost": {"action_type": "action"},
            },
        }
    )
    world.register(skills)
    classes = ClassRegistry()
    classes.load(
        {
            "classes": {
                "wizard": {
                    "id": "wizard",
                    "spellcasting_ability": "int",
                    "prepared_formula": "int_mod + level",
                }
            }
        }
    )
    world.register(classes)
    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {
                "id": "goblin",
                "name": "Goblin",
                "hp": 7,
                "ac": 13,
                "abilities": {"dex": goblin_dex},
            }
        }
    )
    world.register(monsters)
    return world


def _active_save_combat_state(extra_spell: str, *, participant_hp: int = 7) -> StateContainer:
    """Combat state with one goblin participant + extra_spell added to known/prepared."""
    state = _make_state()
    snap = state.player.snapshot()
    snap["known_spells"] = list(snap.get("known_spells", [])) + [extra_spell]
    snap["prepared_spells"] = list(snap.get("prepared_spells", [])) + [extra_spell]
    state.player.restore(snap)
    state.areas.register_hostile(
        "combat_1",
        {
            "area_id": "forest",
            "status": "engaged",
            "cleared": False,
            "blocking": True,
            "combat_active": True,
            "combat_round": 1,
            "participants": [
                {
                    "monster_id": "goblin",
                    "name": "Goblin",
                    "hp": participant_hp,
                    "max_hp": max(1, participant_hp),
                    "ac": 13,
                    "alive": True,
                }
            ],
        },
    )
    return state


# ---------------------------------------------------------------------------
# New tests: resolve_spell_dc / multi-target / saving throws
# ---------------------------------------------------------------------------

class TestSpellDcAndSavingThrows:
    def test_resolve_spell_dc_uses_proficiency_and_spellcasting_mod(self) -> None:
        # wizard: int=16 → mod=3; proficiency_bonus=2 → DC = 8+2+3 = 13
        state = _make_state()
        world = _make_world()
        assert resolve_spell_dc(state, world) == 13

    def test_cast_spell_multi_target_damages_all_combat_targets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = _two_target_combat_state(participant_hp=10)
        _patch_rolls(monkeypatch, [4, 3])  # goblin=4, orc=3

        result = _make_engine().execute(
            Command(
                type="cast_spell",
                params={"spell_id": "arc_burst", "targets": ["goblin", "orc"]},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["damage_total"] == 7  # 4 + 3
        _apply(result, state)
        # 验证两个目标 HP 均已更新
        area_snap = state.areas.snapshot()["areas"]["forest"]
        participants = area_snap["hostile_tracking"]["combat_1"]["participants"]
        hp_map = {p["monster_id"]: p["hp"] for p in participants}
        assert hp_map["goblin"] == 6   # 10 - 4
        assert hp_map["orc"] == 7      # 10 - 3

    def test_cast_spell_saving_throw_monster_fails_save_full_damage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # goblin dex=8 → save_mod=-1; roll=1 → total=0 < DC=13 → 失败 → 受全伤
        state = _active_save_combat_state("fireball", participant_hp=20)
        world = _make_save_world(goblin_dex=8)
        _patch_rolls(monkeypatch, [8, 1])  # damage骰=8, save骰=1

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "fireball", "targets": ["goblin"]}),
            state,
            world,
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["damage_total"] == 8
        assert result.metadata["save_dc"] == 13
        save_roll = result.metadata["save_rolls"][0]
        assert save_roll["succeeded"] is False
        assert save_roll["save_ability"] == "dex"
        assert save_roll["total"] == 0  # 1 + (-1)
        _apply(result, state)
        assert state.player.spell_slots[1]["current"] == 1

    def test_cast_spell_saving_throw_monster_passes_save_half_damage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # goblin dex=20 → save_mod=5; roll=8 → total=13 >= DC=13 → 成功 → 半伤
        state = _active_save_combat_state("fireball", participant_hp=20)
        world = _make_save_world(goblin_dex=20)
        _patch_rolls(monkeypatch, [10, 8])  # damage骰=10, save骰=8

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "fireball", "targets": ["goblin"]}),
            state,
            world,
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["damage_total"] == 5  # max(1, 10 // 2)
        save_roll = result.metadata["save_rolls"][0]
        assert save_roll["succeeded"] is True
        assert save_roll["total"] == 13  # 8 + 5

    def test_cast_spell_saving_throw_no_half_on_save_zero_damage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ice_lance: half_on_save=False → 存档通过则伤害=0，目标 HP 不变
        state = _active_save_combat_state("ice_lance", participant_hp=7)
        world = _make_save_world(goblin_dex=20)
        _patch_rolls(monkeypatch, [4, 20])  # damage骰=4, save骰=20

        result = _make_engine().execute(
            Command(type="cast_spell", params={"spell_id": "ice_lance", "targets": ["goblin"]}),
            state,
            world,
        )

        assert result.success is True
        assert result.metadata["status"] == "cast"
        assert result.metadata["damage_total"] == 0
        assert result.metadata["target_hp"] == 7   # HP 不变
        assert result.metadata["target_alive"] is True
        save_roll = result.metadata["save_rolls"][0]
        assert save_roll["succeeded"] is True
