from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.classes import ClassRegistry
from app.game_core.content.registries.factions import FactionRegistry
from app.game_core.content.registries.items import ItemRegistry
from app.game_core.content.registries.lore import LoreRegistry
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.monsters import MonsterRegistry
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.content.registries.skills import SkillRegistry
from app.game_core.content.registries.tag import TagRegistry


def test_map_registry_prefers_explicit_starting_area_and_validates_runtime_fields():
    registry = MapRegistry()
    registry.load(
        {
            "forest": {
                "id": "forest",
                "base_danger": -1,
                "sub_locations": {
                    "camp": {"id": "camp"},
                    "bad": {"id": "   "},
                    "broken": "nope",
                },
                "is_starting_area": [],
            },
            "town": {
                "id": "town",
                "danger_level": 0.0,
                "starting_area": True,
            },
        }
    )

    assert registry.starting_area().id == "town"

    issues = registry.validate()

    assert "map 'forest' has invalid base_danger" in issues
    assert "map 'forest' sub_location 'bad' has invalid id" in issues
    assert "map 'forest' sub_location 'broken' must be a mapping" in issues
    assert "map 'forest' has invalid is_starting_area" in issues


def test_map_registry_loads_encounter_table_new_format():
    registry = MapRegistry()
    registry.load(
        {
            "forest": {
                "id": "forest",
                "encounter_slot_capacity": 2,
                "encounter_table": [
                    {"monster_ids": ["goblin", "wolf"], "weight": 1.5, "min_danger": 0.3},
                    {"id": "forest_boss", "monster_ids": ["troll"], "weight": 0.5, "min_danger": 0.7},
                ],
            },
            "wilds": {
                "id": "wilds",
                "encounter_slot_capacity": "bad",
                "encounter_table": [
                    {"monster_ids": []},       # no monster_ids → load issue recorded
                    {"monster_ids": ["orc"], "weight": "invalid"},  # invalid weight → issue
                ],
            },
        }
    )

    issues = registry.validate()

    forest = registry.get("forest")
    assert forest is not None
    assert forest.encounter_slot_capacity == 2
    assert len(forest.encounter_table) == 2
    # id auto-assigned for first entry (no id in raw), preserved for second
    assert forest.encounter_table[0].id == "forest_0"
    assert forest.encounter_table[1].id == "forest_boss"
    assert forest.encounter_table[0].monster_ids == ["goblin", "wolf"]
    assert forest.encounter_table[0].weight == 1.5
    assert forest.encounter_table[0].min_danger == 0.3

    # invalid slot_capacity → issue
    assert "map 'wilds' has invalid encounter_slot_capacity" in issues
    # empty monster_ids → issue
    assert any("wilds" in i and "no monster_ids" in i for i in issues)
    # invalid weight → issue
    assert any("wilds" in i and "invalid weight" in i for i in issues)


def test_map_registry_encounter_profile_backward_compat():
    # 旧 encounter_profile dict 格式可以被 load，不产生 issue（向后兼容）
    registry = MapRegistry()
    registry.load(
        {
            "forest": {
                "id": "forest",
                "encounter_profile": {
                    "slot_capacity": 2,
                    "templates": [
                        {"id": "forest_patrol", "monster_ids": ["goblin"]},
                    ],
                },
            },
        }
    )
    issues = registry.validate()
    forest = registry.get("forest")
    assert forest is not None
    assert forest.encounter_slot_capacity == 2
    assert len(forest.encounter_table) == 1
    assert forest.encounter_table[0].id == "forest_patrol"
    assert not any("forest" in i for i in issues)


def test_character_registry_validates_inventory_and_shop_shapes():
    registry = CharacterRegistry()
    registry.load(
        {
            "merchant": {
                "id": "merchant",
                "area_id": "   ",
                "current_area": None,
                "inventory": [
                    {"item_id": "potion", "count": -1},
                    {"count": 2},
                    "bad-entry",
                ],
                "shop": {
                    "inventory": [
                        {"item_id": "", "price": 5},
                        {"item_id": "rope", "price": -2},
                        "bad-shop-entry",
                    ]
                },
            },
            "guard": {
                "id": "guard",
                "shop": [],
            },
        }
    )

    issues = registry.validate()

    assert "character 'merchant' has invalid area_id" in issues
    assert "character 'merchant' has invalid current_area" in issues
    assert "character 'merchant' inventory[0] has invalid count" in issues
    assert "character 'merchant' inventory[1] missing item_id" in issues
    assert "character 'merchant' inventory[2] must be a mapping" in issues
    assert "character 'merchant' shop.inventory[0] missing item_id" in issues
    assert "character 'merchant' shop.inventory[1] has invalid price" in issues
    assert "character 'merchant' shop.inventory[2] must be a mapping" in issues
    assert "character 'guard' has invalid shop" in issues


def test_item_registry_validates_numeric_and_shape_fields():
    registry = ItemRegistry()
    registry.load(
        {
            "potion": {
                "id": "potion",
                "price": -1,
                "heal_amount": -5,
                "slot": "   ",
                "tags": "healing",
            }
        }
    )

    issues = registry.validate()

    assert "item 'potion' has invalid price" in issues
    assert "item 'potion' has invalid heal_amount" in issues
    assert "item 'potion' has invalid slot" in issues
    assert "item 'potion' has invalid tags" in issues


def test_monster_registry_validates_combat_and_loot_fields():
    registry = MonsterRegistry()
    registry.load(
        {
            "goblin": {
                "id": "goblin",
                "hp": 0,
                "ac": "bad",
                "loot_table": [
                    {"item_id": "   ", "chance": 2.0},
                    "bad-entry",
                ],
            }
        }
    )

    issues = registry.validate()

    assert "monster 'goblin' has invalid hp" in issues
    assert "monster 'goblin' has invalid ac" in issues
    assert "monster 'goblin' loot_table[0] has invalid item_id" in issues
    assert "monster 'goblin' loot_table[0] has invalid chance" in issues
    assert "monster 'goblin' loot_table[1] must be a mapping" in issues


def test_skill_registry_validates_spell_templates_used_by_runtime():
    registry = SkillRegistry()
    registry.load(
        {
            "bad_heal": {
                "id": "bad_heal",
                "category": "spell",
                "spell_level": -1,
                "effect": {"type": "heal", "applies_status": "   "},
                "cost": [],
                "upcast_dice": "   ",
            },
            "bad_damage": {
                "id": "bad_damage",
                "type": "spell",
                "spell_level": 1,
                "effect": {"type": "damage", "damage_amount": -3},
                "cost": {"resource_amount": 0, "action_type": "   "},
            },
            "bad_effect": {
                "id": "bad_effect",
                "spell_level": 1,
                "effect": "boom",
            },
        }
    )

    issues = registry.validate()

    assert "spell 'bad_heal' has invalid spell level" in issues
    assert "spell 'bad_heal' has invalid cost" in issues
    assert "spell 'bad_heal' has invalid upcast_dice" in issues
    assert "spell 'bad_heal' has invalid applies_status" in issues
    assert "spell 'bad_heal' heal effect missing dice" in issues
    assert "spell 'bad_damage' has invalid resource amount" in issues
    assert "spell 'bad_damage' has invalid action_type" in issues
    assert "spell 'bad_damage' has invalid damage_amount" in issues
    assert "spell 'bad_effect' has invalid effect" in issues


def test_class_registry_validates_spellcasting_fields_and_xp_curve():
    registry = ClassRegistry()
    registry.load(
        {
            "classes": {
                "wizard": {
                    "id": "wizard",
                    "spellcasting_ability": "   ",
                    "prepared_limit": -1,
                    "prepared_formula": "   ",
                }
            },
            "subclasses": {
                "evoker": {
                    "id": "evoker",
                    "class_id": "unknown",
                }
            },
            "xp_curve": {
                "foo": 100,
                "2": -1,
            },
        }
    )

    issues = registry.validate()

    assert "class entry 'wizard' has invalid spellcasting_ability" in issues
    assert "class entry 'wizard' has invalid prepared_limit" in issues
    assert "class entry 'wizard' has invalid prepared_formula" in issues
    assert "subclass entry 'evoker' references unknown class 'unknown'" in issues
    assert "xp_curve key 'foo' must be numeric" in issues
    assert "xp_curve entry '2' must be a non-negative int" in issues


def test_quest_registry_validates_chapter_and_milestone_links():
    registry = QuestRegistry()
    registry.load(
        {
            "milestones": {
                "intro": {
                    "id": "intro",
                    "chapter_id": "chapter-2",
                    "next_milestones": ["missing", ""],
                }
            },
            "chapters": [
                {"id": "chapter-1"},
                {"chapter_id": "chapter-1"},
                {"id": "   "},
            ],
            "initial_events": [{"event_id": "   "}],
        }
    )

    issues = registry.validate()

    assert "duplicate chapter id 'chapter-1'" in issues
    assert "chapter entry 2 missing id" in issues
    assert "milestone 'intro' references unknown chapter 'chapter-2'" in issues
    assert "milestone 'intro' references unknown next milestone 'missing'" in issues
    assert (
        "milestone 'intro' next_milestones[1] must be a non-empty string" in issues
    )
    assert "initial_events[0] has invalid event id" in issues


# ------------------------------------------------------------------
# Monster: game mechanic fields + balance
# ------------------------------------------------------------------


def test_monster_validates_cr_and_creature_type():
    registry = MonsterRegistry()
    registry.load({
        "dragon": {"id": "dragon", "cr": -1, "creature_type": "unknown_type"},
        "goblin": {"id": "goblin", "cr": 0.25, "creature_type": "humanoid"},
    })

    issues = registry.validate()

    assert "monster 'dragon' has invalid cr" in issues
    assert "monster 'dragon' has invalid creature_type" in issues
    assert not any("goblin" in i for i in issues)


def test_monster_validates_abilities_and_attacks():
    registry = MonsterRegistry()
    registry.load({
        "troll": {
            "id": "troll",
            "abilities": {"str": 18, "dex": 0, "con": "bad"},
            "attacks": [
                {"name": "claw"},
                {"name": "  "},
                "bad_entry",
            ],
        },
        "slime": {"id": "slime", "abilities": "not_a_mapping"},
    })

    issues = registry.validate()

    assert "monster 'troll' abilities has invalid dex" in issues
    assert "monster 'troll' abilities has invalid con" in issues
    assert "monster 'troll' attacks[1] has invalid name" in issues
    assert "monster 'troll' attacks[2] must be a mapping" in issues
    assert "monster 'slime' has invalid abilities" in issues


def test_monster_validates_resistances():
    registry = MonsterRegistry()
    registry.load({
        "golem": {
            "id": "golem",
            "resistances": ["fire", "  "],
            "immunities": "poison",
        },
    })

    issues = registry.validate()

    assert "monster 'golem' resistances[1] must be a non-empty string" in issues
    assert "monster 'golem' has invalid immunities" in issues


def test_monster_balance_warning():
    registry = MonsterRegistry()
    registry.load({
        "weak": {"id": "weak", "cr": 0.5, "hp": 200, "ac": 5},
        "strong": {"id": "strong", "cr": 15, "hp": 50, "ac": 10},
        "normal": {"id": "normal", "cr": 3, "hp": 50, "ac": 15},
    })

    issues = registry.validate()

    assert any("weak" in i and "balance warning" in i and "HP" in i for i in issues)
    assert any("weak" in i and "balance warning" in i and "AC" in i for i in issues)
    assert any("strong" in i and "balance warning" in i and "HP" in i for i in issues)
    assert any("strong" in i and "balance warning" in i and "AC" in i for i in issues)
    assert not any("normal" in i and "balance" in i for i in issues)


def test_monster_query_by_cr_and_type():
    registry = MonsterRegistry()
    registry.load({
        "goblin": {"id": "goblin", "cr": 0.25, "creature_type": "humanoid"},
        "dragon": {"id": "dragon", "cr": 15, "creature_type": "dragon"},
        "wolf": {"id": "wolf", "cr": 0.5, "creature_type": "beast"},
    })

    low_cr = registry.get_by_cr(0, 1)
    assert {m.id for m in low_cr} == {"goblin", "wolf"}

    dragons = registry.get_by_type("dragon")
    assert len(dragons) == 1
    assert dragons[0].id == "dragon"


# ------------------------------------------------------------------
# Item: game mechanic fields + queries
# ------------------------------------------------------------------


