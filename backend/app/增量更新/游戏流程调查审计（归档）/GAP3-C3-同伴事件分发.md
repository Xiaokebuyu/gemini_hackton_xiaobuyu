# GAP-3 C3：同伴事件分发（Companion Event Dispatch）

创建时间：2026-03-06
状态：实现方案，待审批
前置文档：`审计-第三阶段设计vs实现对比.md` GAP-3
设计来源：`编排层设计规范.md` §4.3(C3)、`NPC与队友运行时规范.md` §10.4-10.7

---

## 一、问题陈述

### 设计要求

```
C3: 同伴事件分发
    CompanionInstance 接收本次动作的所有事件
    更新同伴内部状态（记忆摘要、事件日志）
```

§10.4 定义了 `SharedExperience` 结构（type / summary / emotion_tags / critical_moments）。
§10.5 定义了记忆选择算法（今日重大 > 7 日内未回忆 > 加权随机）。
§10.7 定义了记忆驱动战斗行为（"你曾经救过我" → 行为偏好）。

### 当前实现

CompanionInstance 是最小 wrapper（`actor_id` + `context_window` + 交互遥测），**没有事件日志，没有事件分发入口**。

### 已有补偿链（5 个机制，覆盖 ~70%）

| 机制 | 覆盖范围 | 粒度 | 局限 |
|------|---------|------|------|
| SharedExperienceHook (P62) | combat/quest/rest 3 种 | 格结算级 | 仅 3 种事件类型，模板 summary |
| CampfireHook (P63) | 长休后回忆 + 对话 | 长休触发 | 选择范围受限于 SharedExperience |
| Teammate Agent 反应 | SceneBus 全部条目 | tick 级 | 知识留在 LLM context，不固化 |
| CompanionRuntimeManager | context_window 持久对话历史 | 对话级 | 仅对话，不含游戏事件 |
| SceneBus | 所有场景条目 | 格级（重置后丢失） | 短命，格结算后清空 |

### 真正缺失的

1. **结构化事件日志**：CompanionInstance 没有 `event_log` — 同伴不知道"上一轮玩家做了什么"
2. **事件分发入口**：pipeline 完成后没有"告知同伴发生了什么"的步骤
3. **精细化经历记录**：SharedExperienceHook 仅检测 3 种类型（combat/quest/rest），设计要求 8 种（+ exploration / dialogue / crisis / celebration / loss / betrayal）
4. **消费链断裂**：write_episode 只接收 `messages`（对话溢出），不包含游戏事件上下文；CampfireHook 只能从 3 种粗粒度记忆中选择

### 受损场景

- 同伴不记住非战斗/任务/休息类事件（搜索容器、发现陷阱、交易、施法等）
- 长期行为模式记忆无法形成（"你从来不跟我商量就做决定"）
- §10.7 记忆驱动战斗行为无实现基础（"你上次没有救我" → 不信任）
- CampfireHook 选择范围窄，营火对话容易重复

---

## 二、架构分析

### 事件分发的生命周期定位

```
TickCoordinator.process()
    │
    ├─ pipeline.process()          # A 阶段 + B 阶段 + C1(event check)
    │   └─ 返回 PipelineResult
    │
    ├─ _record_action(result)      # 已有
    ├─ _emit_action_tags(result)   # 已有（Phase 0）
    │
    ├─ [NEW] _dispatch_companion_events(result)   ← C3 插入点
    │
    ├─ accumulate(time_cost)
    └─ settlement loop
```

C3 在 `_emit_action_tags` 之后、`accumulate` 之前。此时：
- PipelineResult 完整（含 engine 结果 + agent 反应 + event transitions）
- SceneBus 已写入 ENGINE 标签（_emit_action_tags 的产出）
- 时间尚未累积（同伴看到的是"刚发生的事"，不是"已过去的事"）

### 职责划分

| 组件 | 职责 |
|------|------|
| `companion_runtime.py` | `TickRecord` 数据类 + `CompanionInstance.receive_tick()` + `CompanionRuntimeManager.dispatch_tick()` |
| `tick_coordinator.py` | `_dispatch_companion_events()` — 从 PipelineResult + SceneBus 收集事件，调用 manager |
| SharedExperienceHook | 不变 — 格结算级粗粒度记录，与 C3 tick 级记录互补 |
| CampfireHook | Phase 2 扩展 — 查询 event_log 获取更丰富的记忆候选 |
| write_episode | Phase 2 扩展 — context 参数包含 event_log 摘要 |

