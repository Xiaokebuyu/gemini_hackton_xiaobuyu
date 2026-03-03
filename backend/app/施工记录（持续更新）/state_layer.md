# ❸ 状态层施工记录

**设计文档**：状态层设计规范（已读）.md
**代码路径**：`app/game_core/state/`
**Phase**：0B（基类）+ 3（具体 Slice）

## 模块状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `StateSlice` ABC | [完成] | restore/serialize/snapshot/dirty 完整 |
| `StateContainer` | [完成] | typed properties + apply(delta) |
| `StateDelta` / `StateChange` | [完成] | changes + reason + metadata |
| `SupportsStateChange` Protocol | [完成] | runtime_checkable，所有 Slice 隐式满足 |
| `TimeSlice` | [完成] | day/slot/period + 时段映射 + apply_state_change |
| `PlayerSlice` | [完成] | 属性/背包/装备/法术/效果/职业资源 完整 |
| `RelationSlice` | [完成] | dispositions/stages/factions/impressions/shops |
| `QuestSlice` | [完成] | MilestoneState dataclass + dynamic_quests 双区模型 |
| `FlagSlice` | [完成] | 通用 key-value |
| `AreaSlice` | [完成] | 探索/危险/容器/敌对区/临时子区域 |
| `EventSlice` | [完成] | active/pending/rumors + pop_due_pending |
| `PartySlice` | [完成] | members/approval/shared_experiences |
| `SceneSlice` | [完成] | entries + state_changes + visibility 过滤 |
| `NarrativePlanSlice` | [完成] | 规划元数据 + 行为窗口 |

## 决策记录

### [D-S01] typed properties via TYPE_CHECKING + cast

见 `骨架搭建.md` [D-002]。避免循环导入，运行时零开销。

### [D-S02] SupportsStateChange 用 Protocol 而非继承

`internal.py` 中定义 `@runtime_checkable class SupportsStateChange(Protocol)`。
所有 Slice 通过结构子类型自动匹配，无需显式继承。
好处：StateSlice ABC 保持精简，apply_state_change 不污染公共接口。

### [D-S03] QuestSlice MilestoneState dataclass

见 `骨架搭建.md` [D-004]。
- `MilestoneState(state, activated_tick, completed_tick)`
- `from_dict()` 兼容 str 和 dict 两种输入
- `advance_milestone()` 自动记录 ACTIVE/COMPLETED 时间戳

### [D-S04] NarrativePlanSlice 额外字段保留

`play_style_tags`、`behavior_window`、`ticks_since_milestone_progress`、`next_scheduled_tick`
来源于叙事规划子系统设计规范的更详细设计。保留但标记为"来自子系统规范"。

### [D-S05] SceneEntry visibility 三值模型

```
public  → 所有人可见
system  → 仅引擎内部（GM/NPC/队友 Agent 不可见）
private → 按 audience 列表精确控制
```

`get_for_character()` 过滤逻辑：排除 system，private 需 audience 包含该角色。

## 接口变更

### [I-S01] milestone_states 类型变更

- **Before**：`dict[str, str]`（milestone_id → 状态字符串）
- **After**：`dict[str, MilestoneState]`（milestone_id → 结构化状态）
- **序列化兼容**：snapshot 输出 dict，restore 接受 str 或 dict

## D-S01: setattr 后门修复（P1 结构性清理）

**日期**：2026-02-28

3 个 Slice 的 `apply_state_change()` 使用 `setattr(self, change.path, change.value)` 作为 fallback，允许未经类型校验的任意属性写入。

**修复方式**：白名单 + 类型强转
- **TimeSlice**：`_MUTABLE_FIELDS: ClassVar` → `{day: int, slot: int, accumulated: float, action_count: int}`
- **PlayerSlice**：`_SIMPLE_FIELDS: ClassVar` → 15 个标量字段 + 显式分支处理 stats/class_features/equipment/active_effects/concentration/known_spells/prepared_spells/save_proficiencies
- **NarrativePlanSlice**：`_SIMPLE_FIELDS: ClassVar` → 6 个标量字段 + 显式分支处理 current_target_milestone/next_scheduled_tick/play_style_tags

