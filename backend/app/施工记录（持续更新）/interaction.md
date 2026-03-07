# 交互层施工记录

## 边界定义

| 职责 | 归属 | 说明 |
|------|------|------|
| 空间可达性验证 | `game_core/orchestration/interaction.py` | NPC/Board 是否在当前位置 |
| 任务状态转换验证 | `game_core/orchestration/interaction.py` | 任务接取/完成/退出的前置条件 |
| 告示牌-任务关联查询 | `game_core/orchestration/interaction.py` | `board_has_quest()` 最小查询 |
| 视图快照构建 | `app/interaction_service.py` + `app/interaction_views.py` | 前端展示数据格式化 |
| SSE 事件信封 | `app/interaction_service.py` | InteractionOutputEvent 构造 |
| 执行分发 | `app/interaction_service.py` | snapshot / pipeline_action / shop_refresh 路由 |
| 管线执行 | 通过 `_execute_structured_action` → TickCoordinator | 正确走管线 |

## D-I01 交互层增强 — Shop/Talk/InspectItem（2026-02-28）

**问题**：交互层 snapshot 过于简陋，前端缺乏足够信息渲染 UI。

**改动**：

### InteractionContext 扩展
- 新增 `player_gold: int`、`player_inventory: list[dict]`、`item_catalog: dict[str, dict]`
- `build_interaction_context()` 从 PlayerSlice 和 ItemRegistry 填充

### Shop Snapshot 增强
- `build_shop_snapshot_payload()` 新增：
  - `player_gold`：玩家当前金币
  - stock 每项富化 `name`/`type`/`rarity`（从 item_catalog 查询）
  - `player_sellable_items`：玩家可出售物品列表（含 name、base_price）

### Talk Snapshot 增强
- `build_talk_snapshot_payload()` 新增 `available_intents` 列表
- 逻辑：基础 ["talk", "greet"] + 商人 ["browse", "buy", "sell", "inspect_item"] + 有动态任务时追加 quest intents

### inspect_item Intent
- 新 builder：`build_inspect_item_payload()` — 物品详情（name/type/rarity/base_price/slot/damage/ac_bonus/player_owned_count）
- InputPort：NPC intent 白名单 + inspect_item 分支（要求 item_id 非空）
- `_execute_snapshot` 扩展提取 `item_id`，所有 builder wrapper 签名加 `item_id` 参数

### 测试
- 7 新测试 in `tests/test_interaction_service.py`（依赖 FastAPI，独立运行）
- 1 现有测试更新（error message 同步）

**文件清单**：
| 文件 | 改动 |
|------|------|
| `app/interaction_service.py` | InteractionContext +3 字段, build_interaction_context 填充, _execute_snapshot +item_id, 9 wrapper 签名更新, +1 新 wrapper |
| `app/interaction_views.py` | shop/talk builder 增强, +build_inspect_item_payload |
| `app/game_core/adapters/inbound.py` | NPC intent 白名单 +inspect_item, +分支 |
| `tests/test_interaction_service.py` | +7 测试 |
| `tests/test_input_port.py` | error message 同步 |

---

## D-I02 NpcInteractionCoordinator — 6 步交互管线（2026-03-01）

**问题**：interact_stream 只有 3 步（验证 → snapshot → NPC LLM），缺少 GM 观察、Teammate 旁观和对话选项。设计文档（编排层设计规范 §5）要求完整 6 步流程。

**改动**：

### 新建 `app/game_core/orchestration/npc_interaction.py`
- `NpcInteractionResult` — 原始结果模型（AgentResult 集合 + dialogue_options + time_cost）
- `NpcInteractionCoordinator` — 6 步协调器，纯 game_core（不依赖 ManagedSession）
  - Step 1: 验证 NPC + 写 SceneBus + 构建上下文
  - Step 2: NPC Agent（AgenticExecutor，max_turns=3）
  - Step 3: GM 观察（新提示模板，默认 pass_turn）
  - Step 4: Teammate 旁观（response_tendency 概率过滤）
  - Step 5: 静态对话选项（talk/farewell/browse/ask_quest，DC 预留 N-6）
  - Step 6: 聚合返回，time_cost=1/6
- `_should_teammate_respond(world, char_id)` — 读 response_tendency（默认 0.3），clamp [0.05, 0.95]
- `_extract_speech_text(result)` — 提取 NPC 发言文本供上下文使用
- `_build_static_dialogue_options(world, state, npc_id)` — 上下文感知静态选项

### 修改 `app/game_core/narrative/context_builder.py`
- 新增 `GM_INTERACTION_OBSERVATION_PROMPT` — GM 观察 NPC 对话专用提示（默认 pass_turn）
- 新增 `TEAMMATE_INTERACTION_PROMPT_TEMPLATE` — 队友旁观对话专用提示
- 新增 `build_gm_interaction_prompt()` — 返回 GM 交互提示
- 新增 `build_teammate_interaction_prompt(char_id)` — 格式化队友交互提示

### 修改 `app/agent_orchestration.py`
- 新增 `run_npc_interaction()` — 实例化 coordinator → execute_interaction → SSE 转换
- 新增 `_interaction_result_to_sse()` — NpcInteractionResult → 有序 SSE 事件列表（NPC → GM → Teammate → Options）

### 修改 `app/routers/gameplay.py`
- `interact_stream()` 中 `generate_npc_response` → `run_npc_interaction`（+intent 参数）

**设计偏离**：
1. Step 5 对话选项静态实现（DC 计算推迟至 N-6）
2. NPCInstance/InstanceManager 用 AgentContextBuilder 替代（MemoryGraph 推迟至 N-1）

**测试**：
- `tests/test_npc_interaction.py`：22 新测试（Coordinator + Teammate + Options + Helpers）
- `tests/test_agent_orchestration.py`：+8 测试（InteractionResultToSSE + RunNpcInteraction）

**文件清单**：
| 文件 | 改动 |
|------|------|
| `app/game_core/orchestration/npc_interaction.py` | 新建 ~300 行 |
| `app/game_core/narrative/context_builder.py` | +2 提示模板 + 2 builder 方法 |
| `app/game_core/orchestration/__init__.py` | +2 导出 |
| `app/agent_orchestration.py` | +run_npc_interaction + _interaction_result_to_sse |
| `app/routers/gameplay.py` | generate_npc_response → run_npc_interaction |
| `tests/test_npc_interaction.py` | 新建 22 测试 |
| `tests/test_agent_orchestration.py` | +8 测试 |

测试基线：541 → 571 passed（+30）

---

## [D-I03] 任务看板交互路径收口到 action 管线（2026-03-07）

**背景**：P9 任务入口对齐后，`board` 相关交互不应再走独立的旧 interact 快照/校验逻辑。

### 收口决策

- `board` 浏览与接取统一走 `BoardHandler` + `ActionDispatcher`（`/action/stream`）。
- `BoardHandler` 负责：
  - 在 `current_area/current_location` 上校验 `board_id` 可达性；
  - 输出 `browse_board` 的 `entries`；
  - 将 `board_accept_quest` 等指令转为 `QuestSlice` 状态变更。
- 交互层/适配层相关旧路径清理：
  - 移除 `interact` 体系中与看板直接绑定的规范化与校验代码；
  - `board` 读取统一由 `AreaSlice` 侧数据提供，不再在 `interaction_service/interaction_views` 维护。

### 验收

- `tests/test_board_handler.py`：静态命令 + 返回结构 + 验证分支
- `tests/test_api_shell.py`：端到端 `/action/stream` 链路新增 4 条