### 为什么不用 PipelineHook("after_agents")

设计文档将 C3 标注在 Pipeline 内部，但：
1. PipelineOrchestrator 不持有 CompanionRuntimeManager（隔离红线）
2. 需要 SceneBus ENGINE 标签（由 TickCoordinator._emit_action_tags 写入，pipeline 返回后才执行）
3. 与 GAP-4 一致 — B 阶段也从 pipeline 移到 TickCoordinator，C3 遵循同样模式

### TickRecord vs SharedExperience 的关系

| 维度 | TickRecord（C3 新增） | SharedExperience（P62 已有） |
|------|----------------------|---------------------------|
| 粒度 | 每个 tick | 每次格结算 |
| 生命周期 | 会话内（内存，不持久化） | 持久化到 PartySlice |
| 类型范围 | 所有动作类型 | combat/quest/rest |
| 消费者 | write_episode / teammate context / 行为决策 | CampfireHook / 关系阶段判定 |
| 数据量 | 最近 50 条（滑动窗口） | 无限增长（PartySlice 持久化） |

两者**互补**。TickRecord 是短期工作记忆，SharedExperience 是长期持久记忆。

---

## 三、实现方案

### Phase 1：核心机制（事件分发 + 事件日志）

#### 3.1 `companion_runtime.py` — 新增 TickRecord + 扩展 CompanionInstance

```python
# companion_runtime.py 新增（文件顶部 import 区）
from dataclasses import dataclass, field
from typing import Any

MAX_EVENT_LOG = 50  # 滑动窗口大小

@dataclass(slots=True)
class TickRecord:
    """Structured record of one tick as observed by a companion.

    Short-lived working memory — survives within a session but is not
    persisted.  Long-term memory is handled by SharedExperienceHook
    (PartySlice) and write_episode (knowledge graph).
    """

    tick: int
    action_type: str        # Player action: "skill_check", "navigate", "attack", ...
    success: bool
    summary: str            # First narrative_hint or action_type fallback
    tags: list[str] = field(default_factory=list)           # ENGINE semantic tags
    involved_npcs: list[str] = field(default_factory=list)  # NPC IDs in this tick
    has_rolls: bool = False                                 # Dice were rolled
    event_transitions: list[str] = field(default_factory=list)  # Event IDs that changed state
```

CompanionInstance 扩展：

```python
@dataclass(slots=True)
class CompanionInstance:
    actor_id: str
    context_window: ContextWindow
    last_interaction_tick: int = 0
    interaction_count: int = 0
    event_log: list[TickRecord] = field(default_factory=list)  # [NEW]

    def record_interaction(self, current_tick: int) -> None:
        self.last_interaction_tick = max(self.last_interaction_tick, current_tick)
        self.interaction_count += 1

    # [NEW]
    def receive_tick(self, record: TickRecord) -> None:
        """Append a tick record and enforce sliding window cap."""
        self.event_log.append(record)
        if len(self.event_log) > MAX_EVENT_LOG:
            self.event_log = self.event_log[-MAX_EVENT_LOG:]

    # [NEW]
    def get_recent_events(self, n: int = 10) -> list[TickRecord]:
        """Return last N events (most recent last)."""
        return list(self.event_log[-n:])

    # [NEW]
    def get_events_by_tag(self, tag: str) -> list[TickRecord]:
        """Return events containing a specific tag."""
        return [e for e in self.event_log if tag in e.tags]
```

CompanionRuntimeManager 扩展：

```python
class CompanionRuntimeManager:
    # ... 现有代码不变 ...

    # [NEW]
    def dispatch_tick(self, record: TickRecord) -> None:
        """Push a tick record to all active companion instances."""
        for instance in self._pool.values():
            instance.receive_tick(record)
```

snapshot() 也需更新，包含 event_log 长度：

