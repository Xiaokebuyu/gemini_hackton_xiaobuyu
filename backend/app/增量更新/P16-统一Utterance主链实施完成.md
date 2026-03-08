# P16 - 统一 Utterance 主链实施完成

状态：已完成
日期：2026-03-08
设计真相源：`P15-统一发言语义与输入主链设计.md`

## 1. 目标

本次实施的目标是把玩家“说一句话”的语义从 UI 分流和旧路由分叉中收口回来，建立统一的内部 utterance 主链。

对应 P15 的核心要求：

- `public` 与 `public + focus_target` 共享一条主链
- `private` 与 `party` 只作为 scope 差异
- `receive` 与 `respond` 分层
- `/interact/stream` 与 `/private_chat/stream` 退化为 adapter
- `/input/stream` 保持文本命令，不再承担自然语言发言主链

## 2. 实际完成项

### 2.1 后端统一真相源

已新增内部统一编排器：

- `app/utterance_orchestration.py`

其中包含：

- `UtteranceRequest`
- `UtteranceTarget`
- `UtteranceExecutionResult`
- `UtteranceOrchestrator`
- `build_utterance_request()`

统一能力已经覆盖：

- `scope=public` + 无焦点
- `scope=public` + `focus_target=npc`
- `scope=private` + `focus_target=npc`
- `scope=party`
- `utterance + check`

### 2.2 路由收口

以下路由已经改成 adapter：

- `/interact/stream`
  - 先规范化为 `UtteranceRequest`
  - 若属于 utterance 语义，则直接交给 `UtteranceOrchestrator`
  - 非 utterance 交互继续走旧的 interaction path
- `/private_chat/stream`
  - 统一映射为 `UtteranceRequest(scope="private", focus_target=npc)`

当前对外公开路由没有新增，内部真相源已经切换。

### 2.3 编排分层

当前实际编排顺序为：

- `public + focus_target=npc`
  - 玩家 utterance
  - 可选 check
  - 焦点 NPC
  - GM
  - 队友
- `public + no focus`
  - 玩家 utterance
  - 可选 check
  - GM
  - 队友
- `private + focus_target=npc`
  - 玩家 utterance
  - 目标 NPC
- `party`
  - 玩家 utterance
  - 队友

首版仍保持保守边界：

- 非焦点附近 NPC 不参与主动 round
- 公开发言时，附近 NPC 主要通过公开 SceneBus 获得格内语境

### 2.4 `receive` / `respond` 拆分

队友侧已经完成首版分层：

- audience 内队友即使不回应，也会获得最小 transcript/receipt 写入
- 概率门只决定“是否开口”，不再决定“是否收到”

这修正了之前“没回应就像没收到”的偏差。

### 2.5 前端输入分流回正

输入框默认语义已改为：

- `gameMode=private_chat` -> `scope=private`
- 有焦点 NPC -> `scope=public + focus_target=npc`
- 默认 -> `scope=public`

同时：

- 删除了 `hasParty => 自动 chat`
- `party` 改为显式频道切换
- `private_chat` 分流优先看 `gameMode`

### 2.6 对话检定保持在 utterance 主链

checked dialogue 没有再退回裸 action。

当前行为：

- utterance 上附加 `check`
- 先执行 `skill_check`
- 产出标准 `dice_roll`
- 再把 `check_result` 带回同一条 utterance 编排链

## 3. 代码落点

本次实施的主要代码落点：

- `app/utterance_orchestration.py`
- `app/routers/gameplay.py`
- `app/game_core/orchestration/npc_interaction.py`
- `app/agent_orchestration.py`
- `app/game_core/orchestration/tick_coordinator.py`
- `app/game_core/orchestration/hooks/ai_osiris.py`
- `app/game_core/orchestration/hooks/narrative_planner.py`
- `app/game_core/orchestration/hooks/shared_experience.py`
- `app/api_models.py`
- `frontend/src/game/OptionPanel.tsx`
- `frontend/src/game/DialogueArea.tsx`
- `frontend/src/hooks/useGameStream.ts`
- `frontend/src/pages/GamePage.tsx`
- `frontend/src/types/api.ts`

## 4. 验证结果

本次实施完成后，已执行的针对性验证包括：

- `./venv/bin/pytest tests/test_group_dialogue.py tests/test_agent_orchestration.py tests/test_api_shell.py tests/test_skill_check_dialogue.py tests/test_input_port.py -q`
  - 结果：`131 passed`
- `./venv/bin/pytest tests/test_private_chat.py tests/test_npc_interaction.py tests/test_interaction_service.py -q`
  - 结果：`64 passed`
- `./venv/bin/pytest tests/test_dialogue_turn_lifecycle.py tests/test_private_chat_enrichment.py tests/test_ai_osiris_hook.py tests/test_round3_engine_tags.py tests/test_round5_companion.py -q`
  - 结果：`99 passed`
- `npm run build`
  - 结果：通过

## 5. 当前已知边界

这次是首版统一，不是最终态。当前仍保留以下边界：

- 非焦点附近 NPC 暂不主动插话
- 非焦点 NPC 暂未新增持久 receipt 写入
- 未新增 `/utterance/stream` 别名路由
- 旧的非 utterance interaction path 仍保留，用于浏览、购买、任务等非发言交互

## 6. 结论

P15 的主设计目标已经在首版实现中落地：

- 玩家发言已经有统一内部模型
- 默认自由输入重新变回公开发言
- `party` 不再抢走普通输入语义
- `receive` 与 `respond` 已开始分层
- 对话检定继续留在同一条 utterance 主链

后续若继续演进，优先级应是：

1. 决定是否开放非焦点附近 NPC 的受控插话
2. 决定是否为公开发言引入专门的 `/utterance/stream` 别名路由
3. 继续把架构设计文档中的旧入口和旧术语彻底收口
