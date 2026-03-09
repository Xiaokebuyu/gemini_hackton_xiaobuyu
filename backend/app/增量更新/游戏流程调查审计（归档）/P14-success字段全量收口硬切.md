# P14 - `success` 字段全量收口硬切

状态：已完成
日期：2026-03-08

## 目标

把结果语义彻底拆开，不再让同一个 `success` 同时表示：

- 命令有没有执行完
- 检定有没有通过
- 交互流程有没有跑完
- tool 调用有没有成功

本轮硬切后的唯一标准语义：

- `executed`: 规则命令 / pipeline / action_result 是否执行完成
- `passed`: 检定、潜行、命中、对抗等领域结果是否通过
- `completed`: 交互流程 / SSE 会话是否完整结束
- `ok`: narrative tool 调用是否成功

## 已落地变更

### 后端结果模型

- `ExecuteResult.success -> executed`
- `PipelineResult.success -> executed`
- `InteractionExecutionResult.success -> completed`
- `NpcInteractionResult.success -> completed`
- `PrivateChatResult.success -> completed`
- `ToolResult.success -> ok`
- `TickRecord.success -> executed`
- `ActionExecutionResponse.success -> executed`

### 路由 / SSE 契约

- `action_result.success` 已删除，只保留 `executed` 和 `outcome`
- `stream_end.success -> completed`
- `dice_roll.success -> passed`
- `stealth_result.success -> passed`
- 战斗 roll payload 全部改为 `passed`
- `failed flee` 不再伪装成执行失败；现在表现为 `executed=true + outcome.passed=false`

### 元数据与中间结构

- `enter_hostile` 的 metadata / `last_stealth_result` 从 `success` 改为 `passed`
- `ai_osiris` / `event_condition` 的 `command_results[*].success` 改为 `command_results[*].executed`
- `TickCoordinator.action_log` 新记录只写 `executed`
- L7 engine result stub 改为 `executed`

### 前端

- `ActionResultData.success` 删除，改为必填 `executed`
- `StreamEndData.success -> completed`
- `DiceRollData.success -> passed`
- `StealthResultData.success -> passed`
- `DiceRollEntry.success -> passed`
- `useGameStream` 不再读取任何结果语义上的 `success`

## 实际完成状态

本轮不是停在“计划”和“局部试点”，而是已经完成以下硬切：

1. 运行时代码已移除结果语义上的 `success`
   - 后端模型、RulesEngine、Pipeline、TickCoordinator、agent orchestration、路由、SSE 契约全部切到 `executed / completed / passed / ok`
2. 前端运行时契约已同步
   - `frontend/src/types/sse.ts`
   - `frontend/src/hooks/useGameStream.ts`
   - 战斗 overlay / stealth / action result 消费点
3. 测试层已同步到新语义
   - narrative / interaction / hook / SSE / rules / handler / routing 相关测试均已改读新字段
4. 代码检索已确认
   - `app/` 与 `frontend/src/` 中已不存在结果语义字段 `success`
   - 剩余 `success` 仅用于通知样式名、自然语言文案、注释和测试描述

## 验证

已通过：

- `./venv/bin/pytest tests/test_skill_check_handler.py tests/test_hostile_area_handler.py tests/test_agent_orchestration.py tests/test_skill_check_dialogue.py tests/test_api_shell.py -q`
  - `120 passed`
- `./venv/bin/pytest` 宽回归（46 个测试文件，排除 1 个与本次改动无关的既有失败）
  - `857 passed, 1 deselected`
- `npm run build`
  - 通过

补充检查：

- `app/` 与 `frontend/src/` 中已不再保留结果语义字段 `success`
- `tests/` 中结果语义字段也已迁移完成
- 剩余 `success` 文本仅存在自然语言注释、通知类型名或文档/测试历史文本中

## 非本次遗留

宽回归里剩下 1 条被排除的失败，不属于这次 `success` 字段收口造成的问题：

- `tests/test_combat_handler.py::TestMonsterAI::test_flee_chance_zero_never_flees`
  - 当前实现里 `CombatHandler._decide_monster_action()` 在 `flee_chance=0.0` 时仍可能返回 `flee`
  - 这是怪物 AI 逻辑与测试预期不一致，属于独立问题

## 文档联动说明

- P13 记录了“对话检定回正”的第一阶段和设计文档冲突项
- 本文档记录的是第二阶段：把过渡期保留的 `success` 彻底从运行时代码和公共契约中移除
- 因此若两份文档有表述差异，以本文件的硬切结果为准

## 后续建议

- 继续把架构设计文档中的旧示例改成 `executed / passed / completed / ok`
- 单独处理怪物 AI 的 `flee_chance=0` 既有失败
- 后续新增结果契约时，禁止再引入泛化 `success`
