# [DEPRECATED] 实体提取任务 — graph_extractor.py 已改用内联 prompt

你是一个知识图谱构建助手。请从以下世界书条目中提取实体信息。

## 输入格式

每个条目包含：
- `entry_type`: 条目类型（character/location/deity/race/monster/faction/event/item/concept）
- `entry_name`: 条目名称
- `content`: 条目内容
- `key`: 触发关键词

## 输出要求

为每个条目提取一个实体节点，输出 JSON 格式：

```json
{
  "entities": [
    {
      "id": "类型_名称ID",
      "type": "类型",
      "name": "中文名称",
      "importance": 0.0-1.0,
      "properties": {
        "description": "简短描述",
        "aliases": ["别名列表"],
        "source_entry": "原条目名"
      }
    }
  ]
}
```

## 实体类型定义

| type | 描述 | 示例 |
|------|------|------|
| `character` | 有名有姓的角色 | priestess, goblin_slayer |
| `location` | 地点/区域 | frontier_town, royal_capital |
| `faction` | 组织/势力 | adventurers_guild |
| `deity` | 神祇 | earth_mother, supreme_god |
| `race` | 种族 | human, elf, goblin |
| `monster` | 怪物类型 | goblin, ogre, dragon |
| `event` | 历史事件 | demon_war |
| `item` | 重要物品 | holy_sword |
| `concept` | 抽象概念/规则 | divine_game, adventurer_rank |

## ID 生成规则

1. 格式：`{type}_{name_id}`
2. `name_id` 使用英文小写 + 下划线
3. 中文名转拼音或英文译名
4. 示例：
   - 女神官 → `character_priestess`
   - 边境小镇 → `location_frontier_town`
   - 地母神 → `deity_earth_mother`

## importance 评估

- 1.0: 主要角色/核心地点
- 0.7-0.9: 重要配角/关键地点
- 0.4-0.6: 次要角色/普通地点
- 0.1-0.3: 背景设定/概念

## 处理规则

1. 跳过过于泛化的条目（如"角色成长途径"）
2. 合并重复实体（同一实体多个条目）
3. 从 content 中提取关键属性填入 properties
4. 保持名称的官方译名一致性

---

## 待处理条目

{entries}
