# 世界引擎升级计划

记录日期：2026-03-15（最后更新：2026-03-15 逐项验证后二次修正）
关联存档：sess_a82d7b2a4662（Lv2 Fighter，第1天夜晚，frontier_town/north_gate）

> 本文档既是问题清单也是可执行的实施计划。每个阶段内的任务可由 code-implementer agent 直接执行。
>
> **修正记录**：基于对 EncounterHook(719行)、PassivePerceptionHook(174行)、EventConditionHook(366行)+event_engine.py(824行)、CampfireHook(285行) 的完整代码审查，以及 SceneBus ACTION_TAGS 的实际枚举，对阶段2做了重大修正。

---

## 阶段1：基础设施（无依赖，1-A ~ 1-D 可并行）

### 1-A：AreaSlice 新增 area_situation + area_events

**问题**：各系统产出没有共同汇总点，NPC/Planner 看不到完整区域态势，NPC 编造不存在的事件。（KI-03, KI-04）

**改动**：

`app/game_core/state/slices/area.py`（当前 1909 行，AreaState 已有 17 个字段）：

AreaState dataclass 新增两个字段：
```python
area_situation: str = ""
# Planner 每轮汇总的区域态势自然语言描述

area_events: list[dict] = field(default_factory=list)
# 各系统写入的区域事件日志，滚动保留最近 20 条
# 每条：{"tick": int, "event": str, "source": str, "severity": "minor"|"major"|"critical"}
```

AreaSlice 改动：
- `snapshot()` / `restore()` — 新增两个字段的序列化/反序列化（镜像 `properties` 的处理方式）
- `validate()` — 类型检查（area_situation 是 str，area_events 是 list[dict]）
- `snapshot()` — 新增两行：`"area_situation": self.area_situation` + `"area_events": [dict(e) for e in self.area_events]`
- `_coerce_area_state()` — 新增两行：`area_situation=str(raw.get("area_situation", ""))` + `area_events=[dict(e) for e in raw.get("area_events", []) if isinstance(e, Mapping)]`。旧存档自动获得默认空值，无需迁移。
- `validate()` — 新增类型检查（str / list[dict]）
- `apply_state_change()` 新增两个路径（放在 `field_name == "dynamic_room"` 之后、final raise 之前）：
  - `{area_id}.area_events`：operation="add" → 追加单条 + 截断到 20；operation="set" → 替换
  - `{area_id}.area_situation`：operation="set" → 直接赋值
- 新增辅助方法：
  - `append_area_event(area_id, event_dict)` — 追加 + 超过 20 条时 `del events[:len-20]`
  - `set_area_situation(area_id, text)` — 设置态势描述
  - `get_area_events(area_id) -> list[dict]` — 防御性拷贝 `[dict(e) for e in ...]`
  - `get_area_situation(area_id) -> str`

**测试**：~10 个（snapshot 往返、event 追加+滚动截断、situation 设置、旧存档无新字段的兼容性、apply_state_change 非法 operation 日志警告）

**预估**：~150 行新增

---

### 1-B：RelationSlice 新增 npc_blackboards

**问题**：NPC 没有持久的个人认知数据。（ARCH-03 基础）

**改动**：

`app/game_core/state/slices/relations.py`（当前 228 行，已有 5 个字段：npc_dispositions, relationship_stages, faction_standings, npc_impressions, shop_states）：

新增字段：
```python
npc_blackboards: dict[str, dict[str, Any]] = field(default_factory=dict)
# {
#   "goblin_slayer": {
#     "thoughts": "哥布林的行动越来越有组织性",
#     "goals": ["前往古代遗迹侦察", "等待冒险者准备完毕"],
#     "observations": ["北门守卫报告夜间哥布林活动增加"],
#     "mood": "警惕",
#     "attitude_towards_player": "这个新人有些实力，但还需观察",
#     "updated_tick": 15
#   }
# }
```

RelationSlice 改动（当前 228 行，已有 5 个字段）：
- `snapshot()` — 新增：`"npc_blackboards": {k: dict(v) for k, v in self.npc_blackboards.items()}`
- `restore()` — 新增：`self.npc_blackboards = {str(k): dict(v) for k, v in payload.get("npc_blackboards", {}).items() if isinstance(v, Mapping)}`。旧存档自动获得空 dict，无需迁移。
- `validate()` — 新增：`isinstance(npc_blackboards, dict)` + 每值 `isinstance(v, dict)`
- `apply_state_change()` 新增路径（在 `shop_states.{npc_id}` 之后）：
  - `npc_blackboards.{npc_id}`（operation="set" → 替换整个黑板 dict）
