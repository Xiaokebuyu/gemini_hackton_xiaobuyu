# Phase 5: GM Foundation

本阶段只搭建 GM Flash 与事件总线的基础设施，不接 LLM。

## Scope
- 事件模型（Event）
- GM 事件写入与分发（GMFlashService）
- 简易事件总线（EventBus）

## API

### GM 事件摄入
`POST /api/gm/{world_id}/events/ingest`

Payload 示例：
```
{
  "event": {
    "type": "action",
    "game_day": 12,
    "location": "黑石镇",
    "participants": ["player"],
    "witnesses": ["gorn"],
    "content": {
      "raw": "玩家在铁匠铺修好了炉子"
    },
    "nodes": [
      {
        "id": "event_fix_furnace",
        "type": "event",
        "name": "修炉子",
        "properties": {
          "day": 12,
          "summary": "玩家修好了炉子"
        }
      }
    ],
    "edges": [
      {
        "id": "edge_gorn_event_fix",
        "source": "person_gorn",
        "target": "event_fix_furnace",
        "relation": "witnessed",
        "weight": 0.8
      }
    ]
  },
  "distribute": true,
  "known_characters": ["gorn", "marcus"],
  "write_indexes": true
}
```

## Dispatch 规则（当前实现）
若未显式提供 `recipients`，则按以下规则决定分发对象：
1) participants  
2) witnesses  
3) visibility.known_to  
4) visibility.public 且提供 `known_characters`  
5) 提供 `character_locations` 且与事件 location 匹配  

支持 `per_character` 覆盖，允许对指定角色写入不同的 nodes/edges/state_updates。

## CLI
```
python -m app.tools.gm_cli ingest --world demo_world --payload examples/phase5/gm_event.json
```

## Phase 5b（待办：由你测试/跑通）
- 验证 GM 图谱写入是否正确（nodes/edges）
- 验证分发规则是否满足预期（participants / witnesses / public）
- 验证 `per_character` 覆盖逻辑
- 确认事件在角色图谱中可被 Flash recall 检索到
- 若有需要，再补全事件可见性/隐匿策略
