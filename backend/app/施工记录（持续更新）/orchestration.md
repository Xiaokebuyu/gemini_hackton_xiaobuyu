# 编排层施工记录

**设计文档**：编排层设计规范.md
**代码路径**：`app/game_core/orchestration/`
**Phase**：2（骨架）+ 4B（具体 Hook）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `TickCoordinator` | [完成] | process → accumulate → check_settlement → _tick_settlement |
| `PipelineOrchestrator` | [完成] | A/B/C 三阶段 + PipelineHook 扩展点 |
| `SettlementContext` | [完成] | change_log/state/world/scene_bus + execute_command() |
| `SettlementHook` ABC | [完成] | priority/name/should_skip/execute |
| `SceneBus` | [完成] | SceneSlice 运行时视图 |
| `ActionDispatcher` | [完成] | action_type → command_type 映射 + transform |
| `ContextAssembler` | [完成] | L0-L7 八层 context 组装，覆盖全部 10 Slice + WorldInstance |
| `SharedContext` | [完成] | world/state/rules_engine/scene_bus 打包 |
| `AdminCoordinator` | [有意不做] | 规范 §2.1。当前仅 TickCoordinator+InteractionService 两条路径，flat routing 足够。等 3+ coordinator 类型再抽象 |
| `PrivateChatCoordinator` | [延后] | 规范 §2.5。依赖叙事层 Agent 集成（LLM），无 LLM 只是空壳。随 Agent 主线同步实现 |
| `EventEngine` 独立模块 | [延后] | 规范 §8。核心逻辑已在 BasicEventConditionEvaluator（Protocol 注入），独立化主要是命名清理。当前无 A6/C1 调用需求 |
| B 阶段 Agent 管线 | [延后] | 规范 §4.2。PipelineOrchestrator 只做 A 阶段，Agent 会话推到应用层。依赖 LLM 接入 |
| A6/C1 条件检查 | [延后] | 规范 §4 A6+C1。条件检查仅在 P50 settlement 运行，回合制下同 tick 内无感延迟，够用 |

### Hook 实施状态

| 优先级 | Hook | 状态 | 关键依赖 |
|--------|------|------|---------|
| P10 | `ScheduledEventHook` | [完成] | EventSlice |
| P20 | `StatusEffectHook` | [完成] | StatusEffectHandler |
| P30 | `AIOsirisHook` | [完成] | WorldStateHandler |
| P35 | `NarrativePlannerHook` | [完成] | QuestSlice + NarrativePlanSlice |
| P40 | `EncounterHook` | [完成] | EncounterHandler |
| P50 | `EventConditionHook` | [完成] | EventSlice |
| P60 | `NpcScheduleHook` | [完成] | AreaSlice |
| P70 | `TimeAdvanceHook` | [完成] | TimeSlice |
| P75 | `DynamicSubAreaExpiryHook` | [完成] | AreaSlice.tick_expiry |
| P80 | `GmNarrationHook` | [完成] | AgenticExecutor |
| P90 | `SceneBusResetHook` | [完成] | — |

## 决策记录

### [D-O01] SettlementContext 收窄 Hook 访问面

Hook 不直接持有 RulesEngine 引用。通过 `execute_command(cmd)` 调用引擎。
`_rules_engine` 用 `field(repr=False)` 隐藏，防止 debug 打印泄漏。

### [D-O02] _tick_settlement 收集 SSE events

见 `骨架搭建.md` [D-005]。Hook 返回的 `HookResult.sse_events` 被收集并 merge 到 PipelineResult。

### [D-O03] SceneBusResetHook 同时清 change_log

`context.change_log.clear()` — change_log 与 SceneBus 同生命周期。
这是 TickCoordinator 共享的同一个列表引用，格末统一清零。

### [D-O04] NoOpSettlementHook 共享 stub 基类

类似 Handler 的 StaticCommandHandler 模式。9 个 stub Hook 继承 NoOpSettlementHook，
`execute()` 返回空 HookResult。只有 SceneBusResetHook 有实际逻辑（reset + clear）。