- 新增辅助方法：
  - `get_blackboard(npc_id) -> dict` — `dict(self.npc_blackboards.get(npc_id, {}))`
  - `update_blackboard(npc_id, updates: dict)` — `existing = self.npc_blackboards.setdefault(npc_id, {}); existing.update(updates); self._dirty = True`

**测试**：~10 个（snapshot 往返、update 合并、get 防御性拷贝、旧存档兼容、apply_state_change set）

**预估**：~150 行新增

---

### 1-C：Clue 系统修复（KI-01 + KI-02）

**问题1（KI-01）**：调查线索显示"生成出错"。
**问题2（KI-02）**：线索调查结果不持久化。

**改动**：

`app/game_core/rules/handlers/clue.py`（KI-02）：
- `_compute_resolve()` 写入 `interactable_states` 时新增：
  ```python
  updated_state = {
      ...现有字段（area_id, clue_id, first_inspected, resolved_option_id, resolved_at_tick）...,
      "outcome_text": outcome_text,       # 从 outcomes dict 查出
      "check_passed": check_passed,       # bool 或 None
      "effects_applied": effects_list,    # 应用的效果摘要
  }
  ```
- handler 返回的 `metadata` 中新增 `outcome_text` 字段（供 agent_orchestration fallback 使用）
- **注意**：handler 不能直接调用 `append_area_event()`（handler 模式是产出 StateChange 列表）。改为在 `changes` 列表中新增一条 StateChange：
  ```python
  changes.append(StateChange(
      "areas", "add", f"{area_id}.area_events",
      {"tick": current_tick, "event": f"调查了{clue_name}：{outcome_text[:80]}",
       "source": "clue_investigation", "severity": "minor"}
  ))
  ```

`app/agent_orchestration.py`（KI-01）：
- `_generate_clue_gm_events()` 失败时返回 `[]`（空列表），调用方检测到后走 fallback
- `_build_fallback_clue_comment_event(clue_payload)` 修改：从 `clue_payload` 中提取 `outcome_text`（由 handler metadata 携带），直接用作 GM 评论内容。当前是静态模板文本（"先把方向拧了出来..."），改为使用真实 outcome。
- `_build_clue_resolution_comment_event(result)` 同理：从 `result.metadata["outcome_text"]` 提取。

**关键路径**：handler metadata → PipelineResult.metadata → agent_orchestration fallback → SSE gm_comment

**测试**：~8 个（fallback 使用 outcome_text、无 outcome_text 时优雅降级为模板、area_event 写入、结果持久化到 interactable_states）

**预估**：~150 行修改

---

### 1-D：新增任务条件类型（KI-07）

**问题**：任务目标不引用具体区域内容。

**现有条件类型**（event_engine.py BasicEventConditionEvaluator，已有 12 种）：
`flag_set`、`location_entered`、`location_visited`、`period_reached`、`time_reached`、`quest_state`、`disposition`、`time_elapsed`、`npc_talked`、`item_obtained`、`kill_count`、`level_reached`

> 注意：`flag_set` 已存在，不需要新增。

**新增 4 种**（在 event_engine.py 的 `_condition_met()` 中添加分支）：

```python
"encounter_cleared":       # areas.hostile_tracking[encounter_id].get("cleared") == True
  params: {area_id: str, encounter_id: str}

"clue_investigated":       # areas.interactable_states[clue_id] 存在且有 resolved_option_id
  params: {area_id: str, clue_id: str}

"all_encounters_cleared":  # area 的 hostile_tracking 全部 cleared
  params: {area_id: str}

"danger_below":            # areas.get_area(area_id).danger_level < threshold
  params: {area_id: str, threshold: float}
```

**改动文件**：`app/game_core/orchestration/event_engine.py`（824 行）

**具体位置**：`BasicEventConditionEvaluator._condition_met()` 方法（第 222-259 行），if/elif 分派链。

**方法签名**：`_condition_met(self, state: StateContainer, condition: Mapping) -> tuple[bool, int]`
- 返回 `(是否满足, 不支持的条件数)`，新条件返回 `(True/False, 0)`