def test_item_validates_type_and_rarity():
    registry = ItemRegistry()
    registry.load({
        "sword": {"id": "sword", "type": "weapon", "rarity": "rare", "base_price": 100},
        "junk": {"id": "junk", "type": "unknown_type", "rarity": "mythic", "base_price": -1},
    })

    issues = registry.validate()

    assert "item 'junk' has invalid type" in issues
    assert "item 'junk' has invalid rarity" in issues
    assert "item 'junk' has invalid base_price" in issues
    assert not any("sword" in i for i in issues)


def test_item_validates_weapon_armor_fields():
    registry = ItemRegistry()
    registry.load({
        "bad_weapon": {
            "id": "bad_weapon",
            "damage_dice": "  ",
            "damage_type": "  ",
            "ac_bonus": -1,
            "weight": -0.5,
            "requires_attunement": "maybe",
        },
    })

    issues = registry.validate()

    assert "item 'bad_weapon' has invalid damage_dice" in issues
    assert "item 'bad_weapon' has invalid damage_type" in issues
    assert "item 'bad_weapon' has invalid ac_bonus" in issues
    assert "item 'bad_weapon' has invalid weight" in issues
    assert "item 'bad_weapon' has invalid requires_attunement" in issues


def test_item_query_methods():
    registry = ItemRegistry()
    registry.load({
        "sword": {"id": "sword", "slot": "main_hand", "type": "weapon", "rarity": "rare"},
        "potion": {"id": "potion", "type": "potion", "rarity": "common"},
        "ring": {"id": "ring", "slot": "ring", "type": "ring", "rarity": "rare"},
    })

    equippable = registry.get_equippable()
    assert {i.id for i in equippable} == {"sword", "ring"}

    weapons = registry.get_by_type("weapon")
    assert len(weapons) == 1 and weapons[0].id == "sword"

    rares = registry.get_by_rarity("rare")
    assert {i.id for i in rares} == {"sword", "ring"}


# ------------------------------------------------------------------
# Skill: game mechanic fields + queries
# ------------------------------------------------------------------


def test_skill_validates_concentration_and_duration():
    registry = SkillRegistry()
    registry.load({
        "bad_spell": {
            "id": "bad_spell",
            "category": "spell",
            "spell_level": 1,
            "concentration": "maybe",
            "duration": -1,
            "status_duration": "bad",
            "effect": {"type": "buff"},
        },
    })

    issues = registry.validate()

    assert "spell 'bad_spell' has invalid concentration" in issues
    assert "spell 'bad_spell' has invalid duration" in issues
    assert "spell 'bad_spell' has invalid status_duration" in issues


def test_skill_validates_effect_type_and_school():
    registry = SkillRegistry()
    registry.load({
        "weird": {
            "id": "weird",
            "category": "spell",
            "spell_level": 1,
            "school": "chronomancy",
            "ritual": "maybe",
            "range": [],
            "targets": False,
            "effect": {"type": "explode"},
        },
    })

    issues = registry.validate()

    assert "spell 'weird' has invalid effect type 'explode'" in issues
    assert "spell 'weird' has invalid school" in issues
    assert "spell 'weird' has invalid ritual" in issues
    assert "spell 'weird' has invalid range" in issues
    assert "spell 'weird' has invalid targets" in issues


def test_skill_query_spells():
    registry = SkillRegistry()
    registry.load({
        "fireball": {"id": "fireball", "category": "spell", "spell_level": 3, "school": "evocation", "effect": {"type": "damage", "dice": "8d6"}},
        "heal": {"id": "heal", "category": "spell", "spell_level": 1, "school": "evocation", "effect": {"type": "heal", "dice": "1d8"}},
        "stealth": {"id": "stealth"},
    })

    all_spells = registry.get_spells()
    assert {s.id for s in all_spells} == {"fireball", "heal"}

    level_3 = registry.get_spells_by_level(3)
    assert len(level_3) == 1 and level_3[0].id == "fireball"

    evocation = registry.get_spells_by_school("evocation")
    assert {s.id for s in evocation} == {"fireball", "heal"}


# ------------------------------------------------------------------
# Map: game mechanic fields + queries
# ------------------------------------------------------------------


def test_map_validates_connections_and_region():
    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "connections": ["forest", "  "],
            "region": "  ",
        },
        "cave": {
            "id": "cave",
            "adjacent_areas": "not_a_list",
        },
    })

    issues = registry.validate()

    assert "map 'town' connections[1] must be a non-empty string" in issues
    assert "map 'town' has invalid region" in issues
    assert "map 'cave' has invalid adjacent_areas" in issues


def test_map_warns_multiple_starting_areas():
    registry = MapRegistry()
    registry.load({
        "town": {"id": "town", "is_starting_area": True},
        "village": {"id": "village", "starting_area": True},
    })

    issues = registry.validate()

    assert any("multiple starting areas" in i for i in issues)


def test_map_query_adjacent_and_region():
    registry = MapRegistry()
    registry.load({
        "town": {"id": "town", "connections": ["forest", "cave"], "region": "central"},
        "forest": {"id": "forest", "region": "central"},
        "cave": {"id": "cave", "region": "underground"},
    })

    adj = registry.get_adjacent("town")
    assert adj == ["forest", "cave"]

    assert registry.get_adjacent("unknown") == []

    central = registry.get_by_region("central")
    assert {a.id for a in central} == {"town", "forest"}


def test_map_get_returns_typed_template():
    from app.game_core.content.registries.maps import AreaTemplate, Connection

    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "name": "Town Square",
            "region": "central",
            "base_danger": 0.5,
            "connections": ["forest"],
            "sub_locations": {
                "inn": {"id": "inn", "name": "Rusty Dragon"},
            },
            "encounter_slot_capacity": 1,
            "encounter_table": [],
            "is_starting_area": True,
            "tags": ["safe", "urban"],
        },
    })

    template = registry.get("town")
    assert isinstance(template, AreaTemplate)
    assert template.id == "town"
    assert template.name == "Town Square"
    assert template.region == "central"
    assert template.base_danger == 0.5
    assert template.connections == [Connection(target="forest")]
    assert "inn" in template.sub_locations
    assert template.sub_locations["inn"].name == "Rusty Dragon"
    assert template.encounter_slot_capacity == 1
    assert template.encounter_table == []
    assert template.is_starting_area is True
    assert template.tags == ["safe", "urban"]

    all_templates = registry.list_all()
    assert len(all_templates) == 1
    assert all(isinstance(t, AreaTemplate) for t in all_templates)


def test_tag_registry_validates_tags_shape_and_empty_values():
    registry = TagRegistry()
    registry.load(
        {
            "terrain": {
                "id": "terrain",
                "tags": ["forest", "  "],
            },
            "faction": {
                "id": "faction",
                "tags": "guild",
            },
        }
    )

    issues = registry.validate()

    assert "tag dimension 'terrain' tags[1] must be a non-empty string" in issues
    assert "tag dimension 'faction' has invalid tags" in issues


# ------------------------------------------------------------------
# Character: consumer fields + shop_inventory + queries (Phase 2)
# ------------------------------------------------------------------


def test_character_validates_name_tags_class_faction():
    registry = CharacterRegistry()
    registry.load({
        "npc": {
            "id": "npc",
            "name": "  ",
            "tags": "not_a_list",
            "character_class": "  ",
            "class_id": "  ",
            "faction": "  ",
            "faction_id": "  ",
        },
    })

    issues = registry.validate()

    assert "character 'npc' has invalid name" in issues
    assert "character 'npc' has invalid tags" in issues
    assert "character 'npc' has invalid character_class" in issues
    assert "character 'npc' has invalid class_id" in issues
    assert "character 'npc' has invalid faction" in issues
    assert "character 'npc' has invalid faction_id" in issues


def test_character_validates_shop_inventory_structure():
    registry = CharacterRegistry()
    registry.load({
        "merchant": {
            "id": "merchant",
            "shop_inventory": {
                "sell_markup": -1.0,
                "buy_rate": 1.5,
                "rotating_slots": "bad",
            },
        },
        "broken": {"id": "broken", "shop_inventory": "not_a_mapping"},
    })

    issues = registry.validate()

    assert "character 'merchant' shop_inventory has invalid sell_markup" in issues
    assert "character 'merchant' shop_inventory has invalid buy_rate" in issues
    assert "character 'merchant' shop_inventory has invalid rotating_slots" in issues
    assert "character 'broken' has invalid shop_inventory" in issues


def test_character_validates_shop_inventory_pools():
    registry = CharacterRegistry()
    registry.load({
        "merchant": {
            "id": "merchant",
            "shop_inventory": {
                "base_pool": [
                    {"item_id": "rope"},
                    {"item_id": "  "},
                    "bad_entry",
                ],
                "rotating_pool": "not_a_list",
                "refresh_on": ["quest_done", "  "],
            },
        },
    })

    issues = registry.validate()

    assert "character 'merchant' shop_inventory.base_pool[1] missing item_id" in issues
    assert "character 'merchant' shop_inventory.base_pool[2] must be a mapping" in issues
    assert "character 'merchant' shop_inventory has invalid rotating_pool" in issues
    assert "character 'merchant' shop_inventory.refresh_on[1] must be a non-empty string" in issues


def test_character_shop_inventory_balance_warning():
    registry = CharacterRegistry()
    registry.load({
        "greedy": {
            "id": "greedy",
            "shop_inventory": {"sell_markup": 3.0},
        },
        "normal": {
            "id": "normal",
            "shop_inventory": {"sell_markup": 1.5},
        },
    })

    issues = registry.validate()

    assert any("greedy" in i and "balance warning" in i and "sell_markup" in i for i in issues)
    assert not any("normal" in i and "balance" in i for i in issues)


def test_character_query_by_area_merchants_faction():
    registry = CharacterRegistry()
    registry.load({
        "guard": {"id": "guard", "area_id": "town", "faction": "watch"},
        "merchant": {"id": "merchant", "current_area": "town", "shop": {"inventory": []}},
        "bandit": {"id": "bandit", "area_id": "forest", "faction_id": "thieves"},
        "shopkeeper": {"id": "shopkeeper", "area_id": "town", "shop_inventory": {"base_pool": []}},
    })

    town = registry.get_by_area("town")
    assert {c.id for c in town} == {"guard", "merchant", "shopkeeper"}

    merchants = registry.get_merchants()
    assert {c.id for c in merchants} == {"merchant", "shopkeeper"}

    watch = registry.get_by_faction("watch")
    assert len(watch) == 1 and watch[0].id == "guard"


def test_character_get_returns_typed_template():
    from app.game_core.content.registries.characters import CharacterTemplate

    registry = CharacterRegistry()
    registry.load({
        "guard": {
            "id": "guard",
            "name": "Town Guard",
            "area_id": "town_square",
            "tags": ["military", "lawful"],
            "faction": "city_watch",
            "character_class": "fighter",
            "personality": "Stoic and dutiful.",
        },
    })

    char = registry.get("guard")
    assert isinstance(char, CharacterTemplate)
    assert char.id == "guard"
    assert char.name == "Town Guard"
    assert char.area_id == "town_square"
    assert char.tags == ["military", "lawful"]
    assert char.faction == "city_watch"
    assert char.character_class == "fighter"
    assert char.personality == "Stoic and dutiful."

    assert registry.get("nonexistent") is None


def test_character_shop_inventory_typed():
    from app.game_core.content.registries.characters import CharacterTemplate, ShopInventory

    registry = CharacterRegistry()
    registry.load({
        "merchant": {
            "id": "merchant",
            "name": "Shopkeeper",
            "shop_inventory": {
                "sell_markup": 1.5,
                "buy_rate": 0.4,
                "base_pool": [{"item_id": "rope"}, {"item_id": "torch"}],
                "rotating_pool": [{"item_id": "potion"}],
                "rotating_slots": 2,
                "refresh_on": "rest",
            },
        },
    })

    char = registry.get("merchant")
    assert isinstance(char, CharacterTemplate)
    assert isinstance(char.shop_inventory, ShopInventory)
    assert char.shop_inventory.sell_markup == 1.5
    assert char.shop_inventory.buy_rate == 0.4
    assert len(char.shop_inventory.base_pool) == 2
    assert char.shop_inventory.base_pool[0].item_id == "rope"
    assert len(char.shop_inventory.rotating_pool) == 1
    assert char.shop_inventory.rotating_slots == 2
    assert char.shop_inventory.refresh_on == "rest"


# ------------------------------------------------------------------
# CharacterTemplate 战斗字段扩展（Batch 1-3）
# ------------------------------------------------------------------


