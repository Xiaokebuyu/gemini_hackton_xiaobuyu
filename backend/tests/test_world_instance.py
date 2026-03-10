import pytest

from typing import Any

from app.game_data_loader_v2 import load_v2_world_data
from app.game_core.bootstrap import build_default_world
from app.game_core.content.base import ContentRegistry
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.classes import ClassRegistry
from app.game_core.content.registries.factions import FactionRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.content.registries.skills import SkillRegistry
from app.game_core.content.registries.tag import TagRegistry
from app.game_core.content.world import WorldInstance


class RecordingRegistry(ContentRegistry):
    def __init__(
        self,
        name: str,
        calls: list[str],
        validation_issues: list[str] | None = None,
    ) -> None:
        super().__init__(name)
        self._calls = calls
        self._validation_issues = list(validation_issues or [])
        self._items: dict[str, Any] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._calls.append(self.name)
        self._items = dict(data)

    def get(self, content_id: str) -> Any | None:
        return self._items.get(content_id)

    def list_all(self) -> list[Any]:
        return list(self._items.values())

    def validate(self) -> list[str]:
        return list(self._validation_issues)


def test_world_instance_load_all_uses_dependency_order_before_insertion_order():
    calls: list[str] = []
    world = WorldInstance("test_world")
    world.register(RecordingRegistry("custom", calls))
    world.register(RecordingRegistry("characters", calls))
    world.register(RecordingRegistry("tags", calls))
    world.register(RecordingRegistry("items", calls))
    world.register(RecordingRegistry("maps", calls))
    world.register(RecordingRegistry("battle_maps", calls))
    world.register(RecordingRegistry("skills", calls))

    world.load_all({})

    assert calls == [
        "tags",
        "maps",
        "battle_maps",
        "skills",
        "characters",
        "items",
        "custom",
    ]


def test_build_default_world_registers_empty_battle_maps_registry() -> None:
    world = build_default_world("test_world", world_data={})

    assert world.has_registry("battle_maps") is True
    assert world.battle_maps.list_all() == []


def test_build_default_world_fail_fast_includes_battle_maps_registry_name() -> None:
    invalid_world_data = {
        "battle_maps": {
            "cave": {
                "variants": [
                    {
                        "name": "broken_map",
                        "size": [4, 3],
                        "terrain": ["GGGG", "GGGG"],
                        "player_spawn": [[0, 0]],
                        "enemy_spawn": [[3, 0]],
                        "tags": ["indoor"],
                    }
                ]
            }
        }
    }

    with pytest.raises(ValueError, match="battle_maps:"):
        build_default_world("bad_world", world_data=invalid_world_data)


def test_world_instance_validate_reports_registry_and_cross_registry_issues():
    world = WorldInstance("test_world")

    tags = TagRegistry()
    tags.load({"terrain": {"id": "terrain", "tags": "bad"}})

    maps = MapRegistry()
    maps.load({"town": {"id": "town"}})

    items = ItemRegistry()
    items.load({"rope": {"id": "rope"}})

    characters = CharacterRegistry()
    characters.load(
        {
            "merchant": {
                "id": "merchant",
                "area_id": "missing_map",
                "inventory": [{"item_id": "missing_item"}],
                "shop": {"inventory": [{"item_id": "missing_shop_item"}]},
            }
        }
    )

    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {
                "id": "goblin",
                "loot_table": [{"item_id": "missing_loot"}],
            }
        }
    )

    for registry in (tags, maps, items, characters, monsters):
        world.register(registry)

    issues = world.validate()

    assert issues["tags"] == ["tag dimension 'terrain' has invalid tags"]
    assert "_world" in issues
    assert (
        "character 'merchant' references unknown map 'missing_map' via area_id"
        in issues["_world"]
    )
    assert (
        "character 'merchant' references unknown item 'missing_item' via inventory[0]"
        in issues["_world"]
    )
    assert (
        "character 'merchant' references unknown item 'missing_shop_item' via shop.inventory[0]"
        in issues["_world"]
    )
    assert (
        "monster 'goblin' references unknown item 'missing_loot' via loot_table[0]"
        in issues["_world"]
    )


