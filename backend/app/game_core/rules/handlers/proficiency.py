"""Proficiency 熟练度校验工具函数（always-True 占位）。

不是 CommandHandler，被其他 handler import 调用。

设计意图（深化时实现）：
- check_weapon_proficiency: class_template.weapon_proficiency ∋ weapon_data.proficiency
  → 否则攻击不加 proficiency_bonus
- check_armor_proficiency: class_template.armor_proficiency ∋ armor_data.armor_type
  → 否则检定/豁免/攻击劣势，不能施法
- check_save_proficiency: class_template.save_proficiency ∋ save_type
  → 熟练则 proficiency_bonus 加入豁免骰
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.game_core.content.registries.characters import CharacterTemplate
    from app.game_core.content.registries.items import ItemTemplate


def check_weapon_proficiency(
    class_template: "CharacterTemplate",
    weapon_data: "ItemTemplate",
) -> bool:
    """检查武器熟练度。weapon_data.weapon_proficiency 须在 class_template.weapon_proficiency 中。"""
    prof = getattr(weapon_data, "weapon_proficiency", "")
    if not prof:
        return True
    return prof in getattr(class_template, "weapon_proficiency", [])


def check_armor_proficiency(
    class_template: "CharacterTemplate",
    armor_data: "ItemTemplate",
) -> bool:
    """检查护甲熟练度。armor_data.armor_type 须在 class_template.armor_proficiency 中。"""
    armor_type = getattr(armor_data, "armor_type", "")
    if not armor_type:
        return True
    return armor_type in getattr(class_template, "armor_proficiency", [])


def check_save_proficiency(
    class_template: "CharacterTemplate",
    save_type: str,
) -> bool:
    """检查豁免熟练度。save_type 须在 class_template.save_proficiency 中。"""
    if not save_type:
        return True
    return save_type in getattr(class_template, "save_proficiency", [])
