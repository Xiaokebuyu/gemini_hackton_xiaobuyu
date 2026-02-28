from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.content.registries.classes import ClassRegistry
from app.game_core.content.registries.items import ItemRegistry
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

    assert registry.starting_area()["id"] == "town"

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
