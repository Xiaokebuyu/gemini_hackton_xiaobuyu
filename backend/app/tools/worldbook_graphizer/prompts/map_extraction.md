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
      "key_features": ["冒险者公会", "酒馆", "神殿", "市场"]
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

## 世界书内容

{worldbook_content}
