# GAP-1：EventEngine 主管线调用补齐（A6 + C1 + External）

创建时间：2026-03-06
状态：实现方案，已审批
前置文档：`审计-第三阶段设计vs实现对比.md` GAP-1
设计来源：`编排层设计规范.md` SS4.1(A6)、SS4.3(C1)、SS8.4

---

## 一、问题陈述

### 设计要求

EventEngine.check_conditions() 在每个 tick 中应被调用 4 次：

```
A6: 主管线引擎执行后（pre-check）       — delta 已应用、Agent 未运行
C1: 主管线 Agent 操作后（post-check）    — agent_round_hooks 执行完毕、时间尚未累积
P10: 格结算延时事件（check_scheduled）    — 已实现
P50: 格结算条件检查（check_conditions）   — 已实现
```

### 当前实现

仅 P10 + P50（2 次/tick）。A6 和 C1 完全缺失。
此外 `finalize_external_turn()`（interact/private_chat 管线）也没有事件条件检查。

### 影响

1. **事件响应延迟**：引擎执行后立即满足的事件条件（例：玩家 pick_up 关键物品 → item_obtained 满足），要等到下次格结算 P50 才被检测。最坏延迟 5 个 1/6 动作。
2. **Agent 无法感知即时事件**：A6 的设计意图是"满足则转换事件状态 → 写入 SceneBus（Agent 可见）"。缺失 A6 导致 GM/NPC/Teammate Agent 在 B 阶段看不到刚触发的事件变化。
3. **Agent 工具效果延迟**：NPC 工具调用（advance_quest → set_flag）的状态变化同样要等 P50，因果链断裂。
4. **独立管线遗漏**：interact/private_chat 中 NPC 设的 flag，如果 1/6 格未触发结算，事件变化完全丢失到下次动作。

---

## 二、架构分析

### 为什么 A6/C1 不在 PipelineOrchestrator 里

设计文档将 A6 标注在 A 阶段末尾、C1 在 C 阶段开头。但实际实现中：

- **delta 应用在 TickCoordinator**（pipeline 返回 PipelineResult.delta，由 TickCoordinator._apply_delta() 写入状态）
- **B 阶段在 TickCoordinator.agent_round_hooks**

因此 A6/C1 必须放在 TickCoordinator.process() 中。

### 职责归属（避免 god file）

- `tick_coordinator.py`（323 行）— 格生命周期管理，不应承担事件评估逻辑
- `event_engine.py`（531 行）— "可复用的事件条件评估核心"，正是放此逻辑的地方

**方案**：在 `event_engine.py` 中新增一个 standalone 函数 `run_inline_event_check()`，封装"评估 → 转换 → 执行 on_trigger"。TickCoordinator 只做一行调用。

### A6/C1 与 P50 的关系

| 维度 | P50 (EventConditionHook) | A6/C1/External |
|------|--------------------------|----------------|
| 调用时机 | 格结算期间 | 格内动作期间 |
| 上下文 | SettlementContext | 直接参数注入 |
| Evaluator | 可注入 | 固定 BasicEventConditionEvaluator |
| 重复触发风险 | 无 — 已转换的事件不再匹配 locked/dormant | 无 |
| 目的 | 兜底 | 即时响应 + 写入 SceneBus 供 Agent 消费 |

两者**互补**。A6/C1 是"快速预检"，P50 是"完整兜底"。

---

## 三、实现方案

### 3.1 新增函数：`event_engine.run_inline_event_check()`

在 `event_engine.py` 底部新增。职责单一：评估 → 转换 → 执行 on_trigger → 返回 SSE。