def test_npc_attack_dataclass():
    from app.game_core.content.registries.characters import NpcAttack

    atk = NpcAttack(name="Sword Strike", hit_bonus=3, damage_dice="1d8", damage_type="slashing", range=1, tags=["melee"])
    assert atk.name == "Sword Strike"
    assert atk.hit_bonus == 3
    assert atk.damage_dice == "1d8"
    assert atk.damage_type == "slashing"
    assert atk.range == 1
    assert atk.tags == ["melee"]

    default = NpcAttack()
    assert default.name == ""
    assert default.hit_bonus == 0
    assert default.damage_dice == "1d4"
    assert default.damage_type == "physical"
    assert default.range == 1
    assert default.tags == []


def test_shop_entry_dataclass():
    from app.game_core.content.registries.characters import ShopEntry

    entry = ShopEntry(item_id="torch", count="unlimited", min_player_level=2, restock=False)
    assert entry.item_id == "torch"
    assert entry.count == "unlimited"
    assert entry.min_player_level == 2
    assert entry.restock is False

    default = ShopEntry()
    assert default.item_id == ""
    assert default.count == "1"
    assert default.min_player_level == 0
    assert default.restock is True


def test_character_combat_fields_load():
    registry = CharacterRegistry()
    registry.load({
        "warrior_npc": {
            "id": "warrior_npc",
            "name": "Village Guard",
            "base_hp": 30,
            "base_ac": 14,
            "level": 3,
            "proficiency_bonus": 2,
            "combat_capable": True,
            "stats": {"str": 16, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 9},
            "attacks": [
                {"name": "Spear Thrust", "hit_bonus": 5, "damage_dice": "1d6+3", "damage_type": "piercing", "range": 1, "tags": ["melee", "reach"]},
            ],
            "skills": ["athletics", "perception"],
            "secrets": ["knows_the_smuggler"],
        },
    })

    char = registry.get("warrior_npc")
    assert char is not None
    assert char.base_hp == 30
    assert char.base_ac == 14
    assert char.level == 3
    assert char.proficiency_bonus == 2
    assert char.combat_capable is True
    assert char.stats == {"str": 16, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 9}
    assert len(char.attacks) == 1
    atk = char.attacks[0]
    assert atk.name == "Spear Thrust"
    assert atk.hit_bonus == 5
    assert atk.damage_dice == "1d6+3"
    assert atk.damage_type == "piercing"
    assert atk.tags == ["melee", "reach"]
    assert char.skills == ["athletics", "perception"]
    from app.game_core.content.registries.characters import SecretEntry
    assert len(char.secrets) == 1
    assert isinstance(char.secrets[0], SecretEntry)
    assert char.secrets[0].content == "knows_the_smuggler"
    assert char.secrets[0].trust_threshold == 50
    assert not registry.validate()


def test_character_combat_fields_defaults():
    """旧格式数据（无战斗字段）应填充默认值，不产生 issues。"""
    registry = CharacterRegistry()
    registry.load({
        "innkeeper": {
            "id": "innkeeper",
            "name": "Old Tom",
        },
    })

    char = registry.get("innkeeper")
    assert char is not None
    assert char.base_hp is None
    assert char.base_ac is None
    assert char.level == 1
    assert char.proficiency_bonus == 2
    assert char.combat_capable is False
    assert char.attacks == []
    assert char.stats == {}
    assert char.skills == []
    assert char.secrets == []
    assert not registry.validate()


def test_character_shop_pool_typed():
    """base_pool / rotating_pool 加载后元素应为 ShopEntry 实例。"""
    from app.game_core.content.registries.characters import ShopEntry

    registry = CharacterRegistry()
    registry.load({
        "shop_npc": {
            "id": "shop_npc",
            "shop_inventory": {
                "base_pool": [
                    {"item_id": "rope", "count": "5", "restock": False},
                    {"item_id": "torch", "count": "unlimited"},
                ],
                "rotating_pool": [
                    {"item_id": "potion", "min_player_level": 2},
                ],
            },
        },
    })

    char = registry.get("shop_npc")
    assert char is not None
    si = char.shop_inventory
    assert si is not None

    assert len(si.base_pool) == 2
    rope = si.base_pool[0]
    assert isinstance(rope, ShopEntry)
    assert rope.item_id == "rope"
    assert rope.count == "5"
    assert rope.restock is False

    torch = si.base_pool[1]
    assert torch.item_id == "torch"
    assert torch.count == "unlimited"
    assert torch.restock is True  # 默认

    assert len(si.rotating_pool) == 1
    potion = si.rotating_pool[0]
    assert isinstance(potion, ShopEntry)
    assert potion.item_id == "potion"
    assert potion.min_player_level == 2
    assert not registry.validate()


# ------------------------------------------------------------------
# Batch 1-5: QuestRegistry / SkillTemplate / ItemRegistry 补全
# ------------------------------------------------------------------


def test_milestone_condition_dataclass():
    from app.game_core.content.registries.quests import MilestoneCondition

    cond = MilestoneCondition(type="flag_set", params={"flag": "quest_started"}, optional=True)
    assert cond.type == "flag_set"
    assert cond.params == {"flag": "quest_started"}
    assert cond.optional is True

    default = MilestoneCondition()
    assert default.type == ""
    assert default.params == {}
    assert default.optional is False


def test_milestone_template_new_fields_load():
    registry = QuestRegistry()
    registry.load({
        "milestones": {
            "rescue_villager": {
                "id": "rescue_villager",
                "title": "救援村民",
                "chapter_id": "chapter_1",
                "completion_value": 20,
                "sequence": 10,
                "narrative_context": "玩家需要在时限内救出被困的村民",
                "key_elements": ["villager_npc", "burning_building"],
                "involved_npcs": ["npc_elder", "npc_villager"],
                "involved_locations": ["village_square", "barn"],
                "success_conditions": [
                    {"type": "npc_talked", "params": {"npc_id": "npc_villager"}},
                    {"type": "location_visited", "params": {"area": "barn"}, "optional": True},
                ],
                "failure_conditions": [
                    {"type": "time_elapsed", "params": {"ticks": 10}},
                ],
                "failure_fallback": "村民被困，任务失败",
            },
        },
        "chapters": [{"id": "chapter_1", "title": "第一章"}],
    })

    m = registry.get_milestone("rescue_villager")
    assert m is not None
    assert m.completion_value == 20
    assert m.sequence == 10
    assert m.narrative_context == "玩家需要在时限内救出被困的村民"
    assert m.key_elements == ["villager_npc", "burning_building"]
    assert m.involved_npcs == ["npc_elder", "npc_villager"]
    assert m.involved_locations == ["village_square", "barn"]
    assert len(m.success_conditions) == 2
    assert m.success_conditions[0].type == "npc_talked"
    assert m.success_conditions[0].params == {"npc_id": "npc_villager"}
    assert m.success_conditions[0].optional is False
    assert m.success_conditions[1].optional is True
    assert len(m.failure_conditions) == 1
    assert m.failure_conditions[0].type == "time_elapsed"
    assert m.failure_fallback == "村民被困，任务失败"
    assert not registry.validate()


def test_milestone_conditions_validation():
    registry = QuestRegistry()
    registry.load({
        "milestones": {
            "bad_milestone": {
                "id": "bad_milestone",
                "success_conditions": "not_a_list",
                "failure_conditions": [
                    "not_a_mapping",
                    {"type": ""},        # 空 type
                    {"type": "flag_set"},  # 合法
                ],
            },
        },
    })

    issues = registry.validate()
    assert any("bad_milestone" in i and "success_conditions" in i for i in issues)
    assert any("bad_milestone" in i and "failure_conditions[0]" in i and "mapping" in i for i in issues)
    assert any("bad_milestone" in i and "failure_conditions[1]" in i and "missing type" in i for i in issues)
    m = registry.get_milestone("bad_milestone")
    assert m is not None
    assert len(m.success_conditions) == 0
    assert len(m.failure_conditions) == 1  # 只有合法的那个
    assert m.failure_conditions[0].type == "flag_set"


def test_skill_template_new_fields_load():
    from app.game_core.content.registries.skills import SkillRegistry

    registry = SkillRegistry()
    registry.load({
        "fireball": {
            "id": "fireball",
            "name": "火球术",
            "spell_level": 3,
            "school": "evocation",
            "requirements": {"class": ["wizard", "sorcerer"], "level": 5},
            "usable_in": ["combat", "exploration"],
        },
        "sneak": {
            "id": "sneak",
            "name": "潜行",
        },
    })

    fb = registry.get("fireball")
    assert fb is not None
    assert fb.requirements == {"class": ["wizard", "sorcerer"], "level": 5}
    assert fb.usable_in == ["combat", "exploration"]

    sneak = registry.get("sneak")
    assert sneak is not None
    assert sneak.requirements == {}
    assert sneak.usable_in == []
    assert not registry.validate()


def test_accessory_data_dataclass():
    from app.game_core.content.registries.items import AccessoryData
    from app.game_core.content.registries.shared_types import Effect

    eff = Effect(type="buff", params={"stat": "dex", "bonus": 2}, target="self")
    acc = AccessoryData(slot="neck", effects=[eff])
    assert acc.slot == "neck"
    assert len(acc.effects) == 1
    assert acc.effects[0].type == "buff"
    assert acc.effects[0].params["bonus"] == 2

    default = AccessoryData()
    assert default.slot == ""
    assert default.effects == []


def test_item_registry_accessory_type_load():
    from app.game_core.content.registries.items import AccessoryData, ItemRegistry

    registry = ItemRegistry()
    registry.load({
        "amulet_dex": {
            "id": "amulet_dex",
            "name": "敏捷护符",
            "type": "accessory",
            "rarity": "uncommon",
            "accessory_slot": "neck",
            "effects": [
                {"type": "buff", "params": {"stat": "dex", "bonus": 2}, "target": "self"},
            ],
        },
        "basic_sword": {
            "id": "basic_sword",
            "name": "普通剑",
            "type": "weapon",
            "damage_dice": "1d6",
            "damage_type": "slashing",
        },
    })

    amulet = registry.get("amulet_dex")
    assert amulet is not None
    assert isinstance(amulet.accessory_data, AccessoryData)
    assert amulet.accessory_data.slot == "neck"
    assert len(amulet.accessory_data.effects) == 1
    assert amulet.accessory_data.effects[0].type == "buff"

    sword = registry.get("basic_sword")
    assert sword is not None
    assert sword.accessory_data is None
    assert not registry.validate()


# ------------------------------------------------------------------
# Quest: milestone/chapter/event fields + queries (Phase 2)
# ------------------------------------------------------------------


def test_quest_validates_milestone_fields():
    registry = QuestRegistry()
    registry.load({
        "milestones": {
            "start": {
                "id": "start",
                "title": "  ",
                "description": "  ",
                "tags": "not_a_list",
                "prerequisites": ["valid", "  "],
            },
        },
        "chapters": [{"id": "ch1"}],
    })

    issues = registry.validate()

    assert "milestone 'start' has invalid title" in issues
    assert "milestone 'start' has invalid description" in issues
    assert "milestone 'start' has invalid tags" in issues
    assert "milestone 'start' prerequisites[1] must be a non-empty string" in issues


def test_quest_validates_chapter_fields():
    registry = QuestRegistry()
    registry.load({
        "milestones": {},
        "chapters": [
            {"id": "ch1", "title": "  ", "description": "  "},
        ],
    })

    issues = registry.validate()

    assert "chapter entry 0 has invalid title" in issues
    assert "chapter entry 0 has invalid description" in issues


def test_quest_validates_initial_event_fields():
    registry = QuestRegistry()
    registry.load({
        "milestones": {},
        "initial_events": [
            {
                "event_id": "ev1",
                "event_type": "  ",
                "conditions": "not_valid",
                "payload": "not_valid",
                "metadata": "not_valid",
            },
            {
                "id": "ev2",
                "preconditions": 42,
            },
        ],
    })

    issues = registry.validate()

    assert "initial_events[0] has invalid event_type" in issues
    assert "initial_events[0] has invalid conditions" in issues
    assert "initial_events[0] has invalid payload" in issues
    assert "initial_events[0] has invalid metadata" in issues
    assert "initial_events[1] has invalid preconditions" in issues


def test_quest_query_chapter_and_event():
    registry = QuestRegistry()
    registry.load({
        "milestones": {},
        "chapters": [
            {"id": "ch1", "title": "Chapter 1"},
            {"id": "ch2", "title": "Chapter 2"},
        ],
        "initial_events": [
            {"event_id": "ev1", "event_type": "quest"},
            {"event_id": "ev2", "event_type": "encounter"},
        ],
    })

    ch = registry.get_chapter("ch1")
    assert ch is not None and ch.title == "Chapter 1"
    assert registry.get_chapter("missing") is None

    ev = registry.get_initial_event("ev2")
    assert ev is not None and ev.event_type == "encounter"
    assert registry.get_initial_event("missing") is None