### [D-O05] PipelineOrchestrator 支持 3 种输入

1. `Command` 对象 → 直接执行
2. `StructuredAction` 对象 → ActionDispatcher 转译
3. `dict` with `action_type` → 构造 StructuredAction → 转译

### [D-O06] ContextAssembler L0-L7 八层结构

对齐设计规范 §4.1，从临时编号 `layer0_meta`~`layer6_narrative` 重构为：
- L0 `l0_world_constants`：LoreRegistry.list_all() + FactionRegistry.list_all()
- L1 `l1_chapter_state`：QuestSlice + NarrativePlanSlice
- L2 `l2_area_environment`：MapRegistry 模板 + AreaSlice 动态状态（仅当前区域）
- L3 `l3_location_details`：MapRegistry 子地点模板（仅当前子地点）
- L4 `l4_dynamic_state`：TimeSlice + PlayerSlice + RelationSlice + FlagSlice + PartySlice + EventSlice
- L5 `l5_scene_bus`：SceneBus snapshot
- L6 `l6_memory_recall`：空 stub（Phase 5 MemoryGraph 适配器）
- L7 `l7_engine_result`：None，由 PipelineOrchestrator 在 A4 后填充

PipelineOrchestrator 在 engine 执行后将 ExecuteResult 摘要写入 L7（narrative_hints/rolls/time_cost）。

### [D-O07] ScheduledEventHook 继续使用 absolute trigger_tick

本轮不引入完整 `trigger_condition` 条件树。
`ScheduledEventHook` 直接使用 `TimeSlice.absolute_tick()` 与
`EventSlice.pending_events[*].trigger_tick` 做比较：

- `trigger_tick <= current_tick` → 触发
- 触发后写入 `active_events[event_id]`
- 同步发出 `event_triggered` SSE

这样先把现有 `schedule_event` → `pending_events` 的闭环补完整，再考虑条件化触发器。

### [D-O08] AIOsirisHook 先落可执行 MVP，不接真实 LLM

本轮 `AIOsirisHook` 采用可注入 evaluator 的实现：

- 默认使用 no-op evaluator，保持默认装配链可运行
- Hook 内部直接调用 `SettlementContext.execute_command()` 执行 Osiris 后果
- evaluator 返回的所有 consequence 一律强制落为 `source="ai_osiris"`
- 暂不向 `SceneBus` 写文本，玩家可见叙事延后到后续 `GmNarrationHook`

这样先把 `change_log -> summary/snapshot -> consequence -> world-state command`
这条主链跑通，后续再把 evaluator 从 no-op 深化为规则版或 LLM 版。

### [D-O08A] AIOsirisHook 已收紧为稳定扩展点

在 MVP 基础上，本轮补了两层边界约束：

- 只允许执行 10 个 world-state 白名单命令
- 单次最多执行 5 条 consequence，超出部分计入 `truncated_count`
- 执行状态细化为 `applied / partial_failure / failed`

这样后续即使接入更激进的 evaluator，也不会把不受控命令或过长 consequence 批次直接推进结算链。

### [D-O08B] AIOsirisHook 默认 evaluator 已从 no-op 进入最小真实实现

在保持同样 evaluator 注入边界和白名单约束的前提下，本轮把默认 evaluator
从 `NullAIOsirisEvaluator` 推进为确定性的 `BasicAIOsirisEvaluator`：

- 默认只生成低风险的 `set_flag` consequence
- 只响应高信号变化：
  - `quests` 变化时记录当前 chapter
  - `flags.quest_started` 时写确认 flag
- 对纯 `player` / 常规移动类变化继续保持 no-op

这样默认装配链不再完全空转，但也不会频繁干扰当前稳定的主路径。

### [D-O09] StatusEffectHook 先只处理 PlayerSlice.active_effects

本轮 `StatusEffectHook` 固定在每次 settlement 调用一次
`Command(type="tick_effects", source="system")`：