**新增 4 个 elif 分支**（在 `level_reached` 之后、最终 `return (False, 1)` 之前）：
```python
elif condition_type == "encounter_cleared":
    area_id = params.get("area_id", "")
    encounter_id = params.get("encounter_id", "")
    if state.has_slice("areas"):
        hostile = state.areas.get_area(area_id).hostile_tracking.get(encounter_id, {})
        return (bool(hostile.get("cleared")), 0)
    return (False, 0)

elif condition_type == "clue_investigated":
    area_id = params.get("area_id", "")
    clue_id = params.get("clue_id", "")
    if state.has_slice("areas"):
        ist = state.areas.get_area(area_id).interactable_states.get(clue_id, {})
        return (bool(ist.get("resolved_option_id")), 0)
    return (False, 0)

elif condition_type == "all_encounters_cleared":
    area_id = params.get("area_id", "")
    if state.has_slice("areas"):
        tracking = state.areas.get_area(area_id).hostile_tracking
        if not tracking:
            return (True, 0)  # 没有遭遇视为已清除
        return (all(e.get("cleared") for e in tracking.values()), 0)
    return (False, 0)

elif condition_type == "danger_below":
    area_id = params.get("area_id", "")
    threshold = float(params.get("threshold", 1.0))
    if state.has_slice("areas"):
        return (state.areas.get_area(area_id).danger_level < threshold, 0)
    return (False, 0)
```

**测试**：每种 2 个（满足/不满足），共 ~8 个

**预估**：~100 行新增

---

## 阶段2：Osiris 机械化改造（依赖阶段1-A）

> **重要修正**：基于代码审查，合并策略从"搬代码进 Osiris 文件"改为"Osiris 协调调用，逻辑留在原处"。
> 原因：EncounterHook(719行) 有 injectable detector 抽象 + slot 管理；EventConditionHook 依赖 event_engine.py(824行) 这个被多处复用的共享模块。物理合并会制造 god file，违反项目红线。

### 2-A：Osiris 去 LLM → 机械规则引擎

**改动**：

`app/game_core/orchestration/hooks/ai_osiris.py`（重写核心）：

删除 LLM 调用路径，新增 `MechanicalOsirisEngine`：
```python
class MechanicalOsirisEngine:
    """纯规则表驱动的因果引擎。基于 SceneBus ACTION_TAGS 匹配。"""

    def evaluate(self, action_tags: set[str], context: OsirisContext) -> list[Command]:
        """遍历规则表，匹配后收集 Commands + area_events。"""
```

**规则表**（基于实际存在的 SceneBus tags）：

```python
# 实际 ACTION_TAGS（来自 tick_coordinator.py _SEMANTIC_TAGS）：
# COMBAT, COMBAT_END, NAVIGATION, SKILL_CHECK, QUEST_PROGRESS,
# INVESTIGATION, CLUE, REST, LONG_REST, SHORT_REST,
# DIALOGUE, NPC_INTERACTION, PUBLIC_UTTERANCE, PARTY_CHAT, PRIVATE_CHAT

CONSEQUENCE_RULES = [
    # ── 战斗 ──
    {"trigger": {"tag": "COMBAT", "area_tag": "safe_zone"},
     "effects": [
         {"type": "adjust_danger", "delta": +0.5},
         {"type": "set_flag", "flag": "disturbance_{area_id}"},
         {"type": "area_event", "event": "区域内发生了战斗", "severity": "major"},
     ]},
    {"trigger": {"tag": "COMBAT_END"},
     "effects": [
         {"type": "adjust_danger", "delta": -0.3},
         {"type": "area_event", "event": "战斗结束", "severity": "minor"},
     ]},

    # ── 探索 ──
    {"trigger": {"tag": "NAVIGATION"},
     "effects": [
         {"type": "area_event", "event": "冒险者移动到新区域", "severity": "minor"},
     ]},
    {"trigger": {"tag": "INVESTIGATION"},
     "effects": []},  # area_event 由 clue handler 直接写入（1-C）

    # ── 休息 ──
    {"trigger": {"tag": "LONG_REST", "area_tag": "hostile"},
     "effects": [
         {"type": "adjust_danger", "delta": +0.1},
         {"type": "area_event", "event": "在危险区域休息，敌人有时间重新部署", "severity": "minor"},
     ]},

    # ── 任务 ──
    {"trigger": {"tag": "QUEST_PROGRESS"},
     "effects": [
         {"type": "area_event", "event": "任务取得进展", "severity": "major"},
     ]},
]
```

