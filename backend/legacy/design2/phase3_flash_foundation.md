# 阶段 3：Flash 基础层

本文档定义了单角色 Flash 服务的第三阶段基础功能。
LLM 集成部分有意留空，将在后续阶段接入。

## 范围
- 事件摄入（结构化节点/边）
- 记忆召回（扩散激活 + 子图）
- 可选的引用解析

## API

### 摄入事件
`POST /api/flash/{world_id}/characters/{character_id}/ingest`

请求体示例：
```
{
  "description": "玩家修好了火炉",
  "game_day": 12,
  "nodes": [
    {
      "id": "event_fix_furnace",
      "type": "event",
      "name": "火炉已修好",
      "properties": {
        "day": 12,
        "summary": "我看到玩家修理了火炉"
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
  ],
  "write_indexes": true
}
```

### 召回记忆
`POST /api/flash/{world_id}/characters/{character_id}/recall`

请求体示例：
```
{
  "seed_nodes": ["person_gorn"],
  "include_subgraph": true,
  "config": {
    "max_iterations": 3,
    "decay": 0.6,
    "fire_threshold": 0.1,
    "output_threshold": 0.15,
    "hub_threshold": 20,
    "hub_penalty": 0.5
  }
}
```

## CLI（手动）

```
python -m app.tools.flash_cli ingest --world demo_world --character gorn --payload examples/phase3/ingest.json
python -m app.tools.flash_cli recall --world demo_world --character gorn --payload examples/phase3/recall.json
```

## 备注
- `state_updates` 存储在角色文档下的 `state` 字段中。
- 引用节点可以通过在 recall/subgraph API 中使用 `resolve_refs=true` 来解析。

## Phase 3b（待办）
- 角色档案（profile）结构落地与存储
- Pro 上下文组装模板
- Flash → Pro 的记忆请求协议（结构化调用）