```python
    def snapshot(self) -> list[dict[str, int | str]]:
        return [
            {
                "actor_id": instance.actor_id,
                "interaction_count": instance.interaction_count,
                "last_interaction_tick": instance.last_interaction_tick,
                "event_log_size": len(instance.event_log),  # [NEW]
            }
            for instance in self._pool.values()
        ]
```

#### 3.2 `tick_coordinator.py` — 新增 _dispatch_companion_events

```python
# tick_coordinator.py 新增 import
from app.game_core.narrative.companion_runtime import TickRecord

# process() 中插入调用（在 _emit_action_tags 之后、accumulate 之前）
async def process(self, input_payload, event_sink=None) -> PipelineResult:
    ...
    self._record_action(result)
    self._emit_action_tags(result)
    self._dispatch_companion_events(result)   # [NEW] C3
    self.accumulate(result.time_cost)
    ...

# [NEW] 新增方法
def _dispatch_companion_events(self, result: PipelineResult) -> None:
    """C3: Dispatch tick events to all active companion instances.

    Collects a structured TickRecord from PipelineResult + SceneBus,
    then pushes to all companions via CompanionRuntimeManager.
    Only dispatches on successful actions (failed actions are noise).
    """
    if self.companion_manager is None:
        return
    if not result.success:
        return
    if not self.state.has_slice("party"):
        return
    members = self.state.party.get_members()
    if not members:
        return

    tick = self.state.time.current_tick if self.state.has_slice("time") else 0
    self.companion_manager.sync_members(
        list(members.keys()), current_tick=tick,
    )

    # Collect ENGINE semantic tags from SceneBus
    tags: list[str] = []
    involved: list[str] = []
    for entry in self.scene_bus.snapshot().get("entries", []):
        if not isinstance(entry, dict):
            continue
        source = entry.get("source", "")
        if source == "ENGINE":
            tags.extend(entry.get("tags", []))
        elif source.startswith("NPC:") or source.startswith("npc:"):
            npc_id = source.split(":", 1)[1]
            if npc_id not in involved:
                involved.append(npc_id)

    # Collect event transitions from SSE events
    transitions = [
        e.payload.get("event_id", "")
        for e in result.sse_events
        if e.event_type == "event_state_changed"
        and e.payload.get("event_id")
    ]

    summary = (
        result.narrative_hints[0]
        if result.narrative_hints
        else result.action_type
    )

    record = TickRecord(
        tick=tick,
        action_type=result.action_type,
        success=result.success,
        summary=summary,
        tags=list(set(tags)),
        involved_npcs=involved,
        has_rolls=bool(result.rolls),
        event_transitions=transitions,
    )
    self.companion_manager.dispatch_tick(record)
```

### Phase 2：消费者接入（下一阶段，本次不实施）

列出后续消费者接入方案，确保 Phase 1 的数据结构设计兼容这些场景。

#### 2a. write_episode 上下文增强

当 ContextWindow 溢出触发 write_episode 时，将 event_log 摘要作为 context 附加信息：

```python
# agent_orchestration.py _write_episode_for_world() 中
context = {
    "world": world,
    "recent_events": [     # [NEW] 来自 CompanionInstance.event_log
        {"action": r.action_type, "summary": r.summary, "tags": r.tags}
        for r in companion.get_recent_events(5)
    ],
}
await graph.write_episode(actor_id=actor_id, messages=messages, context=context)
```

**好处**：LLM 三元组提取时有游戏事件上下文，可以提取"玩家在战斗中救了同伴"等关系性三元组。

#### 2b. CampfireHook 记忆扩展

CampfireHook 当前只从 `PartySlice.shared_experiences` 选择记忆（3 种类型）。接入 event_log 后可以：

```python
# campfire.py _select_memory() 扩展
def _select_memory(member_id, context, today, companion_instance=None):
    # 原有逻辑：从 shared_experiences 选择
    experiences = context.state.party.get_shared_experiences(with_character=member_id)

    # [NEW] 从 event_log 补充今日事件
    if companion_instance is not None:
        for record in companion_instance.get_recent_events(20):
            if record.tick >= today_start_tick:
                experiences.append({
                    "type": _infer_type(record),
                    "summary": record.summary,
                    "day": today,
                    "participants": [member_id],
                    "critical_moment": record.has_rolls,
                    "emotion_tags": _infer_emotions(record.tags),
                })

    # 原有评分逻辑不变
    return max(experiences, key=_score) if experiences else None
```

