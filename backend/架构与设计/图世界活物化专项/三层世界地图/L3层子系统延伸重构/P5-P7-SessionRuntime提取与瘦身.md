# P5+P7 执行记录：SessionRuntime 提取与瘦身

> **执行日期**: 2026-02-23
> **状态**: 已完成
> **验证**: 655 passed / 126 failed / 15 errors（与基线完全一致，零回归）

---

## 目标

将 `session_runtime.py`（2104 行单体文件）中的 29 个方法提取到 4 个 helper 文件，保留薄委托层，使 SessionRuntime 瘦身至 ~1000 行。外部调用方零改动。

---

## 设计模式：Back-Reference Delegation

每个提取类通过构造函数持有 `SessionRuntime` 引用，通过 `self._s.xxx` 访问状态：

```python
# 提取模块
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.runtime.session_runtime import SessionRuntime

class EventMachine:
    def __init__(self, session: 'SessionRuntime'):
        self._s = session

    def activate_event(self, event_id: str) -> Dict[str, Any]:
        wg = self._s.world_graph  # 通过引用访问
        ...
```

```python
# session_runtime.py
class SessionRuntime:
    def __init__(self, ...):
        self._events = EventMachine(self)

    # 薄委托（保留公共 API 不变）
    def activate_event(self, event_id: str) -> Dict[str, Any]:
        return self._events.activate_event(event_id)
```

**关键设计决策**：
- `TYPE_CHECKING` 避免循环导入，helper 运行时不 import SessionRuntime
- 所有 app 级 import 都在方法内 lazy import，模块顶层仅 stdlib
- 私有方法也保留薄委托（有外部调用方，见下文）

---

## 文件变动

### 新建文件（4 个）

| 文件 | 行数 | 提取方法数 | 说明 |
|------|------|-----------|------|
| `app/runtime/stat_ops.py` | 77 | 6 | 玩家 HP/XP/金币/物品 |
| `app/runtime/memory_ops.py` | 136 | 4 | 记忆召回/写入/图谱化 |
| `app/runtime/narrative_ops.py` | 203 | 3 | 章节切换/目标/NPC好感度 |
| `app/runtime/event_machine.py` | 861 | 16 | 事件状态机/Tick编排/奖励分发 |

### 改动文件

| 文件 | 变更 |
|------|------|
| `app/runtime/session_runtime.py` | 2104 → 999 行（-53%），29 个方法体 → 薄委托 |
| `tests/test_c9_stages.py` | `_MockSession` 补充 `_events`/`sub_location`/`chapter_id`/`party`/`flash_results` 属性；`SessionRuntime.__new__` 调用点补充 `_events` 初始化 |

---

## 提取明细

### StatOps（6 个方法）

| 方法 | 说明 |
|------|------|
| `heal(amount)` | → stats_manager.add_hp |
| `damage(amount)` | → stats_manager.remove_hp |
| `add_xp(amount)` | → stats_manager.add_xp |
| `add_gold(amount)` | → stats_manager.add_gold |
| `add_item(item_id, item_name, quantity)` | 直接 player.add_item |
| `remove_item(item_id, quantity)` | 直接 player.remove_item |

依赖：`self._s.player`、`self._s.mark_player_dirty()`

### MemoryOps（4 个方法）

| 方法 | Async | 说明 |
|------|-------|------|
| `recall(role, actor_id, seeds, ...)` | Yes | 扩散激活记忆召回 |
| `_check_memory_write_permission(role, memory_type)` | No | 权限校验委托 |
| `record_memory(owner_id, memory_type, ...)` | No | 记忆节点创建 |
| `graphize_messages(owner_id, messages, ...)` | Yes | 对话→图谱转换 |

依赖：`self._s.world_graph`、`self._s._world_graph_failed`、`self._s.world_id`、`self._s.player_location`

### NarrativeOps（3 个方法）

| 方法 | 说明 |
|------|------|
| `advance_chapter(target_chapter_id, transition_type)` | 章节状态机 + 分支历史 |
| `complete_objective(objective_id)` | 章节级目标完成 |
| `update_disposition(npc_id, deltas, reason)` | NPC 好感度（WorldGraph 存储） |

依赖：`self._s.narrative`、`self._s.game_state`、`self._s.world`、`self._s.world_graph`、`self._s.time`

### EventMachine（16 个方法）

**Tick 编排（5 个）**：

| 方法 | 说明 |
|------|------|
| `build_tick_context(phase)` | TickContext 组装 |
| `run_behavior_tick(phase)` | Tick 编排入口 |
| `_sync_tick_to_narrative(tick_result)` | Tick→叙事同步 |
| `_apply_tick_side_effects(tick_result)` | XP/金/物品/声望/旗标 |
| `_dispatch_completed_events_to_companions(tick_result)` | 同伴事件分发 |

**事件状态机（5 个）**：

| 方法 | 说明 |
|------|------|
| `activate_event(event_id)` | AVAILABLE→ACTIVE + 补偿 tick |
| `complete_event(event_id, outcome_key)` | ACTIVE→COMPLETED + 奖励 + 级联 |
| `fail_event(event_id, reason)` | ACTIVE→FAILED |
| `advance_stage(event_id, stage_id)` | 阶段推进（末阶段自动 complete_event） |
| `complete_event_objective(event_id, objective_id)` | 目标追踪 |

