# L1 状态层 (State Layer)

> 路径：`backend/app/game_core/state/`
> 职责：游戏世界的全部可变状态，以不变性 + 原子 delta 为核心设计原则。

---

## 一、整体结构

```
state/
├── base.py            — StateSlice ABC + StateContainer
├── delta.py           — StateDelta + StateChange（变更描述）
├── internal.py        — SupportsStateChange Protocol（容器内部协议）
├── quest_runtime.py   — 任务运行时辅助
├── __init__.py
└── slices/
    ├── time.py          — 时间（day/slot/period/accumulated）
    ├── player.py        — 玩家（HP/金/背包/法术/状态效果）
    ├── area.py          — 区域（探索/容器/NPC位置/公告栏）
    ├── quests.py        — 任务（里程碑状态/动态任务）
    ├── flags.py         — 旗帜（无 schema 的 key-value）
    ├── events.py        — 事件队列（触发条件状态机）
    ├── party.py         — 队伍（成员/好感/共同经历）
    ├── relations.py     — 关系（NPC倾向/关系阶段/商店库存）
    ├── scene.py         — 场景（当前 tick 叙事缓冲，不持久化）
    └── narrative_plan.py — 叙事规划（指令/里程碑大纲/能力）
```

---

## 二、核心数据结构

### StateChange — 变更原子

```python
@dataclass(slots=True)
class StateChange:
    slice: str      # 目标切片名，如 "player"、"relations"
    operation: str  # "set" | "add" | "modify" | "remove"
    path: str       # 点分路径，如 "npc_dispositions.goblin_chef.approval"
    value: Any      # 要写入的值
```

### StateDelta — 变更批次

```python
@dataclass(slots=True)
class StateDelta:
    changes: list[StateChange] = []
    reason: str = ""              # 审计上下文，如 "combat_attack"
    metadata: dict[str, Any] = {}
```

### StateContainer — 聚合容器

```python
class StateContainer:
    # 工厂方法
    @classmethod
    def create_new(cls, world: WorldInstance) -> StateContainer
        # 从 WorldInstance 初始化全部 10 个切片

    @classmethod
    def create_restored(cls, world, session_data) -> StateContainer
        # 从存档恢复 9 个切片（scene 永远重置）

    # 唯一变更入口
    def apply(self, delta: StateDelta) -> None
        # 遍历 changes → 按 slice 名分发到对应切片的 apply_state_change()

    # 持久化
    def export_dirty(self) -> dict[str, dict]  # 只导出脏切片
    def snapshot(self) -> dict[str, dict]      # 完整只读快照
    def mark_clean(self, slice_names) -> None

    # 类型化访问器（10 个 property）
    @property time -> TimeSlice
    @property player -> PlayerSlice
    @property areas -> AreaSlice
    @property quests -> QuestSlice
    @property flags -> FlagSlice
    @property events -> EventSlice
    @property party -> PartySlice
    @property relations -> RelationSlice
    @property scene -> SceneSlice
    @property narrative_plan -> NarrativePlanSlice
```

---

## 三、10 个 StateSlice 详解

### 1. TimeSlice — 时间
| 字段 | 类型 | 说明 |
|------|------|------|
| `day` | int | 天数（从 1 开始）|
| `slot` | int | 时段（1-24，8=黎明）|
| `period` | str | "dawn"/"day"/"dusk"/"night" |
| `accumulated` | float | 累积行动时间，≥1.0 触发结算 |
| `action_count` | int | 本 tick 行动次数 |

**关键方法**：`absolute_tick()` → `(day-1)*24 + slot`；`get_period_info()` → 危险系数/商业状态/NPC活动

---

