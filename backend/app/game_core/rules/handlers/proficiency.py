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
    """检查武器熟练度。当前 always True（占位）。"""
    # TODO: class_template.weapon_proficiency 包含 weapon_data.proficiency 才返回 True
    return True


def check_armor_proficiency(
    class_template: "CharacterTemplate",
    armor_data: "ItemTemplate",
) -> bool:
    """检查护甲熟练度。当前 always True（占位）。"""
    # TODO: class_template.armor_proficiency 包含 armor_data.armor_type 才返回 True
    return True


def check_save_proficiency(
    class_template: "CharacterTemplate",
    save_type: str,
) -> bool:
    """检查豁免熟练度。当前 always True（占位）。"""
    # TODO: class_template.save_proficiency 包含 save_type 才返回 True
    return True