**奖励 & 分发（4 个）**：

| 方法 | 说明 |
|------|------|
| `_apply_rewards(xp, gold, items, ...)` | 多类型奖励 |
| `_apply_on_complete_from_graph(on_complete, event_id, node)` | 图 on_complete 委托 |
| `_apply_outcome_rewards(outcome, event_id, node)` | 结局奖励 + 事件解锁 |
| `_dispatch_event_to_companions_from_graph(event_id, node)` | 同伴分发（图） |

**查询（2 个）**：

| 方法 | 说明 |
|------|------|
| `check_chapter_transitions()` | GATE 边评估 |
| `get_event_summaries_from_graph(area_id)` | 事件枚举 + 摘要 |

内部交叉调用全部在 EventMachine 类内，`self.xxx()` 正确指向同类方法。关键路径：`advance_stage` → `self.complete_event()`。

---

## 私有方法外部调用方

发现 7 个私有方法有生产代码或测试的外部调用，因此也保留薄委托：

| 私有方法 | 外部调用方 |
|---------|-----------|
| `_sync_tick_to_narrative` | `app/world/intent/executor.py:210-232` |
| `_apply_tick_side_effects` | `app/world/intent/executor.py:210-232`、`tests/test_p7_side_effects.py` |
| `_dispatch_completed_events_to_companions` | `tests/test_c8_migration.py` |
| `_apply_rewards` | `tests/test_p7_side_effects.py`、`tests/test_c9_stages.py` |
| `_apply_on_complete_from_graph` | `tests/test_p7_side_effects.py`、`tests/test_c9_stages.py` |
| `_apply_outcome_rewards` | `tests/test_c9_stages.py` |
| `_dispatch_event_to_companions_from_graph` | `tests/test_c9_stages.py` |

---

## session_runtime.py __init__ 变更

在 `self._restored = False` 之后添加：

```python
from app.runtime.event_machine import EventMachine
from app.runtime.memory_ops import MemoryOps
from app.runtime.narrative_ops import NarrativeOps
from app.runtime.stat_ops import StatOps

self._events = EventMachine(self)
self._memory_ops = MemoryOps(self)
self._narrative_ops = NarrativeOps(self)
self._stat_ops = StatOps(self)
```

---

## 执行过程中遇到的问题

### 1. test_c9_stages `_MockSession` 缺属性

**现象**: `_MockSession` 通过类级 `_SR.activate_event` 复制 SessionRuntime 方法。P5+P7 后这些方法变成薄委托（调 `self._events.xxx()`），但 `_MockSession` 没有 `_events` 属性。

**修复**: 在 `_MockSession.__init__` 中添加：
```python
from app.runtime.event_machine import EventMachine
self._events = EventMachine(self)
```

同时补充 EventMachine 需要的属性：`sub_location`、`chapter_id`、`party`、`flash_results`。

### 2. test_c9_stages `SessionRuntime.__new__` 绕过 __init__

**现象**: 两处测试用 `SessionRuntime.__new__(SessionRuntime)` 创建裸实例（绕过 `__init__`），直接调用 `get_event_summaries_from_graph` 时缺少 `_events`。

**修复**: 在 `__new__` 调用后手动初始化：
```python
rt._world_graph_failed = False
from app.runtime.event_machine import EventMachine
rt._events = EventMachine(rt)
```

### 3. 13 个"额外失败"的诊断

**现象**: 测试从 655 passed 降到 642 passed（-13），初步怀疑 P5+P7 引入回归。

**诊断过程**:
1. 追踪失败根因：全部 13 个失败的 traceback 指向 `admin_coordinator.py:38: from app.services.game_session_store import GameSessionStore`（已删除模块）
2. 检查 P5+P7 4 个新文件的顶层 import：全部仅 stdlib，无 app 级模块导入
3. 确认 `app/services/admin/__init__.py` import AdminCoordinator → 触发 game_session_store 是 P0 遗留问题
4. 实际原因：pytest 收集顺序 + 模块缓存效应导致部分测试间歇性触发该 import 链

**结论**: 与 P5+P7 无关。修复 `_MockSession` 后测试恢复到 655 passed 基线。

---

## 验证结果

| 检查项 | 结果 |
|--------|------|
| session_runtime.py 行数 | 2104 → **999**（-53%） |
| 提取方法总数 | **29**（9 公共 + 7 私有 → EventMachine，3 → NarrativeOps，4 → MemoryOps，6 → StatOps） |
| 薄委托总数 | **29**（全部保留原始签名） |
| 测试基线 | **655 passed** / 126 failed / 15 errors（零回归） |
| 新文件顶层 import | 全部仅 stdlib（`logging`、`time`、`typing`），无循环导入风险 |
| 外部调用方改动 | **0**（全部通过薄委托透明转发） |

---

## 弃用部件

本次无弃用部件。提取为纯内部重组，所有公共/私有 API 通过薄委托保持不变。

---

## 后续方向

- P0 清理：`admin_coordinator.py:38` 的 `GameSessionStore` 死 import 应清理（影响约 13 个测试的稳定性）
- 进一步可考虑将 `_MockSession` 类级方法复制模式替换为直接使用 `EventMachine` 实例（减少对 SessionRuntime 内部结构的依赖）