### 2. PlayerSlice — 玩家角色
| 字段分组 | 关键字段 |
|----------|---------|
| 身份/成长 | `character_id`, `level`, `xp`, `character_class`, `subclass`, `class_features` |
| 战斗 | `hp`, `max_hp`, `ac`, `stats(str/dex/con/int/wis/cha)`, `proficiency_bonus` |
| 位置 | `current_area`, `current_location`, `current_room` |
| 背包 | `inventory: list[ItemStack]`, `equipment: dict[slot, item]`（13个装备槽）|
| 法术 | `spell_slots: dict[level, {current,max}]`, `known_spells`, `prepared_spells`, `concentration` |
| 状态效果 | `active_effects: list[{effect_id, remaining_duration, prevents_action, ...}]` |
| 经济 | `gold`, `guild_rank`, `guild_reputation` |

**关键方法**：`get_modifier(stat)` → `(stat-10)//2`；`is_action_prevented()`；`add_xp(amount)→bool(升级了?)`

---

### 3. AreaSlice — 区域探索
核心数据结构 `AreaState`（per-area）：

| 字段 | 说明 |
|------|------|
| `exploration` | "undiscovered"/"discovered"/... |
| `danger_level` | 浮点危险系数 |
| `npc_locations` | `{npc_id → location_id}` |
| `npc_rooms` | `{npc_id → room_id}` |
| `container_states` | `{id → {opened,looted,trap_detected,remaining_items,...}}` |
| `interactable_states` | `{id → {used: bool}}` |
| `hostile_tracking` | `{sub_area_id → {status:"active/cleared", enemies, cleared_at_tick}}` |
| `board_bulletins` | `{board_id → [{quest_id,title,content,published_at_tick}]}` |
| `discovered_items` | 已发现的物品 ID 集合 |
| `temporary_sub_areas` | 动态生成的临时子区域 |

---

### 4. QuestSlice — 任务
| 字段 | 类型 | 说明 |
|------|------|------|
| `milestone_states` | `dict[id, MilestoneState]` | 每个里程碑的状态机（LOCKED/AVAILABLE/ACTIVE/COMPLETED/FAILED）|
| `dynamic_quests` | `dict[id, dict]` | 动态生成的任务（in_progress/accepted/completed/retired）|
| `chapter_completion` | `dict[chapter_id, float]` | 章节完成度 0.0-1.0 |

---

### 5. FlagSlice — 全局旗帜
```python
flags: dict[str, Any]  # 完全无 schema，任意 key-value
```
用于：触发条件检测、剧情开关、冷静期时间戳等。

---

### 6. EventSlice — 事件队列
| 字段 | 说明 |
|------|------|
| `active_events` | 已激活的事件（state 状态机）|
| `pending_events` | 等待触发条件的事件 |
| `rumors` | 谣言（{rumor_id, known_by: list[npc_id]}）|

**`check_triggers()`** — 评估 pending_events 的条件类型：
- `absolute_tick`：绝对 tick 数
- `time_slots_elapsed`：相对时段数
- `period_reached`：到达某时段
- `location_entered`：进入某区域/位置
- `flag_set`：某旗帜等于某值

---

### 7. PartySlice — 队伍
| 字段 | 说明 |
|------|------|
| `members` | `{character_id → member_data}` |
| `companion_approval` | `{character_id → int}` 队伍好感分 |
| `shared_experiences` | 共同经历列表（战斗/任务/休息，含 critical_moment 标记）|

---

### 8. RelationSlice — NPC 关系
| 字段 | 说明 |
|------|------|
| `npc_dispositions` | `{npc_id → {approval,trust,fear,romance}}` |
| `relationship_stages` | `{npc_id → stage}` stranger/acquaintance/friend/close_friend/intimate |
| `faction_standings` | `{faction_id → int}` |
| `npc_impressions` | `{npc_id → [tag,...]}` |
| `shop_states` | `{npc_id → {inventory:[{item_id,count}],...}}` |

**`reduce_stock(npc_id, item_id, count)`** — 两层深拷贝（RelationSlice → AreaSlice），防止库存引用泄漏。

---

