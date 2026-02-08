# D&D 怪物数据提取

你是一个 TRPG 游戏数据设计助手。请从以下怪物/生物的描述中提取 D&D 风格的数据。

## 怪物信息
- ID: {monster_id}
- 名称: {monster_name}
- 原始描述:
{monster_description}

## 已有属性（可能为空）
{existing_properties}

## 输出格式（严格 JSON）

```json
{{
  "id": "{monster_id}",
  "name": "{monster_name}",
  "type": "怪物种族类型（如 undead/beast/humanoid/aberration/dragon/fiend/elemental）",
  "challenge_rating": "冒险者等级（白瓷/黑曜石/钢铁/青铜/翠玉/白银/黄金/白金）",
  "description": "简短描述（50字以内）",
  "stats": {{
    "hp": 0, "ac": 0,
    "str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10
  }},
  "stats_confidence": 0.5,
  "skills": [{{"name": "技能名", "level": "初级/中级/高级"}}],
  "special_abilities": ["特殊能力列表"],
  "attacks": [{{"name": "攻击名", "damage": "伤害骰（如1d6+2）"}}],
  "loot": ["可能掉落的物品"]
}}
```

注意：
1. stats_confidence: 你对数值的信心（0-1），基于描述中是否有明确的战力描述
2. 如果描述中没有明确的数值线索，使用基于 challenge_rating 的合理估计
3. challenge_rating 使用世界观中的等级体系