**目击者→阵营传播**（利用 action summary 中已有的 `witnessed_by` 字段）：
```python
FACTION_PROPAGATION = {
    "decay": 0.5,       # 传播到阵营时 delta 衰减 50%
    "max_hops": 1,       # 不做多跳
    # 触发条件：action_tags 包含 COMBAT + area_tag 为 safe_zone + has_witnesses
    # 效果：目击者同阵营 NPC 的 fear/trust 变化
}
```

> 注意：目击者传播规则在初始版本中可以简化——只在战斗/犯罪类标签时触发。后续迭代可扩展更多社交类标签（如 STEAL、ATTACK_CIVILIAN，需先在 SceneBus 中新增这些 tags）。

**保留**：安静休息压制逻辑（quiet_rest_slot → 清空 consequences）
**保留**：在场性检查框架（为未来扩展预留）

**删除**：
- `app/evaluators.py` 中的 `GeminiAIOsirisProvider`、`AgenticAIOsirisEvaluator`、`OSIRIS_SYSTEM_PROMPT`、`SUBMIT_CONSEQUENCES_TOOL`
- `app/deps.py` 中的 Osiris LLM 提供者创建代码（`_osiris_evaluator_factory`）
- `app/game_core/adapters/ai_osiris.py` 中 LLM protocol 相关代码（保留 null stub 供测试）

**测试**：~15 个（每条规则 1-2 个、传播逻辑、静默期压制、空 tags 不崩溃）

**预估**：~300 行新增，~500 行删除

---

### 2-B：Hook 整合 — 协调模式（非物理合并）

> **关键决策**：不把 719 行 EncounterHook 和 824 行 event_engine.py 物理搬进 ai_osiris.py。
> 改为：Osiris hook 作为协调器，按 Phase 顺序调用各引擎的核心方法。原 hook 文件保留为内部模块。

**改动**：

`app/game_core/orchestration/hooks/ai_osiris.py`（扩展 execute()）：
```python
class AIOsirisHook(NoOpSettlementHook):
    HOOK_PRIORITY = 30

    def __init__(self, *, engine, encounter_logic, perception_logic, event_logic):
        self._engine = engine                  # MechanicalOsirisEngine
        self._encounter_logic = encounter_logic  # 原 EncounterHook 的核心逻辑
        self._perception_logic = perception_logic  # 原 PassivePerceptionHook 的核心逻辑
        self._event_logic = event_logic          # 原 EventConditionHook 的核心逻辑

    async def execute(self, context):
        events = []

        # Phase 1: 因果规则（MechanicalOsirisEngine）
        consequences = self._engine.evaluate(action_tags, context)
        self._apply_consequences(consequences, context)

        # Phase 2: 被动感知
        perception_results = self._perception_logic.run(context)
        events.extend(perception_results.sse_events)

        # Phase 3: 遭遇触发
        encounter_results = self._encounter_logic.run(context)
        events.extend(encounter_results.sse_events)

        # Phase 4: 事件条件检查
        event_results = self._event_logic.run(context)
        events.extend(event_results.sse_events)

        # Phase 5: 汇总写入 area_events
        self._flush_area_events(context, consequences)

        return HookResult(sse_events=events, ...)
```

原 hook 文件改造：
- `encounter.py` — 提取核心逻辑为 `EncounterPhase` 类（`run(context) -> PhaseResult`），删除 `execute()`，不再作为独立 hook
- `passive_perception.py` — 同上，提取为 `PerceptionPhase`
- `event_condition.py` — 同上，提取为 `EventConditionPhase`
  - **注意**：`event_engine.py` 完全不动，`EventConditionPhase` 继续调用 `BasicEventConditionEvaluator`

`app/game_core/orchestration/defaults.py`：
- `DEFAULT_SETTLEMENT_HOOK_TYPES` 中移除 `EncounterHook`、`PassivePerceptionHook`、`EventConditionHook`
- `AIOsirisHook` 保持 P30（这些 phase 的执行顺序在 Osiris 内部控制，不再依赖 hook 优先级排序）