def test_quest_get_returns_typed_templates():
    from app.game_core.content.registries.quests import (
        ChapterMeta,
        InitialEvent,
        MilestoneTemplate,
    )

    registry = QuestRegistry()
    registry.load({
        "milestones": {
            "ms1": {"id": "ms1", "title": "First", "chapter_id": "ch1",
                     "prerequisites": ["ms0"], "next_milestones": []},
        },
        "chapters": [{"id": "ch1", "title": "Chapter One"}],
        "initial_events": [{"id": "ev1", "event_type": "quest"}],
    })

    m = registry.get("ms1")
    assert isinstance(m, MilestoneTemplate)
    assert m.id == "ms1"
    assert m.title == "First"
    assert m.chapter_id == "ch1"
    assert m.prerequisites == ["ms0"]

    all_milestones = registry.list_all()
    assert len(all_milestones) == 1
    assert isinstance(all_milestones[0], MilestoneTemplate)

    chapters = registry.chapters()
    assert len(chapters) == 1
    assert isinstance(chapters[0], ChapterMeta)
    assert chapters[0].title == "Chapter One"

    events = registry.initial_events()
    assert len(events) == 1
    assert isinstance(events[0], InitialEvent)
    assert events[0].event_type == "quest"


# ------------------------------------------------------------------
# Class: game mechanic fields + xp_curve monotonic (Phase 2)
# ------------------------------------------------------------------


def test_class_validates_hit_die_hp_ac_fields():
    registry = ClassRegistry()
    registry.load({
        "classes": {
            "fighter": {
                "id": "fighter",
                "hit_die": 10,
                "base_hp": 10,
                "hp_per_level": 6,
                "base_ac": 10,
                "subclass_level": 3,
                "starting_gold": 50,
            },
            "broken": {
                "id": "broken",
                "hit_die": "  ",
                "base_hp": 0,
                "hp_per_level": -1,
                "base_ac": -1,
                "subclass_level": 0,
                "starting_gold": -10,
            },
        },
    })

    issues = registry.validate()

    assert not any("fighter" in i for i in issues)
    assert "class entry 'broken' has invalid hit_die" in issues
    assert "class entry 'broken' has invalid base_hp" in issues
    assert "class entry 'broken' has invalid hp_per_level" in issues
    assert "class entry 'broken' has invalid base_ac" in issues
    assert "class entry 'broken' has invalid subclass_level" in issues
    assert "class entry 'broken' has invalid starting_gold" in issues


def test_class_validates_level_features_shape():
    from app.game_core.content.registries import Feature
    registry = ClassRegistry()
    registry.load({
        "classes": {
            "wizard": {
                "id": "wizard",
                "level_features": {
                    "1": ["cantrips"],
                    "foo": ["bad_key"],
                    "3": "not_a_list",   # 单字符串简写 → 合法
                },
            },
        },
    })

    issues = registry.validate()

    # non-numeric key → issue
    assert "class entry 'wizard' level_features key 'foo' must be numeric" in issues
    # valid key "1" → no issue
    assert not any("level_features key '1'" in i for i in issues)
    # "3": "not_a_list" → 简写字符串，被接受为 [Feature(id="not_a_list")]
    wizard = registry.get_class("wizard")
    assert wizard is not None
    assert "3" in wizard.level_features
    assert len(wizard.level_features["3"]) == 1
    assert isinstance(wizard.level_features["3"][0], Feature)
    assert wizard.level_features["3"][0].id == "not_a_list"


def test_class_validates_race_fields():
    registry = ClassRegistry()
    registry.load({
        "races": {
            "elf": {
                "id": "elf",
                "stat_bonuses": {"dex": 2},
                "racial_traits": ["darkvision"],
            },
            "broken": {
                "id": "broken",
                "stat_bonuses": "not_a_mapping",
                "racial_traits": ["valid", "  "],
            },
        },
    })

    issues = registry.validate()

    assert not any("elf" in i for i in issues)
    assert "race entry 'broken' has invalid stat_bonuses" in issues
    assert "race entry 'broken' racial_traits[1] must be a non-empty string" in issues


def test_class_validates_background_and_subclass_fields():
    registry = ClassRegistry()
    registry.load({
        "classes": {"warrior": {"id": "warrior"}},
        "backgrounds": {
            "noble": {"id": "noble", "feature": "  ", "gold_bonus": -1},
        },
        "subclasses": {
            "champion": {
                "id": "champion",
                "class_id": "warrior",
                "features": ["improved_critical", "  "],
                "level_features": "not_a_mapping",
            },
        },
    })

    issues = registry.validate()

    assert "background entry 'noble' has invalid feature" in issues
    assert "background entry 'noble' has invalid gold_bonus" in issues
    assert "subclass entry 'champion' features[1] must be a non-empty string" in issues
    assert "subclass entry 'champion' has invalid level_features" in issues


def test_class_xp_curve_monotonic_increase():
    registry = ClassRegistry()
    registry.load({
        "classes": {},
        "xp_curve": [0, 300, 200, 900],
    })

    issues = registry.validate()

    assert any("xp_curve[2] breaks monotonic increase" in i for i in issues)
    assert not any("xp_curve[1]" in i and "monotonic" in i for i in issues)

    # Also test dict mode
    registry2 = ClassRegistry()
    registry2.load({
        "classes": {},
        "xp_curve": {"1": 0, "2": 300, "3": 100},
    })

    issues2 = registry2.validate()

    assert any("xp_curve level 3 breaks monotonic increase" in i for i in issues2)


def test_class_get_returns_typed_templates():
    from app.game_core.content.registries.classes import (
        BackgroundTemplate,
        ClassTemplate,
        RaceTemplate,
        SubclassTemplate,
    )

    registry = ClassRegistry()
    registry.load({
        "classes": {
            "fighter": {
                "id": "fighter",
                "name": "Fighter",
                "hit_die": 10,
                "base_ac": 12,
                "starting_equipment": ["longsword", "shield"],
                "default_equipped": {"main_hand": "longsword", "off_hand": "shield"},
            },
        },
        "subclasses": {
            "champion": {
                "id": "champion",
                "class_id": "fighter",
                "features": ["improved_critical"],
                "level_features": {"3": ["remarkable_athlete"]},
            },
        },
        "races": {
            "human": {
                "id": "human",
                "name": "Human",
                "stat_bonuses": {"str": 1, "dex": 1},
                "racial_traits": ["versatile"],
            },
        },
        "backgrounds": {
            "soldier": {
                "id": "soldier",
                "name": "Soldier",
                "feature": "military_rank",
                "gold_bonus": 10,
                "skill_proficiency": ["athletics", "intimidation"],
            },
        },
    })

    cls = registry.get_class("fighter")
    assert isinstance(cls, ClassTemplate)
    assert cls.id == "fighter"
    assert cls.hit_die == 10
    assert cls.starting_equipment == ["longsword", "shield"]
    assert cls.default_equipped == {"main_hand": "longsword", "off_hand": "shield"}

    sub = registry.get_subclass("champion")
    assert isinstance(sub, SubclassTemplate)
    assert sub.class_id == "fighter"
    assert sub.features == ["improved_critical"]
    assert "3" in sub.level_features
    assert len(sub.level_features["3"]) == 1
    assert sub.level_features["3"][0].id == "remarkable_athlete"

    race = registry.get_race("human")
    assert isinstance(race, RaceTemplate)
    assert race.stat_bonuses == {"str": 1, "dex": 1}
    assert len(race.racial_traits) == 1
    assert race.racial_traits[0].id == "versatile"
    assert race.racial_traits[0].name == "versatile"

    bg = registry.get_background("soldier")
    assert isinstance(bg, BackgroundTemplate)
    assert bg.feature is not None
    assert bg.feature.id == "military_rank"
    assert bg.gold_bonus == 10
    assert bg.skill_proficiency == ["athletics", "intimidation"]


def test_class_list_returns_typed_templates():
    from app.game_core.content.registries.classes import (
        BackgroundTemplate,
        ClassTemplate,
        RaceTemplate,
    )

    registry = ClassRegistry()
    registry.load({
        "classes": {
            "fighter": {"id": "fighter", "name": "Fighter"},
            "wizard": {"id": "wizard", "name": "Wizard"},
        },
        "races": {
            "elf": {"id": "elf", "name": "Elf"},
        },
        "backgrounds": {
            "sage": {"id": "sage", "name": "Sage"},
        },
    })

    classes = registry.list_classes()
    assert len(classes) == 2
    assert all(isinstance(c, ClassTemplate) for c in classes)

    races = registry.list_races()
    assert len(races) == 1
    assert isinstance(races[0], RaceTemplate)

    backgrounds = registry.list_backgrounds()
    assert len(backgrounds) == 1
    assert isinstance(backgrounds[0], BackgroundTemplate)


# ------------------------------------------------------------------
# Faction: consumer fields + query (Phase 3)
# ------------------------------------------------------------------


def test_faction_validates_name_description_and_tags():
    registry = FactionRegistry()
    registry.load({
        "watch": {
            "id": "watch",
            "name": "City Watch",
            "description": "Protectors of the city",
            "alignment": "lawful",
            "relations": {"thieves": "hostile"},
            "tags": ["law", "military"],
        },
        "broken": {
            "id": "broken",
            "name": "  ",
            "description": "  ",
            "alignment": "  ",
            "relations": "not_a_mapping",
            "tags": ["valid", "  "],
        },
    })

    issues = registry.validate()

    assert not any("watch" in i for i in issues)
    assert "faction 'broken' has invalid name" in issues
    assert "faction 'broken' has invalid description" in issues
    assert "faction 'broken' has invalid alignment" in issues
    assert "faction 'broken' has invalid faction_relations" in issues
    assert "faction 'broken' tags[1] must be a non-empty string" in issues


def test_faction_query_by_tag():
    registry = FactionRegistry()
    registry.load({
        "watch": {"id": "watch", "tags": ["law", "military"]},
        "guild": {"id": "guild", "tags": ["trade"]},
        "knights": {"id": "knights", "tags": ["military", "noble"]},
    })

    military = registry.get_by_tag("military")
    assert {f.id for f in military} == {"watch", "knights"}

    trade = registry.get_by_tag("trade")
    assert len(trade) == 1 and trade[0].id == "guild"


# ------------------------------------------------------------------
# Tag: description + query methods (Phase 3)
# ------------------------------------------------------------------


def test_tag_validates_description():
    registry = TagRegistry()
    registry.load({
        "terrain": {"id": "terrain", "description": "  ", "tags": ["forest"]},
        "weather": {"id": "weather", "description": "Climate types", "tags": ["rain"]},
    })

    issues = registry.validate()

    assert "tag dimension 'terrain' has invalid description" in issues
    assert not any("weather" in i for i in issues)


def test_tag_all_tags_and_dimension_lookup():
    registry = TagRegistry()
    registry.load({
        "terrain": {"id": "terrain", "tags": ["forest", "mountain"]},
        "climate": {"id": "climate", "tags": ["tropical", "arctic"]},
    })

    all_tags = registry.all_tags()
    assert all_tags == {"forest", "mountain", "tropical", "arctic"}

    assert registry.get_dimension_for_tag("forest") == "terrain"
    assert registry.get_dimension_for_tag("arctic") == "climate"
    assert registry.get_dimension_for_tag("missing") is None


# ------------------------------------------------------------------
# Lore: consumer fields + query (Phase 3)
# ------------------------------------------------------------------


def test_lore_validates_name_content_and_tags():
    registry = LoreRegistry()
    registry.load({
        "creation": {
            "id": "creation",
            "name": "Creation Myth",
            "title": "The Beginning",
            "content": "In the beginning...",
            "text": "Long ago...",
            "tags": ["mythology"],
        },
        "broken": {
            "id": "broken",
            "name": "  ",
            "title": "  ",
            "content": "  ",
            "text": "  ",
            "tags": ["valid", "  "],
        },
    })

    issues = registry.validate()

    assert not any("creation" in i for i in issues)
    assert "lore entry 'broken' has invalid name" in issues
    assert "lore entry 'broken' has invalid title" in issues
    assert "lore entry 'broken' has invalid content" in issues
    assert "lore entry 'broken' has invalid text" in issues
    assert "lore entry 'broken' tags[1] must be a non-empty string" in issues


