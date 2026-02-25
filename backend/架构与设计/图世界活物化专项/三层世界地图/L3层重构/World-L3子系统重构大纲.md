# World 子系统重构大纲

> **状态**: P1-P7 全部完成（文件迁移 + SessionRuntime 提取瘦身）
> **核心理念**: 整个 L3 就是 World 本身——将全部 L3 机械代码重组为 `app/world/` 下的独立子系统
> **范围**: `app/world/`（14 L3）+ `app/services/`（12 L3）+ `app/combat/`（10 L3）+ `session_runtime.py`（~1,060 行 L3）
> **审计增量（2026-02-23）**: Codex（GPT-5）代码审计补丁，补充重构落地阻塞项

---

## 一、现状

### 1.1 L3 文件全量盘点

47 个 L3 文件分散在 5 个目录，总计 ~18,900 行：

**app/world/**（14 个 L3）

| 文件 | 行数 | 功能域 |
|------|------|--------|
| `graph_builder.py` | 1254 | 图谱构建 |
| `behavior_engine.py` | 1202 | 行为引擎 |
| `world_graph.py` | 998 | 图容器 |
| `intent_executor.py` | 787 | 意图执行 |
| `player_node.py` | 484 | Player 图谱适配 |
| `intent_resolver.py` | 434 | 意图解析 |
| `snapshot.py` | 410 | 图谱快照 |
| `constants.py` | 346 | D&D 规则表 |
| `spreading_activation.py` | 271 | 扩散激活 |
| `scene_bus.py` | 204 | 场景总线 |
| `event_propagation.py` | 194 | 事件传播 |
| `stats_manager.py` | 193 | 属性变更 |
| `recall.py` | 165 | 记忆召回 |
| `models.py` | 444 | 世界类型系统（Shared） |

**app/services/**（12 个 L3，不含已删 store）

| 文件 | 行数 | 功能域 |
|------|------|--------|
| `narrative_service.py` | 1336 | 主线/章节进度追踪 |
| `instance_manager.py` | 606 | NPC 实例池 + LRU |
| `context_window.py` | 502 | NPC 上下文窗口 |
| `character_service.py` | 425 | 角色创建/点买验证 |
| `session_history.py` | 408 | 会话历史缓冲 |
| `party_service.py` | 295 | 队伍生命周期 |
| `ability_check_service.py` | 193 | d20 判定 |
| `teammate_visibility_manager.py` | 194 | 队友可见性规则 |
| `npc_reactor.py` | 163 | NPC 相关度排序 |
| `item_registry.py` | 80 | 物品注册表 |
| `graph_schema.py` | 81 | 图谱 Schema |
| `event_bus.py` | 26 | 事件总线 |
| `time_manager.py` | 266 | 时间管理器 |

**app/services/admin/**（1 个 L3）

| 文件 | 行数 | 功能域 |
|------|------|--------|
| `state_manager.py` | 74 | 会话状态快照 |

**app/runtime/**（5+4 个文件）

| 文件 | 行数 | 功能域 |
|------|------|--------|
| `session_runtime.py` | ~~2232~~ **999** | 会话运行时（瘦身后，29 个薄委托） |
| `event_machine.py` ★ | 861 | 事件状态机/Tick/奖励（从 session_runtime 提取） |
| `narrative_ops.py` ★ | 203 | 章节/目标/好感度（从 session_runtime 提取） |
| `memory_ops.py` ★ | 136 | 记忆召回/写入/图谱化（从 session_runtime 提取） |
| `stat_ops.py` ★ | 77 | 玩家 HP/XP/金币/物品（从 session_runtime 提取） |
| `world_instance.py` | 389 | 世界静态数据注册表 |
| `context_assembler.py` | 336 | 上下文组装 |
| `game_runtime.py` | 73 | 全局运行时单例 |
| `__init__.py` | 24 | 包导出 |

**app/combat/**（10 个 L3）

| 文件 | 行数 | 功能域 |
|------|------|--------|
| `combat_engine.py` | 1567 | 核心战斗逻辑 |
| `enemy_registry.py` | 430 | 敌人注册表 |
| `template_mapper.py` | 245 | 模板映射器 |
| `ai_opponent.py` | 174 | 敌人 AI（规则树） |
| `rules.py` | 166 | 战斗规则 |
| `dice.py` | 90 | 骰子系统 |
| `spatial.py` | 66 | 空间计算 |
| `effects.py` | 55 | 效果系统 |
| `spells.py` | 38 | 法术系统 |
| `constants.py` | 3 | 战斗常量 |

### 1.2 session_runtime.py 中的 L3 代码（P5+P7 已提取）

原 `session_runtime.py` 共 2,104 行，其中约 1,060 行纯 L3 机械逻辑已提取到 4 个 helper 文件：

| Zone | 功能 | 方法数 | 提取到 | 状态 |
|------|------|--------|--------|------|
| PlayerStats | heal/damage/xp/gold/item | 6 | `app/runtime/stat_ops.py`（77 行） | ✅ 已提取 |
| NarrativeOps | advance_chapter/complete_objective/update_disposition | 3 | `app/runtime/narrative_ops.py`（203 行） | ✅ 已提取 |
| EventMachine | activate/complete/fail/advance_stage + 奖励 + tick 编排 | 16 | `app/runtime/event_machine.py`（861 行） | ✅ 已提取 |
| MemoryOps | recall/record_memory/graphize_messages | 4 | `app/runtime/memory_ops.py`（136 行） | ✅ 已提取 |
| Navigation | enter/leave_sublocation | — | 保留在 session_runtime | 待定 |

> 提取后 session_runtime.py 为 **999 行**，保留 29 个薄委托方法。

---

## 二、目标结构

```
app/world/                              ← 全部 L3 的家
  │
  ├── graph/                            ← 图谱容器子系统（5 文件，~3,187 行）
  │   ├── __init__.py
  │   ├── world_graph.py                ← 图容器 + 索引 + dirty（998）
  │   ├── models.py                     ← 类型系统（444）
  │   ├── builder.py                    ← 7步构建工厂（1254）
  │   ├── snapshot.py                   ← 快照序列化（410）
  │   └── schema.py                     ← 图谱 Schema 定义（81）← app/services/graph_schema.py
  │
  ├── events/                           ← 事件生命周期子系统（7 文件，~2,093 行）
  │   ├── __init__.py
  │   ├── behavior_engine.py            ← 条件评估 + 动作执行 + Tick（1202）
  │   ├── propagation.py                ← BFS 事件传播（194）
  │   ├── event_bus.py                  ← 事件总线基础设施（26）← app/services/event_bus.py
  │   ├── state_machine.py              ← ★ activate/complete/fail/advance_stage（~350）
  │   ├── rewards.py                    ← ★ 奖励结算（~195）
  │   └── tick_sync.py                  ← ★ Tick 结果投射（~100）
  │
  ├── memory/                           ← 记忆子系统（4 文件，~691 行）
  │   ├── __init__.py
  │   ├── activation.py                 ← 扩散激活算法（271）
  │   ├── recall.py                     ← 召回编排（165）
  │   └── recorder.py                   ← ★ 记忆写入 + 权限检查（~90）
  │
  ├── player/                           ← 玩家机械子系统（6 文件，~1,780 行）
  │   ├── __init__.py
  │   ├── stats.py                      ← 属性变更（stats_manager 193 + session_runtime 59 合并）
  │   ├── node_view.py                  ← Player 图谱适配器（484）
  │   ├── constants.py                  ← D&D 规则表（346）
  │   ├── character.py                  ← 角色创建/点买/装备（425）← app/services/character_service.py
  │   ├── ability_check.py              ← d20 判定 + 骰限（193）← app/services/ability_check_service.py
  │   └── item_registry.py              ← 物品注册表（80）← app/services/item_registry.py
  │
  ├── narrative/                        ← 叙事子系统（4 文件，~1,546 行）
  │   ├── __init__.py
  │   ├── narrative_service.py          ← 主线/章节/进度追踪（1336）← app/services/narrative_service.py
  │   ├── chapters.py                   ← ★ 章节推进 + 门条件检查（~95）
  │   ├── objectives.py                 ← ★ 目标完成追踪（~50）
  │   └── disposition.py                ← ★ NPC 好感度变更（~65）
  │
  ├── scene/                            ← 场景子系统（1 文件，204 行）
  │   ├── __init__.py
  │   └── scene_bus.py                  ← 回合级叙事总线（204）
  │
  ├── time/                             ← 时间子系统（1 文件，266 行）
  │   ├── __init__.py
  │   └── time_manager.py              ← 时间推进/查询（266）← app/services/time_manager.py
  │
  ├── intent/                           ← 意图子系统（2 文件，1,221 行）
  │   ├── __init__.py
  │   ├── resolver.py                   ← 意图解析（434）
  │   └── executor.py                   ← 意图执行（787）
  │
  ├── combat/                           ← 战斗子系统（10 文件，~2,834 行）← app/combat/ L3 部分
  │   ├── __init__.py
  │   ├── combat_engine.py              ← 核心战斗逻辑（1567）
  │   ├── enemy_registry.py             ← 敌人注册表（430）
  │   ├── template_mapper.py            ← 模板映射器（245）
  │   ├── ai_opponent.py                ← 敌人 AI（174）
  │   ├── rules.py                      ← 战斗规则（166）
  │   ├── dice.py                       ← 骰子系统（90）
  │   ├── spatial.py                    ← 空间计算（66）
  │   ├── effects.py                    ← 效果系统（55）
  │   ├── spells.py                     ← 法术系统（38）
  │   └── constants.py                  ← 战斗常量（3）
  │
  ├── party/                            ← 队伍子系统（1 文件，295 行）
  │   ├── __init__.py
  │   └── party_service.py              ← 队伍生命周期 + 成员管理（295）← app/services/party_service.py
  │
  ├── npc/                              ← NPC 机械子系统（4 文件，~1,465 行）
  │   ├── __init__.py
  │   ├── instance_manager.py           ← NPC 实例池 + LRU 淘汰（606）← app/services/instance_manager.py
  │   ├── context_window.py             ← NPC 上下文窗口 200K（502）← app/services/context_window.py
  │   ├── reactor.py                    ← NPC 相关度排序 + 模板反应（163）← app/services/npc_reactor.py
  │   └── visibility.py                 ← 队友信息可见性规则（194）← app/services/teammate_visibility_manager.py
  │
  ├── __init__.py                       ← world 包顶层导出
  │
  │  ── L1 文件保留在根 ──
  ├── immersive_tools.py                ← L1：30 个沉浸式工具（577）
  ├── gm_extra_tools.py                 ← L1：8 个 GM MCP 依赖工具（409）
  ├── agentic_executor.py               ← L1：Agent 执行器（197）
  └── role_registry.py                  ← L1：角色→工具集映射（64）
```

> ★ = 从 session_runtime.py 提取的 L3 代码
> ← = 从其他目录迁入的文件

---

## 三、各子系统详细设计

### 3.1 graph/ — 图谱容器 ✅

> **执行记录**: [P2-graph图谱容器子系统迁移](L3层子系统延伸重构/P2-graph图谱容器子系统迁移.md)

**职责**: 世界知识结构的存储、索引、查询、Schema、序列化。所有子系统的数据基础。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/world_graph.py` | `world_graph.py` | 998 | 移入 |
| `app/world/models.py` | `models.py` | 444 | 移入 |
| `app/world/graph_builder.py` | `builder.py` | 1254 | 移入+改名 |
| `app/world/snapshot.py` | `snapshot.py` | 410 | 移入 |
| `app/services/graph_schema.py` | `schema.py` | 81 | 迁移+改名 |

**公开 API**:
```python
# graph/__init__.py
from .world_graph import WorldGraph, EdgeChange
from .models import (WorldNode, WorldNodeType, WorldEdgeType, EventStatus,
                     Behavior, Action, TriggerType, ActionType,
                     TickContext, BehaviorResult, TickResult, WorldEvent)
from .builder import GraphBuilder
from .snapshot import SnapshotManager
from .schema import GraphSchema
```

**依赖**: `app/models/narrative.py`（ConditionGroup）, `app/runtime/world_instance.py`（builder 输入）
**被依赖**: 几乎所有子系统

---

### 3.2 events/ — 事件生命周期 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)（文件移动）+ [P5-P7-SessionRuntime提取与瘦身](L3层子系统延伸重构/P5-P7-SessionRuntime提取与瘦身.md)（EventMachine 提取）

**职责**: 事件状态机（6 态转换）、行为引擎 tick、条件评估、动作执行、事件传播、奖励结算、事件总线。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/behavior_engine.py` | `events/behavior_engine.py` | 1202 | 移入 |
| `app/world/event_propagation.py` | `events/propagation.py` | 194 | 移入+改名 |
| `app/services/event_bus.py` | `events/event_bus.py` | 26 | 迁移 |
| session_runtime 16 个方法 | `app/runtime/event_machine.py` ★ | 861 | 提取新建 |

> **实际落地与原方案差异**: 原方案计划将事件方法提取到 `events/state_machine.py`、`rewards.py`、`tick_sync.py` 三个文件。
> 实际采用 **Back-Reference Delegation 模式**，将 16 个方法统一提取到 `app/runtime/event_machine.py`（EventMachine 类），
> 通过 `self._s` 引用 SessionRuntime 状态。SessionRuntime 保留 16 个薄委托方法，外部调用方零改动。
> 这种方式更简单安全（无需重构跨模块依赖关系），且 EventMachine 内部的交叉调用（如 `advance_stage → complete_event`）自然工作。

**EventMachine API**（`app/runtime/event_machine.py`）:
```python
class EventMachine:
    def __init__(self, session: 'SessionRuntime'):
        self._s = session

    # Tick 编排
    def build_tick_context(self, phase="pre") -> Optional[TickContext]: ...
    def run_behavior_tick(self, phase="pre") -> Optional[TickResult]: ...
    def _sync_tick_to_narrative(self, tick_result) -> None: ...
    def _apply_tick_side_effects(self, tick_result) -> None: ...
    def _dispatch_completed_events_to_companions(self, tick_result) -> None: ...

    # 事件状态机
    def activate_event(self, event_id) -> Dict: ...
    def complete_event(self, event_id, outcome_key=None) -> Dict: ...
    def fail_event(self, event_id, reason="") -> Dict: ...
    def advance_stage(self, event_id, stage_id=None) -> Dict: ...
    def complete_event_objective(self, event_id, objective_id) -> Dict: ...

    # 奖励 & 分发
    def _apply_rewards(self, xp, gold, items, ...) -> Dict: ...
    def _apply_on_complete_from_graph(self, on_complete, event_id, node) -> None: ...
    def _apply_outcome_rewards(self, outcome, event_id, node) -> None: ...
    def _dispatch_event_to_companions_from_graph(self, event_id, node) -> None: ...

    # 查询
    def check_chapter_transitions(self) -> Optional[Dict]: ...
    def get_event_summaries_from_graph(self, area_id=None) -> List[Dict]: ...
```

**依赖**: graph/（核心）, player/（奖励结算）, narrative/（事件→叙事同步）

---

### 3.3 memory/ — 记忆 ✅

> **执行记录**: [P3-memory与player子系统迁移](L3层子系统延伸重构/P3-memory与player子系统迁移.md)

**职责**: 语义记忆的写入、检索（扩散激活）、权限控制。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/spreading_activation.py` | `activation.py` | 271 | 移入+改名 |
| `app/world/recall.py` | `recall.py` | 165 | 移入 |
| session_runtime Zone:MemoryOps | `recorder.py` ★ | ~90 | 提取新建 |

> `graphize_messages()` 是 L1（调 LLM），不提取到此子系统

**门面 API**:
```python
class MemorySystem:
    def __init__(self, world_graph): ...

    async def recall(self, role, actor_id, seeds, intent_type=None, limit=10) -> List[Dict]: ...
    def record(self, owner_id, memory_type, name, summary, importance, role, **props) -> str: ...
    def check_write_permission(self, role, memory_type) -> None: ...
```

**依赖**: graph/（唯一）
**被依赖**: immersive_tools.py（recall_experience / record_memory 工具）

---

### 3.4 player/ — 玩家机械 ✅

> **执行记录**: [P3-memory与player子系统迁移](L3层子系统延伸重构/P3-memory与player子系统迁移.md)

**职责**: 玩家数值变更（HP/XP/Gold/物品）、角色创建/点买、d20 判定、物品注册、图谱适配、D&D 规则表。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/stats_manager.py` + session_runtime ★ | `stats.py` | ~252 | 合并 |
| `app/world/player_node.py` | `node_view.py` | 484 | 移入+改名 |
| `app/world/constants.py` | `constants.py` | 346 | 移入 |
| `app/services/character_service.py` | `character.py` | 425 | 迁移+改名 |
| `app/services/ability_check_service.py` | `ability_check.py` | 193 | 迁移+改名 |
| `app/services/item_registry.py` | `item_registry.py` | 80 | 迁移 |

**门面 API**:
```python
class PlayerSystem:
    def __init__(self, world_graph): ...

    @property
    def view(self) -> Optional[PlayerNodeView]: ...

    # 属性变更
    def heal(self, amount) -> Dict: ...
    def damage(self, amount) -> Dict: ...
    def add_xp(self, amount) -> Dict: ...
    def add_gold(self, amount) -> Dict: ...
    def add_item(self, item_id, item_name, quantity=1) -> Dict: ...
    def remove_item(self, item_id, quantity=1) -> Dict: ...

    # 战斗同步
    def sync_combat_rewards(self, combat_payload) -> Dict: ...
```

> `character.py` 和 `ability_check.py` 各自有独立类（CharacterService / AbilityCheckService），
> 暂不合入 PlayerSystem 门面，作为同一子系统内的独立模块存在。

**依赖**: graph/（通过 PlayerNodeView）
**被依赖**: events/（奖励结算）, immersive_tools.py, combat/（player_state）

**前置条件**: character.py 和 ability_check.py 当前 import 已删除的 `CharacterStore`，需先完成 Store 迁移（见第七节）

---

### 3.5 narrative/ — 叙事 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)

**职责**: 主线/章节进度追踪、目标完成、章节转换、NPC 好感度变更。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/services/narrative_service.py` | `narrative_service.py` | 1336 | 迁移 |
| session_runtime Zone:NarrativeOps | `chapters.py` ★ | ~95 | 提取新建 |
| session_runtime Zone:NarrativeOps | `objectives.py` ★ | ~50 | 提取新建 |
| session_runtime Zone:NarrativeOps | `disposition.py` ★ | ~65 | 提取新建 |

> **重叠注意**: narrative_service.py（1336 行）与 session_runtime NarrativeOps zone 存在功能重叠——
> 两处都有 chapter 推进、objective 完成逻辑。迁移时需合并去重。

**门面 API**:
```python
class NarrativeSystem:
    def __init__(self, world_graph, world_instance, narrative_progress): ...

    def advance_chapter(self, target_chapter_id, transition_type="normal") -> Dict: ...
    def complete_objective(self, event_id, objective_id, value=1) -> Dict: ...
    def update_disposition(self, character_id, changes, reason="") -> Dict: ...
    def check_chapter_transitions(self) -> Optional[Dict]: ...
    def get_flow_board(self, lookahead=3) -> Dict: ...
    def get_current_plan(self) -> Dict: ...

    @property
    def progress(self) -> NarrativeProgress: ...
```

**依赖**: graph/（disposition 存在图节点 state 中）, `app/runtime/world_instance.py`（chapter_registry）
**被依赖**: events/（事件完成同步叙事）

**前置条件**: narrative_service.py import 已删除的 `GameSessionStore`，需先完成 Store 迁移

---

### 3.6 scene/ — 场景 ✅

> **执行记录**: [P1-scene与time子系统迁移](L3层子系统延伸重构/P1-scene与time子系统迁移.md)

**职责**: 回合级叙事总线、场景成员管理。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/scene_bus.py` | `scene_bus.py` | 204 | 移入 |

**公开 API**: 直接导出现有类
```python
from .scene_bus import SceneBus, BusEntry, BusEntryType
```

**依赖**: 无 | **被依赖**: npc/reactor, L1 工具

---

### 3.7 time/ — 时间 ✅

> **执行记录**: [P1-scene与time子系统迁移](L3层子系统延伸重构/P1-scene与time子系统迁移.md)

**职责**: 游戏时间推进、时段检测、NPC 活动调度。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/services/time_manager.py` | `time_manager.py` | 266 | 迁移 |

**公开 API**: 直接导出
```python
from .time_manager import TimeManager, GameTime, TimePeriod, TimeEvent
```

**依赖**: 无 | **被依赖**: session_runtime（advance_time）

---

### 3.8 intent/ — 意图 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)

**职责**: 玩家意图解析与执行（13 种意图类型）。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/world/intent_resolver.py` | `resolver.py` | 434 | 移入+改名 |
| `app/world/intent_executor.py` | `executor.py` | 787 | 移入+改名 |

> intent_resolver 实质可能是 L2（含 LLM 调用），executor 是纯 L3。放在一起是因为它们是紧密的一对。

**依赖**: graph/, player/（USE_ITEM 等操作）, events/（触发事件）

---

### 3.9 combat/ — 战斗 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)

**职责**: D&D 风格战斗引擎（d20 判定、回合制、AI 对手）。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/combat/combat_engine.py` | `combat_engine.py` | 1567 | 迁移 |
| `app/combat/enemy_registry.py` | `enemy_registry.py` | 430 | 迁移 |
| `app/combat/template_mapper.py` | `template_mapper.py` | 245 | 迁移 |
| `app/combat/ai_opponent.py` | `ai_opponent.py` | 174 | 迁移 |
| `app/combat/rules.py` | `rules.py` | 166 | 迁移 |
| `app/combat/dice.py` | `dice.py` | 90 | 迁移 |
| `app/combat/spatial.py` | `spatial.py` | 66 | 迁移 |
| `app/combat/effects.py` | `effects.py` | 55 | 迁移 |
| `app/combat/spells.py` | `spells.py` | 38 | 迁移 |
| `app/combat/constants.py` | `constants.py` | 3 | 迁移 |

**不迁移的 app/combat/ 文件**:
- `combat_mcp_server.py`（793，L2）→ 保留原位或迁移到 `app/mcp/`
- `models/`（5 个 Shared 文件）→ 保留在 `app/combat/models/` 或迁移到 `app/models/combat/`
- `test_combat_cli.py`（Offline）、`llm_orchestrator_example.py`（Offline）→ 不迁移

> **后续计划**: combat/ 迁入 `app/world/combat/` 后有独立的改造计划（内部重构）。
> 本次只做物理迁移 + import 路径更新，不改内部结构。

**依赖**: 无外部子系统（自包含）
**被依赖**: gm_extra_tools.py（start_combat 等）, player/（combat_rewards 同步）

---

### 3.10 party/ — 队伍 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)

**职责**: 队伍生命周期管理、成员增删、位置同步。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/services/party_service.py` | `party_service.py` | 295 | 迁移 |

**主要 API**（现有 PartyService 类）:
```python
class PartyService:
    def create_party(self, world_id, session_id, leader_id) -> Party: ...
    def get_party(self, world_id, session_id) -> Optional[Party]: ...
    def add_member(self, world_id, session_id, character_id, name, ...) -> Optional[PartyMember]: ...
    def remove_member(self, world_id, session_id, character_id) -> bool: ...
    def disband_party(self, world_id, session_id) -> bool: ...
    def sync_locations(self, world_id, session_id, new_location, new_sub) -> None: ...
    def load_predefined_teammates(self, world_id, session_id, configs) -> List[PartyMember]: ...
```

**依赖**: `app/models/party`（Party, PartyMember）
**被依赖**: session_runtime, gm_extra_tools.py

**前置条件**: 当前 import 已删除的 `PartyStore`，需先完成 Store 迁移

---

### 3.11 npc/ — NPC 机械 ✅

> **执行记录**: [P4-P7-批量迁移与清理](L3层子系统延伸重构/P4-P7-批量迁移与清理.md)

**职责**: NPC 实例池管理（LRU 淘汰）、对话上下文窗口（200K token）、相关度排序、队友可见性规则。

**文件清单**:
| 来源 | 文件 | 行数 | 动作 |
|------|------|------|------|
| `app/services/instance_manager.py` | `instance_manager.py` | 606 | 迁移 |
| `app/services/context_window.py` | `context_window.py` | 502 | 迁移 |
| `app/services/npc_reactor.py` | `reactor.py` | 163 | 迁移+改名 |
| `app/services/teammate_visibility_manager.py` | `visibility.py` | 194 | 迁移+改名 |

**主要类**:
- `InstanceManager` — NPC 实例池，LRU 淘汰，淘汰前强制图谱化
- `NPCInstance`（dataclass）— 单个 NPC 认知状态容器
- `ContextWindow` — 200K token 对话缓冲，graphize 触发
- `NPCReactor` — NPC 相关度排序 + 模板反应
- `TeammateVisibilityManager` — 队友信息可见性过滤

**依赖**: graph/（世界图谱查询）, scene/（SceneBus）, `app/models/`（多个模型）
**被依赖**: pipeline_orchestrator, immersive_tools.py, teammate_response_service

---

## 四、跨子系统依赖关系

```
                         ┌──────────┐
                         │  graph/  │  ← 核心数据层
                         └────┬─────┘
        ┌────────────┬────────┼─────────┬──────────────┬──────────┐
        ▼            ▼        ▼         ▼              ▼          ▼
   ┌─────────┐ ┌─────────┐ ┌────────┐ ┌───────────┐ ┌─────┐ ┌───────┐
   │ events/ │ │ memory/ │ │player/ │ │narrative/ │ │npc/ │ │intent/│
   └────┬────┘ └─────────┘ └────────┘ └───────────┘ └─────┘ └───┬───┘
        │                       ▲            ▲                    │
        ├───────────────────────┘            │                    │
        │  (奖励结算调 player)                │                    │
        └────────────────────────────────────┘                    │
           (事件完成同步叙事)                                       │
                                                                  │
        intent/ ───→ player/, events/（执行意图时调用）               │
                                                                  ▼

  scene/  ── 独立           party/  ── 独立（仅依赖 models）
  time/   ── 独立           combat/ ── 独立（自包含）
```

**无环依赖**: graph → 其他子系统 → 无回指
**跨子系统调用清单**:

| 调用方 | 被调方 | 场景 | 方式 |
|--------|--------|------|------|
| events/ | player/ | 奖励结算（XP/Gold/Item） | 构造函数注入 |
| events/ | narrative/ | 事件完成同步叙事进度 | 构造函数注入 |
| events/ | graph/ | 读写事件节点状态 | 构造函数注入 |
| memory/ | graph/ | 读写记忆节点/边 | 构造函数注入 |
| player/ | graph/ | 通过 PlayerNodeView 读写 | 构造函数注入 |
| narrative/ | graph/ | disposition 存在图节点 | 构造函数注入 |
| npc/ | graph/ | 查询角色/区域节点 | 构造函数注入 |
| npc/ | scene/ | 相关度排序读 SceneBus | 参数传入 |
| intent/ | player/ | USE_ITEM/REST 等操作 | 构造函数注入 |
| intent/ | events/ | 意图触发事件 | 构造函数注入 |

---

## 五、session_runtime.py 瘦身后 ✅

> **执行记录**: [P5-P7-SessionRuntime提取与瘦身](L3层子系统延伸重构/P5-P7-SessionRuntime提取与瘦身.md)

### 瘦身前后对比

| | 瘦身前 | 瘦身后（实际） |
|--|--------|---------------|
| 总行数 | 2,104 | **999**（-53%） |
| 提取到 helper 文件 | — | 4 个文件，1,277 行 |
| 职责 | 12 个 zone | L2 编排 + 29 个薄委托 |

### 实际落地方案：Back-Reference Delegation

原方案设想将方法提取到各子系统目录（如 `events/state_machine.py`），由子系统门面类持有。
实际采用更安全的 **Back-Reference Delegation** 模式：

```
session_runtime.py (999行)
  ├── self._events = EventMachine(self)      → app/runtime/event_machine.py (861行)
  ├── self._narrative_ops = NarrativeOps(self) → app/runtime/narrative_ops.py (203行)
  ├── self._memory_ops = MemoryOps(self)     → app/runtime/memory_ops.py (136行)
  └── self._stat_ops = StatOps(self)         → app/runtime/stat_ops.py (77行)
```

每个 helper 类通过 `self._s` 引用 SessionRuntime，通过 `self._s.xxx` 访问状态。
SessionRuntime 保留 29 个薄委托方法（9 公共 + 7 私有 → EventMachine，3 → NarrativeOps，4 → MemoryOps，6 → StatOps），外部调用方零改动。

**优势**:
- TYPE_CHECKING 避免循环导入，helper 运行时不 import SessionRuntime
- 所有 app 级 import 在方法内 lazy import，模块顶层仅 stdlib
- EventMachine 内部交叉调用（如 `advance_stage → complete_event`）自然工作
- 测试零改动（除 2 个 mock 补属性）

**调用方式**: 外部代码不变——
```python
session.heal(20)                    # → self._stat_ops.heal(20)
session.activate_event("quest_1")   # → self._events.activate_event("quest_1")
session.recall("gm", "player", seeds)  # → self._memory_ops.recall(...)
session.advance_chapter("ch2")      # → self._narrative_ops.advance_chapter("ch2")
```

---

## 六、不迁移文件

以下 L3 文件不迁入 `app/world/` 子系统（L3 基础设施，非世界机械）：

| 文件 | 行数 | 原因 |
|------|------|------|
| `app/runtime/session_runtime.py` | ~1,000（瘦身后） | L2 编排层，持有子系统实例 |
| `app/runtime/world_instance.py` | 389 | 启动时静态数据加载，graph/ builder 的输入源 |
| `app/runtime/context_assembler.py` | 336 | L2/L3 桥，上下文组装依赖多个子系统 |
| `app/runtime/game_runtime.py` | 73 | 全局单例，WorldInstance 缓存 |
| `app/services/session_history.py` | 408 | 会话级历史缓冲，边界案例（见下） |
| `app/services/admin/state_manager.py` | 74 | 会话状态快照，紧耦合 admin_coordinator |

### 边界案例：session_history.py

session_history（408 行）是 L3，管理会话对话历史缓冲 + graphize 触发。从功能看可归入 memory/（最终 graphize 进记忆图谱），但它是 session 级基础设施，与 NPC 级的 context_window 对称。

**暂留 app/services/**，待子系统结构稳定后再决定是否并入 memory/。

---

## 七、前置条件：Firestore Store 迁移

以下 3 个子系统的文件 import 了已删除的 Firestore Store，迁移前需先解决（详见 `Firestore残留Store迁移清单.md`）：

| 子系统 | 文件 | 已删 Store | 影响 |
|--------|------|-----------|------|
| player/ | `character.py` | CharacterStore | 角色创建/查询的持久化 |
| player/ | `ability_check.py` | CharacterStore | 获取角色属性算修正值 |
| party/ | `party_service.py` | PartyStore | 队伍持久化 |
| narrative/ | `narrative_service.py` | GameSessionStore | 叙事进度持久化 |

**解决方向**: 迁移到 SessionRuntime + WorldGraph 内存化运行（已在 Store 迁移清单中规划为 T1-T13）。

### 7.1b L2 入口链路的 Store 残留（新增）

以下文件虽不在 L3 子系统目录，但会阻断服务启动，需与 Store 迁移并行收口：

| 层级 | 文件 | 残留 import | 影响 |
|------|------|------------|------|
| L2 | `app/services/admin/admin_coordinator.py` | `GameSessionStore` / `PartyStore` / `CharacterStore` | FastAPI 主链路初始化失败 |
| L2 | `app/services/admin/world_runtime.py` | `GameSessionStore` | Admin runtime 初始化失败 |
| L2 | `app/mcp/tools/character_tools.py` | `CharacterStore` | game-tools MCP 启动失败 |
| L2 | `app/mcp/tools/inventory_tools.py` | `CharacterStore` | game-tools MCP 启动失败 |
| L2 | `app/mcp/tools/party_tools.py` | `PartyStore` | game-tools MCP 启动失败 |
| L2 | `app/combat/combat_mcp_server.py` | `GameSessionStore` | combat MCP 启动失败 |

### 7.2 Runtime 残留依赖收口（新增，P0）

`session_runtime.py` 仍存在对已删除模块的硬依赖；若不先收口，后续子系统迁移会被阻塞：

| 文件 | 残留依赖 | 当前状态 | 处置建议 |
|------|----------|----------|----------|
| `app/runtime/session_runtime.py` | `app.runtime.area_runtime` | 已删除 | 以新 navigation 协调器替换，并移除静态导入 |
| `app/runtime/session_runtime.py` | `app.runtime.companion_instance` | 已删除 | 以 npc/ 子系统实例池替换 |
| `app/runtime/session_runtime.py` | `app.runtime.models.companion_state` | 已删除 | 改为轻量事件 DTO（放 `app/models/` 或 `app/world/npc/`） |

**验收门槛（P0）**:
- `import app.main` 可通过
- `import app.mcp.game_tools_server` 可通过
- `SessionRuntime("w","s")` 构造不抛 `ModuleNotFoundError`

> 结论修订：Store 迁移不是唯一阻塞项；Runtime 残留依赖与 L2 入口链路需并行收口。

---

## 八、已知风险与待解决

### 8.1 mark_player_dirty 越界

**现状**: `mark_player_dirty()` 直接访问 `world_graph._dirty_nodes.add("player")`
**解法**: WorldGraph 新增 `mark_node_dirty(node_id)` 公开方法

### 8.2 models.py 归属

`models.py`（444 行）被几乎所有子系统引用。放 `graph/` 有偏向性。
**备选**: 保持 `app/world/models.py`（根级共享）或 `app/world/graph/models.py`（与图谱最紧密）

### 8.3 graphize_messages 是 L1

`graphize_messages()` 调 MemoryGraphizer（LLM），是 session_runtime 中唯一的 L1 方法。
**备选**: 留在 SessionRuntime / 移到独立 L1 模块 / 标注为 L1 hook

### 8.4 behavior_engine.py 内部复杂度

1202 行含 3 个类（ConditionEvaluator / ActionExecutor / BehaviorEngine）。先整体迁入 events/，后续在 events/ 内拆分为 3 个文件。

### 8.5 narrative_service 与 session_runtime NarrativeOps 重叠

两处都有 chapter 推进、objective 完成逻辑。迁移到 narrative/ 时需合并去重。

### 8.6 import 路径变更

所有 `from app.world.world_graph import WorldGraph` → `from app.world.graph import WorldGraph`。
影响范围：app/runtime/, app/services/, app/world/ L1 文件, tests/。
**风险点**: 仅依赖 `app/world/__init__.py` re-export 不足以覆盖模块级直引（如 `from app.world.graph_builder import GraphBuilder`）。
**缓解（双轨）**:
- 轨道 A：批量改为新路径（主方案）
- 轨道 B：保留旧模块 shim（临时转发，带 deprecation 注释），待全量替换后删除

### 8.7 count_tokens 共享

`context_window.py` 中的 `count_tokens()` 被 `session_history.py` 引用。
context_window 迁入 npc/ 后，session_history 不应依赖 npc/。
**解法**: `count_tokens` 提取为 `app/world/` 或 `app/utils/` 级共享函数。

### 8.8 combat/ models 与 Shared models

`app/combat/models/`（5 个 Shared 文件）与 L3 代码紧耦合。物理迁移时需决定：
- A）`app/world/combat/models/` 随 L3 一起迁
- B）`app/models/combat/` 归入 Shared 层
- 建议 A，保持自包含

### 8.9 combat_mcp_server.py 归属

当前 `app/combat/combat_mcp_server.py`（793, L2）与 L3 战斗代码同目录。
L3 迁入 `app/world/combat/` 后，这个 L2 文件应留在 `app/combat/` 或迁到 `app/mcp/`。

### 8.10 Navigation 待定会阻塞 session_runtime 瘦身（新增）

第二章中 Navigation 仍标记为“待定”，但运行时主链路依赖该能力（区域切换、条件提示、事件摘要）。
若不先确定归属（建议 L2 协调 + L3 纯规则拆分），`session_runtime.py` 无法实质降重。

### 8.11 数字口径漂移（新增）

当前文档内存在多处口径差异（例如 `app/services/` L3 数量与列举条目不一致，`session_runtime.py` 总行数出现 2,154/2,232 两套数值）。
建议后续统一以“脚本扫描结果”为单一数据源，避免排期误差。

---

## 九、执行顺序建议

> 先做可运行性稳定化，再进入纯移动与提取阶段

| 阶段 | 子系统 | 动作 | 复杂度 | 前置 |
|------|--------|------|--------|------|
| **P0** | startup/runtime | 修复删除后残留 import（admin/mcp/combat/session_runtime）+ 补 shim | 中 | 无 |
| ~~**P1**~~ | ~~scene/~~ | ~~移动 1 文件~~ | ~~纯移动~~ | ✅ 已完成 |
| ~~**P1**~~ | ~~time/~~ | ~~移动 1 文件~~ | ~~纯移动~~ | ✅ 已完成 |
| ~~**P2**~~ | ~~graph/~~ | ~~移动 4 文件 + 迁入 graph_schema~~ | ~~移动+改名~~ | ✅ 已完成 |
| ~~**P3**~~ | ~~memory/~~ | ~~移动 2 文件 + 提取 session_runtime MemoryOps~~ | ~~提取~~ | ✅ 已完成 |
| ~~**P3**~~ | ~~player/~~ | ~~移动 3 + 迁入 3 + 合并 stats~~ | ~~迁移+合并~~ | ✅ 已完成 |
| ~~**P4**~~ | ~~narrative/~~ | ~~迁入 narrative_service + fallback 清理~~ | ~~迁移~~ | ✅ 已完成 |
| ~~**P4**~~ | ~~npc/~~ | ~~迁入 4 文件 + 删 shim~~ | ~~迁移~~ | ✅ 已完成 |
| ~~**P4**~~ | ~~party/~~ | ~~迁入 party_service~~ | ~~迁移~~ | ✅ 已完成 |
| ~~**P5 移动**~~ | ~~events/~~ | ~~移动 3 文件~~ | ~~纯移动~~ | ✅ 已完成（提取待做） |
| ~~**P5**~~ | ~~intent/~~ | ~~移动 2 文件~~ | ~~移动+改名~~ | ✅ 已完成 |
| ~~**P6**~~ | ~~combat/~~ | ~~迁移 10 文件 + models/~~ | ~~批量迁移~~ | ✅ 已完成 |
| ~~**P7 清理**~~ | ~~cleanup~~ | ~~fallback 清理 + shim 删除 + __init__ 更新~~ | ~~收尾~~ | ✅ 已完成 |
| ~~**P5+P7**~~ | ~~events/ + session_runtime~~ | ~~提取 29 方法到 4 个 helper（EventMachine/NarrativeOps/MemoryOps/StatOps），session_runtime 2104→999 行~~ | ~~最复杂~~ | ✅ 已完成 |

> **P1-P7 全部完成（2026-02-23）**。剩余阻塞项为 P0（Store 残留 + Runtime 残留依赖），属独立专项。

---

## 十、数字汇总

| 指标 | 值 |
|------|-----|
| 目标子系统数 | **11 个**（graph/events/memory/player/narrative/scene/time/intent/combat/party/npc/） |
| 涉及 app/world/ 现有文件 | 14 个 L3（重组到子文件夹） |
| 从 app/services/ 迁入 | 12 个文件 |
| 从 app/combat/ 迁入 | 10 个文件 |
| 从 session_runtime 提取 | **4 个 helper 文件**，1,277 行（29 个方法） |
| 总迁移/重组文件 | ~40 个 |
| 新建子文件夹 | 11 个 |
| session_runtime 瘦身 | 2,104 → **999 行**（-53%） |
| import 路径变更 | 80+ 处 |
| 不迁移的 L3 文件 | 6 个（runtime 基础设施 + session_history + state_manager） |
| 受 Store 迁移阻塞 | 3 个子系统（player/character, party/, narrative/） |
| P0 稳定化阻塞 | 4 条链路（admin / mcp tools / combat mcp / session_runtime） |
| **测试验证** | **655 passed** / 126 failed（P0 残留）/ 15 errors — P1-P7 零回归 |
