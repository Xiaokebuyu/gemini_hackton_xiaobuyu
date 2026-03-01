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


def test_map_registry_validates_optional_encounter_profile_shape():
    registry = MapRegistry()
    registry.load(
        {
            "forest": {
                "id": "forest",
                "encounter_profile": {
                    "slot_capacity": 1,
                    "templates": [
                        {
                            "id": "forest_patrol",
                            "periods": ["dusk"],
                            "source": "encounter",
                        }
                    ],
                },
            },
            "wilds": {
                "id": "wilds",
                "encounter_profile": {
                    "slot_capacity": "bad",
                    "templates": [
                        {
                            "id": "   ",
                            "periods": "dusk",
                            "source": "   ",
                        }
                    ],
                },
            },
            "cave": {
                "id": "cave",
                "encounter_profile": [],
            },
        }
    )

    issues = registry.validate()

    assert "map 'cave' has invalid encounter_profile" in issues
    assert "map 'wilds' encounter_profile has invalid slot_capacity" in issues
    assert "map 'wilds' encounter_profile template 0 has invalid id" in issues
    assert "map 'wilds' encounter_profile template 0 has invalid periods" in issues
    assert "map 'wilds' encounter_profile template 0 has invalid source" in issues


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
                "gold_drop": -1,
                "loot_table": [
                    {"item_id": "   ", "count": -1, "chance": 2.0},
                    "bad-entry",
                ],
            }
        }
    )

    issues = registry.validate()

    assert "monster 'goblin' has invalid hp" in issues
    assert "monster 'goblin' has invalid ac" in issues
    assert "monster 'goblin' has invalid gold_drop" in issues
    assert "monster 'goblin' loot_table[0] has invalid item_id" in issues
    assert "monster 'goblin' loot_table[0] has invalid count" in issues
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
    from app.game_core.content.registries.maps import AreaTemplate

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
            "encounter_profile": {"slot_capacity": 1, "templates": []},
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
    assert template.connections == ["forest"]
    assert "inn" in template.sub_locations
    assert template.sub_locations["inn"]["name"] == "Rusty Dragon"
    assert template.encounter_profile is not None
    assert template.encounter_profile["slot_capacity"] == 1
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
    assert char.shop_inventory.base_pool[0]["item_id"] == "rope"
    assert len(char.shop_inventory.rotating_pool) == 1
    assert char.shop_inventory.rotating_slots == 2
    assert char.shop_inventory.refresh_on == "rest"


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
    registry = ClassRegistry()
    registry.load({
        "classes": {
            "wizard": {
                "id": "wizard",
                "level_features": {
                    "1": ["cantrips"],
                    "foo": ["bad_key"],
                    "3": "not_a_list",
                },
            },
        },
    })

    issues = registry.validate()

    assert "class entry 'wizard' level_features key 'foo' must be numeric" in issues
    assert "class entry 'wizard' level_features[3] must be a list" in issues
    assert not any("level_features key '1'" in i for i in issues)


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
    assert sub.level_features == {"3": ["remarkable_athlete"]}

    race = registry.get_race("human")
    assert isinstance(race, RaceTemplate)
    assert race.stat_bonuses == {"str": 1, "dex": 1}
    assert race.racial_traits == ["versatile"]

    bg = registry.get_background("soldier")
    assert isinstance(bg, BackgroundTemplate)
    assert bg.feature == "military_rank"
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
    assert "faction 'broken' has invalid relations" in issues
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
    assert faction.behavioral_rules == "Patrol the streets."
    assert faction.initial_standing == 5
    assert faction.base_standing is None
    assert faction.relations == {}

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
    assert monster.gold_drop == 5

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
    assert first.count == 3

    second = monster.loot_table[1]
    assert second.item_id == "gem"
    assert second.chance == 1.0
    assert second.count == 1


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
    assert item.damage_dice == "1d8"
    assert item.damage_type == "slashing"

    assert registry.get("nonexistent") is None


def test_item_heal_amount_aliases():
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
    assert p1.heal is None
    assert p1.restore_hp is None

    p2 = registry.get("potion2")
    assert p2.heal == 15

    p3 = registry.get("potion3")
    assert p3.restore_hp == 20


# ------------------------------------------------------------------
# Dataclass typed access (Batch 3 migration)
# ------------------------------------------------------------------


def test_skill_get_returns_typed_template():
    from app.game_core.content.registries.skills import SkillTemplate

    registry = SkillRegistry()
    registry.load({
        "fireball": {
            "id": "fireball",
            "category": "spell",
            "spell_level": 3,
            "school": "evocation",
            "concentration": False,
            "effect": {"type": "damage", "dice": "8d6"},
            "cost": {"resource": "spell_slot", "amount": 1},
        },
    })

    spell = registry.get("fireball")
    assert isinstance(spell, SkillTemplate)
    assert spell.id == "fireball"
    assert spell.category == "spell"
    assert spell.spell_level == 3
    assert spell.school == "evocation"
    assert spell.concentration is False
    assert isinstance(spell.effect, dict)
    assert spell.effect["type"] == "damage"
    assert isinstance(spell.cost, dict)
    assert spell.cost["resource"] == "spell_slot"

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