- 只依赖 `StatusEffectHandler`
- 只处理 `PlayerSlice.active_effects`
- 有实际效果变化时发出 `status_effects_ticked` SSE
- 不处理 NPC 效果，不生成叙事文本

这样先把 P20 位置的“逐格效果结算”补成稳定扩展点，再考虑战斗回合内的更细粒度效果流。

### [D-O10] TimeAdvanceHook 收口为稳定 contract

`TimeAdvanceHook` 继续负责“消耗 1.0 accumulated 并推进 1 格”，
但本轮补齐了稳定 metadata / SSE 形状：

- noop 时仍返回固定字段（`advanced/from_tick/to_tick/...`）
- 推进时 `time_advanced` SSE 附带 `absolute_tick`
- 同步标出 `crossed_day` 与 `period_changed`

这样上层不需要再根据缺省字段猜测时间是否真的推进。

### [D-O11] EventConditionHook 先落地为 P50 的最小事件状态机

本轮没有单独引入完整 `EventEngine` 模块，而是先把 P50 的
`EventConditionHook` 补成稳定扩展点：

- 默认使用内建 `BasicEventConditionEvaluator`
- 只处理最小状态推进：
  - `locked/dormant -> available`
  - `triggered -> active`
- 只支持最小条件子集：
  - `flag_set`
  - `location_entered`
  - `period_reached`
  - `time_reached`
  - `quest_state`
- 若 evaluator 生成命令，只允许 world-state 白名单，且强制 `source="system"`
- 本轮不写 `SceneBus` 文本，不做事件叙事

这样先把 quest 初始事件与 scheduled event 的 P50 条件检查补成真实可运行链路，
后续再把完整 EventEngine 从这个边界继续深化。

### [D-O12] active_events 统一为 state/status 双字段兼容 contract

此前运行时事件存在两套字段风格：

- 世界初始化事件：`status="locked"`
- ScheduledEventHook 激活事件：`state="triggered"`

本轮统一为：

- canonical 字段为 `state`
- 同步保留 `status` 镜像，兼容旧数据和旧调用

`EventSlice.activate()` / `set_state()` / restore 以及初始化事件 payload
现在都会自动补齐：

- `id`
- `event_id`
- `state`
- `status`

这样事件状态在不同来源下不再是两套 schema。

### [D-O13] NpcScheduleHook 先收口为稳定 provider 边界

本轮 `NpcScheduleHook` 先补到“稳定扩展点”，不直接实现完整模板日程推理：

- Hook 支持可注入 `NpcScheduleProvider`
- 默认使用 `NullNpcScheduleProvider`，保持默认装配链可运行
- 仅在“下一格将发生时段切换”时调用 provider
- 只通过 `AreaSlice.npc_locations` 直写 NPC 位置（受控例外 B）
- 不触碰玩家位置，不写 `SceneBus` 文本

这样先把 P60 的输入、输出、错误处理和状态写接口稳定下来，后续再在这个边界上深化真实 schedule 逻辑。

### [D-O13A] NpcScheduleHook 默认 provider 已从 no-op 进入最小真实实现

在保持同样 provider 注入边界的前提下，本轮把默认 provider
从 `NullNpcScheduleProvider` 推进为确定性的 `BasicNpcScheduleProvider`：

- 只做低风险的 area-level 迁移
- 时段切换到 `dusk/night` 时，优先把 NPC 收拢到 `town`
- 时段切换到 `dawn/day` 时，优先把 NPC 放回各自的 home area
- 不分配子地点，不做复杂日程推理

这样 P60 在默认链路下不再总是空转，但仍保持低耦合、低扰动。

### [D-O14] AreaSlice 为 P60 增加稳定 NPC 迁移接口

为了让 `NpcScheduleHook` 不再依赖模糊的“更新已有桶，否则写第一个区域”的旧行为，
`AreaSlice` 本轮新增了两个稳定接口：

- `find_npc_area(character_id)`
- `move_npc(character_id, area_id, location_id)`

