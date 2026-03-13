# L6 规划层 (Planning Layer)

> 路径：`backend/app/game_core/planning/`
> 职责：动态世界生成——任务/NPC/地图/物品/叙事节奏的 LLM 驱动规划，通过 Directive 系统驱动 RulesEngine。

---

## 一、整体结构

```
planning/
├── subsystem.py          — PlannerEvent + SubSystemResult + PlannerSubSystem Protocol + PlannerDispatcher
├── semantic_events.py    — 事件类型分类 + collect_planner_events()
├── directive_contracts.py — 18种 Directive 的数据契约 + 校验函数
├── dynamic_sub_area.py   — DynamicSubAreaManager（临时子区域）
├── quest_manager.py      — QuestManagerSubSystem
├── npc_director.py       — NpcDirectorSubSystem
├── narrative_weaver.py   — NarrativeWeaverSubSystem（生命周期/GC）
├── world_builder.py      — WorldBuilderSubSystem
├── pacing_controller.py  — PacingControllerSubSystem
├── item_designer.py      — ItemDesignerSubSystem
├── opening_bootstrap.py  — OpeningBootstrapQuestAgent（首次启动）
├── capabilities.py       — CapabilityDescriptor（运行时 NPC 能力）
└── utils.py              — coerce_non_empty_string / normalize_mapping
```

---

## 二、核心基础设施

### PlannerEvent — 规划事件

```python
@dataclass(slots=True)
class PlannerEvent:
    kind: str                    # 事件分类（见下方事件类型表）
    tick: int                    # 当前游戏 tick
    source: str = ""             # 来源标识
    priority: int = 0            # 排序优先级（越低越先）
    dedupe_key: str = ""         # 去重标识
    round_index: int = 0         # Planner 重放轮次
    emitter: str = ""            # 实际发射者
    payload: dict[str, Any] = {}
```

### SubSystemResult — 子系统输出

```python
@dataclass(slots=True)
class SubSystemResult:
    directives: list[Any] = []          # 要执行的 Directive 列表
    story_facts: list[dict] = []        # LLM 提取的世界知识三元组
    strategy_notes: str = ""            # 策略备注
    metadata: dict[str, Any] = {}
```

### PlannerSubSystem Protocol

```python
class PlannerSubSystem(Protocol):
    name: str                            # 唯一标识
    handles: frozenset[str]              # 处理的 directive kinds

    def accepts_event(event) → bool      # 是否接受此事件
    async def evaluate(event, context) → SubSystemResult  # 生成 directives
    def apply_directive(kind, payload, context, *, current_tick) → bool | str
```

### PlannerDispatcher

```python
class PlannerDispatcher:
    def register(subsystem: PlannerSubSystem) → None
    async def dispatch(event, context) → list[SubSystemResult]
    def apply_directive(kind, payload, context, *, current_tick) → bool | str

    # 忙碌语义：
    # - 执行中的子系统标记 busy
    # - 新事件入队（最大深度 3）
    # - evaluate() 完成后 FIFO 消费队列
    # - 最大递归深度 6，超限阻断
```

---

## 三、六大子系统

### 1. QuestManagerSubSystem

**接受事件**：bootstrap, milestone_*, quest_*

**处理的 Directive kinds**：`create_quest`, `publish_bulletin`, `retire_quest`, `update_quest`, `set_task_monitor`

**关键方法**：
- `_apply_create_quest()` → `planner_create_quest` 命令
- `_apply_publish_bulletin()` → `planner_publish_bulletin` + 向公告栏所在地的 resident NPC 发 `direct_npc`
- `_apply_update_quest()` → `planner_update_quest` + emit `quest_progress_updated` SSE
- `_resident_npcs_for_board()` → 解析公告栏位置，找驻留 NPC

---

### 2. NpcDirectorSubSystem

**接受事件**：quest_*, milestone_*, area_entered, relationship_stage_changed, combat_resolved

**处理的 Directive kinds**：`direct_npc`, `spawn_quest_npc`, `assign_capability`, `revoke_capability`

**关键方法**：
- `_apply_direct_npc()` → `planner_direct_npc` + 若 InstanceManager 存在则 `inject_directive(npc_id, directive, tick)`（实时注入 NPC 对话实例）
- `_apply_assign_capability()` → `planner_assign_capability`（运行时给 NPC 分配动态能力）
- `_apply_revoke_capability()` → `planner_revoke_capability`

---

### 3. NarrativeWeaverSubSystem（生命周期管理）

**接受事件**：tick_settlement, milestone_*, quest_expired, relationship_stage_changed, stagnation_threshold_reached, rest_completed

