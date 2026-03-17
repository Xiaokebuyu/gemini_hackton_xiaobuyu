# Tick 生命周期

## 模块概要

游戏核心循环：玩家行动 → 管线处理 → 时间累积 → 结算 → hook 执行 → SSE 发射。

### 核心模块

| 模块 | 位置 | 职责 |
|------|------|------|
| TickCoordinator | `orchestration/tick_coordinator.py` | Tick 生命周期总控：process() → PipelineOrchestrator → StateDelta 应用 → 累积时间 ≥ 1.0 → SettlementHooks |
| PipelineOrchestrator | `orchestration/pipeline.py` | 单次行动管线：ContextAssembler → ActionDispatcher → RulesEngine |
| ContextAssembler | `orchestration/context_assembler.py` | 构建 8 层稳定上下文 payload（L0-L7） |
| ActionDispatcher | `orchestration/action_dispatcher.py` | StructuredAction → Command 映射 |
| SceneBus | `orchestration/scene_bus.py` | 场景级事件标签和条目管理 |
| SettlementContext | `orchestration/settlement.py` | 结算上下文：state + world + rules_engine + change_log + scene_bus |
| SharedContext | `orchestration/shared_context.py` | 跨管线共享编排上下文 |
| TimeSlice | `state/slices/time.py` | 运行时时钟：day/slot/absolute_tick/accumulated |
| TimeAdvanceHook | `orchestration/hooks/time_advance.py` | 结算后推进游戏时间和时间段 |

### Hook 执行顺序（25 个）

按 HOOK_PRIORITY 排序，数字越小越先执行。关键节点：
- 30: AIOsirisHook（因果/遭遇/感知/事件条件）
- 54-57: 任务完成检测链（Milestone → Unlock → QuestTracking → TaskMonitor）
- 58: XpAdvancement
- 60-63: NPC 行为链（Schedule → Autonomy → SharedExperience → Campfire）
- 65-66: 关系 + Planner
- 75-76: 社交触发（PrivateChat → DirectiveTrigger）
- 80+: 收尾（TimeAdvance → QuestExpiry → SubAreaExpiry → GmNarration → SceneReset）

### 辅助模块

| 模块 | 位置 | 职责 |
|------|------|------|
| defaults.py | `orchestration/defaults.py` | 默认 hook/handler 注册表 |
| models.py | `orchestration/models.py` | HookResult, SSEEvent, TickResult 等编排层模型 |
| interaction.py | `orchestration/interaction.py` | 交互策略校验（game-core 边界） |
| combat_sse.py | `orchestration/combat_sse.py` | v2 战斗 metadata → SSE 转换器 |
