"""Enemy template registry and archetype generator."""
import logging
import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from .rules import ENEMY_TEMPLATES
from .data_repository import CombatDataRepository
from .template_mapper import monster_to_enemy_template, slugify


_BASE_TEMPLATES: Dict[str, Dict[str, Any]] = copy.deepcopy(ENEMY_TEMPLATES)
_DYNAMIC_TEMPLATES: Dict[str, Dict[str, Any]] = {}
_WORLD_TEMPLATES: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
_WORLD_ALIASES: Dict[Tuple[str, str], Dict[str, str]] = {}

logger = logging.getLogger(__name__)

_DICE_PATTERN = re.compile(r"^\d+d\d+([+-]\d+)?$")

_TIER_DAMAGE_DICE = {
    1: "1d6",
    2: "1d8",
    3: "2d6",
    4: "2d8",
    5: "3d6",
}

_ROLE_MODIFIERS = {
    "brute": {
        "hp_mult": 1.4,
        "ac": -1,
        "attack_bonus": -1,
        "damage_bonus": 2,
        "initiative_bonus": 0,
        "ai_personality": "aggressive",
    },
    "skirmisher": {
        "hp_mult": 0.9,
        "ac": 1,
        "attack_bonus": 0,
        "damage_bonus": 0,
        "initiative_bonus": 2,
        "ai_personality": "pack_hunter",
    },
    "tank": {
        "hp_mult": 1.6,
        "ac": 2,
        "attack_bonus": -1,
        "damage_bonus": -1,
        "initiative_bonus": -1,
        "ai_personality": "defensive",
    },
    "caster": {
        "hp_mult": 0.8,
        "ac": -1,
        "attack_bonus": 1,
        "damage_bonus": 0,
        "initiative_bonus": 1,
        "ai_personality": "aggressive",
    },
    "archer": {
        "hp_mult": 0.9,
        "ac": 0,
        "attack_bonus": 2,
        "damage_bonus": -1,
        "initiative_bonus": 2,
        "ai_personality": "cowardly",
    },
}


def _ensure_int(value: Any, field: str, errors: List[str]) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be an integer")
        return None