def test_world_instance_validate_returns_empty_dict_when_all_registered_data_is_valid():
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({"town": {"id": "town", "starting_area": True}})

    items = ItemRegistry()
    items.load({"rope": {"id": "rope", "price": 1}})

    characters = CharacterRegistry()
    characters.load(
        {
            "merchant": {
                "id": "merchant",
                "area_id": "town",
                "inventory": [{"item_id": "rope", "count": 1}],
                "shop": {"inventory": [{"item_id": "rope", "price": 2}]},
            }
        }
    )

    monsters = MonsterRegistry()
    monsters.load(
        {
            "goblin": {
                "id": "goblin",
                "hp": 7,
                "ac": 13,
                "loot_table": [{"item_id": "rope", "chance": 0.5}],
            }
        }
    )

    for registry in (maps, items, characters, monsters):
        world.register(registry)

    assert world.validate() == {}


def test_build_default_world_exposes_expanded_frontier_town_activity_ring() -> None:
    world = build_default_world("goblin_slayer", world_data=load_v2_world_data())

    frontier_town = world.maps.get("frontier_town")
    ancient_ruins = world.maps.get("ancient_ruins")
    assert frontier_town is not None
    assert ancient_ruins is not None

    assert world.characters.get("guild_quartermaster") is not None
    assert world.characters.get("tavern_keeper") is not None
    assert world.characters.get("temple_matron") is not None
    assert world.characters.get("north_gate_warden") is not None

    assert "north_gate" in frontier_town.sub_locations
    assert "forest_approach" in ancient_ruins.sub_locations
    assert "waystone_clearing" in ancient_ruins.sub_locations
    assert world.items.get("trail_rations") is not None
    assert world.items.get("bandage_roll") is not None


# ------------------------------------------------------------------
# Cross-registry reference validation (Tier 4 Phase 1)
# ------------------------------------------------------------------


def test_cross_ref_encounter_template_to_monster():
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({
        "forest": {
            "id": "forest",
            "encounter_table": [
                {"monster_ids": ["goblin"]},
                {"monster_ids": ["missing_monster"]},
            ],
        },
    })

    monsters = MonsterRegistry()
    monsters.load({"goblin": {"id": "goblin", "hp": 7, "ac": 13}})

    for registry in (maps, monsters):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "missing_monster" in i and "encounter_table" in i
        for i in issues["_world"]
    )
    assert not any("goblin" in i and "encounter" in i for i in issues["_world"])


def test_cross_ref_character_to_class():
    world = WorldInstance("test_world")

    characters = CharacterRegistry()
    characters.load({
        "wizard": {"id": "wizard", "character_class": "mage", "class_id": "mage"},
        "fighter": {"id": "fighter", "character_class": "warrior"},
    })

    classes = ClassRegistry()
    classes.load({"classes": {"warrior": {"id": "warrior"}}})

    for registry in (characters, classes):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "wizard" in i and "unknown class 'mage'" in i
        for i in issues["_world"]
    )
    assert not any(
        "fighter" in i and "unknown class" in i
        for i in issues["_world"]
    )


def test_cross_ref_character_to_faction():
    world = WorldInstance("test_world")

    characters = CharacterRegistry()
    characters.load({
        "guard": {"id": "guard", "faction": "city_watch"},
        "bandit": {"id": "bandit", "faction_id": "missing_faction"},
    })

    factions = FactionRegistry()
    factions.load({"city_watch": {"id": "city_watch"}})

    for registry in (characters, factions):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "bandit" in i and "unknown faction 'missing_faction'" in i
        for i in issues["_world"]
    )
    assert not any(
        "guard" in i and "unknown faction" in i
        for i in issues["_world"]
    )