### 9. SceneSlice — 场景（短暂，不持久化）
```python
@dataclass(slots=True)
class SceneEntry:
    source: str          # "gm" | "npc:id" | "teammate:id" | "system"
    content: str         # 叙事/对话文本
    visibility: str      # "public" | "private" | "system"
    audience: list[str]  # 私密对话的收听者
    tags: list[str]      # ["dialogue","action","thought",...]
    timestamp: float
    metadata: dict
```
**每次 tick 开始时 reset()，tick 结束时 drain 到 SSE 客户端，不写存档。**

---

### 10. NarrativePlanSlice — 叙事规划
| 字段分组 | 关键字段 |
|----------|---------|
| 规划状态 | `current_chapter`, `current_target_milestone`, `chapter_completion`, `last_run_tick`, `pacing_frozen` |
| 指令 | `npc_directives: [{npc_id,directive,consumed,expires_at_tick}]` |
| 升级 | `escalation_level`, `ticks_since_milestone_progress` |
| 行为窗口 | `behavior_window`（最近 24 条行动记录）|
| 知识 | `story_facts`（LLM 提取的三元组）, `actor_knowledge`（WKG 快照）|
| 能力 | `npc_capabilities: {npc_id → [{capability_id,expiry_tick}]}` |
| 大纲 | `milestone_outline: {target_milestone_id, steps:[{index,description,completed,quest_id}]}` |
| 临时 NPC | `temporary_npcs: {npc_id → data}` |

---

## 四、路径分发速查

| 切片 | 示例 path | operation |
|------|----------|-----------|
| time | `"accumulated"` | add |
| player | `"hp"`, `"gold"`, `"stats.str"`, `"spell_slots.1"` | set/add/modify |
| areas | `"areas.frontier_town.exploration"` | set |
| areas | `"areas.frontier_town.board_bulletins.board_id"` | add |
| quests | `"milestone_states.ch1_main"` | set |
| quests | `"dynamic_quests.quest_001.status"` | modify |
| flags | `"flags.met_the_witch"` | set/remove |
| events | `"active_events.evt_001"` | set |
| party | `"companion_approval.milim"` | add |
| relations | `"npc_dispositions.goblin_chef.approval"` | add |
| relations | `"relationship_stages.goblin_chef"` | set |
| scene | `"entries"` | add（只增不改）|
| narrative_plan | `"npc_directives"` | add |
| narrative_plan | `"escalation_level"` | modify |

---

## 五、关键设计模式

| 模式 | 说明 |
|------|------|
| **防御性拷贝** | 所有 `get_*()` / `snapshot()` 返回深拷贝，外部修改不影响内部 |
| **脏标记** | 每个切片有 `_dirty: bool`，`export_dirty()` 只保存改动切片，节省 IO |
| **不变性保证** | `container.apply(delta)` 是唯一写入路径，无 setter 公开暴露 |
| **验证钩子** | 每个切片 `validate() → list[str]` 在持久化前检查不变量 |
| **Schema 弹性** | FlagSlice / NarrativePlanSlice 部分字段用 `dict[str, Any]`，支持运行时扩展无需迁移 |
| **SceneSlice 短暂性** | Scene 不写存档，不参与 `create_restored()`，每 tick 重建 |

---

## 六、初始化 vs 恢复

```
新会话：StateContainer.create_new(world)
  ├─ TimeSlice          ← day=1, slot=8
  ├─ PlayerSlice        ← 从 CharacterTemplate seed
  ├─ AreaSlice          ← 从 MapRegistry seed（npc_locations 初始位置）
  ├─ QuestSlice         ← 从 QuestRegistry seed（所有里程碑 LOCKED/AVAILABLE）
  ├─ RelationSlice      ← 从 CharacterRegistry seed（base_disposition）
  ├─ FactionSlice       ← 从 FactionRegistry seed
  ├─ EventSlice         ← 从 InitialEvent seed
  ├─ PartySlice         ← 空
  ├─ NarrativePlanSlice ← 空框架
  └─ SceneSlice         ← 空（永远）

存档恢复：StateContainer.create_restored(world, session_data)
  └─ 同上 9 个切片从 JSON 水化，SceneSlice 始终跳过
```