**不处理 Directive**（纯生命周期操作）：
- **Directive GC**：清理 NarrativePlanSlice 中已消费/过期的指令
- **临时 NPC 解散**：检查 quest_history 中 `despawn_tick` 已过的 NPC → 执行解散
- **自动升级安全网**：stagnation 达到阈值时发 `escalate` directive
  - 阈值：`[4, 7, 10, 13, 16]`（按 escalation_level 索引），兜底=6 ticks

---

### 4. WorldBuilderSubSystem

**接受事件**：area_entered, sub_location_entered, milestone_completed, world_event_*

**处理的 Directive kinds**：`plant_environmental`, `fill_area`, `plant_encounter`, `discover_room`, `fill_room`

**关键方法**：
- `_apply_plant_environmental()` → `planner_plant_environmental` + emit `environment_changed` SSE + SceneBus 系统日志
- `_apply_fill_area()` → 创建永久子区域 + `planner_fill_area` + emit `environment_changed`
- `_apply_plant_encounter()` → 在子区域植入战斗遭遇 + emit `encounter_planted`
- `_apply_discover_room()` → 解锁隐藏房间 + emit `room_discovered`
- `_apply_fill_room()` → 新增房间 + emit `dynamic_room_added`

---

### 5. PacingControllerSubSystem

**接受事件**：tick_settlement, stagnation_threshold_reached, milestone_*, quest_*, combat_resolved, rest_completed

**处理的 Directive kinds**：`escalate`, `adjust_pacing`

- `escalate(delta)` → `planner_escalate`（升高叙事张力）
- `adjust_pacing(frozen)` → `planner_set_pacing_frozen`（冻结/解冻节奏控制）

---

### 6. ItemDesignerSubSystem

**接受事件**：quest_created, quest_accepted, shop_refreshed

**处理的 Directive kinds**：`design_reward`, `curate_shop`

**白名单物品**（任务奖励限定）：
```
cheap_shortsword, throwing_dagger, sturdy_spear, standard_longsword,
scouts_hand_axe, heavy_war_hammer, leather_armor, dirty_chain_mail,
round_shield, healing_potion, basic_antidote, sulfur_smoke_ball, holy_water_flask
```

**关键方法**：
- `_normalize_reward_items()` — 合并重复物品 ID，累加数量
- `_build_player_snapshot()` — 提取玩家职业/属性快照
- `_build_reward_candidates()` — 主题化物品建议
- `_build_merchant_profile()` — NPC 专业化提示（用于 LLM shop curation）

---

## 四、Directive 系统

### 18 种 Directive 类型

```python
SUPPORTED_PLANNER_DIRECTIVE_KINDS = frozenset({
    # 任务
    "create_quest", "publish_bulletin", "retire_quest", "update_quest", "set_task_monitor",
    # NPC
    "direct_npc", "spawn_quest_npc", "assign_capability", "revoke_capability",
    # 世界
    "plant_environmental", "plant_encounter", "fill_area", "fill_room", "discover_room",
    # 叙事
    "escalate", "adjust_pacing",
    # 物品/奖励
    "design_reward", "curate_shop",
})
```

### Directive 数据契约（`directive_contracts.py`）

```python
class CreateQuestPlan:   quest_id: str, payload: dict
class DirectNpcPlan:     npc_id: str, directive: dict
class SpawnQuestNpcPlan: npc_id: str, payload: dict
class PlantEnvironmentalPlan: area_id: str, payload: dict
class PublishBulletinPlan:    board_id: str, payload: dict
class EscalatePlan:      delta: int, payload: dict
class AdjustPacingPlan:  frozen: bool, payload: dict
class RetireQuestPlan:   quest_id: str, payload: dict
class FillAreaPlan:      area_id: str, payload: dict
```

### 校验函数

```python
normalize_planner_directive(raw) → (kind, payload) | None
validate_planner_directive(raw, *, allowed_directives=None) → DirectiveValidationResult
expand_planner_directive(raw) → list[Any]    # 展开旧式多物品 payload
build_payload_digest(kind, payload) → dict   # 提取 ID 摘要（审计用）

@dataclass(slots=True)
class DirectiveValidationResult:
    ok: bool
    kind: str
    payload: dict
    reason_code: str | None
    payload_digest: dict
```

---

## 五、PlannerEvent 类型分类