`move_npc()` 会先从所有 area 的 `npc_locations` 中移除目标 NPC，再写入目标 area。
这样 P60 的跨区域迁移行为有了明确 contract，后续 provider 可以直接依赖它。

### [D-O15] EncounterHook 先收口为稳定 detector 边界

本轮 `EncounterHook` 补到“稳定扩展点”，但仍不实现完整随机遭遇系统：

- Hook 支持可注入 `EncounterDetector`
- 默认使用 `NullEncounterDetector`，在默认装配链下保持 no-op
- 只有玩家处于区域大地图、且当前区域 `danger > 0` 时才进入 detector
- detector 若要求检查，Hook 统一通过 `execute_command(encounter_check)` 触发规则层
- 本轮不自动开战，不写 `SceneBus` 文本，只发结构化 SSE

这样先把 P40 的输入、输出、异常处理和最小触发链稳定下来，后续再深化到真正的随机遭遇与战斗接管。

### [D-O16] NarrativePlannerHook 先落为最小可执行子集

本轮 `NarrativePlannerHook` 先补到“稳定扩展点”，但只执行当前仓库已有稳定落点的最小 directive 子集：

- 允许执行：
  - `create_quest`
  - `direct_npc`
  - `publish_bulletin`
  - `escalate`
  - `adjust_pacing`
  - `retire_quest`
- 明确跳过：
  - `spawn_quest_npc`
  - `plant_environmental`
  - `fill_area`
  - 未知 kind
- 采用触发白名单 + `FALLBACK_INTERVAL=6` 的兜底运行策略
- 不通过 `HookResult.commands` 回传命令，直接更新 `NarrativePlanSlice / QuestSlice`

这样先把 P35 的 planner 输入、bookkeeping、错误处理和最小可执行范围稳定下来，
后续再在同一边界上深化真实规划器。

### [D-O17] GmNarrationHook 先采用 narrator 注入边界，不强绑 AgenticExecutor

本轮 `GmNarrationHook` 采用可注入 `narrator`：

- 默认使用 `NullGmNarrator`，在默认装配链下保持 no-op
- 输入固定为：
  - 格内 `SceneBus.state_changes`
  - 时间/位置摘要
- 输出固定为：
  - 受控 `SceneEntry` 写入（最多 3 条）
  - 结构化 metadata / SSE
- 本轮不直接调用 `AgenticExecutor.run()`

这样先把 P80 的输入 contract、entry 标准化和写入边界稳定下来，
等 `AgenticExecutor` 真正深化后，再把它作为 narrator 的一种实现接入。

### [D-O17A] GmNarrationHook 默认 narrator 已从 no-op 切到模板 narrator

在保留 narrator 注入边界的前提下，本轮把默认 narrator
从 `NullGmNarrator` 切到模板化 narrator：

- 默认会根据 `SceneBus.state_changes` 的 changed_slices 生成 1 条最小 public entry
- 仍复用原有的 entry 标准化、截断和 `gm_narration_added` SSE contract
- `NullGmNarrator` 继续保留，供显式静默场景使用

这样默认叙事链不再只是空壳，但仍不依赖 `AgenticExecutor` 或 LLM。

### [D-O18] DynamicSubAreaExpiryHook 动态子区域生命周期闭合

本轮完成动态子区域"只能造不能用"的三个缺口修复：

**AreaSlice.tick_expiry()**：
- 递减正 expiry 值，移除到期子区域，返回被移除的 ID 列表
- permanent (expiry==-1) 永不过期
- 有移除时标记 dirty

**NavigationHandler 动态子区域 fallback**：
- `_validate_enter_sub_location` 先查静态 `sub_locations`（MapRegistry），未命中则 fallback 到 `state.areas.list_temporary_sub_areas(area_id)`
- 无静态子区域的地图（sub_locations 非 Mapping）归一化为 `{}`，仍可有动态子区域