## D-S02: snapshot() 浅拷贝修复

**日期**：2026-02-28

多个 Slice 的 `snapshot()` 对嵌套 dict/list 仅做 `dict()` 浅拷贝，改为 `deepcopy`：
- **PlayerSlice**：`spell_slots`、`class_resources`、`active_effects`、`equipment`
- **QuestSlice**：`dynamic_quests`
- **AreaSlice**：`container_states`、`properties`

与 PlayerSlice.concentration 已有的 `deepcopy` 先例对齐。

## D-S03: 6 个 StateSlice validate() 补齐

**日期**：2026-02-28

此前 10 个 Slice 中 4 个（Player/Time/Quest/Area）已有 validate() 实现，剩余 6 个返回空列表。

**补齐清单**：
- **FlagSlice**（~10 行）：容器类型 + key 非空字符串
- **PartySlice**（~25 行）：members/approval/experiences 类型检查
- **SceneSlice**（~20 行）：SceneEntry 实例 + visibility 枚举 + source 非空
- **RelationSlice**（~35 行）：5 字段各自容器+值类型校验
- **EventSlice**（~35 行）：canonicalize 不变量（id==event_id==key, state==status）+ trigger_tick 类型
- **NarrativePlanSlice**（~40 行）：标量边界 + behavior_window 上限 24 + list[dict] 批量检查

**测试**：`tests/test_slice_validation.py`，6 个测试类 × 2 方法 = 12 新测试

## [D-S06] 状态层缺漏修复（2026-03-02）

**问题**：对照设计文档排查后发现 1 个运行时 Bug + 5 个缺失 Read API + 1 处代码气味 + 3 处文档命名分歧。

#### Fix-1：`RelationSlice.reduce_stock()` dirty 追踪 Bug

`reduce_stock()` 成功扣减库存后未设 `self._dirty = True`，导致变更不会被 `export_dirty()` 捡到，无法持久化。在 `return True` 前加一行修复。

#### Fix-2：`FlagSlice.get_all()` 补齐

设计文档 §3.5 有此 API，补充实现（`dict(self.flags)` 防御拷贝）。

#### Fix-3：`QuestSlice` 补齐两个 Read API

- `get_active_quests()` — 返回 `status in {in_progress, active, accepted}` 的动态任务
- `get_completion(chapter_id)` — 返回章节完成度，未知章节返回 0.0

#### Fix-4：`PartySlice` 补齐两个 Read API

- `get_shared_experiences(with_character=None)` — 新增可选 participant 过滤参数（向后兼容）
- `count_critical_moments(with_character)` — 统计含该角色且 `critical_moment=True` 的经历数（❺ NPC运行时规范 §382 消费端）

#### Fix-5：`StateContainer.create_new()` 气味消除

`player_slice.restore(player_slice.snapshot())` 和 `narrative_plan_slice.restore(narrative_plan_slice.snapshot())` 替换为 `clear_dirty()`。`_dirty` 在 `StateSlice.__init__` 已为 False，restore(snapshot()) 的唯一副作用是 `clear_dirty()`，直接调用更清晰。

#### Fix-6：设计文档命名分歧同步

| 分歧 | 代码（正确） | 文档（已更正） |
|------|------------|------------|
| FlagSlice write | `remove(key)` | ~~`delete(key)`~~ → `remove(key)` |
| RelationSlice write | `set_relationship_stage()` | ~~`set_stage()`~~ → `set_relationship_stage()` |
| QuestSlice DynamicQuest 字段 | `status` | ~~`state`~~ → `status` |

**测试**：新建 `tests/test_state_read_apis.py`，19 个测试（4 类 × dirty/API/边界）

**测试基线**：811 passed（792 + 19 新增，不含预存在的 spell handler 失败 13 个）

## [D-S07] 状态层剩余项收尾（2026-03-02）

