# P28 Live 验收清单与证据模板

创建时间：2026-03-13
更新时间：2026-03-13
适用范围：`U7`、`U12`

---

## 1. 目标

本清单用于两类 live 验收：

1. `U12`：真实世界内容路径能进入遭遇并继续走到 `combat_start`
2. `U7`：真实 Gemini 环境下，NPC 会按已分配 capability 生成正确行为 / functional 选项

---

## 2. 前置条件

### 通用

- 在仓库根目录启动后端：

```bash
./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 前端如需联动验证，另开窗口启动：

```bash
cd ../frontend
npm run dev
```

### U7 额外前置

- 需要配置真实模型 key：

```bash
export GOOGLE_API_KEY=...
# 或
export GEMINI_API_KEY=...
```

- capability 注入脚本：

```bash
./venv/bin/python scripts/p28_seed_capability.py --help
```

---

## 3. U12 验收步骤

### 3.1 建立会话与角色

```bash
curl -sS -X POST http://127.0.0.1:8000/api/game/goblin_slayer/sessions
```

记下 `session_id` 后建角：

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/character" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"调试者",
    "race":"human",
    "character_class":"fighter",
    "background":"adventurer",
    "ability_scores":{"str":15,"dex":12,"con":14,"int":10,"wis":10,"cha":8},
    "skill_proficiencies":["athletics","survival"],
    "backstory":"P28 live smoke"
  }'
```

### 3.2 将时间推进到 `dusk` 或 `night`

循环执行短休，并用 `resume` 检查时间：

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/action/stream" \
  -H "Content-Type: application/json" \
  -d '{"action_type":"rest_short","params":{}}'
```

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/resume"
```

通过标准：`player.period` 变为 `dusk` 或 `night`。

### 3.3 进入遗迹并确认遭遇出现

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/action/stream" \
  -H "Content-Type: application/json" \
  -d '{"action_type":"move_area","params":{"area_id":"ancient_ruins"}}'
```

通过标准：

1. SSE 中出现 `event: encounter_spotted`
2. 同一轮 `location_overview.location_id == "forest_approach"`
3. `sub_area_id` 可被记录下来供后续 `/encounter/action` 使用

### 3.4 从遭遇推进到战斗

先进入遭遇：

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/encounter/action" \
  -H "Content-Type: application/json" \
  -d "{\"choice\":\"enter\",\"sub_area_id\":\"$SUB_AREA_ID\"}"
```

判定规则：

1. 若直接出现 `event: combat_start`，通过
2. 若先出现 `event: stealth_result` 且 `passed=true`，继续执行：

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/encounter/action" \
  -H "Content-Type: application/json" \
  -d "{\"choice\":\"surprise_attack\",\"sub_area_id\":\"$SUB_AREA_ID\"}"
```

后续通过标准：出现 `event: combat_start`

---

## 4. U7 验收步骤

### 4.1 建立会话并注入 capability

为 `guild_quartermaster` 注入交易能力：

```bash
./venv/bin/python scripts/p28_seed_capability.py \
  --world-id goblin_slayer \
  --session-id "$SESSION_ID" \
  --npc-id guild_quartermaster \
  --capability-id sell_supplies \
  --instruction "展示可用补给并协助购买。" \
  --functional trade_browse
```

为 `guild_girl` 注入公告板能力：

```bash
./venv/bin/python scripts/p28_seed_capability.py \
  --world-id goblin_slayer \
  --session-id "$SESSION_ID" \
  --npc-id guild_girl \
  --capability-id guide_board \
  --instruction "引导玩家查看公会公告板并解释委托流程。" \
  --functional board_browse \
  --functional-params-json '{"board_id":"guild_board"}'
```

为私聊信息型 NPC 注入纯行为能力：

```bash
./venv/bin/python scripts/p28_seed_capability.py \
  --world-id goblin_slayer \
  --session-id "$SESSION_ID" \
  --npc-id goblin_slayer \
  --capability-id share_ruins_hint \
  --instruction "在私聊中透露遗迹外围的最新情报。"
```

通过标准：脚本输出 `ok: true`，并能回显 capability snapshot。

### 4.2 真实 Gemini public interaction

交易能力正例：

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/interact/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "intent":"talk",
    "target_kind":"npc",
    "target_id":"guild_quartermaster",
    "message":"给我看看补给。"
  }'
```

公告板能力正例：

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/interact/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "intent":"talk",
    "target_kind":"npc",
    "target_id":"guild_girl",
    "message":"我想看看公会委托。"
  }'
```

通过标准：

1. `dialogue_options` 中出现 capability 对应功能项
2. `trade_browse` / `board_browse` 的 `functional.params` 正确
3. 文本回复不与 capability 指令冲突

### 4.3 真实 Gemini private chat

```bash
curl -sS -N -X POST "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID/private_chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "npc_id":"goblin_slayer",
    "message":"私下告诉我遗迹外围最近的情况。"
  }'
```

通过标准：

1. 回复体现 capability 指令语义
2. 无 `npc_error` / `npc_response_error`
3. 若模型生成 options，则不违背 capability 边界

### 4.4 负例

- 不给无交易能力的 NPC 注入 `trade_browse`
- 对其发送“给我看看补给”

通过标准：

1. 不应出现 `trade_browse` functional 选项
2. NPC 应明确说明自己做不到，并引导去找合适 NPC

---

## 5. 证据模板

### U12

| 项目 | 结果 | 证据 |
|---|---|---|
| `resume.player.period` 到达 `dusk/night` |  |  |
| `move_area -> ancient_ruins` 出现 `encounter_spotted` |  |  |
| `location_overview.location_id == forest_approach` |  |  |
| `enter` 后直接或经 `surprise_attack` 到达 `combat_start` |  |  |
| 使用的 `sub_area_id` |  |  |

### U7

| 场景 | 结果 | 证据 |
|---|---|---|
| `guild_quartermaster` 产生 `trade_browse` |  |  |
| `guild_girl` 产生 `board_browse(board_id=guild_board)` |  |  |
| 私聊 NPC 回复体现情报 capability |  |  |
| 无能力 NPC 不产生错误 functional |  |  |

---

## 6. 清理

验收结束后删除会话：

```bash
curl -sS -X DELETE "http://127.0.0.1:8000/api/game/goblin_slayer/sessions/$SESSION_ID"
```