```python
# event_engine.py 底部新增

def run_inline_event_check(
    *,
    state: StateContainer,
    world: WorldInstance,
    rules_engine: "RulesEngine",
    apply_delta: Callable[["StateDelta | None"], None],
    change_log: list["StateChange"],
    scene_bus: "SceneBus",
    label: str,
) -> list[dict[str, Any]]:
    """Evaluate event conditions, apply transitions, execute on_trigger commands.

    Standalone function shared by TickCoordinator (A6/C1/External) calls.
    Returns raw SSE payloads (caller wraps as SSEEvent).

    This is the "inline" counterpart to EventConditionHook (P50).
    P50 is the full-featured settlement hook with LLM evaluator support
    and heavy normalization. This function uses BasicEventConditionEvaluator
    only, for fast deterministic checks between settlement rounds.
    """
    if not state.has_slice("events"):
        return []

    evaluator = BasicEventConditionEvaluator()
    decision = evaluator.evaluate(state, world)

    sse_payloads: list[dict[str, Any]] = []

    # 1. Apply state transitions
    for transition in decision.transitions:
        if transition.to_state not in _SUPPORTED_STATES:
            continue
        state.events.set_state(
            transition.event_id,
            transition.to_state,
            patch=transition.patch,
        )
        change = StateChange(
            slice="events",
            operation="set",
            path=f"state.{transition.event_id}",
            value=transition.to_state,
        )
        change_log.append(change)
        scene_bus.record_state_change(change)
        sse_payloads.append({
            "event_id": transition.event_id,
            "from_state": transition.from_state,
            "to_state": transition.to_state,
            "reason": transition.reason,
            "source": label,
        })

    # 2. Execute on_trigger commands
    for raw_cmd in decision.commands:
        cmd = _coerce_event_command(raw_cmd)
        if cmd is None:
            continue
        from app.game_core.rules.models import ExecuteResult
        result = rules_engine.execute(cmd, state, world)
        if result.success and result.delta is not None:
            apply_delta(result.delta)

    return sse_payloads


def _coerce_event_command(raw: Any) -> "Command | None":
    """Convert on_trigger dict/Command to a validated Command, or None."""
    if isinstance(raw, Command):
        return raw if raw.type in _ALLOWED_COMMAND_TYPES else None
    if not isinstance(raw, Mapping):
        return None
    cmd_type = _coerce_string(raw.get("type"))
    if not cmd_type or cmd_type not in _ALLOWED_COMMAND_TYPES:
        return None
    raw_params = raw.get("params", {})
    params = _normalize_mapping(raw_params) if isinstance(raw_params, Mapping) else {}
    return Command(type=cmd_type, params=params, source="system")
```

**import 处理**：函数签名中的 `RulesEngine`、`StateDelta`、`StateChange`、`SceneBus` 使用 TYPE_CHECKING guard 或字符串注解。`Command` 已在文件顶部 import。`StateContainer` 和 `WorldInstance` 也已有。需要新增的 import：

```python
# event_engine.py 顶部新增
from typing import Callable
from app.game_core.state.delta import StateChange

# TYPE_CHECKING guard 新增
if TYPE_CHECKING:
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.rules import RulesEngine
    from app.game_core.state import StateDelta
```

### 3.2 修改 `tick_coordinator.py` — process() 插入 A6/C1

TickCoordinator 只做一行调用，不承担评估逻辑。

```python
# tick_coordinator.py 新增 import
from app.game_core.orchestration.event_engine import run_inline_event_check
```

process() 修改（标注 [NEW] 的行）：

```python
async def process(self, input_payload, event_sink=None) -> PipelineResult:
    shared = SharedContext(...)
    result = await self.pipeline.process(input_payload, shared)
    if event_sink is not None:
        for event in result.sse_events:
            await event_sink(event)
    if result.success and result.delta is not None:
        self._apply_delta(result.delta)
    self._record_action(result)
    self._emit_action_tags(result)

    # [NEW] A6: EventEngine post-engine pre-check
    if result.success:
        a6_payloads = run_inline_event_check(
            state=self.state,
            world=self.world,
            rules_engine=self.rules_engine,
            apply_delta=self._apply_delta,
            change_log=self.change_log,
            scene_bus=self.scene_bus,
            label="post_engine",
        )
        for payload in a6_payloads:
            evt = SSEEvent("event_state_changed", payload)
            result.sse_events.append(evt)
            if event_sink is not None:
                await event_sink(evt)

    # B stage: Agent reactions (existing code, unchanged)
    if result.success and self.agent_round_hooks:
        for hook in self.agent_round_hooks:
            ...  # unchanged

    # [NEW] C1: EventEngine post-agents check
    if result.success:
        c1_payloads = run_inline_event_check(
            state=self.state,
            world=self.world,
            rules_engine=self.rules_engine,
            apply_delta=self._apply_delta,
            change_log=self.change_log,
            scene_bus=self.scene_bus,
            label="post_agents",
        )
        for payload in c1_payloads:
            evt = SSEEvent("event_state_changed", payload)
            result.sse_events.append(evt)
            if event_sink is not None:
                await event_sink(evt)

    self.accumulate(result.time_cost)
    while self.check_settlement():
        ...  # unchanged
    return result
```

### 3.3 修改 `finalize_external_turn()` — 补齐 External 检查

NPC interact/private_chat 管线通过此方法归入 tick 生命周期。在 accumulate 之前插入一次检查。

