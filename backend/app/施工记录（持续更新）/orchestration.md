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