def test_lore_query_by_tag():
    registry = LoreRegistry()
    registry.load({
        "myth": {"id": "myth", "tags": ["mythology", "creation"]},
        "history": {"id": "history", "tags": ["timeline"]},
        "legend": {"id": "legend", "tags": ["mythology", "hero"]},
    })

    mythology = registry.get_by_tag("mythology")
    assert {entry.id for entry in mythology} == {"myth", "legend"}

    timeline = registry.get_by_tag("timeline")
    assert len(timeline) == 1 and timeline[0].id == "history"


# ------------------------------------------------------------------
# Dataclass typed access (Batch 1 migration)
# ------------------------------------------------------------------


def test_faction_get_returns_typed_template():
    from app.game_core.content.registries.factions import FactionTemplate

    registry = FactionRegistry()
    registry.load({
        "watch": {
            "id": "watch",
            "name": "City Watch",
            "alignment": "lawful",
            "tags": ["law", "military"],
            "behavioral_rules": "Patrol the streets.",
            "initial_standing": 5,
        },
    })

    faction = registry.get("watch")
    assert isinstance(faction, FactionTemplate)
    assert faction.id == "watch"
    assert faction.name == "City Watch"
    assert faction.alignment == "lawful"
    assert faction.tags == ["law", "military"]
    assert faction.behavioral_rules == ["Patrol the streets."]
    assert faction.initial_standing == 5
    assert faction.base_standing is None
    assert faction.faction_relations == {}

    assert registry.get("nonexistent") is None


def test_lore_get_returns_typed_entry():
    from app.game_core.content.registries.lore import LoreEntry

    registry = LoreRegistry()
    registry.load({
        "myth": {
            "id": "myth",
            "name": "Creation Myth",
            "text": "In the beginning...",
            "tags": ["mythology"],
        },
    })

    entry = registry.get("myth")
    assert isinstance(entry, LoreEntry)
    assert entry.id == "myth"
    assert entry.title == "Creation Myth"
    assert entry.content == "In the beginning..."
    assert entry.tags == ["mythology"]

    assert registry.get("nonexistent") is None


def test_tag_get_returns_typed_dimension():
    from app.game_core.content.registries.tag import TagDimension

    registry = TagRegistry()
    registry.load({
        "terrain": {
            "id": "terrain",
            "description": "Terrain types",
            "tags": ["forest", "mountain"],
        },
    })

    dim = registry.get("terrain")
    assert isinstance(dim, TagDimension)
    assert dim.id == "terrain"
    assert dim.description == "Terrain types"
    assert dim.tags == ["forest", "mountain"]

    assert registry.get("nonexistent") is None


# ------------------------------------------------------------------
# Dataclass typed access (Batch 2 migration)
# ------------------------------------------------------------------


def test_monster_get_returns_typed_template():
    from app.game_core.content.registries.monsters import MonsterTemplate

    registry = MonsterRegistry()
    registry.load({
        "goblin": {
            "id": "goblin",
            "name": "Goblin",
            "hp": 7,
            "ac": 15,
            "cr": 0.25,
            "creature_type": "humanoid",
            "gold_drop": 5,
        },
    })

    monster = registry.get("goblin")
    assert isinstance(monster, MonsterTemplate)
    assert monster.id == "goblin"
    assert monster.name == "Goblin"
    assert monster.hp == 7
    assert monster.ac == 15
    assert monster.cr == 0.25
    assert monster.creature_type == "humanoid"
    assert monster.gold_drop == "5"

    assert registry.get("nonexistent") is None


def test_monster_loot_entry_typed():
    from app.game_core.content.registries.monsters import LootEntry, MonsterTemplate

    registry = MonsterRegistry()
    registry.load({
        "dragon": {
            "id": "dragon",
            "loot_table": [
                {"item_id": "gold_pile", "chance": 0.8, "count": 3},
                {"item_id": "gem"},
            ],
        },
    })

    monster = registry.get("dragon")
    assert isinstance(monster, MonsterTemplate)
    assert len(monster.loot_table) == 2

    first = monster.loot_table[0]
    assert isinstance(first, LootEntry)
    assert first.item_id == "gold_pile"
    assert first.chance == 0.8
    assert first.count == "3"

    second = monster.loot_table[1]
    assert second.item_id == "gem"
    assert second.chance == 1.0
    assert second.count == "1"


def test_item_get_returns_typed_template():
    from app.game_core.content.registries.items import ItemTemplate

    registry = ItemRegistry()
    registry.load({
        "sword": {
            "id": "sword",
            "name": "Iron Sword",
            "type": "weapon",
            "rarity": "common",
            "base_price": 100,
            "slot": "main_hand",
            "damage_dice": "1d8",
            "damage_type": "slashing",
        },
    })

    item = registry.get("sword")
    assert isinstance(item, ItemTemplate)
    assert item.id == "sword"
    assert item.name == "Iron Sword"
    assert item.type == "weapon"
    assert item.rarity == "common"
    assert item.base_price == 100
    assert item.slot == "main_hand"
    # weapon attributes are now in weapon_data sub-struct
    assert item.weapon_data is not None
    assert item.weapon_data.damage_dice == "1d8"
    assert item.weapon_data.damage_type == "slashing"

    assert registry.get("nonexistent") is None


def test_item_heal_amount_aliases():
    """heal / restore_hp are legacy aliases merged into heal_amount at load time."""
    from app.game_core.content.registries.items import ItemTemplate

    registry = ItemRegistry()
    registry.load({
        "potion1": {"id": "potion1", "heal_amount": 10},
        "potion2": {"id": "potion2", "heal": 15},
        "potion3": {"id": "potion3", "restore_hp": 20},
    })

    p1 = registry.get("potion1")
    assert isinstance(p1, ItemTemplate)
    assert p1.heal_amount == 10

    p2 = registry.get("potion2")
    assert p2.heal_amount == 15  # heal alias merged into heal_amount

    p3 = registry.get("potion3")
    assert p3.heal_amount == 20  # restore_hp alias merged into heal_amount


# ------------------------------------------------------------------
# Dataclass typed access (Batch 3 migration)
# ------------------------------------------------------------------


def test_skill_get_returns_typed_template():
    from app.game_core.content.registries.skills import SkillCost, SkillEffect, SkillTemplate

    registry = SkillRegistry()
    registry.load({
        "fireball": {
            "id": "fireball",
            "category": "spell",
            "spell_level": 3,
            "school": "evocation",
            "effect": {"type": "damage", "dice": "8d6", "concentration": False},
            "cost": {"resource": "spell_slot", "amount": 1},
        },
    })

    spell = registry.get("fireball")
    assert isinstance(spell, SkillTemplate)
    assert spell.id == "fireball"
    assert spell.category == "spell"
    assert spell.spell_level == 3
    assert spell.school == "evocation"
    assert isinstance(spell.effect, SkillEffect)
    assert spell.effect.type == "damage"
    assert spell.effect.concentration is False
    assert isinstance(spell.cost, SkillCost)
    assert spell.cost.resource == "spell_slot"

    assert registry.get("nonexistent") is None


def test_skill_template_category_normalization():
    from app.game_core.content.registries.skills import SkillTemplate

    registry = SkillRegistry()
    registry.load({
        "via_category": {"id": "a", "category": "spell", "spell_level": 1, "effect": {"type": "heal", "dice": "1d8"}},
        "via_type": {"id": "b", "type": "spell", "spell_level": 2, "effect": {"type": "damage", "dice": "2d6"}},
        "via_level": {"id": "c", "spell_level": 0, "effect": {"type": "buff"}},
        "non_spell": {"id": "d", "name": "Stealth"},
    })

    assert registry.get("via_category").category == "spell"
    assert registry.get("via_type").category == "spell"
    assert registry.get("via_level").category == "spell"
    assert registry.get("non_spell").category == ""


# ===========================================================================
# F-B: SkillEffect / SkillCost / StatusEffectTemplate typed dataclasses
# ===========================================================================


def test_skill_effect_fields_parsed_correctly():
    """SkillEffect fields are populated from the effect sub-dict."""
    from app.game_core.content.registries.skills import SkillEffect

    registry = SkillRegistry()
    registry.load({
        "mage_armor": {
            "id": "mage_armor",
            "category": "spell",
            "spell_level": 1,
            "school": "abjuration",
            "effect": {
                "type": "buff",
                "applies_status": "mage_armor_effect",
                "duration_ticks": 6,
                "modifiers": {"ac": 3},
                "concentration": True,
                "upcast_dice": "1d4",
            },
            "cost": {"action_type": "action"},
        },
    })

    spell = registry.get("mage_armor")
    assert isinstance(spell.effect, SkillEffect)
    assert spell.effect.type == "buff"
    assert spell.effect.applies_status == "mage_armor_effect"
    assert spell.effect.status_duration == 6      # parsed from duration_ticks alias
    assert spell.effect.modifiers == {"ac": 3}
    assert spell.effect.concentration is True
    assert spell.effect.upcast_dice == "1d4"


def test_skill_cost_fields_parsed_correctly():
    """SkillCost fields are populated including resource_amount alias."""
    from app.game_core.content.registries.skills import SkillCost

    registry = SkillRegistry()
    registry.load({
        "rage": {
            "id": "rage",
            "effect": {"type": "buff"},
            "cost": {"action_type": "bonus_action", "resource": "rage_charge", "amount": 1},
        },
    })

    skill = registry.get("rage")
    assert isinstance(skill.cost, SkillCost)
    assert skill.cost.action_type == "bonus_action"
    assert skill.cost.resource == "rage_charge"
    assert skill.cost.resource_amount == 1   # normalized from "amount"


def test_skill_effect_old_key_aliases_normalized():
    """Alias keys (damage/heal/duration_ticks) are resolved to canonical fields."""
    from app.game_core.content.registries.skills import SkillEffect

    registry = SkillRegistry()
    registry.load({
        "fire_bolt": {
            "id": "fire_bolt",
            "category": "spell",
            "spell_level": 1,
            "school": "evocation",
            "effect": {"type": "damage", "damage": 5, "duration_ticks": 3},
            "cost": {"action_type": "action"},
        },
        "minor_heal": {
            "id": "minor_heal",
            "category": "spell",
            "spell_level": 1,
            "school": "evocation",
            "effect": {"type": "heal", "heal": 8},
            "cost": {"action_type": "action"},
        },
    })

    bolt = registry.get("fire_bolt")
    assert isinstance(bolt.effect, SkillEffect)
    assert bolt.effect.damage_amount == 5   # from "damage" alias
    assert bolt.effect.status_duration == 3  # from "duration_ticks" alias

    heal = registry.get("minor_heal")
    assert heal.effect.heal_amount == 8     # from "heal" alias


def test_status_effect_template_loaded_from_sub_table():
    """StatusEffectTemplate entries in status_effects key are registered."""
    from app.game_core.content.registries.skills import StatusEffectTemplate

    registry = SkillRegistry()
    registry.load({
        "status_effects": {
            "burning": {
                "id": "burning",
                "name": "燃烧",
                "category": "debuff",
                "stackable": False,
                "tick_damage": "1d4",
                "tick_damage_type": "fire",
                "prevents_action": False,
                "cure_conditions": ["lesser_restoration", "long_rest"],
            },
        },
        "fireball": {
            "id": "fireball",
            "category": "spell",
            "spell_level": 3,
            "school": "evocation",
            "effect": {"type": "damage", "dice": "8d6"},
            "cost": {"action_type": "action"},
        },
    })

    se = registry.get_status_effect("burning")
    assert isinstance(se, StatusEffectTemplate)
    assert se.id == "burning"
    assert se.name == "燃烧"
    assert se.category == "debuff"
    assert se.tick_damage == "1d4"
    assert se.tick_damage_type == "fire"
    assert se.cure_conditions == ["lesser_restoration", "long_rest"]

    assert registry.get_status_effect("nonexistent") is None
    assert len(registry.list_status_effects()) == 1

    # Skills are still loadable alongside status_effects
    assert registry.get("fireball") is not None


def test_skill_effect_defaults_on_empty_effect():
    """SkillEffect has safe defaults when effect dict is absent or empty."""
    from app.game_core.content.registries.skills import SkillEffect

    registry = SkillRegistry()
    registry.load({"stealth": {"id": "stealth", "name": "Stealth"}})

    skill = registry.get("stealth")
    assert isinstance(skill.effect, SkillEffect)
    assert skill.effect.type == ""
    assert skill.effect.concentration is False
    assert skill.effect.modifiers == {}
    assert skill.effect.tags == []


# ===========================================================================
# F-D: Connection typed dataclass
# ===========================================================================