**DynamicSubAreaExpiryHook (P75)**：
- 在 TimeAdvanceHook (P70) 之后运行，确保时钟已推进
- 只 tick 玩家当前区域（非全局），expiry 代表"玩家在此区域时的 tick 数"
- 玩家在过期子区域中 → 执行 `leave_sub_location` 弹出
- 发射 `dynamic_sub_areas_expired` SSE（removed_ids, player_ejected）

**ContextAssembler L3 扩展**：
- 静态 template 未命中 → fallback 到 `temporary_sub_areas` 查找
- 新增 `is_dynamic` 布尔字段
- 新增 `dynamic_sub_areas` 列表（当前区域所有动态子区域供 AI 上下文）

**测试**：12 新 in `tests/test_dynamic_sub_area_lifecycle.py` + 1 现有测试更新。基线 414→429。

## 填充 TODO

- [x] `ContextAssembler`：从 stub 升级为完整实现 ✅（16 tests passed）
- [x] `TimeAdvanceHook`：推进 1 格 + 时段/日期转换事件
- [x] `ScheduledEventHook`：EventSlice.pop_due_pending() + 激活
- [x] `StatusEffectHook`：tick 持续时间 + 伤害/治疗（MVP）
- [x] `NpcScheduleHook`：稳定 provider 边界 + AreaSlice.npc_locations 迁移
- [x] `AIOsirisHook`：change_log → 世界快照 → evaluator → execute_command
- [x] `NarrativePlannerHook`：稳定 planner 边界 + 最小可执行子集
- [x] `EncounterHook`：稳定 detector 边界 + 最小遭遇触发
- [x] `EventConditionHook`：P50 最小条件检查 + 状态推进
- [x] `GmNarrationHook`：稳定 narrator 边界 + 受控 SceneBus 写入

## D-O19 TickCoordinator Hook 统一容错（2026-02-28）

**问题**：`_tick_settlement()` 无 try/except，纯逻辑 hook 异常会炸掉整个 tick。
**修复**：
- hook.execute() 和 result.commands 的 execute_command() 统一 try/except
- 异常时 logger.exception + 发 `hook_error` SSE 事件 + continue
- 不移除已有 hook 内部的 provider try/except（纵深防御）
- 4 新测试 in `tests/test_hook_resilience.py`

## D-O20 交互策略验证下沉（2026-02-28）

**问题**：`InteractionService`（应用层）承担了游戏规则验证（NPC 空间判定、任务状态转换），违反编排层设计规范的职责划分。

**设计规范偏离**：规范要求 `NpcInteractionCoordinator` 在编排层。实际实现不建完整 Coordinator，而是提取最小验证模块 — 因为交互的快照/视图构建是应用层关注点，不宜整体下沉。

**方案**：Policy / View 双上下文拆分。

**新增**：`app/game_core/orchestration/interaction.py`（~280 行）
- `InteractionPolicyContext`：验证最小集（位置、NPC、任务、告示牌 — 不含展示字段）
- `build_interaction_policy_context(state, world)`：从 StateContainer + WorldInstance 构建
- `validate_presence(ctx, target_kind, target_id, intent)`：NPC/Board 空间可达性
- `validate_preconditions(ctx, target_kind, target_id, intent, quest_id)`：任务状态转换合法性
- `board_has_quest(ctx, board_id, quest_id)`：最小告示牌查询（替代下沉整个 `build_board_entries`）

**应用层改动**：`app/interaction_service.py`
- `InteractionContext` → `InteractionViewContext`（明确视图定位）
- `build_interaction_context(session)` → `build_interaction_view_context(state, world)`（去掉 ManagedSession 依赖）
- `execute()` 流程改为：先 policy_ctx 验证 → 按需构建 view_ctx
- 删除 7 个验证方法（~165 行），改为 import game_core 验证函数

**不动部分**：
- 10 个 snapshot builder 留应用层（纯视图格式化）
- `build_board_entries()` 留 `interaction_views.py`（返回展示条目）
- 不加到 `orchestration/__init__.py`（避免导入面膨胀）

