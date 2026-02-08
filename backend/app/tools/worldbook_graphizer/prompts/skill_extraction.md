# D&D 技能/法术数据提取

你是一个 TRPG 游戏数据设计助手。请从以下技能/法术的描述中提取 D&D 风格的数据。

## 技能信息
- ID: {skill_id}
- 名称: {skill_name}
- 原始描述:
{skill_description}

## 已有属性（可能为空）
{existing_properties}

## 输出格式（严格 JSON）

```json
{{
  "id": "{skill_id}",
  "name": "{skill_name}",
  "tier": 0,
  "type": "技能类型（miracle/martial/magic/racial/passive）",
  "school": "学派（如祝福/攻击/防御/治疗/召唤/元素）",
  "source": "来源（如地母神/战神/种族天赋）",
  "description": "简短描述（50字以内）",
  "effect": "游戏效果（如1d6治疗/+2AC）",
  "cost": "使用消耗（如1次/天/3法力）",
  "range": "范围（接触/近程/远程/自身）"
}}
```