def test_map_connection_typed_dataclass_from_dict() -> None:
    """Connection dict entries are parsed into typed Connection objects."""
    from app.game_core.content.registries.maps import Connection, MapRegistry

    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "connections": [
                {
                    "target_map_id": "forest",
                    "connection_type": "travel",
                    "travel_time": "30分钟",
                },
                {
                    "target_map_id": "cave",
                    "connection_type": "secret",
                    "travel_time": "2小时",
                },
            ],
        },
    })

    template = registry.get("town")
    assert len(template.connections) == 2

    forest_conn = template.connections[0]
    assert isinstance(forest_conn, Connection)
    assert forest_conn.target == "forest"
    assert forest_conn.type == "travel"
    assert forest_conn.travel_time_minutes == 30
    assert forest_conn.travel_slots == 1   # ceil(30/60)=1

    cave_conn = template.connections[1]
    assert cave_conn.target == "cave"
    assert cave_conn.type == "secret"
    assert cave_conn.travel_time_minutes == 120
    assert cave_conn.travel_slots == 2   # ceil(120/60)=2


def test_map_connection_travel_time_parsing() -> None:
    """_parse_travel_minutes handles Chinese time strings correctly."""
    from app.game_core.content.registries.maps import MapRegistry

    registry = MapRegistry()
    assert registry._parse_travel_minutes("30分钟") == 30
    assert registry._parse_travel_minutes("2小时") == 120
    assert registry._parse_travel_minutes("2小时30分钟") == 150
    assert registry._parse_travel_minutes("unknown") == 60  # fallback


def test_map_get_connections_and_get_connection() -> None:
    """get_connections() and get_connection() return typed Connection objects."""
    from app.game_core.content.registries.maps import Connection, MapRegistry

    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "connections": [
                {"target_map_id": "forest", "travel_time": "30分钟"},
                {"target_map_id": "cave", "travel_time": "2小时"},
            ],
        },
        "forest": {"id": "forest"},
        "cave": {"id": "cave"},
    })

    conns = registry.get_connections("town")
    assert len(conns) == 2
    assert all(isinstance(c, Connection) for c in conns)

    conn = registry.get_connection("town", "forest")
    assert conn is not None
    assert conn.target == "forest"
    assert conn.travel_slots == 1

    conn_cave = registry.get_connection("town", "cave")
    assert conn_cave is not None
    assert conn_cave.travel_slots == 2

    assert registry.get_connection("town", "unknown") is None
    assert registry.get_connections("unknown") == []


def test_map_get_adjacent_backward_compat() -> None:
    """get_adjacent() still returns list[str] even after Connection migration."""
    from app.game_core.content.registries.maps import MapRegistry

    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "connections": [
                {"target_map_id": "forest"},
                {"target_map_id": "cave"},
            ],
        },
    })

    adj = registry.get_adjacent("town")
    assert adj == ["forest", "cave"]
    assert all(isinstance(x, str) for x in adj)


# ===========================================================================
# F-E: ClassTemplate class_resources_schema
# ===========================================================================

def test_class_resources_schema_loads_correctly() -> None:
    """class_resources_schema is parsed into the ClassTemplate correctly."""
    from app.game_core.content.registries.classes import ClassRegistry

    registry = ClassRegistry()
    registry.load({
        "classes": {
            "fighter": {
                "id": "fighter",
                "class_resources_schema": {
                    "action_surge": {
                        "max_at_level": {"2": 1, "17": 2},
                        "recovery": "short_rest",
                    },
                    "second_wind": {
                        "max_at_level": {"1": 1},
                        "recovery": "short_rest",
                    },
                },
            },
        },
    })

    template = registry.get_class("fighter")
    assert template is not None
    schema = template.class_resources_schema
    assert "action_surge" in schema
    assert schema["action_surge"]["max_at_level"] == {"2": 1, "17": 2}
    assert schema["action_surge"]["recovery"] == "short_rest"
    assert "second_wind" in schema
    assert schema["second_wind"]["max_at_level"] == {"1": 1}


def test_class_resources_schema_validates_format() -> None:
    """Invalid class_resources_schema entries produce load issues."""
    from app.game_core.content.registries.classes import ClassRegistry

    registry = ClassRegistry()
    registry.load({
        "classes": {
            "broken": {
                "id": "broken",
                "class_resources_schema": "not_a_dict",
            },
            "partial": {
                "id": "partial",
                "class_resources_schema": {
                    "bad_resource": "not_a_mapping",
                    "no_level": {
                        "max_at_level": "should_be_mapping",
                        "recovery": "short_rest",
                    },
                },
            },
        },
    })

    issues = registry.validate()
    assert any("broken" in i and "class_resources_schema" in i for i in issues)
    assert any("bad_resource" in i and "must be a mapping" in i for i in issues)
    assert any("no_level" in i and "max_at_level" in i for i in issues)


# ---------------------------------------------------------------------------
# F-A: Item 分类型子结构测试
# ---------------------------------------------------------------------------

def test_item_builds_armor_data_from_type_and_subtype():
    from app.game_core.content.registries.items import ArmorData

    registry = ItemRegistry()
    registry.load({
        "leather": {
            "id": "leather",
            "type": "armor",
            "subtype": "light",
            "ac_bonus": 2,
            "rarity": "common",
        },
        "chainmail": {
            "id": "chainmail",
            "type": "armor",
            "subtype": "heavy",
            "ac_bonus": 4,
        },
        "no_subtype_armor": {
            "id": "no_subtype_armor",
            "type": "armor",
            "ac_bonus": 1,
        },
    })

    assert registry.validate() == []

    leather = registry.get("leather")
    assert leather is not None
    assert leather.weapon_data is None
    assert leather.consumable_data is None
    assert isinstance(leather.armor_data, ArmorData)
    assert leather.armor_data.armor_type == "light"
    assert leather.armor_data.base_ac == 12            # 绝对值：10 + ac_bonus(2)
    assert leather.armor_data.dex_cap is None          # light → 无上限
    assert leather.armor_data.stealth_disadvantage is False
    assert leather.armor_data.proficiency == "light"

    chain = registry.get("chainmail")
    assert chain is not None
    assert chain.armor_data.armor_type == "heavy"
    assert chain.armor_data.base_ac == 14              # 绝对值：10 + ac_bonus(4)
    assert chain.armor_data.dex_cap == 0               # heavy → DEX 无加成
    assert chain.armor_data.stealth_disadvantage is True

    no_sub = registry.get("no_subtype_armor")
    assert no_sub is not None
    assert no_sub.armor_data.armor_type == "light"     # 无 subtype → 默认 light


def test_item_builds_shield_armor_data():
    from app.game_core.content.registries.items import ArmorData

    registry = ItemRegistry()
    registry.load({
        "buckler": {
            "id": "buckler",
            "type": "armor",
            "subtype": "shield",
            "ac_bonus": 2,
        },
    })

    assert registry.validate() == []

    item = registry.get("buckler")
    assert item is not None
    assert isinstance(item.armor_data, ArmorData)
    assert item.armor_data.armor_type == "shield"
    assert item.armor_data.base_ac == 2
    assert item.armor_data.dex_cap is None
    assert item.armor_data.stealth_disadvantage is False
    assert item.armor_data.proficiency == "shield"


def test_item_builds_weapon_data_from_flat_fields():
    from app.game_core.content.registries.items import WeaponData

    registry = ItemRegistry()
    registry.load({
        "longsword": {
            "id": "longsword",
            "type": "weapon",
            "damage_dice": "1d8",
            "damage_type": "slashing",
            "weapon_slot": "main_hand",
            "weapon_properties": ["VERSATILE"],
            "versatile_dice": "1d10",
            "weapon_proficiency": "martial",
        },
        "dagger": {
            "id": "dagger",
            "type": "weapon",
            "damage_dice": "1d4",
            "damage_type": "piercing",
            # no weapon_slot → defaults to main_hand
        },
    })

    assert registry.validate() == []

    sword = registry.get("longsword")
    assert sword is not None
    assert isinstance(sword.weapon_data, WeaponData)
    assert sword.weapon_data.damage_dice == "1d8"
    assert sword.weapon_data.damage_type == "slashing"
    assert sword.weapon_data.slot == "main_hand"
    assert sword.weapon_data.properties == ["VERSATILE"]
    assert sword.weapon_data.versatile_dice == "1d10"
    assert sword.weapon_data.proficiency == "martial"
    assert sword.armor_data is None
    assert sword.consumable_data is None

    dagger = registry.get("dagger")
    assert dagger is not None
    assert dagger.weapon_data.slot == "main_hand"      # default


def test_item_builds_consumable_data_from_heal_amount():
    from app.game_core.content.registries.items import ConsumableData

    registry = ItemRegistry()
    registry.load({
        "potion": {"id": "potion", "heal_amount": 8},
        "trinket": {"id": "trinket", "name": "Odd Trinket"},  # no heal
    })

    potion = registry.get("potion")
    assert potion is not None
    assert isinstance(potion.consumable_data, ConsumableData)
    assert potion.consumable_data.trigger == "on_use"
    assert potion.consumable_data.charges == 1
    assert potion.consumable_data.effect.type == "heal"
    assert potion.consumable_data.effect.params["amount"] == 8
    assert potion.weapon_data is None
    assert potion.armor_data is None

    trinket = registry.get("trinket")
    assert trinket is not None
    assert trinket.consumable_data is None


def test_item_query_get_weapons_get_armors_get_consumables():
    registry = ItemRegistry()
    registry.load({
        "sword": {
            "id": "sword", "type": "weapon",
            "damage_dice": "1d8", "damage_type": "slashing",
            "weapon_properties": ["FINESSE"],
        },
        "bow": {
            "id": "bow", "type": "weapon",
            "damage_dice": "1d6", "damage_type": "piercing",
            "weapon_slot": "ranged",
        },
        "leather": {"id": "leather", "type": "armor", "subtype": "light", "ac_bonus": 2},
        "shield": {"id": "shield", "type": "armor", "subtype": "shield", "ac_bonus": 2},
        "potion": {"id": "potion", "heal_amount": 5},
        "misc": {"id": "misc"},
    })

    weapons = registry.get_weapons()
    assert {w.id for w in weapons} == {"sword", "bow"}

    finesse_weapons = registry.get_weapons(properties=["FINESSE"])
    assert {w.id for w in finesse_weapons} == {"sword"}

    armors = registry.get_armors()
    assert {a.id for a in armors} == {"leather", "shield"}

    light_armors = registry.get_armors(armor_type="light")
    assert {a.id for a in light_armors} == {"leather"}

    shields = registry.get_armors(armor_type="shield")
    assert {s.id for s in shields} == {"shield"}

    consumables = registry.get_consumables()
    assert {c.id for c in consumables} == {"potion"}


def test_item_query_get_by_price_range_and_by_tags():
    registry = ItemRegistry()
    registry.load({
        "cheap": {"id": "cheap", "base_price": 5, "tags": ["loot", "common"]},
        "mid": {"id": "mid", "base_price": 50, "tags": ["loot"]},
        "pricey": {"id": "pricey", "base_price": 200, "tags": ["rare"]},
        "free": {"id": "free", "base_price": 0, "tags": []},
        "no_price": {"id": "no_price"},
    })

    in_range = registry.get_by_price_range(10, 100)
    assert {i.id for i in in_range} == {"mid"}

    cheap_range = registry.get_by_price_range(0, 50)
    assert {i.id for i in cheap_range} == {"cheap", "mid", "free"}

    loot = registry.get_by_tags(["loot"])
    assert {i.id for i in loot} == {"cheap", "mid"}

    loot_common = registry.get_by_tags(["loot", "common"])
    assert {i.id for i in loot_common} == {"cheap"}


# ===========================================================================
# MonsterRegistry: MonsterAttack 新字段 + MonsterTemplate AI 字段（F-C）
# ===========================================================================

def test_monster_attack_loads_damage_fields() -> None:
    """MonsterAttack 应正确加载 damage_dice / hit_bonus / damage_type。"""
    registry = MonsterRegistry()
    registry.load({
        "goblin": {
            "id": "goblin",
            "hp": 7,
            "ac": 13,
            "attacks": [
                {"name": "短剑", "damage_dice": "1d6+2", "hit_bonus": 4, "damage_type": "piercing"},
                {"name": "bite", "damage_dice": "1d4"},  # 仅 name + damage_dice，其余默认
            ],
        }
    })
    template = registry.get("goblin")
    assert template is not None
    assert len(template.attacks) == 2

    sword = template.attacks[0]
    assert sword.name == "短剑"
    assert sword.damage_dice == "1d6+2"
    assert sword.hit_bonus == 4
    assert sword.damage_type == "piercing"

    bite = template.attacks[1]
    assert bite.name == "bite"
    assert bite.damage_dice == "1d4"
    assert bite.hit_bonus == 0      # 默认
    assert bite.damage_type == "physical"  # 默认