**依赖方向**：`app/interaction_service.py` → `game_core/orchestration/interaction.py`（正确：应用层→内核）

**测试**：467 passed（含 interaction_service 17 个测试，行为不变）

## D-O21 AIOsirisHook LLM 链路接通（2026-03-01）

**目标**：将 AIOsirisHook（P30）的 evaluator 从确定性 MVP 升级到 LLM 驱动版本。

**架构决策**：
- `AIOsirisEvaluator` Protocol async 化（`def evaluate` → `async def evaluate`），与 `GmNarrator.compose` 一致
- LLM evaluator 直接调用 `LlmPort.generate()`，不经 `AgenticExecutor`（单次推理，非多轮 agentic）
- 用单个工具声明 `submit_consequences` 确保结构化输出
- 同 GmNarration 的 factory 注入模式（`bootstrap.py` + `runtime.py`）

**改动清单**：

1. **Protocol async 化**（`orchestration/hooks/ai_osiris.py`）
   - `AIOsirisEvaluator.evaluate` → `async def evaluate`
   - `NullAIOsirisEvaluator` / `BasicAIOsirisEvaluator` 同步改 async
   - `AIOsirisHook.execute()` 中加 `await`

2. **AgenticAIOsirisEvaluator**（**新建** `app/evaluators.py`）
   - System prompt 按设计规范 §四（因果判定引擎角色 + 10 种指令类型 + 约束）
   - `submit_consequences` 工具声明（reasoning + consequences 数组）
   - Response 解析：优先 tool_call args → 降级 text JSON → 解析失败 noop

3. **注入链路**（`bootstrap.py` + `runtime.py` + `session_store.py`）
   - `build_runtime_for_world` / `build_restored_runtime_for_world` 新增 `osiris_evaluator_factory` 参数
   - `GameRuntime._osiris_evaluator_factory()` 回调
   - `SaveStore.load_runtime_for_world` 透传 factory

**优雅降级**：无 API key → `_osiris_evaluator_factory()` 返回 None → 不注册 LLM hook → 使用 BasicAIOsirisEvaluator

**测试**：477 passed（468 + 9 新增），14 个现有 ai_osiris 测试无回归

## D-O21 SSE 事件两类来源定性（2026-02-28）

**问题**：设计文档将 `action_result` 等事件列为 SSE 协议事件，暗示它们与 `dice_roll`/`scene_change` 同源于编排层。

**定性**：这些事件实际由应用层（`gameplay.py`）构造，是 PipelineResult 的摘要投影和流协议信号，不属于游戏世界状态事件。

**判定标准**：事件内容代表游戏世界发生了什么 → 编排层产出（`PipelineResult.sse_events`）；事件内容代表 SSE 会话怎么和客户端交互 → 应用层构造。

**改动**：
- `gameplay.py`：提取 4 个 helper（`_build_action_result_event`、`_build_stream_end_event`、`_build_stream_error_event`、`_emit_terminal_error`），消除重复，加块注释标明两类事件边界
- 编排层设计规范 §A4：加 D-O21 偏差注记
- 表现层设计规范 §三：加 D-O21 偏差注记

## D-O22 持久化时序偏差与命名修正（2026-02-28）

**问题**：设计规范 P5 要求 TickCoordinator.process() 末尾 `await self.persist()` 统一持久化。实际实现中 TickCoordinator 不持有 SaveStore 和 session_id，`persist()` 只导出脏数据字典，实际写存储由调用方（`deps._execute_structured_action` → `save_session`）负责。

**偏差原因**：若 TickCoordinator 注入 SaveStore，game_core 耦合应用层持久化适配器。同时 create_session / complete_character_creation 等非管线路径也需要持久化，不经过 TickCoordinator。P5"管线内部不做持久化"仍然遵守。