### Fix-A：EventSlice validate() 状态值白名单

新增 `_VALID_STATES: ClassVar[frozenset[str]]`，含六个合法态：
`dormant / triggered / active / resolved / expired / cancelled`

在 `validate()` 的 active_events 循环中，检查 `state not in _VALID_STATES`。

### Fix-B：EventSlice.trigger() 便捷方法

设计文档 §3.7 Write API 列出 `trigger(event_id)` 但代码缺失（`resolve()` 已有）。
新增 `trigger()` → `set_state(event_id, "triggered")`，与 `resolve()` 对称。

**测试**：在 `tests/test_state_read_apis.py` 追加 3 个测试（22 passed total）

### 关闭说明（无代码修改）

| 项目 | 定性 | 处置 |
|------|------|------|
| `advance_quest()` fallthrough | 有意设计 | `world_state.py` 中 `_resolve_quest_kind("auto")` 是上层语义，状态层 fallthrough 是其底层支撑，不改 |
| `AreaSlice.tick_expiry()` | 状态层已完整 | 该方法已正确实现（递减/移除/dirty）。"触发回调/hostile 同步"属编排层 Hook 职责，不在状态层 |
| `EventSlice.spread_rumor()` | 推迟，等 schema | rumor schema 无 `id` 字段；实现需破坏 serialize/restore，且谣言系统整体零消费端，等 AI Osiris create_rumor 深化时统一加 ID |
| `PlayerSlice.add_xp()` 阈值 | 跨层，转规则层 | 设计规范 §3.6 GrowthHandler 管升级，ClassRegistry.xp_curve 是数据源。状态层只存 xp/level，升级判断从 add_xp() 迁出属规则层工作 |

## 填充 TODO

- [x] 各 Slice 的 `validate()` 实现 — D-S03 完成
- [x] PlayerSlice：等级提升阈值表 — D-S07：架构偏差，迁移至规则层 GrowthHandler
- [x] AreaSlice：临时子区域过期清理逻辑 — D-S07：状态层已完整，调用时机属编排层
- [x] EventSlice：6 状态事件状态机完整转换校验 — D-S07 Fix-A：validate() 加白名单完成

## [D-S04] S-3：EventSlice trigger_condition 完整迁移（2026-03-01）

**问题**：`WorldStateHandler._compute_schedule_event()` 将 `trigger_condition` 预算为整数 `trigger_tick` 后丢弃原始条件信息；`EventSlice` 的 `pop_due_pending()` API 设计与规范不符；缺少 `check_triggers()` / `resolve()` Read API。

**迁移策略**：原始 `trigger_condition` + `created_at` 直接存储，在 `check_triggers()` 中懒惰求值；向后兼容旧数据（`trigger_tick` 字段自动降级处理）。

#### 修改 `app/game_core/state/slices/events.py`

- 新增模块级 `_absolute_tick(time_dict) -> int` 辅助函数
- 删除 `pop_due_pending(current_tick: int)`
- 新增 `check_triggers(current_time, current_flags=None, current_location=None) -> list[dict]`：评估条件、移除并返回到期 pending 事件
- 新增 `_is_condition_met(event, current_abs, current_flags, current_location)` 静态方法：支持 `absolute_tick` / `time_slots_elapsed` / 旧 `trigger_tick` 降级
- 新增 `resolve(event_id: str)`：`set_state(event_id, "resolved")`
- 更新 `validate()`：`trigger_condition must be dict`（兼容旧 `trigger_tick must be int`）

#### 修改 `app/game_core/rules/handlers/world_state.py`

- `_compute_schedule_event()`：存储 `trigger_condition + created_at`，不再存储 `trigger_tick`
- 新增 `_normalize_trigger_condition(params)`：`trigger_tick: N` → `{"type": "absolute_tick", "tick": N}`；`trigger_condition: dict` 直接保存
- `_validate_schedule_event()` 仍调用 `_resolve_trigger_tick()` 做合法性校验（不影响存储）