**好处**：营火对话可以提及"你刚才搜索了那个宝箱"等非战斗/任务事件，丰富对话质量。

**注意**：需要解决 SettlementHook 如何访问 CompanionRuntimeManager 的问题。可通过 SettlementContext 扩展或注入。

#### 2c. Teammate 上下文层增强

在 ContextBuilder 的 L 层中加入 event_log 摘要：

```python
# context_builder.py
def _build_companion_memory_context(companion: CompanionInstance) -> str:
    recent = companion.get_recent_events(5)
    if not recent:
        return ""
    lines = ["## Recent observations"]
    for r in recent:
        lines.append(f"- [{r.action_type}] {r.summary}")
    return "\n".join(lines)
```

**好处**：队友 Agent 反应时不仅看到 SceneBus（当前格），还能回忆前几轮发生的事。

#### 2d. SharedExperienceHook 类型扩展

从 3 种扩展到设计要求的 8 种：

| 新类型 | 检测条件 |
|--------|---------|
| exploration | NAVIGATION tag + 首次到达区域 |
| dialogue | NPC: 前缀 SceneBus 条目数 >= 3 |
| crisis | event_state_changed + 关键事件 |
| celebration | advance_quest + 重大任务完成 |
| loss | 同伴倒下 / 关键物品丢失 |
| betrayal | 关系阶段负向跃迁 |

**注意**：这是 SharedExperienceHook 的独立优化，不依赖 C3。但 C3 的 TickRecord 提供了更丰富的检测信号。

---

## 四、变更文件清单

### Phase 1（本次实施）

| 文件 | 变更类型 | 变更内容 |
|------|----------|----------|
| `app/game_core/narrative/companion_runtime.py` | **修改** | 新增 `TickRecord` dataclass；扩展 `CompanionInstance`（`event_log` + `receive_tick()` + `get_recent_events()` + `get_events_by_tag()`）；扩展 `CompanionRuntimeManager`（`dispatch_tick()` + snapshot 更新） |
| `app/game_core/orchestration/tick_coordinator.py` | **修改** | 新增 `_dispatch_companion_events()` 方法 + `process()` 中插入调用 + import `TickRecord` |
| `tests/test_companion_event_dispatch.py` | **新建** | C3 专项测试（见第五节） |

### 不变更的文件

| 文件 | 原因 |
|------|------|
| `pipeline.py` | C3 在 TickCoordinator 层，不进入 pipeline |
| `shared_experience.py` | P62 不变，Phase 2 再扩展 |
| `campfire.py` | P63 不变，Phase 2 再接入 event_log |
| `agent_orchestration.py` | write_episode 不变，Phase 2 再增强 context |
| `models.py` | PipelineResult 不变，C3 只读 |
| `scene_bus.py` | 只读 snapshot，不修改 |

---

## 五、测试计划

### 5.1 核心测试用例

```
test_tick_record_creation
    构造 TickRecord，验证字段赋值和默认值

test_companion_instance_receive_tick
    CompanionInstance.receive_tick() 正确追加到 event_log

test_event_log_sliding_window
    超过 MAX_EVENT_LOG 后自动截断，保留最近 N 条

test_get_recent_events
    返回最近 N 条（不足时返回全部）

test_get_events_by_tag
    按 tag 过滤事件

test_manager_dispatch_tick
    CompanionRuntimeManager.dispatch_tick() 推送到所有活跃实例

test_dispatch_companion_events_in_process
    TickCoordinator.process() 成功动作后，companion event_log 被填充
    验证 TickRecord 包含正确的 action_type/summary/tags

test_dispatch_skipped_on_failure
    动作失败时不分发事件

test_dispatch_skipped_without_companion_manager
    companion_manager 为 None 时安全跳过

test_dispatch_skipped_without_party
    无 party slice 时安全跳过

test_dispatch_collects_engine_tags
    SceneBus ENGINE 条目的 tags 被收集到 TickRecord.tags

test_dispatch_collects_npc_involvement
    SceneBus NPC: 前缀条目的 NPC ID 被收集到 involved_npcs

test_dispatch_collects_event_transitions
    result.sse_events 中 event_state_changed 的 event_id 被收集

test_dispatch_uses_narrative_hint_as_summary
    有 narrative_hints 时用第一条作为 summary

test_dispatch_fallback_summary
    无 narrative_hints 时用 action_type 作为 summary

test_snapshot_includes_event_log_size
    CompanionRuntimeManager.snapshot() 包含 event_log_size 字段
```