**改动**：
- `TickCoordinator.persist()` → `export_dirty()` — 方法名反映真实语义（导出，非写入）
- `StateContainer.persist()` → `export_dirty()` — 同上
- `SaveStore.save_runtime()` 调用点同步更新
- `InteractionService.__init__` 移除 `save_session` 参数 — 注入但从未使用的死代码，其持久化需求已被 `_execute_structured_action` 内置的 `save_session` 覆盖
- `deps.py` 构造 InteractionService 时移除 `save_session=...`
- 编排层设计规范 §2.2 + §10.3：加 D-O22 偏差注记

**文件清单**：
| 文件 | 改动 |
|------|------|
| `game_core/orchestration/tick_coordinator.py` | `persist()` → `export_dirty()` + docstring |
| `game_core/state/base.py` | `persist()` → `export_dirty()` + docstring |
| `game_core/adapters/session_store.py` | 调用点更新 |
| `app/interaction_service.py` | 移除 `save_session` 参数和 `self._save_session` |
| `app/deps.py` | 构造 InteractionService 移除 `save_session=...` |

## D-O23 AIOsirisHook 数据富化 Phase 1A（2026-03-01）

**目标**：将 AIOsirisHook 喂给 evaluator 的输入数据从 MVP 骨架向设计规范 §2 对齐。Phase 1A 只做纯增量改动（新增字段，不修改现有字段格式）。

**依赖分析**（决定分阶段的关键发现）：
- `_build_rules_context` → BasicAIOsirisEvaluator `del rules_context`，完全不读 → 可自由加字段
- `_build_snapshot.current_chapter` → BasicAIOsirisEvaluator 做 `str(snapshot.get("current_chapter"))` → 不能改成 dict，加兄弟字段代替

**改动**：

1. **`_build_rules_context`**：`@staticmethod` → `@classmethod`，接收 `context` 参数
   - 新增 `world_lore`：从 LoreRegistry 提取（id/content/tags）
   - 新增 `faction_rules`：从 FactionRegistry 提取（id/name/alignment/behavioral_rules）
   - 新增 `tag_dimensions`：从 TagRegistry 提取（dimension_id → tag 列表）
   - 原有 `allowed_commands` / `command_source` / `constraints` 不变

2. **`_build_snapshot`**：新增 `chapter_completion` 兄弟字段（float|None）
   - 从 NarrativePlanSlice.chapter_completion 读取
   - `current_chapter` 保持 string 不变（保护 BasicAIOsirisEvaluator）

**测试**：481 passed（477 + 4 新增），14 个现有 ai_osiris 测试零回归

**Phase 1B**（nearby_npcs 动态化）：

3. **`_build_nearby_npcs`**：算法替换
   - 双源查找：AreaSlice 动态主源（npc_locations）+ CharacterRegistry 静态补源（area_id 兜底）
   - 去重：`seen_ids` 集合，动态源优先
   - 输出形状：从完整模板 dump 改为精选字段（id/name/tags/faction/disposition/location_id）
   - 新增 `_build_npc_entry` 辅助方法：从模板提取 name/tags/faction + 从 RelationSlice 提取 disposition

**测试**：484 passed（481 + 3 新增），18 个现有 ai_osiris 测试零回归

**Phase 2A**（duration_minutes 填充）：

4. **`_build_summary.duration_minutes`**：`0` → `60`
   - 每次 settlement tick = TimeAdvance(P70) 消费 1.0 accumulated = 1 slot
   - 24 slots/day = 24h → 1 slot = 60 game minutes
   - AIOsiris(P30) 在 TimeAdvance(P70) 之前执行，数据一致性无问题

**测试**：484 passed（断言更新 0→60），21 个 ai_osiris 测试零回归

**Phase 2B**（actions 管线建立）：

5. **SettlementContext**：新增 `action_log: list[dict[str, Any]]` 字段（`default_factory=list`，9 个现有构造点零破坏）
6. **TickCoordinator**：
   - 新增 `action_log` 缓冲 + `_record_action(result)` 方法
   - 从 PipelineResult 提取：type（action_type）、actor（command.source）、params（command.params）、success、time_cost、narrative_hints
   - 跳过 noop 动作，传递防御性拷贝到 SettlementContext