def test_cross_ref_monster_spell_to_skill():
    world = WorldInstance("test_world")

    monsters = MonsterRegistry()
    monsters.load({
        "lich": {
            "id": "lich",
            "hp": 135,
            "ac": 17,
            "spells": ["fireball", "missing_spell"],
            "abilities": [],
        },
        "dragon": {
            "id": "dragon",
            "hp": 200,
            "ac": 19,
            "abilities": [{"id": "breath_weapon"}, {"id": "missing_ability"}],
        },
    })

    skills = SkillRegistry()
    skills.load({
        "fireball": {"id": "fireball", "category": "spell", "spell_level": 3, "effect": {"type": "damage", "dice": "8d6"}},
        "breath_weapon": {"id": "breath_weapon"},
    })

    for registry in (monsters, skills):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "lich" in i and "missing_spell" in i
        for i in issues["_world"]
    )
    assert any(
        "dragon" in i and "missing_ability" in i
        for i in issues["_world"]
    )
    assert not any("fireball" in i for i in issues["_world"])
    assert not any("breath_weapon" in i for i in issues["_world"])


# ------------------------------------------------------------------
# Cross-registry reference validation (Tier 4 Phase 2)
# ------------------------------------------------------------------


def test_cross_ref_milestone_prerequisite_to_milestone():
    world = WorldInstance("test_world")

    quests = QuestRegistry()
    quests.load({
        "milestones": {
            "start": {"id": "start"},
            "mid": {"id": "mid", "prerequisites": ["start", "missing_milestone"]},
        },
        "chapters": [],
    })

    world.register(quests)
    issues = world.validate()

    assert "_world" in issues
    assert any(
        "mid" in i and "missing_milestone" in i
        for i in issues["_world"]
    )
    assert not any("start" in i and "prerequisite" in i for i in issues["_world"])


def test_cross_ref_character_shop_pool_to_item():
    world = WorldInstance("test_world")

    characters = CharacterRegistry()
    characters.load({
        "merchant": {
            "id": "merchant",
            "shop_inventory": {
                "base_pool": [{"item_id": "rope"}, {"item_id": "missing_item"}],
                "rotating_pool": [{"item_id": "missing_rotating"}],
            },
        },
    })

    items = ItemRegistry()
    items.load({"rope": {"id": "rope"}})

    for registry in (characters, items):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "missing_item" in i and "shop_inventory.base_pool" in i
        for i in issues["_world"]
    )
    assert any(
        "missing_rotating" in i and "shop_inventory.rotating_pool" in i
        for i in issues["_world"]
    )
    assert not any("rope" in i for i in issues["_world"])


# ------------------------------------------------------------------
# Cross-registry tag value validation (Tier 4 Phase 3)
# ------------------------------------------------------------------


def test_cross_ref_tag_values_across_registries():
    world = WorldInstance("test_world")

    tags = TagRegistry()
    tags.load({
        "terrain": {"id": "terrain", "tags": ["forest", "mountain"]},
        "climate": {"id": "climate", "tags": ["tropical"]},
    })

    items = ItemRegistry()
    items.load({
        "sword": {"id": "sword", "tags": ["forest", "unknown_tag"]},
    })

    factions = FactionRegistry()
    factions.load({
        "guild": {"id": "guild", "tags": ["tropical", "missing_tag"]},
    })

    for registry in (tags, items, factions):
        world.register(registry)

    issues = world.validate()

    assert "_world" in issues
    assert any(
        "sword" in i and "unknown_tag" in i
        for i in issues["_world"]
    )
    assert any(
        "guild" in i and "missing_tag" in i
        for i in issues["_world"]
    )
    assert not any("forest" in i for i in issues["_world"])
    assert not any("tropical" in i for i in issues["_world"])


# ===========================================================================
# Batch 1-6: 新增跨 registry 引用校验
# ===========================================================================

from app.game_core.content.registries.lore import LoreRegistry


def test_cross_ref_sub_location_npc_valid():
    """resident_npc 引用存在的 character → 无 issue。"""
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "inn": {"id": "inn", "resident_npcs": ["barkeep"]},
            },
        },
    })

    characters = CharacterRegistry()
    characters.load({"barkeep": {"id": "barkeep", "name": "老板"}})

    world.register(maps)
    world.register(characters)
    issues = world.validate()

    world_issues = issues.get("_world", [])
    assert not any("sub_location" in i and "barkeep" in i for i in world_issues)


