"""Spell templates for combat."""
from typing import Any, Dict

SPELL_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "fire_bolt": {
        "name": "Fire Bolt",
        "level": 0,
        "type": "attack",
        "damage_dice": "1d10",
        "damage_type": "fire",
        "range": "far",
    },
    "magic_missile": {
        "name": "Magic Missile",
        "level": 1,
        "type": "auto_hit",
        "damage_dice": "3d4+3",
        "damage_type": "force",
        "range": "far",
    },
    "healing_word": {
        "name": "Healing Word",
        "level": 1,
        "type": "heal",
        "heal_amount": "1d4+3",
        "range": "close",
        "bonus_action": True,
    },
    "ray_of_frost": {
        "name": "Ray of Frost",
        "level": 0,
        "type": "attack",
        "damage_dice": "1d8",
        "damage_type": "cold",
        "range": "far",
        "apply_effect": {"effect": "restrained", "duration": 1},
    },
}