| 优先级 | 类别 | 事件 kind |
|--------|------|----------|
| **10** | 任务事件 | bootstrap, milestone_available/activated/completed/failed, quest_accepted/created/updated/objective_completed/completed/retired/expired |
| **20** | 场景事件 | area_entered, sub_location_entered/left, scene_changed |
| **30** | 世界事件 | relationship_stage_changed, shop_refreshed, world_event_available/active/resolved/expired, rest_completed, combat_resolved |
| **40** | 健康事件 | area_sparse, stagnation_threshold_reached |
| **50** | 系统事件 | tick_settlement |

### collect_planner_events()

```python
collect_planner_events(context, current_tick, *, change_window_start, round_index, ...) → list[PlannerEvent]
```

处理顺序：
1. **Seed events**（外部注入）
2. **Action log** → board_accept/complete/retire_quest, move_area, enter_sub_location, refresh_shop, rest/combat 动作转化
3. **Change log** → 里程碑状态变化, 动态任务变化, 关系阶段变化, 商店状态变化, 玩家位置变化
4. **合成事件** → area_sparse（区域无动态子区域）, stagnation_threshold_reached（基于 escalation_level）
5. **去重 + 优先级排序**（priority + round_index + 插入顺序）

---

## 六、DynamicSubAreaManager

```python
class DynamicSubAreaManager:
    def create(area_id, spec) → dict | None
        # 校验集群容量（max 8 permanent per area）
        # 规范化 spec 字段
        # 返回子区域 dict 或 None（容量超限）

    def expire(area_id, sub_area_id) → bool
    def list_active(area_id) → list[dict]
    def get_cluster_status(area_id) → dict[str, int]  # temporary/permanent/total
    def tick_expiry(area_id, elapsed=1) → list[str]   # 返回已过期的 ID
```

### 子区域 Schema

```python
{
    "id": str,
    "label": str,
    "description": str,
    "tags": list[str],
    "type": str,              # "discovery"|"visit"|"shelter"|...
    "tier": str,              # "temporary"|"permanent"
    "discovery_mode": str,    # "auto"|"check"|...
    "discovery_dc": int,
    "hostile_config": Any,
    "interactables": list,
    "resident_npcs": list[str],
    "linked_quest_id": str | None,
    "linked_milestone": str | None,
    "source": str,
    "created_at_tick": int,
    "expiry": int,            # 剩余 tick 数，-1=无限
    "status": "active",
}
```

---

## 七、CapabilityDescriptor — 运行时 NPC 能力

```python
@dataclass(slots=True)
class CapabilityDescriptor:
    capability_id: str
    npc_id: str
    instruction: str          # 中文行为指引（注入 NPC system prompt）
    functional: str = ""      # UI 绑定类型
    functional_params: dict = {}
    assigned_tick: int = 0
    expiry_tick: int = 0      # 0 = 永久
    source: str = "planner"

# 合法的 functional 绑定类型：
VALID_FUNCTIONAL_TYPES = frozenset({
    "trade_browse", "quest_accept", "board_browse", "navigate",
    "inspect_item", "rest", ""
})
```

**用途**：NarrativePlannerHook 分配给 NPC → 注入 system prompt → NPC 行为动态扩展（如临时变成商人）

---

## 八、OpeningBootstrapQuestAgent

```python
class OpeningBootstrapQuestAgent:
    async evaluate(context) → dict
    # - 接受 "bootstrap" 事件
    # - 找第一个 available/active 里程碑
    # - 生成 create_quest directive
    # - 返回 {directives, story_facts, strategy_notes, metadata}

def build_opening_bootstrap_planner_system() → PlannerSystemAssembly
    # 最简规划系统（无 LLM agent），用于游戏开始时的确定性初始化
```

---

## 九、子系统-Directive 映射表

| 子系统 | 监听事件优先级 | 处理 Directives | 对应 RulesEngine 命令 |
|--------|-------------|----------------|---------------------|
| **QuestManager** | 10 | create_quest, publish_bulletin, retire_quest, update_quest, set_task_monitor | planner_create_quest 等 |
| **NpcDirector** | 10,20,30 | direct_npc, spawn_quest_npc, assign_capability, revoke_capability | planner_direct_npc 等 |
| **NarrativeWeaver** | 10,30,40,50 | —（生命周期） | planner_prune_npc_directives, planner_despawn_quest_npc |
| **WorldBuilder** | 20,30 | plant_environmental, fill_area, plant_encounter, discover_room, fill_room | planner_plant_environmental 等 |
| **PacingController** | 10,30,40,50 | escalate, adjust_pacing | planner_escalate, planner_set_pacing_frozen |
| **ItemDesigner** | 10,30 | design_reward, curate_shop | planner_design_reward, planner_curate_shop |