`app/game_core/bootstrap.py`：
- 当前 AIOsirisHook 已经是**手动构造+注入**的（不走 defaults 无参路径）：
  ```python
  # bootstrap.py 第 227-231 行（现有模式）：
  if osiris_evaluator_factory is not None:
      evaluator = osiris_evaluator_factory()
      tick_coordinator.register_settlement_hook(AIOsirisHook(evaluator=evaluator))
  ```
- 改为构造合并后的 Osiris（注入三个 Phase）：
  ```python
  tick_coordinator.register_settlement_hook(
      AIOsirisHook(
          engine=MechanicalOsirisEngine(),
          encounter_phase=EncounterPhase(detector=BasicEncounterDetector()),
          perception_phase=PerceptionPhase(),
          event_phase=EventConditionPhase(evaluator=BasicEventConditionEvaluator()),
      )
  )
  ```
- EncounterHook/PassivePerceptionHook/EventConditionHook 不再在 defaults 中注册，也不再手动注册

**注意**：EncounterHook 当前走 defaults 无参路径（内部 fallback 到 BasicEncounterDetector）；EventConditionHook 也是。合并后这些 fallback 逻辑搬到 bootstrap 的构造参数中。

**测试**：原 hook 的测试迁移为 Phase 级别测试 + Osiris 集成测试 ~10 个

**预估**：~200 行重组（原文件拆出 Phase 类 + Osiris 协调器），旧 hook 的 execute() 方法删除

---

### 2-C：Osiris 不再发射 7 个指令

**本质**：Osiris 机械化后只发射 `set_flag` 和 `adjust_danger` 两种 Command。以下 7 种 Command 不再由 Osiris 产生：

**迁移到 Planner（由 LLM 在叙事决策时发射）**：
| 原 Osiris 指令 | 新归属 | WorldStateHandler 处理器 | 备注 |
|---|---|---|---|
| `advance_quest` | QuestManager | `_compute_advance_quest` 不变 | Planner LLM 决定何时推进任务 |
| `modify_completion` | PacingController | `_compute_modify_completion` 不变 | Planner 决定章节进度 |
| `schedule_event` | NarrativeWeaver | `_compute_schedule_event` 不变 | Planner 决定何时安排事件 |
| `create_rumor` | NpcDirector | `_compute_create_rumor` 不变 | Planner 决定何时传播谣言 |
| `modify_location` | NpcDirector | `_compute_modify_location` 不变 | Planner 决定 NPC 移动 |

**迁移到 NPC 自身**：
| 原 Osiris 指令 | 新归属 | 备注 |
|---|---|---|
| `modify_disposition` | NPC update_feeling 工具 | NPC 在对话/空跑中自主决定态度变化 |
| `modify_approval` | 队友在空跑中自主决定 | 同上 |

**代码改动**：
- `ai_osiris.py`：`_ALLOWED_COMMAND_TYPES` 缩减为 `{"set_flag", "adjust_danger"}`
- Planner 各子系统的 `_HANDLES` 和 `apply_directive()` **不需要改动** — 这些 Command 的发射权转移是在 Planner LLM prompt 层面引导的（告诉 Planner 它可以输出这些 directive kinds）
- `directive_contracts.py`：确认 `SUPPORTED_PLANNER_DIRECTIVE_KINDS` 包含对应的指令种类（目前 advance_milestone 已有，create_rumor/schedule_event/modify_location 需要检查是否需要新增 directive kind 或复用已有的）

**实施细节**（基于验证）：

| 原 Osiris Command | Planner 中已有等价机制？ | 实施方式 |
|---|---|---|
| `advance_quest` | 已有 `advance_milestone` directive | Planner LLM prompt 中引导使用 advance_milestone |
| `modify_completion` | 已有 PacingController `escalate` | Planner LLM prompt 中引导使用 escalate |
| `schedule_event` | NarrativeWeaver 处理 tick_settlement | 在 NarrativeWeaver._HANDLES 中加 `"schedule_event"`，新增 _apply_schedule_event() |
| `create_rumor` | NpcDirector 处理 NPC 行为 | 在 NpcDirector._HANDLES 中加 `"create_rumor"`，新增 _apply_create_rumor() |
| `modify_location` | NpcDirector 已有 spawn_quest_npc | 在 NpcDirector._HANDLES 中加 `"modify_location"`，新增 _apply_modify_location() |

> 注：WorldStateHandler 中的 `_compute_schedule_event`/`_compute_create_rumor`/`_compute_modify_location` 保持不变——它们处理 Command 执行。改的只是"谁发射 Command"（从 Osiris → Planner 子系统）。

