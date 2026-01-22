# Phase 6: Game Loop Foundation

本阶段提供完整游戏循环的基础设施（会话/场景/战斗），不接 LLM。

## Scope
- 游戏会话状态存储
- 场景切换
- 战斗启动与结束
- 战斗结果 → GM 事件 → 分发

## API

### 1) 创建会话
`POST /api/game/{world_id}/sessions`

Payload 示例：
```
{
  "session_id": "sess_demo",
  "participants": ["player", "gorn"]
}
```

### 2) 更新场景
`POST /api/game/{world_id}/sessions/{session_id}/scene`

Payload 示例：
```
{
  "scene": {
    "description": "铁匠铺内火光通明",
    "location": "黑石镇",
    "participants": ["player", "gorn"],
    "atmosphere": "炎热"
  }
}
```

### 3) 启动战斗
`POST /api/game/{world_id}/sessions/{session_id}/combat/start`

Payload 示例：
```
{
  "player_state": {
    "name": "勇者艾伦",
    "hp": 50,
    "max_hp": 50,
    "ac": 15,
    "attack_bonus": 3,
    "damage_dice": "1d6",
    "damage_bonus": 2,
    "initiative_bonus": 2
  },
  "enemies": [
    {"type": "goblin", "level": 1}
  ],
  "combat_context": {
    "location": "黑石镇",
    "participants": ["player"],
    "witnesses": ["gorn"],
    "visibility_public": false
  }
}
```

### 4) 结束战斗
`POST /api/game/{world_id}/sessions/{session_id}/combat/resolve`

Payload 示例（使用引擎结果）：
```
{
  "use_engine": true
}
```

Payload 示例（外部结果覆盖）：
```
{
  "use_engine": false,
  "result_override": {
    "summary": "玩家击败了两只哥布林",
    "result": "victory"
  }
}
```

战斗结束后，会生成 GM 事件并分发给角色图谱。

## CLI
```
python -m app.tools.game_cli create --world demo_world --payload session.json
python -m app.tools.game_cli scene --world demo_world --session sess_demo --payload scene.json
python -m app.tools.game_cli combat-start --world demo_world --session sess_demo --payload combat_start.json
python -m app.tools.game_cli combat-resolve --world demo_world --session sess_demo --payload combat_resolve.json
```

## Phase 6b（待办：由你测试/跑通）
- 验证会话状态读写（sessions 文档）
- 场景切换是否正确写入
- 战斗启动后 `combat_id` 与 session 状态更新
- 战斗结束事件是否写入 GM 图谱并正确分发
- 与 CombatEngine 实测联动（回合制流程）