7. **`_build_summary.actions`**：`[]` → `list(context.action_log)`
   - MVP 格式：机械数据（type/actor/params/success/time_cost），LLM 可从中推理
   - 设计规范 §2.1 的 detail/tags 是后续语义深化工作

**测试**：486 passed（484 + 2 新增），21 个现有 ai_osiris 测试零回归

**Phase 3A**（snapshot 补全）：

8. **`_build_player`**：新增方法，player 从 24 字段全量 dump 裁剪为 13 字段
   - 保留核心：character_id, character_name, level, hp, max_hp, gold, character_class, current_area, current_location, guild_rank, ac
   - 裁掉噪声：xp, stats, proficiency_bonus, subclass, class_features, inventory, equipment, spell_slots, known_spells, prepared_spells, concentration, active_effects, save_proficiencies, guild_reputation, class_resources
   - 富化 `active_quests`：从 QuestSlice 提取（AVAILABLE/ACTIVE milestone + 非终态 dynamic quest）
   - 富化 `tags`：从 CharacterRegistry 按 player.character_id 查模板取 tags
9. **`_build_party`**：新增方法，party 从 raw snapshot dict 重塑为 enriched list
   - 每个成员：id + approval（PartySlice） + disposition/relationship_stage（RelationSlice） + name/tags/faction（CharacterRegistry）
   - 与 `_build_npc_entry` 富化模式一致（同源 tags/disposition/faction），增加 party 特有的 approval/relationship_stage
10. **`_build_snapshot` 更新**：`player` 和 `party` 字段改用新方法

**测试**：490 passed（486 + 4 新增），23 个现有 ai_osiris 测试零回归

**Phase 3B**（action enrichment：target + tags）：

11. **`_enrich_actions`**：新增方法，在 `_build_summary` 消费端富化 action_log
    - target 提取：按优先级尝试 params 中的 NPC/实体 key（target, seller_npc, buyer_npc, npc_id, target_npc, character_id, character, container_id, object_id）
    - 类别标签：`_ACTION_CATEGORY_TAGS` 覆盖全部 55 个 command type → 13 个语义类别
    - 内容标签：从 ItemRegistry（tags + type）和 SkillRegistry（school + effect.type）提取，全部大写
    - 无标签时不输出空 tags 字段
12. **`_build_summary` 更新**：`list(context.action_log)` → `cls._enrich_actions(context)`
13. **新增模块常量**：`_TARGET_PARAM_KEYS`（9 个 key 优先级列表）+ `_ACTION_CATEGORY_TAGS`（55 entries）

**测试**：495 passed（490 + 5 新增），27 个现有 ai_osiris 测试零回归

**Phase 3C**（action 语义富化：detail + witnessed_by）：

14. **`_build_action_detail`**：构造自然语言动作描述
    - verb 映射：`_ACTION_VERBS` 覆盖全部 55 个 command type → 过去式动词
    - 拼接逻辑：verb + primary entity（item/spell/skill name）+ preposition + target + modifiers（DC）
    - 名称解析：通过 `_resolve_entity_name` 从 WorldInstance registry 查找人类可读名称
    - narrative_hints 拼接 + 失败标记（`— failed`）
    - 未知 action type 降级为下划线转空格
15. **`_resolve_entity_name`**：通用 registry name 查找，无匹配时返回原始 ID
16. **`_collect_witness_ids`**：预计算 party members + 同区域 NPC（AreaSlice 动态源 + CharacterRegistry 静态源）
    - 一次计算，所有 action 共享；排除每个 action 的 target（直接参与者不算目击者）
17. **`_enrich_actions` 更新**：每个 action 追加 detail（总是）+ witnessed_by（非空时）
18. **新增模块常量**：`_ACTION_VERBS`（55 entries）+ `_TARGET_PREPOSITIONS`（8 entries，非默认介词）

**测试**：500 passed（495 + 5 新增），32 个现有 ai_osiris 测试零回归