**代码改动**：
- `ai_osiris.py`：`_ALLOWED_COMMAND_TYPES` 缩减为 `("set_flag", "adjust_danger")`
- `narrative_weaver.py`：`_HANDLES` 加 `"schedule_event"`，新增 `_apply_schedule_event()`
- `npc_director.py`：`_HANDLES` 加 `"create_rumor"` + `"modify_location"`，各加一个 apply 方法
- `directive_contracts.py`：`SUPPORTED_PLANNER_DIRECTIVE_KINDS` 加 `"schedule_event"` + `"create_rumor"` + `"modify_location"`
- Planner agent prompt 调整（告知 Planner 它现在可以输出这些 directive kinds）

**测试**：~6 个（Osiris 不再发射这些 Command、新 directive kind 通过 Planner 子系统正确路由）

**预估**：~150 行修改

---

## 阶段3：信息流通 + NPC 提示词精简（依赖阶段1+2）

### 3-A：NPC L2 上下文注入 area_situation（KI-04）

**改动**：

`app/game_core/narrative/context_builder.py`：
- `_build_l2()` 返回的 dict 中新增 `area_situation` 和 `recent_area_events`（从 AreaSlice 读取）
- `_build_npc_prompt_text()` 新增区块（在角色基础信息之后、关系区块之前）：
  ```
  ## 你所在区域的当前态势
  {area_situation}

  ## 近期发生的事件
  - {event_1}
  - {event_2}
  ...（最近5条）
  ```

**预估**：~50 行

---

### 3-B：Planner 上下文注入 area_situation（KI-03）

**改动**：

`app/game_core/orchestration/hooks/narrative_planner.py`：
- `_build_planner_context()` 中新增：
  ```python
  area_context["area_situation"] = area_state.area_situation
  area_context["area_events"] = area_state.area_events[-10:]
  area_context["exploration_summary"] = {
      "encounters_total": len(hostile_tracking),
      "encounters_cleared": sum(1 for e in hostile_tracking.values() if e.get("cleared")),
      "clues_total": ...,
      "clues_investigated": len(interactable_states),
      "discovered_rooms": len(discovered_rooms),
  }
  ```
- Planner 每轮汇总 area_events → 更新 area_situation（在 NarrativeWeaverSubSystem.evaluate() 中或 hook.execute() 末尾）

**预估**：~80 行

---

### 3-C：NPC 提示词精简 — 黑板替代旧字段

**改动**：

`app/game_core/narrative/context_builder.py` 的 `_build_npc_prompt_text()`：

**去掉**：
- `npc_impressions` 独立注入区块（"## Your memories of the player"）
- 关系行为指南文本（`_STAGE_GUIDES`/`_trust_hint()`/`_fear_hint()`/`_romance_hint()` 的输出）— 保留纯数值
- story_facts 最近 5 条注入区块
- active_directive 独立注入区块（"## [重要行为指令]"）

**新增**：
```
## 你当前的想法
- 思绪：{thoughts}
- 目标：{goals}
- 近期观察：{observations}
- 情绪：{mood}
- 对冒险者的看法：{attitude_towards_player}
```

**关系区块精简为**：
```
## 与冒险者的关系
阶段：{stage} | 好感：{approval} | 信任：{trust} | 恐惧：{fear} | 浪漫：{romance}
当前时间：第{day}天 {period}
```

**Token 变化**：省 ~500 旧字段，加 ~400 新字段（黑板+area_situation），净减 ~100

**预估**：~100 行修改

---

### 3-D：NPC 任务联动移动（KI-05）

**改动**：

`app/game_core/orchestration/hooks/npc_schedule.py`：
- `BasicNpcScheduleProvider._scheduled_destination()` 开头新增检查：
  ```python
  # 检查 NarrativePlanSlice.npc_directives 中是否有带 destination 的活跃指令
  if state.has_slice("narrative_plan"):
      for directive in state.narrative_plan.npc_directives:
          if directive.get("npc_id") != npc_id or directive.get("consumed"):
              continue
          dest = directive.get("directive", {}).get("destination")
          if isinstance(dest, dict) and dest.get("area_id"):
              return dest.get("area_id"), dest.get("location_id"), dest.get("room_id")
  # 无指令时 fallback 到日程/全局规则
  ```

