# 地图/箱庭提取 Prompt

你是一个世界观分析专家。请从以下世界书内容中提取所有可探索的**地点/地图**信息。

## 提取要求

对于每个地点，提取以下信息：

1. **id**: 英文标识符（使用下划线分隔，如 "frontier_town"、"goblin_cave"）
2. **name**: 中文名称
3. **description**: 详细的环境描述（2-3句话）
4. **atmosphere**: 氛围（如"繁忙喧嚣"、"阴森恐怖"、"宁静祥和"）
5. **danger_level**: 危险等级 (low/medium/high/extreme)
6. **region**: 所属区域（如"边境地区"、"荒野"）
7. **connections**: 与其他地图的连接，包括：
   - target_map_id: 目标地图 ID
   - connection_type: 连接类型 (walk=步行可达/travel=需要旅行/explore=需要探索发现)
   - travel_time: 旅行时间描述（可选）
8. **available_actions**: 在此地点可执行的动作类型（如"购物"、"休息"、"接任务"、"战斗"）
9. **key_features**: 关键地标或特征
10. **sub_locations**: 地图内的可导航子地点（如酒馆、铁匠铺、公会大厅等），每个子地点包含：
    - id: 子地点 ID，格式为 `{功能或动作}_英文`（如 "guild_hall"、"tavern"、"blacksmith"）
    - name: 中文名称
    - description: 子地点描述
    - interaction_type: 交互类型（visit=普通访问/shop=商店/quest=任务/rest=休息）
    - resident_npcs: 常驻 NPC 的角色 ID 列表（如 ["guild_girl", "tavern_keeper"]）
    - available_actions: 在此子地点可执行的动作列表
    - passerby_spawn_rate: 路人 NPC 生成概率（0.0-1.0）

## 输出格式（JSON）

```json
{
  "maps": [
    {
      "id": "frontier_town",
      "name": "边境小镇",
      "description": "位于王国西部边境的小型城镇，是冒险者公会的所在地...",
      "atmosphere": "繁忙、充满活力",
      "danger_level": "low",
      "region": "边境地区",
      "connections": [
        {"target_map_id": "guild_hall", "connection_type": "walk", "travel_time": "5分钟"},
        {"target_map_id": "forest_edge", "connection_type": "travel", "travel_time": "2小时"}
      ],
      "available_actions": ["购物", "休息", "接任务", "情报收集"],
      "key_features": ["冒险者公会", "酒馆", "神殿", "市场"],
      "sub_locations": [
        {
          "id": "guild_hall",
          "name": "冒险者公会大厅",
          "description": "冒险者公会的主要大厅，可以接取任务和查看公告板",
          "interaction_type": "quest",
          "resident_npcs": ["guild_girl"],
          "available_actions": ["接任务", "查看公告", "情报收集"],
          "passerby_spawn_rate": 0.5
        },
        {
          "id": "tavern",
          "name": "酒馆",
          "description": "冒险者们休息聚会的酒馆",
          "interaction_type": "rest",
          "resident_npcs": ["tavern_keeper"],
          "available_actions": ["休息", "用餐", "打听消息"],
          "passerby_spawn_rate": 0.6
        }
      ]
    }
  ],
  "passerby_templates": {
    "frontier_town": [
      {
        "template_id": "rookie_adventurer",
        "name_pattern": "新手冒险者",
        "personality_template": "充满热情但经验不足，对哥布林退治任务跃跃欲试",
        "speech_pattern": "热情、略带紧张",
        "appearance_hints": "简陋的装备，闪亮的眼神"
      }
    ]
  }
}
```

## 注意事项

1. 地点 ID 必须唯一且使用英文
2. 连接必须是双向的（如果 A 连接 B，B 也应该连接 A）
3. 优先提取剧情中出现的重要地点
4. 路人模板应该符合地点的氛围
5. 危险等级参考：
   - low: 城镇、神殿等安全区域
   - medium: 郊外、农场等有小型威胁的区域
   - high: 森林、洞穴等有怪物的区域
   - extreme: Boss 区域、高等级地下城
6. 子地点 ID 格式为 `{功能或动作}_英文`，如 "guild_hall"、"blacksmith"、"temple"
7. `resident_npcs` 应引用角色 ID（与 NPC 提取中使用的 ID 一致）
8. 城镇/安全区域应包含子地点（如公会、酒馆、商店等），野外/地下城可以没有子地点

## 世界书内容

{worldbook_content}
