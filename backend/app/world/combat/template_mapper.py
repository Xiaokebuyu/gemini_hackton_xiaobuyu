"""World combat entity -> engine template mappers."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_DICE_PATTERN = re.compile(r"(\d+d\d+(?:[+-]\d+)?)")


def slugify(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        elif ch.isspace() or ch in ("/", "\\", ":", "|", "."):
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug


def _extract_damage_dice(text: str, default: str = "1d6") -> str:
    match = _DICE_PATTERN.search(text or "")
    if not match:
        return default
    return match.group(1)


def _extract_damage_bonus(dice_notation: str, fallback: int = 1) -> int:
    if "+" in dice_notation:
        try:
            return int(dice_notation.rsplit("+", 1)[1])
        except ValueError:
            return fallback
    if "-" in dice_notation[1:]:
        try:
            return int(dice_notation.rsplit("-", 1)[1]) * -1
        except ValueError:
            return fallback
    return fallback


def _derive_damage_type(monster: Dict[str, Any]) -> str:
    text = " ".join(
        str(monster.get(key, ""))
        for key in ("type", "name", "description")
    ).lower()

    if any(token in text for token in ("fire", "火", "焰")):
        return "fire"
    if any(token in text for token in ("cold", "冰", "霜")):
        return "cold"
    if any(token in text for token in ("poison", "毒")):
        return "poison"
    if any(token in text for token in ("arcane", "魔法", "法术", "force")):
        return "force"
    if any(token in text for token in ("箭", "pierc", "弩", "刺")):
        return "piercing"
    if any(token in text for token in ("锤", "棍", "bludge", "砸")):
        return "bludgeoning"
    return "slashing"


def _extract_stat(monster: Dict[str, Any], key: str, default: int) -> int:
    stats = monster.get("stats") if isinstance(monster.get("stats"), dict) else {}
    raw = stats.get(key, monster.get(key, default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value


def _normalize_skill_ids(monster: Dict[str, Any]) -> List[str]:
    raw_skills = monster.get("skills", [])
    result: List[str] = []

    if not isinstance(raw_skills, list):
        return result

    for skill in raw_skills:
        if isinstance(skill, str):
            skill_id = slugify(skill)
            if skill_id:
                result.append(skill_id)
            continue

        if isinstance(skill, dict):
            skill_id = str(skill.get("id") or skill.get("name") or "").strip()
            skill_id = slugify(skill_id)
            if skill_id:
                result.append(skill_id)

    return list(dict.fromkeys(result))


def monster_to_enemy_template(monster: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a monster entry from world data into combat enemy template."""
    if not isinstance(monster, dict):
        return None

    source_id = str(monster.get("id") or monster.get("name") or "").strip()
    if not source_id:
        return None

    enemy_type = slugify(source_id)
    if not enemy_type:
        return None

    name = str(monster.get("name") or source_id)

    hp = _extract_stat(monster, "hp", 12)
    ac = _extract_stat(monster, "ac", 12)

    attacks = monster.get("attacks") if isinstance(monster.get("attacks"), list) else []
    damage_dice = "1d6"
    if attacks:
        first_attack = attacks[0]
        if isinstance(first_attack, dict):
            damage_dice = _extract_damage_dice(str(first_attack.get("damage", "")), default="1d6")
        elif isinstance(first_attack, str):
            damage_dice = _extract_damage_dice(first_attack, default="1d6")

    challenge = str(monster.get("challenge_rating") or "").strip().lower()
    tier_hint = 1
    if any(token in challenge for token in ("黄金", "gold", "high")):
        tier_hint = 3
    elif any(token in challenge for token in ("白银", "silver", "mid")):
        tier_hint = 2
    elif any(token in challenge for token in ("bronze", "白瓷", "low")):
        tier_hint = 1

    attack_bonus = max(1, _extract_stat(monster, "dex", 10) // 3)
    damage_bonus = max(1, _extract_damage_bonus(damage_dice, fallback=tier_hint))
    initiative_bonus = max(0, _extract_stat(monster, "dex", 10) // 4)

    special_abilities = monster.get("special_abilities")
    ai_personality = "aggressive"
    if isinstance(special_abilities, list):
        special_text = " ".join(str(item) for item in special_abilities).lower()
        if any(token in special_text for token in ("陷阱", "潜行", "逃", "coward")):
            ai_personality = "cowardly"
        elif any(token in special_text for token in ("群", "协同", "pack")):
            ai_personality = "pack_hunter"

    type_tag = slugify(str(monster.get("type", "")))
    tags = [type_tag] if type_tag else []

    template = {
        "enemy_type": enemy_type,
        "name": name,
        "max_hp": max(1, hp),
        "ac": max(1, ac),
        "attack_bonus": attack_bonus,
        "damage_dice": damage_dice,
        "damage_bonus": damage_bonus,
        "damage_type": _derive_damage_type(monster),
        "initiative_bonus": initiative_bonus,
        "ai_personality": ai_personality,
        "xp_reward": max(0, tier_hint * 60),
        "gold_reward": max(0, tier_hint * 8),
        "tags": tags,
        "resistances": list(monster.get("resistances", []) or []),
        "vulnerabilities": list(monster.get("vulnerabilities", []) or []),
        "immunities": list(monster.get("immunities", []) or []),
        "role": None,
        "tier": tier_hint,
        "source_id": source_id,
        "spells_known": _normalize_skill_ids(monster),
        "loot": list(monster.get("loot", []) or []),
    }

    return template


def skill_to_spell_template(skill: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map skill entry to combat spell-like template when parseable."""
    if not isinstance(skill, dict):
        return None

    skill_id_raw = str(skill.get("id") or skill.get("name") or "").strip()
    if not skill_id_raw:
        return None

    skill_id = slugify(skill_id_raw)
    if not skill_id:
        return None

    name = str(skill.get("name") or skill_id_raw)
    effect_text = str(skill.get("effect") or skill.get("description") or "")
    lower = effect_text.lower()

    template: Dict[str, Any] = {
        "id": skill_id,
        "name": name,
        "level": max(0, int(skill.get("tier", 0) or 0)),
        "range": "near",
    }

    range_text = str(skill.get("range") or "").lower()
    if any(token in range_text for token in ("远", "range", "ranged", "long")):
        template["range"] = "far"
    elif any(token in range_text for token in ("近", "touch", "melee")):
        template["range"] = "close"

    # heal skills
    if any(token in lower for token in ("恢复", "治疗", "heal")):
        template["type"] = "heal"
        template["heal_amount"] = _extract_damage_dice(effect_text, default="1d4+1")
        return template

    # damaging skills
    damage_dice = _extract_damage_dice(effect_text, default="")
    if damage_dice:
        template["damage_dice"] = damage_dice
        template["type"] = "auto_hit" if any(token in lower for token in ("必定命中", "必中", "auto hit")) else "attack"

        school_text = str(skill.get("school") or "").lower()
        if any(token in school_text for token in ("火", "fire", "元素")):
            template["damage_type"] = "fire"
        elif any(token in school_text for token in ("冰", "cold", "霜")):
            template["damage_type"] = "cold"
        elif any(token in school_text for token in ("毒", "poison")):
            template["damage_type"] = "poison"
        elif any(token in school_text for token in ("死灵", "necrotic")):
            template["damage_type"] = "necrotic"
        else:
            template["damage_type"] = "force"

        # basic status hooks
        if any(token in lower for token in ("中毒", "poisoned")):
            template["apply_effect"] = {"effect": "poisoned", "duration": 1}
        elif any(token in lower for token in ("定身", "束缚", "restrain")):
            template["apply_effect"] = {"effect": "restrained", "duration": 1}
        elif any(token in lower for token in ("恐慌", "畏惧", "fright")):
            template["apply_effect"] = {"effect": "frightened", "duration": 1}

        return template

    return None