### 5.2 测试构造

- 每个 TickCoordinator 测试独立构造（同 test_event_conditions_pipeline.py 模式）
- 使用真实 PipelineOrchestrator + 注册 set_flag action
- CompanionRuntimeManager 直接实例化（无需 mock）
- PartySlice 预填充 1-2 个 member

### 5.3 验证命令

```bash
# C3 专项测试
PYTHONPATH=. pytest tests/test_companion_event_dispatch.py -v

# 全量回归
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v
```

---

## 六、执行序列最终形态

```
TickCoordinator.process()
    │
    ├─ Pipeline.process()                             # A + B + C1
    │     ├─ ContextAssembler.assemble()
    │     ├─ ActionDispatcher / RulesEngine
    │     ├─ run_inline_event_check("post_engine")     # A6
    │     ├─ (optional) Stage B: agent_round_runner
    │     ├─ run_inline_event_check("post_agents")     # C1
    │     └─ return PipelineResult
    │
    ├─ event_sink(result.sse_events)                  # 流式推送
    ├─ _record_action(result)                         # action_log
    ├─ _emit_action_tags(result)                      # Phase 0 ENGINE tags
    │
    ├─ [NEW] _dispatch_companion_events(result)       # C3 同伴事件分发
    │     ├─ sync_members()                           #   确保池同步
    │     ├─ 收集 tags / involved / transitions       #   从 SceneBus + SSEEvents
    │     ├─ 构造 TickRecord                          #   结构化记录
    │     └─ manager.dispatch_tick(record)            #   推送到所有同伴
    │
    ├─ accumulate(time_cost)
    │
    └─ while check_settlement():                      # 格结算
          ├─ P10: ScheduledEventHook
          ├─ ...
          ├─ P50: EventConditionHook (兜底)
          ├─ P62: SharedExperienceHook (粗粒度持久记忆)
          ├─ P63: CampfireHook (消费持久记忆)
          ├─ P65: RelationshipHook
          └─ P90: SceneBusResetHook
```

---

## 七、风险与边界

### 7.1 性能

`_dispatch_companion_events` 是纯内存操作（SceneBus snapshot 遍历 + list append），无 IO、无 LLM。典型 SceneBus 条目 < 20 条，party 成员 <= 4。**耗时可忽略**（< 0.1ms）。

### 7.2 sync_members 的重复调用

Stage B runner（agent_orchestration）已在生成 teammate 反应前 sync_members。C3 再调一次是为了处理"无 stage_b_runner 时也能保持池同步"的场景。sync_members 是幂等的（dict diff），重复调用无副作用。

### 7.3 event_log 不持久化

TickRecord 存在 CompanionInstance 内存中，会话结束/重启后丢失。这是 **by design**：
- 短期工作记忆（50 条滑动窗口）不值得持久化
- 长期记忆由 SharedExperience（PartySlice）和 write_episode（知识图谱）处理
- 会话重启 = "醒来后短期记忆清空"，合理

### 7.4 finalize_external_turn 未接入

NPC 对话/私聊产生的事件暂不分发给同伴。原因：
- finalize_external_turn 不持有 PipelineResult，缺少 action_type/narrative_hints
- 对话事件已通过 write_episode 进入知识图谱
- Phase 2 可在应用层（gameplay.py）补充 dispatch，传入对话摘要

### 7.5 与现有测试的兼容性

新增 `_dispatch_companion_events` 调用在 process() 中：
- companion_manager 为 None 时直接 return，对现有测试零影响
- 现有测试不构造 CompanionRuntimeManager → 不触发新逻辑