def test_cross_ref_sub_location_npc_missing():
    """resident_npc 引用不存在的 character → issue 包含字段信息。"""
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "inn": {"id": "inn", "resident_npcs": ["ghost_npc"]},
            },
        },
    })

    characters = CharacterRegistry()
    characters.load({})  # 空，ghost_npc 不存在

    world.register(maps)
    world.register(characters)
    issues = world.validate()

    assert "_world" in issues
    assert any("ghost_npc" in i and "sub_location" in i for i in issues["_world"])


def test_cross_ref_hostile_pool_monster_missing():
    """hostile_pool monster_id 引用不存在的 monster → issue。"""
    world = WorldInstance("test_world")

    maps = MapRegistry()
    maps.load({
        "forest": {
            "id": "forest",
            "hostile_pool": [
                {
                    "id": "patrol",
                    "name": "巡逻",
                    "hostile_config": {
                        "hostile_groups": [
                            {"monster_ids": ["wolf", "phantom_beast"], "count": "1d4"},
                        ],
                    },
                }
            ],
        },
    })

    monsters = MonsterRegistry()
    monsters.load({"wolf": {"id": "wolf", "name": "狼"}})  # phantom_beast 不存在

    world.register(maps)
    world.register(monsters)
    issues = world.validate()

    assert "_world" in issues
    assert any("phantom_beast" in i and "hostile" in i for i in issues["_world"])
    assert not any("wolf" in i and "hostile" in i for i in issues["_world"])


def test_cross_ref_milestone_involved_npc_missing():
    """milestone.involved_npcs 引用不存在的 character → issue。"""
    world = WorldInstance("test_world")

    quests = QuestRegistry()
    quests.load({
        "milestones": {
            "m1": {
                "id": "m1",
                "name": "寻找线人",
                "chapter": "chapter1",
                "involved_npcs": ["informant_npc"],
                "involved_locations": [],
            },
        },
    })

    characters = CharacterRegistry()
    characters.load({})  # informant_npc 不存在

    maps = MapRegistry()
    maps.load({})

    world.register(quests)
    world.register(characters)
    world.register(maps)
    issues = world.validate()

    assert "_world" in issues
    assert any("informant_npc" in i and "milestone" in i for i in issues["_world"])


def test_cross_ref_faction_leader_missing():
    """faction.leader_id 引用不存在的 character → issue。"""
    world = WorldInstance("test_world")

    factions = FactionRegistry()
    factions.load({
        "guild": {
            "id": "guild",
            "name": "商人公会",
            "leader_id": "missing_boss",
            "member_ids": [],
        },
    })

    characters = CharacterRegistry()
    characters.load({})  # missing_boss 不存在

    world.register(factions)
    world.register(characters)
    issues = world.validate()

    assert "_world" in issues
    assert any("missing_boss" in i and "leader" in i for i in issues["_world"])


def test_cross_ref_faction_influence_area_missing():
    """faction.influence_areas 引用不存在的 area → issue。"""
    world = WorldInstance("test_world")

    factions = FactionRegistry()
    factions.load({
        "thieves": {
            "id": "thieves",
            "name": "盗贼",
            "influence_areas": ["docks", "ghost_area"],
        },
    })

    maps = MapRegistry()
    maps.load({"docks": {"id": "docks"}})  # ghost_area 不存在

    world.register(factions)
    world.register(maps)
    issues = world.validate()

    assert "_world" in issues
    assert any("ghost_area" in i and "influence_area" in i for i in issues["_world"])
    assert not any("docks" in i and "influence_area" in i for i in issues["_world"])


def test_cross_ref_world_rule_scope_id_missing():
    """WorldRule scope_id 引用不存在的 area → issue。"""
    world = WorldInstance("test_world")

    lore = LoreRegistry()
    lore.load({
        "rules": {
            "no_magic": {
                "id": "no_magic",
                "title": "禁魔区",
                "scope": "area",
                "scope_id": "forbidden_zone",  # 不存在
            },
        },
    })

    maps = MapRegistry()
    maps.load({})  # forbidden_zone 不存在

    factions = FactionRegistry()
    factions.load({})

    world.register(lore)
    world.register(maps)
    world.register(factions)
    issues = world.validate()

    assert "_world" in issues
    assert any("forbidden_zone" in i and "rule" in i for i in issues["_world"])