```python
async def finalize_external_turn(
    self,
    time_cost: float,
    event_sink: Callable[[SSEEvent], Awaitable[None]] | None = None,
) -> list[SSEEvent]:
    collected_events: list[SSEEvent] = []

    # [NEW] Post-external event check (NPC tools may have set flags)
    ext_payloads = run_inline_event_check(
        state=self.state,
        world=self.world,
        rules_engine=self.rules_engine,
        apply_delta=self._apply_delta,
        change_log=self.change_log,
        scene_bus=self.scene_bus,
        label="post_external",
    )
    for payload in ext_payloads:
        evt = SSEEvent("event_state_changed", payload)
        collected_events.append(evt)
        if event_sink is not None:
            await event_sink(evt)

    self.accumulate(time_cost)
    while self.check_settlement():
        ...  # unchanged (existing code)
    return collected_events
```

---

## 四、变更文件清单

| 文件 | 变更类型 | 变更内容 |
|------|----------|----------|
| `app/game_core/orchestration/event_engine.py` | **修改** | 新增 `run_inline_event_check()` + `_coerce_event_command()` + 必要 import |
| `app/game_core/orchestration/tick_coordinator.py` | **修改** | `process()` 插入 A6/C1；`finalize_external_turn()` 插入 External 检查；新增 1 行 import |
| `tests/test_event_conditions_pipeline.py` | **新建** | A6/C1/External 专项测试 |

### 不变更的文件

- `event_condition.py` (EventConditionHook) — P50 逻辑不变，保持兜底
- `pipeline.py` — 不修改
- `settlement.py` — 不修改
- `deps.py` — 不修改

---

## 五、测试计划

### 5.1 核心测试用例

```
test_a6_fires_after_engine_delta
    引擎执行 pick_up → item_obtained 满足 → 事件立即转换 (source="post_engine")

test_a6_writes_to_scene_bus
    事件转换后 SceneBus 记录 state_change，Agent 可通过 L5 看到

test_c1_fires_after_agent_round
    Agent 工具 set_flag → flag_set 满足 → 事件转换 (source="post_agents")

test_a6_and_p50_no_double_trigger
    A6 已转换的事件在 P50 不重复触发（event_state_changed SSE 只出现 1 次）

test_a6_on_trigger_commands_execute
    on_trigger 事件的命令在 A6 立即执行（事件 → resolved + 任务推进）

test_a6_skipped_on_failed_action
    动作失败时 A6 不运行

test_c1_runs_even_without_agent_hooks
    无 agent_round_hooks 时 C1 仍运行

test_no_events_slice_safe
    无 events slice 时安全返回空

test_external_check_in_finalize
    finalize_external_turn() 中事件条件被检查

test_run_inline_event_check_standalone
    直接测试 run_inline_event_check() 函数：evaluate → transition → on_trigger
```

### 5.2 测试要点

- 每个测试独立构造 TickCoordinator（不依赖外部 fixture）
- Mock agent_round_hook 用 CallableAgentRoundHook + lambda
- 验证 SSE event_type="event_state_changed" 和 payload.source 字段
- 验证 scene_bus.snapshot()["state_changes"] 和 change_log 的副作用

---

## 六、执行序列最终形态

```
TickCoordinator.process()
    |
    |-- Pipeline.process()                             # A1-A5
    |     |-- ContextAssembler.assemble()               # A3
    |     |-- ActionDispatcher / RulesEngine             # A4
    |     |-- PipelineHook(after_engine)                 # A5
    |     '-- return PipelineResult
    |
    |-- _apply_delta(result.delta)                      # 状态写入
    |-- _record_action(result)
    |-- _emit_action_tags(result)
    |
    |-- [NEW] run_inline_event_check("post_engine")     # A6
    |
    |-- agent_round_hooks (GM -> Teammate)              # B1, B3
    |
    |-- [NEW] run_inline_event_check("post_agents")     # C1
    |
    |-- accumulate(time_cost)
    |
    '-- while check_settlement():                       # 格结算
          |-- P10: ScheduledEventHook
          |-- ...
          |-- P50: EventConditionHook                   # 兜底
          '-- P90: SceneBusResetHook


TickCoordinator.finalize_external_turn()
    |
    |-- [NEW] run_inline_event_check("post_external")   # NPC 工具效果
    |
    |-- accumulate(time_cost)
    |
    '-- while check_settlement():                       # 格结算
          '-- ... (same hooks)
```