`app/game_core/planning/directive_contracts.py`：
- `direct_npc` 的 `directive` 字段（已是开放 Mapping）内可包含 `destination: {area_id, location_id?, room_id?}`
- 不需要修改验证逻辑（directive 是 freeform Mapping，destination 是其内部可选键）

**测试**：~6 个（指令优先于日程、无指令回退日程、指令过期后恢复日程、跨区域移动）

**预估**：~80 行

---

### 3-E：retire_quest 先发奖励（KI-09）

**改动**：

`app/game_core/planning/quest_manager.py` 的 `_apply_retire_quest()`：
- 执行 retire command 之前检查：
  ```python
  if all_objectives_completed and not rewards_claimed and rewards:
      # 通过 dispatcher 路由到 NpcDirector.assign_service
      self._dispatcher.apply_directive("assign_service", {
          "npc_id": self._find_receptionist(context),
          "service_id": f"reward_{quest_id}",
          "label": "领取任务报酬",
          "price": 0,
          "effects": NpcDirectorSubSystem._rewards_to_effects(rewards),
          "one_shot": True,
      }, context, current_tick=current_tick)
  ```

**注意**：`_rewards_to_effects` 已在 `npc_director.py` 中实现（Phase 6 产出），可直接复用。需确认是否需要改为 @staticmethod 或模块级函数以便跨模块调用。

**预估**：~50 行

---

## 阶段4：NPC 自主活动（依赖阶段1+2+3）

### 4-A：NpcAutonomyHook 核心

**改动**：

新建 `app/game_core/orchestration/hooks/npc_autonomy.py`：
- `NpcAutonomyHook`（HOOK_PRIORITY = 61，NpcSchedule P60 之后、SharedExperience P62 之前）
- `execute()` 逻辑：
  ```python
  async def execute(self, context):
      player_room = (context.state.player.current_area,
                     context.state.player.current_location,
                     context.state.player.current_room)

      # 同 room 的 NPC（不含队伍同伴——同伴在下面单独处理）
      colocated_npcs = self._find_colocated_npcs(context, *player_room)

      for npc_id in colocated_npcs:
          await self._run_npc_idle(npc_id, context)

      # 队伍同伴（无论在哪个 room 都空跑）
      for companion_id in context.state.party.members:
          await self._run_companion_idle(companion_id, context)
  ```

- `_run_npc_idle(npc_id, context)`：
  - 构建精简上下文（角色基础 + 黑板 + area_situation + 同 room NPC 列表）
  - 调用 LLM（temperature=0.3，max_tokens=500）
  - **工具集**（精简版，5 个）：
    ```
    update_blackboard(thoughts, goals, observations, mood, attitude_towards_player)
    update_feeling(dimension, delta)
    want_to_talk(reason)       → 触发 npc_wants_to_chat SSE
    discover_clue(clue_id)     → 调查区域内线索
    pass_turn()                → 什么都不做
    ```
  - 结果处理：黑板更新/好感变化/线索发现/想说话

- `_run_companion_idle(companion_id, context)`：
  - 同上，但多一个工具：`share_discovery(content)` → SSE `teammate_discovery`

- CampfireHook 简化：长休时 NpcAutonomyHook 的概率从默认值提高到 100%

**注册**：`defaults.py` 添加 `NpcAutonomyHook`

**测试**：~15 个

**预估**：~250 行

---

### 4-B：队友探索工具

**改动**：

新建 `app/game_core/narrative/exploration_tools.py`（或加入 character_tools.py）：

- `DiscoverClueTool`：
  - `name = "discover_clue"`，`allowed_roles = ["npc", "teammate"]`
  - 检查当前 location 的 `scoped_interactable_overlays` 中未被调查的线索
  - 执行 `investigate_clue` command
  - 结果写入 area_events

- `ShareDiscoveryTool`（队友专属）：
  - `name = "share_discovery"`，`allowed_roles = ["teammate"]`
  - 把自己发现的线索分享给玩家
  - 触发 SSE `teammate_discovery`

**测试**：~8 个

**预估**：~150 行

---

### 4-C：印象→黑板消化 + Planner 指令→黑板

**改动**：

NPC 空跑时（4-A 的 `_run_npc_idle()`），如果黑板为空（首次或旧存档）：
- 从 `npc_impressions` 读取历史印象 → 合成初始 `observations` + `attitude_towards_player`
- 从 `npc_directives` 读取活跃指令 → 写入 `goals`