def _validate_tags(tags: Any, errors: List[str]) -> List[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        errors.append("tags must be a list of strings")
        return []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            errors.append("tags must be a list of strings")
            return []
    return tags


def _validate_template_payload(template: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    enemy_type = template.get("enemy_type")
    if not isinstance(enemy_type, str) or not enemy_type.strip():
        errors.append("enemy_type is required and must be a string")

    name = template.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name is required and must be a string")

    max_hp = _ensure_int(template.get("max_hp"), "max_hp", errors)
    if max_hp is not None and max_hp < 1:
        errors.append("max_hp must be >= 1")

    ac = _ensure_int(template.get("ac"), "ac", errors)
    if ac is not None and ac < 1:
        errors.append("ac must be >= 1")

    attack_bonus = _ensure_int(template.get("attack_bonus"), "attack_bonus", errors)
    if attack_bonus is not None and (attack_bonus < -10 or attack_bonus > 30):
        errors.append("attack_bonus out of range (-10..30)")

    damage_dice = template.get("damage_dice")
    if not isinstance(damage_dice, str) or not _DICE_PATTERN.match(damage_dice):
        errors.append("damage_dice must be like '1d6' or '2d6+1'")

    damage_type = template.get("damage_type")
    if damage_type is not None and (not isinstance(damage_type, str) or not damage_type.strip()):
        errors.append("damage_type must be a string")

    damage_bonus = _ensure_int(template.get("damage_bonus"), "damage_bonus", errors)
    if damage_bonus is not None and (damage_bonus < -10 or damage_bonus > 30):
        errors.append("damage_bonus out of range (-10..30)")

    initiative_bonus = _ensure_int(
        template.get("initiative_bonus"), "initiative_bonus", errors
    )
    if initiative_bonus is not None and (initiative_bonus < -10 or initiative_bonus > 30):
        errors.append("initiative_bonus out of range (-10..30)")

    xp_reward = template.get("xp_reward")
    if xp_reward is not None:
        xp_value = _ensure_int(xp_reward, "xp_reward", errors)
        if xp_value is not None and xp_value < 0:
            errors.append("xp_reward must be >= 0")

    gold_reward = template.get("gold_reward")
    if gold_reward is not None:
        gold_value = _ensure_int(gold_reward, "gold_reward", errors)
        if gold_value is not None and gold_value < 0:
            errors.append("gold_reward must be >= 0")

    role = template.get("role")
    if role is not None and role not in _ROLE_MODIFIERS:
        errors.append(f"role must be one of: {', '.join(sorted(_ROLE_MODIFIERS))}")

    tier = template.get("tier")
    if tier is not None:
        tier_value = _ensure_int(tier, "tier", errors)
        if tier_value is not None and tier_value < 1:
            errors.append("tier must be >= 1")

    _validate_tags(template.get("tags"), errors)
    _validate_tags(template.get("resistances"), errors)
    _validate_tags(template.get("vulnerabilities"), errors)
    _validate_tags(template.get("immunities"), errors)

    return errors


def _validate_archetype_payload(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    enemy_type = spec.get("enemy_type")
    if not isinstance(enemy_type, str) or not enemy_type.strip():
        errors.append("enemy_type is required and must be a string")

    role = spec.get("role")
    if role is not None and role not in _ROLE_MODIFIERS:
        errors.append(f"role must be one of: {', '.join(sorted(_ROLE_MODIFIERS))}")

    tier = spec.get("tier")
    if tier is not None:
        tier_value = _ensure_int(tier, "tier", errors)
        if tier_value is not None and tier_value < 1:
            errors.append("tier must be >= 1")

    damage_dice = spec.get("damage_dice")
    if damage_dice is not None:
        if not isinstance(damage_dice, str) or not _DICE_PATTERN.match(damage_dice):
            errors.append("damage_dice must be like '1d6' or '2d6+1'")

    damage_type = spec.get("damage_type")
    if damage_type is not None and (not isinstance(damage_type, str) or not damage_type.strip()):
        errors.append("damage_type must be a string")

    _validate_tags(spec.get("tags"), errors)
    _validate_tags(spec.get("resistances"), errors)
    _validate_tags(spec.get("vulnerabilities"), errors)
    _validate_tags(spec.get("immunities"), errors)

    return errors


def _normalize_template(enemy_type: str, template: Dict[str, Any]) -> Dict[str, Any]:
    working = dict(template)
    working["enemy_type"] = enemy_type
    errors = _validate_template_payload(working)
    if errors:
        raise ValueError("; ".join(errors))

    normalized = {
        "enemy_type": enemy_type,
        "name": working["name"],
        "max_hp": int(working["max_hp"]),
        "ac": int(working["ac"]),
        "attack_bonus": int(working["attack_bonus"]),
        "damage_dice": working["damage_dice"],
        "damage_bonus": int(working["damage_bonus"]),
        "damage_type": working.get("damage_type", "slashing"),
        "initiative_bonus": int(working["initiative_bonus"]),
        "ai_personality": working.get("ai_personality"),
        "xp_reward": int(working.get("xp_reward", 0)),
        "gold_reward": int(working.get("gold_reward", 0)),
        "tags": list(working.get("tags", [])),
        "resistances": list(working.get("resistances", [])),
        "vulnerabilities": list(working.get("vulnerabilities", [])),
        "immunities": list(working.get("immunities", [])),
        "role": working.get("role"),
        "tier": working.get("tier"),
    }

    return normalized


def register_template(template: Dict[str, Any]) -> Dict[str, Any]:
    """Register a full enemy template defined by the caller."""
    errors = _validate_template_payload(template)
    if errors:
        raise ValueError("; ".join(errors))

    enemy_type = template["enemy_type"]
    normalized = _normalize_template(enemy_type, template)
    _DYNAMIC_TEMPLATES[enemy_type] = normalized
    return copy.deepcopy(normalized)


def _tier_to_damage_dice(tier: int) -> str:
    if tier in _TIER_DAMAGE_DICE:
        return _TIER_DAMAGE_DICE[tier]
    if tier <= 0:
        return "1d4"
    return "3d8"


def generate_template_from_archetype(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a template based on role/tier archetype."""
    errors = _validate_archetype_payload(spec)
    if errors:
        raise ValueError("; ".join(errors))

    enemy_type = spec["enemy_type"]

    role = spec.get("role", "skirmisher")
    tier = int(spec.get("tier", 1))
    tier = max(1, tier)
    modifiers = _ROLE_MODIFIERS.get(role, _ROLE_MODIFIERS["skirmisher"])

    base_hp = 10 + tier * 8
    base_ac = 12 + (tier // 2)
    base_attack_bonus = 2 + tier
    base_damage_dice = _tier_to_damage_dice(tier)
    base_damage_bonus = max(1, tier // 2)
    base_initiative_bonus = tier // 2

    hp = int(base_hp * modifiers["hp_mult"])
    ac = base_ac + modifiers["ac"]
    attack_bonus = base_attack_bonus + modifiers["attack_bonus"]
    damage_bonus = base_damage_bonus + modifiers["damage_bonus"]
    initiative_bonus = base_initiative_bonus + modifiers["initiative_bonus"]

    return {
        "enemy_type": enemy_type,
        "name": spec.get("name", enemy_type),
        "max_hp": max(1, hp),
        "ac": max(1, ac),
        "attack_bonus": attack_bonus,
        "damage_dice": spec.get("damage_dice", base_damage_dice),
        "damage_bonus": damage_bonus,
        "damage_type": spec.get("damage_type", "slashing"),
        "initiative_bonus": initiative_bonus,
        "ai_personality": spec.get("ai_personality", modifiers["ai_personality"]),
        "xp_reward": int(spec.get("xp_reward", tier * 50)),
        "gold_reward": int(spec.get("gold_reward", tier * 5)),
        "tags": list(spec.get("tags", [])),
        "resistances": list(spec.get("resistances", [])),
        "vulnerabilities": list(spec.get("vulnerabilities", [])),
        "immunities": list(spec.get("immunities", [])),
        "role": role,
        "tier": tier,
    }


def register_archetype(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Register an enemy template based on archetype inputs."""
    errors = _validate_archetype_payload(spec)
    if errors:
        raise ValueError("; ".join(errors))
    template = generate_template_from_archetype(spec)
    return register_template(template)


def load_world_templates(
    world_id: str,
    template_version: Optional[str] = None,
    *,
    force_reload: bool = False,
    repository: Optional[CombatDataRepository] = None,
) -> List[Dict[str, Any]]:
    """Load world-scoped monster templates into in-memory cache."""
    version = template_version or "default"
    cache_key = (world_id, version)
    if cache_key in _WORLD_TEMPLATES and not force_reload:
        return [copy.deepcopy(t) for t in _WORLD_TEMPLATES[cache_key].values()]

    repo = repository or CombatDataRepository(world_id=world_id, template_version=version)
    monsters = repo.list_monsters()

    mapped_templates: Dict[str, Dict[str, Any]] = {}
    aliases: Dict[str, str] = {}
    for monster in monsters:
        mapped = monster_to_enemy_template(monster)
        if not mapped:
            continue

        enemy_type = mapped["enemy_type"]
        try:
            normalized = _normalize_template(enemy_type, mapped)
        except ValueError as exc:
            logger.debug(
                "Skip invalid world enemy template world=%s enemy=%s: %s",
                world_id,
                enemy_type,
                exc,
            )
            continue

        # Preserve worldbook-only extension fields.
        normalized["source_id"] = str(monster.get("id", enemy_type))
        normalized["spells_known"] = list(mapped.get("spells_known", []))
        normalized["loot"] = list(mapped.get("loot", []))
        mapped_templates[enemy_type] = normalized

        for alias in (
            slugify(str(monster.get("id", ""))),
            slugify(str(monster.get("name", ""))),
            slugify(enemy_type),
        ):
            if alias:
                aliases[alias] = enemy_type

    _WORLD_TEMPLATES[cache_key] = mapped_templates
    _WORLD_ALIASES[cache_key] = aliases
    return [copy.deepcopy(t) for t in mapped_templates.values()]


def get_template(
    enemy_type: str,
    world_id: Optional[str] = None,
    template_version: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Get a template by enemy type."""
    lookup_key = slugify(enemy_type)
    if world_id:
        version = template_version or "default"
        cache_key = (world_id, version)
        if cache_key not in _WORLD_TEMPLATES:
            load_world_templates(world_id, template_version=version)

        world_templates = _WORLD_TEMPLATES.get(cache_key, {})
        world_aliases = _WORLD_ALIASES.get(cache_key, {})
        resolved = world_aliases.get(lookup_key, lookup_key)
        if resolved in world_templates:
            return copy.deepcopy(world_templates[resolved])

    if enemy_type in _DYNAMIC_TEMPLATES:
        return copy.deepcopy(_DYNAMIC_TEMPLATES[enemy_type])
    if lookup_key in _DYNAMIC_TEMPLATES:
        return copy.deepcopy(_DYNAMIC_TEMPLATES[lookup_key])

    base = _BASE_TEMPLATES.get(enemy_type)
    if not base and lookup_key in _BASE_TEMPLATES:
        enemy_type = lookup_key
        base = _BASE_TEMPLATES.get(lookup_key)
    if not base:
        return None

    normalized = _normalize_template(enemy_type, base)
    return normalized


def list_templates(
    tags: Optional[List[str]] = None,
    scene: Optional[str] = None,
    *,
    world_id: Optional[str] = None,
    template_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List templates, optionally filtered by tags or scene tag."""
    merged: Dict[str, Dict[str, Any]] = {}
    if world_id:
        version = template_version or "default"
        cache_key = (world_id, version)
        if cache_key not in _WORLD_TEMPLATES:
            load_world_templates(world_id, template_version=version)
        for key, value in _WORLD_TEMPLATES.get(cache_key, {}).items():
            merged[key] = copy.deepcopy(value)

    for key, value in _BASE_TEMPLATES.items():
        merged.setdefault(key, _normalize_template(key, value))
    for key, value in _DYNAMIC_TEMPLATES.items():
        merged[key] = copy.deepcopy(value)

    if scene:
        tags = list(tags or []) + [scene]

    if tags:
        tag_set = {tag for tag in tags}
        filtered = []
        for template in merged.values():
            template_tags = set(template.get("tags", []))
            if tag_set.issubset(template_tags):
                filtered.append(copy.deepcopy(template))
        return filtered

    return [copy.deepcopy(template) for template in merged.values()]