def test_monster_template_ai_fields_load() -> None:
    """MonsterTemplate 应正确加载 ai_personality / flee_threshold / xp_reward。"""
    registry = MonsterRegistry()
    registry.load({
        "cowardly_rat": {
            "id": "cowardly_rat",
            "hp": 5,
            "ac": 10,
            "ai_personality": "cowardly",
            "flee_threshold": 0.5,
            "xp_reward": 10,
        },
        "aggressive_troll": {
            "id": "aggressive_troll",
            "hp": 84,
            "ac": 15,
            # 不填 → 使用默认
        },
    })
    rat = registry.get("cowardly_rat")
    assert rat is not None
    assert rat.ai_personality == "cowardly"
    assert rat.flee_threshold == 0.5
    assert rat.xp_reward == 10

    troll = registry.get("aggressive_troll")
    assert troll is not None
    assert troll.ai_personality == "aggressive"   # 默认
    assert troll.flee_threshold == 0.0             # 默认
    assert troll.xp_reward == 0                    # 默认


def test_roll_damage_dice_various_formats() -> None:
    """roll_damage_dice 应正确解析 NdM / NdM+B / NdM-B 格式，无效时返回 1。"""
    from app.game_core.rules.handler_utils import roll_damage_dice

    # 多次运行验证范围
    for _ in range(50):
        r = roll_damage_dice("1d4")
        assert 1 <= r <= 4, f"1d4 out of range: {r}"

        r = roll_damage_dice("2d6")
        assert 2 <= r <= 12, f"2d6 out of range: {r}"

        r = roll_damage_dice("1d8+3")
        assert 4 <= r <= 11, f"1d8+3 out of range: {r}"

        r = roll_damage_dice("1d4-1")
        assert 1 <= r <= 3, f"1d4-1 out of range: {r}"  # min clamped to 1

    # 无效格式
    assert roll_damage_dice("invalid") == 1
    assert roll_damage_dice("") == 1
    assert roll_damage_dice("0d4") == 1   # 骰数为 0 → fallback


# ===========================================================================
# Batch 1-4: MonsterTemplate / FactionTemplate / LoreRegistry 新字段
# ===========================================================================


def test_monster_new_fields() -> None:
    """MonsterTemplate 新字段：vulnerabilities/speed/flee_chance/tactics_notes/preferred_terrain/group_size。"""
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({
        "goblin": {
            "id": "goblin",
            "name": "地精",
            "description": "狡猾的小型类人生物",
            "vulnerabilities": ["fire", "radiant"],
            "speed": 40,
            "flee_chance": 0.8,
            "tactics_notes": "优先攻击最脆弱的目标",
            "preferred_terrain": ["cave", "forest"],
            "group_size": "pack",
        },
    })
    m = reg.get("goblin")
    assert m is not None
    assert m.description == "狡猾的小型类人生物"
    assert "fire" in m.vulnerabilities
    assert len(m.vulnerabilities) == 2
    assert m.speed == 40
    assert m.flee_chance == 0.8
    assert m.tactics_notes == "优先攻击最脆弱的目标"
    assert "cave" in m.preferred_terrain
    assert m.group_size == "pack"


def test_monster_new_fields_defaults() -> None:
    """MonsterTemplate 新字段缺省时使用默认值。"""
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({"troll": {"id": "troll", "name": "巨魔"}})
    m = reg.get("troll")
    assert m is not None
    assert m.description == ""
    assert m.vulnerabilities == []
    assert m.speed == 30
    assert m.flee_chance == 0.5
    assert m.tactics_notes == ""
    assert m.preferred_terrain == []
    assert m.group_size == "solo"


def test_monster_flee_chance_invalid() -> None:
    """flee_chance 非法值 → issue 收集，使用默认 0.5。"""
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({"bat": {"id": "bat", "name": "蝙蝠", "flee_chance": 2.5}})
    m = reg.get("bat")
    assert m is not None
    assert m.flee_chance == 0.5
    issues = reg.validate()
    assert any("flee_chance" in i for i in issues)


def test_faction_new_fields() -> None:
    """FactionTemplate 新字段：influence_areas/leader_id/member_ids。"""
    from app.game_core.content.registries.factions import FactionRegistry

    reg = FactionRegistry()
    reg.load({
        "thieves_guild": {
            "id": "thieves_guild",
            "name": "盗贼公会",
            "influence_areas": ["town_market", "docks"],
            "leader_id": "npc_shadow_boss",
            "member_ids": ["npc_rogue_a", "npc_rogue_b", "npc_fence"],
        },
    })
    f = reg.get("thieves_guild")
    assert f is not None
    assert "town_market" in f.influence_areas
    assert len(f.influence_areas) == 2
    assert f.leader_id == "npc_shadow_boss"
    assert len(f.member_ids) == 3
    assert "npc_fence" in f.member_ids


def test_faction_new_fields_defaults() -> None:
    """FactionTemplate 新字段缺省时为空值。"""
    from app.game_core.content.registries.factions import FactionRegistry

    reg = FactionRegistry()
    reg.load({"guard": {"id": "guard", "name": "城卫"}})
    f = reg.get("guard")
    assert f is not None
    assert f.influence_areas == []
    assert f.leader_id is None
    assert f.member_ids == []


def test_lore_entry_scope_fields() -> None:
    """LoreEntry scope/scope_id 字段加载验证。"""
    from app.game_core.content.registries.lore import LoreRegistry

    reg = LoreRegistry()
    reg.load({
        "area_rule": {
            "id": "area_rule",
            "title": "禁魔区域",
            "content": "此区域内不得施法",
            "scope": "area",
            "scope_id": "dungeon_zone_3",
        },
        "global_law": {
            "id": "global_law",
            "title": "帝国法律",
            "content": "禁止公开携带武器",
        },
    })
    area_rule = reg.get("area_rule")
    assert area_rule is not None
    assert area_rule.scope == "area"
    assert area_rule.scope_id == "dungeon_zone_3"

    global_law = reg.get("global_law")
    assert global_law is not None
    assert global_law.scope == "global"
    assert global_law.scope_id is None


def test_lore_registry_structured_format() -> None:
    """结构化格式 {"entries":..., "rules":...} → WorldRule 正确构建。"""
    from app.game_core.content.registries.lore import LoreRegistry

    reg = LoreRegistry()
    reg.load({
        "entries": {
            "lore_01": {"id": "lore_01", "title": "世界起源", "content": "传说……"},
        },
        "rules": {
            "rule_magic": {
                "id": "rule_magic",
                "title": "魔法规则",
                "description": "施法消耗法力",
                "tags": ["magic", "system"],
                "scope": "global",
                "priority": 10,
            },
        },
    })
    assert reg.get("lore_01") is not None
    rule = reg.get_rule("rule_magic")
    assert rule is not None
    assert rule.title == "魔法规则"
    assert rule.priority == 10
    assert "magic" in rule.tags
    assert rule.scope == "global"
    assert rule.scope_id is None


def test_lore_list_rules_by_scope_sorted() -> None:
    """list_rules_by_scope() 过滤正确 + 按优先级降序排列。"""
    from app.game_core.content.registries.lore import LoreRegistry

    reg = LoreRegistry()
    reg.load({
        "rules": {
            "r1": {"id": "r1", "title": "规则1", "scope": "area", "scope_id": "town", "priority": 5},
            "r2": {"id": "r2", "title": "规则2", "scope": "area", "scope_id": "town", "priority": 15},
            "r3": {"id": "r3", "title": "规则3", "scope": "area", "scope_id": "forest", "priority": 20},
            "r4": {"id": "r4", "title": "规则4", "scope": "global", "priority": 100},
        },
    })
    # 按 scope=area + scope_id=town 过滤
    town_rules = reg.list_rules_by_scope("area", "town")
    assert len(town_rules) == 2
    assert town_rules[0].id == "r2"  # priority 15 > 5
    assert town_rules[1].id == "r1"

    # 只按 scope=area 过滤（不指定 scope_id）
    area_rules = reg.list_rules_by_scope("area")
    assert len(area_rules) == 3

    # global 规则
    global_rules = reg.list_rules_by_scope("global")
    assert len(global_rules) == 1
    assert global_rules[0].id == "r4"


def test_lore_flat_format_backward_compat() -> None:
    """旧格式（平坦 dict）→ entries 正常解析，rules 为空。"""
    from app.game_core.content.registries.lore import LoreRegistry

    reg = LoreRegistry()
    reg.load({
        "lore_history": {"id": "lore_history", "title": "历史", "content": "往事如烟"},
        "lore_magic": {"id": "lore_magic", "title": "魔法起源", "content": "远古魔法"},
    })
    assert len(reg.list_all()) == 2
    assert reg.list_rules() == []
    assert reg.get("lore_history") is not None


# ---------------------------------------------------------------------------
# R-2a: MonsterAttack range/tags + gold_drop str + LootEntry.count str + skills
# ---------------------------------------------------------------------------

def test_monster_attack_range_and_tags():
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({
        "troll": {
            "id": "troll",
            "attacks": [
                {"name": "claw", "damage_dice": "1d6", "range": 2, "tags": ["MELEE", "SLASHING"]},
            ],
        },
    })
    monster = reg.get("troll")
    assert monster is not None
    atk = monster.attacks[0]
    assert atk.range == 2
    assert atk.tags == ["MELEE", "SLASHING"]


def test_monster_attack_range_default():
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({
        "goblin": {
            "id": "goblin",
            "attacks": [{"name": "dagger"}],
        },
    })
    monster = reg.get("goblin")
    assert monster is not None
    assert monster.attacks[0].range == 1
    assert monster.attacks[0].tags == []


def test_monster_gold_drop_str_default():
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({"rat": {"id": "rat"}})
    monster = reg.get("rat")
    assert monster is not None
    assert monster.gold_drop == "0"


def test_monster_gold_drop_int_backward_compat():
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({"goblin": {"id": "goblin", "gold_drop": 3}})
    monster = reg.get("goblin")
    assert monster is not None
    assert monster.gold_drop == "3"


def test_monster_loot_entry_count_str_default():
    from app.game_core.content.registries.monsters import LootEntry, MonsterRegistry

    reg = MonsterRegistry()
    reg.load({
        "dragon": {
            "id": "dragon",
            "loot_table": [
                {"item_id": "gold_pile", "chance": 1.0, "count": "1d4"},
                {"item_id": "gem"},
            ],
        },
    })
    monster = reg.get("dragon")
    assert monster is not None
    assert isinstance(monster.loot_table[0], LootEntry)
    assert monster.loot_table[0].count == "1d4"
    assert monster.loot_table[1].count == "1"   # default


def test_monster_skills_field():
    from app.game_core.content.registries.monsters import MonsterRegistry

    reg = MonsterRegistry()
    reg.load({
        "wizard_golem": {
            "id": "wizard_golem",
            "skills": ["arcana", "perception"],
        },
    })
    monster = reg.get("wizard_golem")
    assert monster is not None
    assert monster.skills == ["arcana", "perception"]


# ---------------------------------------------------------------------------
# R-2b: ConsumableData.effect typed + behavioral_rules list + terrain_type + blocked
# ---------------------------------------------------------------------------

def test_consumable_data_effect_typed():
    """consumable_data 子 Mapping → Effect 对象."""
    from app.game_core.content.registries.items import ConsumableData, ItemRegistry
    from app.game_core.content.registries.shared_types import Effect

    reg = ItemRegistry()
    reg.load({
        "elixir": {
            "id": "elixir",
            "type": "consumable",
            "consumable_data": {
                "trigger": "on_use",
                "charges": 2,
                "effect": {"type": "buff", "params": {"stat": "str", "bonus": 2}, "target": "self"},
            },
        },
    })
    item = reg.get("elixir")
    assert item is not None
    assert isinstance(item.consumable_data, ConsumableData)
    assert item.consumable_data.trigger == "on_use"
    assert item.consumable_data.charges == 2
    assert isinstance(item.consumable_data.effect, Effect)
    assert item.consumable_data.effect.type == "buff"
    assert item.consumable_data.effect.params["stat"] == "str"
    assert item.consumable_data.effect.target == "self"


