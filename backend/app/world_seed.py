"""Built-in synthetic world data for the API shell."""

from __future__ import annotations

from typing import Any

WORLD_CATALOG = [
    {
        "world_id": "goblin_slayer",
        "name": "哥布林杀手",
        "description": "当前运行内建骨架世界，用于会话与接口联调。",
        "cover_image": "worlds/goblin_slayer/cover.png",
    }
]


def _shell_world_seed(world_id: str) -> dict[str, Any]:
    """Return one built-in synthetic world used by the API shell."""

    if world_id != "goblin_slayer":
        raise ValueError(f"unsupported shell world: {world_id}")
    return {
        "maps": {
            "guild_hall": {
                "id": "guild_hall",
                "name": "Guild Hall",
                "is_starting_area": True,
                "base_danger": 0.1,
                "sub_locations": {
                    "counter": {"id": "counter", "name": "Front Counter"},
                    "board": {"id": "board", "name": "Quest Board"},
                },
            },
            "training_grounds": {
                "id": "training_grounds",
                "name": "Training Grounds",
                "base_danger": 0.2,
                "sub_locations": {
                    "yard": {"id": "yard", "name": "Sparring Yard"},
                },
            },
            "frontier": {
                "id": "frontier",
                "name": "Frontier",
                "base_danger": 0.6,
                "sub_locations": {
                    "camp": {"id": "camp", "name": "Frontier Camp"},
                },
            },
        },
        "classes": {
            "classes": {
                "fighter": {
                    "id": "fighter",
                    "name": "Fighter",
                    "description": "A disciplined martial combatant.",
                    "hit_die": 10,
                    "hp_per_level": 6,
                    "subclass_level": 3,
                    "starting_gold": 10,
                    "starting_equipment": ["training_sword", "wooden_shield"],
                    "default_equipped": {
                        "main_hand": "training_sword",
                        "off_hand": "wooden_shield",
                    },
                    "level_features": {
                        "1": ["Second Wind"],
                        "2": ["Action Surge"],
                    },
                }
            },
            "races": {
                "human": {
                    "id": "human",
                    "name": "Human",
                    "description": "Adaptable and resilient.",
                    "stat_bonuses": {"str": 1},
                    "racial_traits": ["Adaptable"],
                }
            },
            "backgrounds": {
                "adventurer": {
                    "id": "adventurer",
                    "name": "Adventurer",
                    "description": "A road-worn beginner guild member.",
                    "feature": "Road-Worn",
                    "gold_bonus": 15,
                }
            },
        },
        "items": {
            "training_sword": {
                "id": "training_sword",
                "name": "Training Sword",
                "slot": "main_hand",
                "base_price": 12,
            },
            "wooden_shield": {
                "id": "wooden_shield",
                "name": "Wooden Shield",
                "slot": "off_hand",
                "base_price": 8,
            },
            "bandage": {
                "id": "bandage",
                "name": "Bandage",
                "base_price": 5,
            },
            "torch": {
                "id": "torch",
                "name": "Torch",
                "base_price": 3,
            },
        },
        "quests": {
            "chapters": [
                {
                    "id": "chapter_intro",
                    "name": "Introduction",
                }
            ],
            "milestones": {
                "report_in": {
                    "id": "report_in",
                    "name": "Report In",
                    "chapter_id": "chapter_intro",
                    "next_milestones": [],
                }
            },
        },
        "tags": {},
        "skills": {},
        "lore": {},
        "characters": {
            "merchant": {
                "id": "merchant",
                "name": "Guild Merchant",
                "current_area": "guild_hall",
                "current_location": "counter",
                "sell_markup": 1.0,
                "buy_rate": 0.5,
                "shop_inventory": {
                    "base_pool": [
                        {"item_id": "bandage", "count": 5},
                        {"item_id": "torch", "count": 3},
                    ]
                },
            }
        },
        "monsters": {},
        "factions": {},
    }
