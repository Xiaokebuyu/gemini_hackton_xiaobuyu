# D&D 物品数据提取

你是一个 TRPG 游戏数据设计助手。请从以下物品的描述中提取 D&D 风格的数据。

## 物品信息
- ID: {item_id}
- 名称: {item_name}
- 原始描述:
{item_description}

## 已有属性（可能为空）
{existing_properties}

## 输出格式（严格 JSON）

```json
{{
  "id": "{item_id}",
  "name": "{item_name}",
  "type": "物品类型（weapon/armor/potion/scroll/wondrous/tool/consumable/material）",
  "subtype": "子类型（如 light/medium/heavy, melee/ranged, healing 等）",
  "description": "简短描述（50字以内）",
  "properties": {{
    "ac_bonus": 0,
    "damage": "伤害骰（武器用，如1d8+1）",
    "weight": "轻/中/重",
    "price": "估计价格"
  }},
  "effects": ["效果描述列表"],
  "rarity": "common/uncommon/rare/very_rare/legendary"
}}
```