def test_consumable_data_top_level_effect():
    """顶层 effect 键 → ConsumableData with Effect 对象."""
    from app.game_core.content.registries.items import ConsumableData, ItemRegistry
    from app.game_core.content.registries.shared_types import Effect

    reg = ItemRegistry()
    reg.load({
        "antidote": {
            "id": "antidote",
            "type": "consumable",
            "effect": {"type": "cure", "params": {"condition": "poison"}, "target": "self"},
        },
    })
    item = reg.get("antidote")
    assert item is not None
    assert isinstance(item.consumable_data, ConsumableData)
    assert isinstance(item.consumable_data.effect, Effect)
    assert item.consumable_data.effect.type == "cure"
    assert item.consumable_data.effect.params["condition"] == "poison"


def test_consumable_data_heal_amount_backward_compat():
    """heal_amount → Effect(type='heal') 向后兼容."""
    from app.game_core.content.registries.shared_types import Effect
    from app.game_core.content.registries.items import ItemRegistry

    reg = ItemRegistry()
    reg.load({"potion": {"id": "potion", "heal_amount": 5}})
    item = reg.get("potion")
    assert item is not None
    assert item.consumable_data is not None
    assert isinstance(item.consumable_data.effect, Effect)
    assert item.consumable_data.effect.type == "heal"
    assert item.consumable_data.effect.params["amount"] == 5


def test_faction_behavioral_rules_list():
    """behavioral_rules list 格式."""
    from app.game_core.content.registries.factions import FactionRegistry

    reg = FactionRegistry()
    reg.load({
        "guild": {
            "id": "guild",
            "name": "Merchant Guild",
            "behavioral_rules": ["No theft", "Fair trade only"],
        },
    })
    faction = reg.get("guild")
    assert faction is not None
    assert faction.behavioral_rules == ["No theft", "Fair trade only"]


def test_faction_behavioral_rules_str_compat():
    """旧单字符串格式向后兼容 → 单元素 list."""
    from app.game_core.content.registries.factions import FactionRegistry

    reg = FactionRegistry()
    reg.load({
        "watch": {
            "id": "watch",
            "behavioral_rules": "Enforce law and order",
        },
    })
    faction = reg.get("watch")
    assert faction is not None
    assert faction.behavioral_rules == ["Enforce law and order"]


def test_area_terrain_type_and_connection_blocked():
    """terrain_type 字段读取 + Connection.blocked 默认与显式."""
    from app.game_core.content.registries.maps import MapRegistry

    reg = MapRegistry()
    reg.load({
        "forest": {
            "id": "forest",
            "terrain_type": "forest",
            "connections": [
                {"target": "town", "blocked": True},
                {"target": "cave"},
            ],
        },
    })
    area = reg.get("forest")
    assert area is not None
    assert area.terrain_type == "forest"
    assert len(area.connections) == 2
    assert area.connections[0].target == "town"
    assert area.connections[0].blocked is True
    assert area.connections[1].target == "cave"
    assert area.connections[1].blocked is False


# ---------------------------------------------------------------------------
# R-2c: RaceTemplate.racial_traits list[Feature] + BackgroundTemplate.feature Feature|None
# ---------------------------------------------------------------------------

def test_race_racial_traits_str_shorthand():
    """str 简写格式 → Feature(id=s, name=s)."""
    from app.game_core.content.registries.classes import ClassRegistry, RaceTemplate
    from app.game_core.content.registries.class_types import Feature

    reg = ClassRegistry()
    reg.load({
        "races": {
            "elf": {
                "id": "elf",
                "racial_traits": ["darkvision", "fey_ancestry"],
            },
        },
    })
    race = reg.get_race("elf")
    assert isinstance(race, RaceTemplate)
    assert len(race.racial_traits) == 2
    assert all(isinstance(t, Feature) for t in race.racial_traits)
    assert race.racial_traits[0].id == "darkvision"
    assert race.racial_traits[1].id == "fey_ancestry"


def test_race_racial_traits_mapping_format():
    """dict Mapping 格式 → Feature via _build_feature."""
    from app.game_core.content.registries.classes import ClassRegistry
    from app.game_core.content.registries.class_types import Feature

    reg = ClassRegistry()
    reg.load({
        "races": {
            "dwarf": {
                "id": "dwarf",
                "racial_traits": [
                    {"id": "stonecunning", "name": "Stonecunning", "description": "Bonus on stone checks."},
                ],
            },
        },
    })
    race = reg.get_race("dwarf")
    assert race is not None
    assert len(race.racial_traits) == 1
    trait = race.racial_traits[0]
    assert isinstance(trait, Feature)
    assert trait.id == "stonecunning"
    assert trait.name == "Stonecunning"


def test_background_feature_typed():
    """str 简写 + Mapping 两种格式均产生 Feature 对象."""
    from app.game_core.content.registries.classes import ClassRegistry
    from app.game_core.content.registries.class_types import Feature

    reg = ClassRegistry()
    reg.load({
        "backgrounds": {
            "soldier": {"id": "soldier", "feature": "military_rank"},
            "sage": {
                "id": "sage",
                "feature": {"id": "researcher", "name": "Researcher", "description": "Find info."},
            },
            "hermit": {"id": "hermit"},
        },
    })
    soldier = reg.get_background("soldier")
    assert soldier is not None
    assert isinstance(soldier.feature, Feature)
    assert soldier.feature.id == "military_rank"

    sage = reg.get_background("sage")
    assert sage is not None
    assert isinstance(sage.feature, Feature)
    assert sage.feature.id == "researcher"

    hermit = reg.get_background("hermit")
    assert hermit is not None
    assert hermit.feature is None


def test_growth_resolves_feature_typed_traits():
    """GrowthHandler._resolve_class_features 正确从 Feature 对象提取 id."""
    from app.game_core.content import WorldInstance
    from app.game_core.content.registries import ClassRegistry
    from app.game_core.rules import Command, RulesEngine
    from app.game_core.rules.handlers import GrowthHandler
    from app.game_core.state import StateContainer
    from app.game_core.state.slices import PlayerSlice

    world = WorldInstance("test")
    classes = ClassRegistry()
    classes.load({
        "classes": {"fighter": {"id": "fighter", "hit_die": 10}},
        "races": {"human": {"id": "human", "racial_traits": ["versatile", "bonus_feat"]}},
        "backgrounds": {"soldier": {"id": "soldier", "feature": "military_rank"}},
    })
    world.register(classes)

    state = StateContainer()
    player = PlayerSlice()
    player.restore({})
    state.register(player)

    engine = RulesEngine()
    engine.register(GrowthHandler())
    cmd = Command(type="create_character", params={
        "character_id": "pc_1",
        "name": "Hero",
        "race_id": "human", "class_id": "fighter", "background_id": "soldier",
        "ability_scores": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    })
    result = engine.execute(cmd, state, world)
    assert result.success is True, result.errors
    state.apply(result.delta)
    class_features = state.player.class_features
    assert "versatile" in class_features
    assert "bonus_feat" in class_features
    assert "military_rank" in class_features


# ---------------------------------------------------------------------------
# R-4: Registry Query API 补全
# ---------------------------------------------------------------------------


def test_map_get_sub_location():
    from app.game_core.content.registries.maps import MapRegistry

    registry = MapRegistry()
    registry.load({
        "town": {
            "id": "town",
            "sub_locations": {
                "inn": {"id": "inn", "name": "The Inn"},
                "market": {"id": "market", "name": "Market Square"},
            },
        },
        "forest": {"id": "forest"},
    })

    # 正向：找到子地点
    inn = registry.get_sub_location("town", "inn")
    assert inn is not None
    assert inn.id == "inn"

    # loc_id 不存在
    assert registry.get_sub_location("town", "dungeon") is None

    # area_id 不存在
    assert registry.get_sub_location("nowhere", "inn") is None

    # area 无子地点
    assert registry.get_sub_location("forest", "inn") is None


def test_faction_get_factions_in_area():
    from app.game_core.content.registries.factions import FactionRegistry

    registry = FactionRegistry()
    registry.load({
        "watch": {"id": "watch", "influence_areas": ["town", "docks"]},
        "guild": {"id": "guild", "influence_areas": ["town", "market"]},
        "bandits": {"id": "bandits", "influence_areas": ["forest"]},
    })

    town_factions = registry.get_factions_in_area("town")
    assert {f.id for f in town_factions} == {"watch", "guild"}

    forest_factions = registry.get_factions_in_area("forest")
    assert len(forest_factions) == 1 and forest_factions[0].id == "bandits"

    assert registry.get_factions_in_area("desert") == []


def test_faction_get_relations_of():
    from app.game_core.content.registries.factions import FactionRegistry

    registry = FactionRegistry()
    registry.load({
        "watch": {"id": "watch", "faction_relations": {"guild": "neutral", "bandits": "hostile"}},
        "guild": {"id": "guild"},
    })

    relations = registry.get_relations_of("watch")
    assert relations == {"guild": "neutral", "bandits": "hostile"}

    # 返回副本：修改不影响原始数据
    relations["guild"] = "allied"
    assert registry.get_relations_of("watch")["guild"] == "neutral"

    # faction 不存在返回空 dict
    assert registry.get_relations_of("nonexistent") == {}


def test_faction_get_behavioral_rules():
    from app.game_core.content.registries.factions import FactionRegistry

    registry = FactionRegistry()
    registry.load({
        "watch": {"id": "watch", "behavioral_rules": ["Patrol the streets.", "Arrest criminals."]},
        "guild": {"id": "guild"},
    })

    rules = registry.get_behavioral_rules("watch")
    assert rules == ["Patrol the streets.", "Arrest criminals."]

    # 返回副本
    rules.append("extra")
    assert len(registry.get_behavioral_rules("watch")) == 2

    # faction 无规则 → []
    assert registry.get_behavioral_rules("guild") == []

    # faction 不存在 → []
    assert registry.get_behavioral_rules("nonexistent") == []


def test_lore_get_rules_for_context_global():
    from app.game_core.content.registries.lore import LoreRegistry

    registry = LoreRegistry()
    registry.load({
        "rules": {
            "r1": {"id": "r1", "scope": "global", "priority": 10},
            "r2": {"id": "r2", "scope": "area", "scope_id": "town", "priority": 5},
            "r3": {"id": "r3", "scope": "faction", "scope_id": "watch", "priority": 3},
        },
    })

    # 不传参数只取 global
    result = registry.get_rules_for_context()
    assert len(result) == 1
    assert result[0].id == "r1"


def test_lore_get_rules_for_context_mixed():
    from app.game_core.content.registries.lore import LoreRegistry

    registry = LoreRegistry()
    registry.load({
        "rules": {
            "g1": {"id": "g1", "scope": "global", "priority": 10},
            "a1": {"id": "a1", "scope": "area", "scope_id": "town", "priority": 7},
            "a2": {"id": "a2", "scope": "area", "scope_id": "forest", "priority": 6},
            "f1": {"id": "f1", "scope": "faction", "scope_id": "watch", "priority": 4},
            "f2": {"id": "f2", "scope": "faction", "scope_id": "guild", "priority": 2},
        },
    })

    result = registry.get_rules_for_context(
        area_id="town",
        faction_ids=["watch"],
    )
    ids = [r.id for r in result]
    assert ids == ["g1", "a1", "f1"]  # priority 降序，forest/guild 被过滤

    # 空参数 → 只有 global
    result2 = registry.get_rules_for_context()
    assert [r.id for r in result2] == ["g1"]


def test_skill_get_by_category():
    from app.game_core.content.registries.skills import SkillRegistry

    registry = SkillRegistry()
    registry.load({
        "slash": {"id": "slash", "category": "martial"},
        "fireball": {"id": "fireball", "category": "spell"},
        "toughness": {"id": "toughness", "category": "passive"},
    })

    martial = registry.get_by_category("martial")
    assert len(martial) == 1 and martial[0].id == "slash"

    assert registry.get_by_category("unknown") == []


def test_skill_get_combat_and_exploration():
    from app.game_core.content.registries.skills import SkillRegistry

    registry = SkillRegistry()
    registry.load({
        "slash": {"id": "slash", "usable_in": ["combat"]},
        "stealth": {"id": "stealth", "usable_in": ["exploration"]},
        "versatile": {"id": "versatile", "usable_in": ["combat", "exploration"]},
        "passive": {"id": "passive", "usable_in": []},
    })

    combat = registry.get_combat_skills()
    assert {s.id for s in combat} == {"slash", "versatile"}

    exploration = registry.get_exploration_skills()
    assert {s.id for s in exploration} == {"stealth", "versatile"}

    # 空结果
    registry2 = SkillRegistry()
    registry2.load({"p": {"id": "p", "usable_in": []}})
    assert registry2.get_combat_skills() == []
    assert registry2.get_exploration_skills() == []
