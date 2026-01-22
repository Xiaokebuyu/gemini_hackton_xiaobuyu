# Phase 4: Pro Foundation

本阶段仅搭建基础设施，不接入 LLM。目标是让 Pro 能获取角色档案 + 场景 + 记忆，并组装成可直接注入的上下文。

## Scope
- 角色档案（profile）读取/写入
- 上下文组装（ProContextBuilder）
- Flash 记忆检索联动（通过结构化 RecallRequest）

## API

### 1) 角色档案
`GET /api/pro/{world_id}/characters/{character_id}/profile`  
`PUT /api/pro/{world_id}/characters/{character_id}/profile`

Payload 示例：
```
{
  "name": "Gorn",
  "occupation": "Blacksmith",
  "age": 45,
  "personality": "沉稳、直率",
  "speech_pattern": "短句，直接表达",
  "example_dialogue": "炉子坏了？我来看看。",
  "system_prompt": "保持谨慎，不要泄露不该知道的信息"
}
```

### 2) 上下文组装
`POST /api/pro/{world_id}/characters/{character_id}/context`

Payload 示例：
```
{
  "scene": {
    "description": "铁匠铺内火光通明",
    "location": "黑石镇",
    "present_characters": ["player", "gorn"]
  },
  "recent_conversation": "玩家：这炉子还能修吗？",
  "recall": {
    "seed_nodes": ["person_gorn"],
    "include_subgraph": true
  },
  "include_prompt": true
}
```

响应包含 `assembled_prompt`，可直接给 Pro 模型使用。

## CLI
```
python -m app.tools.pro_cli profile --world demo_world --character gorn --payload profile.json
python -m app.tools.pro_cli context --world demo_world --character gorn --payload context.json
```

## Notes
- `recall` 字段会直接调用 FlashService（结构化 RecallRequest）。
- `assembled_prompt` 是基础模板，可按需扩展。