#### 修改 `app/game_core/orchestration/hooks/scheduled_event.py`

- `pop_due_pending(current_tick)` → `check_triggers(current_time)` 调用
- active_event 存储 `trigger_condition`（来自 pending_event 或旧 `trigger_tick` 降级重构），去掉 `trigger_tick` 字段
- SSE payload 使用 `trigger_condition`

#### 测试更新（3 文件，13 处）

- `tests/test_scheduled_event_hook.py`：5 处 `trigger_tick` → `trigger_condition: {"type": "absolute_tick", "tick": N}` + `created_at`
- `tests/test_world_state_handler.py`：2 处断言对齐存储格式
- `tests/test_slice_validation.py`：3 处 validate() 断言对齐

**测试基线**：719 passed（零回归，S-3 为迁移改动）

## 待办：Read API 补齐 + 文档对齐 [挂起]

**来源**：2026-03-01 边界审查，对比设计文档与实现的逐字段差异。

| Slice | 匹配度 | 缺失项 |
|-------|--------|--------|
| EventSlice | 60% | 架构偏差（trigger_tick vs trigger_condition）、缺 check_triggers/resolve/spread_rumor |
| PartySlice | 65% | 缺 get_members/get_approval/get_shared_experiences/count_critical_moments |
| RelationSlice | 70% | 缺 get_disposition/get_stage/get_faction/get_shop_state、reduce_stock |
| QuestSlice | 85% | 缺 get_active_quests/get_completion、chapter_completion 范围 0-1 vs 文档 0-100 |
| FlagSlice | 85% | 缺 get_all()、delete vs remove 命名差异 |
| AreaSlice | 92% | 缺 count_dynamic_sub_areas/has_cluster_capacity |

**处理原则**：
- Read API getter 缺失优先补齐（上层逻辑直接依赖）
- EventSlice 架构偏差需先确认哪种方案保留，再更新文档或代码
- API 命名差异按"代码为准、更新文档"方向处理（除非代码命名明显不如文档）

## [增量执行计划] Phase 2 — AreaSlice 运行时字段补全

**日期**：2026-03-03
**基线**：977 → 989 passed（+5 新测试，其余含 Batch 1-4/1-6 等外部改动），0 regression

**背景**：内容层 Phase 1 已完成，`AreaSlice` 对应的 `interactable_states` 字段缺失，5 个 read API 方法缺失。

**主要改动（`app/game_core/state/slices/area.py`）**：

1. **`AreaState` 新增字段**：
   - `interactable_states: dict[str, dict[str, Any]]` — one-time 交互物使用状态

2. **`AreaState.snapshot()`**：新增 `"interactable_states": deepcopy(self.interactable_states)`

3. **`_coerce_area_state()` 两路径补全**：
   - AreaState 分支：`interactable_states={k: dict(v) for k, v in raw.interactable_states.items()}`
   - dict 分支：`raw.get("interactable_states", {})` 向后兼容（旧存档无此字段不 crash）

4. **新增 5 个 Read API 方法**：
   - `get_discovered(area_id) -> set[str]`
   - `is_hostile_cleared(area_id, sub_area_id) -> bool`（读 `hostile_tracking` status=="cleared"）
   - `is_container_opened(area_id, container_id) -> bool`（读 `container_states` opened/looted）
   - `is_trap_detected(area_id, interactable_id) -> bool`（读 `container_states` trap_detected）
   - `is_interactable_used(area_id, interactable_id) -> bool`（读 `interactable_states` used）

5. **新增 Write 方法**：`mark_interactable_used(area_id, interactable_id)`

6. **`apply_state_change()` 新分支**：`interactable_states.*` 路径（must include area_id in value）

7. **`validate()` 扩展**：`interactable_states must be a dict` 检查

**关键设计决策**：
- `is_trap_detected` 读 `container_states`（trap 信息跟随容器状态），不读 `interactable_states`
- `validate()` 只做 is dict 检查，内部结构不做强校验（与 `container_states` 保持同等粒度）
