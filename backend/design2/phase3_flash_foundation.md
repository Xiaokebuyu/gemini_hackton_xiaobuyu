# Phase 3: Flash Foundation

This document defines the Phase 3 foundation for a single-character Flash service.
LLM integration is intentionally left out and will be plugged in later.

## Scope
- Event ingestion (structured nodes/edges)
- Memory recall (spreading activation + subgraph)
- Optional reference resolution

## API

### Ingest Event
`POST /api/flash/{world_id}/characters/{character_id}/ingest`

Payload (example):
```
{
  "description": "Player fixed the furnace",
  "game_day": 12,
  "nodes": [
    {
      "id": "event_fix_furnace",
      "type": "event",
      "name": "Furnace fixed",
      "properties": {
        "day": 12,
        "summary": "I saw the player repair the furnace"
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

### Recall Memory
`POST /api/flash/{world_id}/characters/{character_id}/recall`

Payload (example):
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

## CLI (manual)

```
python -m app.tools.flash_cli ingest --world demo_world --character gorn --payload ingest.json
python -m app.tools.flash_cli recall --world demo_world --character gorn --payload recall.json
```

## Notes
- `state_updates` are stored under the character document as `state` fields.
- Reference nodes can be resolved by using `resolve_refs=true` on recall/subgraph APIs.

## Phase 3b（待办）
- 角色档案（profile）结构落地与存储
- Pro 上下文组装模板
- Flash → Pro 的记忆请求协议（结构化调用）
