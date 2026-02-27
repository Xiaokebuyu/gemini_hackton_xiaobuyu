"""物品数据模型 — 对齐 BG3 / D&D 5e SRD。"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ── 枚举 ──────────────────────────────────────────────

class ItemType(str, Enum):
    weapon = "weapon"
    armor = "armor"
    accessory = "accessory"
    consumable = "consumable"
    misc = "misc"


class Rarity(str, Enum):
    common = "common"
    uncommon = "uncommon"
    rare = "rare"
    very_rare = "very_rare"
    legendary = "legendary"


class DamageType(str, Enum):
    # 物理
    slashing = "slashing"
    piercing = "piercing"
    bludgeoning = "bludgeoning"
    # 元素
    fire = "fire"
    cold = "cold"
    lightning = "lightning"
    thunder = "thunder"
    acid = "acid"
    poison = "poison"
    # 魔法
    radiant = "radiant"
    necrotic = "necrotic"
    force = "force"
    psychic = "psychic"


class EquipSlot(str, Enum):
    helmet = "helmet"
    armor = "armor"
    gloves = "gloves"
    boots = "boots"
    cloak = "cloak"
    amulet = "amulet"
    ring_1 = "ring_1"
    ring_2 = "ring_2"
    weapon_main = "weapon_main"
    weapon_off = "weapon_off"
    shield = "shield"
    ranged = "ranged"
    camp_slot = "camp_slot"  # 预留


class EffectTrigger(str, Enum):
    on_equip = "on_equip"
    on_hit = "on_hit"
    on_crit = "on_crit"
    on_use = "on_use"
    on_damaged = "on_damaged"


class EffectType(str, Enum):
    damage_bonus = "damage_bonus"
    heal = "heal"
    ac_bonus = "ac_bonus"
    ability_bonus = "ability_bonus"
    saving_throw_bonus = "saving_throw_bonus"
    skill_bonus = "skill_bonus"
    resistance = "resistance"
    condition_immunity = "condition_immunity"
    apply_condition = "apply_condition"
    remove_condition = "remove_condition"
    cast_spell = "cast_spell"
    speed_bonus = "speed_bonus"
    temp_hp = "temp_hp"


# ── 类型专属数据块 ────────────────────────────────────

class WeaponData(BaseModel):
    model_config = ConfigDict(extra="allow")

    damage_dice: str                                # "1d8"
    damage_type: DamageType
    versatile_dice: Optional[str] = None            # "1d10"
    properties: List[str] = Field(default_factory=list)  # finesse, light, heavy, ...
    range_normal: int = 5                           # ft
    range_long: Optional[int] = None                # 远程长射程


class ArmorData(BaseModel):
    model_config = ConfigDict(extra="allow")

    base_ac: int                                    # D&D 基础 AC（非加值）
    max_dex_bonus: Optional[int] = None             # heavy=0, medium=2, light=None(无上限)
    stealth_disadvantage: bool = False
    strength_requirement: int = 0                   # 不满足→移速 -10ft


class ConsumableData(BaseModel):
    model_config = ConfigDict(extra="allow")

    uses: int = 1
    consumed_on_use: bool = True
    spell_level: Optional[int] = None               # 卷轴专属


# ── 效果 ──────────────────────────────────────────────

class SpecialEffect(BaseModel):
    model_config = ConfigDict(extra="allow")

    trigger: EffectTrigger
    effect_type: EffectType
    params: Dict[str, Union[str, int, float, bool, None]] = Field(default_factory=dict)


# ── 主模型 ────────────────────────────────────────────

class Item(BaseModel):
    """物品定义（对齐 BG3 / D&D 5e）。"""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str = ""
    type: ItemType
    subtype: str                                    # 见 Schema 规格 §二
    rarity: Rarity = Rarity.common
    price_cp: int = 0                               # 铜币
    weight: float = 0.0                             # 磅
    slot: Optional[EquipSlot] = None
    proficiency: Optional[str] = None
    enchantment: int = 0
    requires_attunement: bool = False

    weapon_data: Optional[WeaponData] = None
    armor_data: Optional[ArmorData] = None
    consumable_data: Optional[ConsumableData] = None

    special_effects: List[SpecialEffect] = Field(default_factory=list)
