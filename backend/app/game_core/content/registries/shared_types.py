"""跨 registry 共享的基础类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Effect:
    """通用效果定义，用于 ConsumableData.effect / AccessoryData.effects。"""

    type: str = ""                           # heal / damage / buff / debuff / cure / utility
    params: dict[str, Any] = field(default_factory=dict)  # 灵活参数 {dice, bonus, duration, ...}
    target: str = ""                         # self / single / area
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LootTableDef:
    """容器掉落表（区别于 MonsterTemplate 的 loot_table，金币用骰子表达式）。"""

    gold: str = "0"                          # 骰子表达式 "2d6+5" 或 "0"
    items: list[Any] = field(default_factory=list)  # runtime: list[LootEntry]，list[Any] 避免与 monsters.py 循环 import