Planner 发 `direct_npc` 时同时更新黑板：
```python
# NpcDirectorSubSystem._apply_direct_npc() 末尾：
goal_text = payload.get("directive", {}).get("npc_goal")
if goal_text and context.state.has_slice("relations"):
    existing = context.state.relations.get_blackboard(npc_id)
    goals = existing.get("goals", [])
    if goal_text not in goals:
        goals.append(goal_text)
    context.state.relations.update_blackboard(npc_id, {"goals": goals})
```

**预估**：~80 行

---

## 阶段5：收尾

### 5-A：Planner Prompt 调整（KI-08）

**改动**：`app/narrators.py` 中 Planner agent prompt 强调创建任务必须指定 rewards。

**预估**：~10 行

---

### 5-B：背景图动态化（KI-11）— 低优先级

**改动**：前端 `SceneBackground.tsx` 读取区域 tags + danger_level + area_situation 选择背景变体。

**预估**：~50 行前端代码

---

### 5-C：danger_level 语义化（KI-12）

被 area_situation 覆盖，无需额外改动。

---

## 依赖关系图

```
阶段1（基础设施，无依赖，可并行）
  ├── 1-A  area_situation + area_events
  ├── 1-B  npc_blackboards
  ├── 1-C  Clue 修复（依赖 1-A 的 append_area_event）
  └── 1-D  新增任务条件类型
        │
        ▼
阶段2（Osiris 改造，依赖 1-A）
  ├── 2-A  Osiris 去 LLM + 规则表
  ├── 2-B  Hook 整合（协调模式）
  └── 2-C  7 个指令不再由 Osiris 发射
        │
        ▼
阶段3（信息流通，依赖 1-A + 1-B + 2-*）
  ├── 3-A  NPC L2 注入 area_situation
  ├── 3-B  Planner 上下文注入 + 汇总
  ├── 3-C  NPC 提示词精简（黑板替代旧字段）
  ├── 3-D  NPC 任务联动移动
  └── 3-E  retire_quest 先发奖励
        │
        ▼
阶段4（NPC 自主活动，依赖全部前置）
  ├── 4-A  NpcAutonomyHook
  ├── 4-B  队友探索工具
  └── 4-C  印象→黑板消化
        │
        ▼
阶段5（收尾）
  ├── 5-A  Planner prompt 调整
  └── 5-B  背景图动态化
```

## 规模汇总

| 阶段 | 新增 | 删除 | 测试 | Agent 数（~300行/个） |
|------|------|------|------|---------------------|
| 阶段1（1-A~1-D） | ~550行 | 0 | ~36 | 2-3 个 |
| 阶段2（2-A~2-C） | ~650行 | ~500行 | ~31 | 2-3 个 |
| 阶段3（3-A~3-E） | ~330行 | ~200行 | ~12 | 2 个 |
| 阶段4（4-A~4-C） | ~480行 | ~50行 | ~23 | 2 个 |
| 阶段5（5-A~5-B） | ~60行 | 0 | 0 | 1 个 |
| **总计** | **~2070行** | **~750行** | **~102** | **~10 个** |

**净增**：~1320 行代码 + ~102 个测试

## 验证方式

每个阶段完成后：
```bash
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v
```

全部完成后端到端验证：
1. 启动服务，加载存档
2. 结算 → Osiris 纯机械执行（无 LLM 调用），查日志确认只有 set_flag/adjust_danger
3. NPC 对话 → 引用 area_situation 中的真实态势，不再编造
4. 查看存档 → NPC 黑板有内容（thoughts/goals/observations）
5. 接任务 → 相关 NPC 下个时间段移动到任务区域
6. 结算时同 room NPC 空跑 → 黑板更新（可通过查看 state 验证）
7. 队友空跑 → 发现线索 → area_events 记录
8. 线索调查 → outcome 文本正确展示 + 写入 area_events

## 施工记录

每个子任务完成后更新 `app/施工记录（持续更新）/narrative.md`：
- 阶段1：D-WE01a ~ D-WE01d
- 阶段2：D-WE02a ~ D-WE02c
- 阶段3：D-WE03a ~ D-WE03e
- 阶段4：D-WE04a ~ D-WE04c
- 阶段5：D-WE05a ~ D-WE05b
