# C8: 全面切换到 BehaviorEngine — 弃用 AreaRuntime 事件系统

> **状态：已完成** | 181 测试通过，0 C8 相关失败

## Context

C7 双写已完成（171 测试全通过），WorldGraph/BehaviorEngine 与 AreaRuntime 并行运行。用户要求：**全面放弃旧有世界底层，立刻上线新系统**。已 git 提交，无回退顾虑。

**设计文档对齐**：
- `世界活图详细设计.md` §0: "替代 AreaRuntime — 硬编码事件状态机 → 图上通用行为引擎"
- `世界活图详细设计.md` §8.2: 目标管线 = `A4: BehaviorEngine.tick("pre") → B: 工具操作 WorldGraph → C1: BehaviorEngine.tick("post")`
- `世界活图详细设计.md` §8.3: 工具直接操作 WorldGraph（`set_state` + `handle_event`）
- `世界活图详细设计.md` §11 Step C7 = "管线集成"，本次完成其从双写到全面切换
- `队友Agent化与世界活图计划.md` Phase C = 世界活图上线

**目标**：BehaviorEngine.tick() 成为**唯一事件系统**。AreaRuntime 仅保留非事件功能（区域上下文、子地点、访问记录）。

---

## 8 步实施

### Step 1: `app/world/graph_builder.py` — 补充 EVENT_DEF 节点属性

`_build_events()` 的 `evt_props` 缺少 `importance`、`on_complete`、`completion_conditions`。

**修改位置**: L507-514 的 `evt_props` 构造

```python
evt_props: Dict[str, Any] = {
    "chapter_id": ch_id,
    "narrative_directive": event.narrative_directive,
    "is_required": event.is_required,
    "is_repeatable": event.is_repeatable,
    "importance": "main" if event.is_required else "side",  # C8
}
if event.description:
    evt_props["description"] = event.description
if event.on_complete:                                        # C8
    evt_props["on_complete"] = event.on_complete
if event.completion_conditions:                              # C8
    evt_props["completion_conditions"] = event.completion_conditions.model_dump()
```

---

### Step 2: `app/runtime/session_runtime.py` — 新增 6 个方法 + 修改 2 处

#### 2a: `run_behavior_tick(phase)` — 统一 tick 入口（替代 pipeline 的 `_run_behavior_tick`）

```python
def run_behavior_tick(self, phase: str = "pre") -> Optional[Any]:
    """BehaviorEngine.tick() + narrative 同步 + 副作用。返回 TickResult 或 None。"""
    if not self._behavior_engine or self._world_graph_failed:
        return None
    ctx = self.build_tick_context(phase)
    if ctx is None:
        return None
    try:
        tick_result = self._behavior_engine.tick(ctx)
        logger.info("[SessionRuntime] tick(%s): %d fired, %d hints, %d events",
                    phase, len(tick_result.results), len(tick_result.narrative_hints),
                    len(tick_result.all_events))
        self._sync_tick_to_narrative(tick_result)
        self._apply_tick_side_effects(tick_result)
        return tick_result
    except Exception as exc:
        logger.error("[SessionRuntime] tick(%s) failed: %s", phase, exc, exc_info=True)
        return None
```

#### 2b: `_sync_tick_to_narrative(tick_result)` — 状态同步

```python
def _sync_tick_to_narrative(self, tick_result) -> None:
    if not self.narrative or not self.world_graph:
        return
    for nid, changes in tick_result.state_changes.items():
        if changes.get("status") != "completed":
            continue
        node = self.world_graph.get_node(nid)
        if not node or node.type != "event_def":
            continue
        if nid not in self.narrative.events_triggered:
            self.narrative.events_triggered.append(nid)
            self.mark_narrative_dirty()
            logger.info("[SessionRuntime] 同步事件完成: %s", nid)
```

#### 2c: `_apply_tick_side_effects(tick_result)` — XP/物品/同伴

```python
def _apply_tick_side_effects(self, tick_result) -> None:
    for event in tick_result.all_events:
        if event.event_type == "xp_awarded":
            amount = event.data.get("amount", 0)
            if amount and self.player and hasattr(self.player, "xp"):
                self.player.xp = (self.player.xp or 0) + amount
                self.mark_player_dirty()
                logger.info("[SessionRuntime] 副作用: +%d XP", amount)
        elif event.event_type == "item_granted":
            if self.player:
                inventory = getattr(self.player, "inventory", None)
                if inventory is not None and hasattr(inventory, "append"):
                    inventory.append(event.data)
                    self.mark_player_dirty()
                    logger.info("[SessionRuntime] 副作用: +物品 %s", event.data)
    # 同伴分发
    self._dispatch_completed_events_to_companions(tick_result)
```

#### 2d: `_dispatch_completed_events_to_companions(tick_result)`

```python
def _dispatch_completed_events_to_companions(self, tick_result) -> None:
    if not self.companions or not self.world_graph:
        return
    from app.runtime.models.companion_state import CompactEvent
    game_day = self.time.day if self.time else 1
    area_id = self.player_location or ""
    for nid, changes in tick_result.state_changes.items():
        if changes.get("status") != "completed":
            continue
        node = self.world_graph.get_node(nid)
        if not node or node.type != "event_def":
            continue
        compact = CompactEvent(
            event_id=nid, event_name=node.name,
            summary=node.properties.get("description", node.name),
            area_id=area_id, game_day=game_day,
            importance=node.properties.get("importance", "side"),
        )
        for companion in self.companions.values():
            if hasattr(companion, "add_event"):
                companion.add_event(compact)
```

#### 2e: `check_chapter_transitions()` — 替代 AreaRuntime.check_chapter_transition()

```python
def check_chapter_transitions(self) -> Optional[Dict[str, Any]]:
    """从 WorldGraph GATE 边评估章节转换。"""
    if not self.world_graph or not self.narrative:
        return None
    from app.world.models import WorldNodeType
    current_chapter = self.narrative.current_chapter
    for ch_id in self.world_graph.get_by_type(WorldNodeType.CHAPTER.value):
        if ch_id == current_chapter:
            continue
        node = self.world_graph.get_node(ch_id)
        if not node or node.state.get("status") != "active":
            continue
        # 找到 GATE 边获取转换信息
        edges = self.world_graph.get_edges_between(current_chapter, ch_id)
        transition_type, narrative_hint, priority = "normal", "", 0
        for key, edge_data in edges:
            if edge_data.get("relation") == "gate":
                transition_type = edge_data.get("transition_type", "normal")
                narrative_hint = edge_data.get("narrative_hint", "")
                priority = edge_data.get("priority", 0)
                break
        return {"target_chapter_id": ch_id, "transition_type": transition_type,
                "priority": priority, "narrative_hint": narrative_hint}
    return None
```

#### 2f: `get_event_summaries_from_graph(area_id)` — 替代 get_area_context 事件段

```python
def get_event_summaries_from_graph(self, area_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not self.world_graph:
        return []
    target = area_id or self.player_location
    if not target:
        return []
    summaries = []
    for eid in self.world_graph.find_events_in_scope(target):
        node = self.world_graph.get_node(eid)
        if not node:
            continue
        status = node.state.get("status", "locked")
        if status not in ("available", "active"):
            continue
        entry = {
            "id": node.id, "name": node.name,
            "description": node.properties.get("description", ""),
            "status": status,
            "importance": node.properties.get("importance", "side"),
        }
        if node.properties.get("narrative_directive"):
            entry["narrative_directive"] = node.properties["narrative_directive"]
        if status == "active" and node.properties.get("completion_conditions"):
            from app.runtime.area_runtime import AreaRuntime
            hint = AreaRuntime._summarize_completion_conditions(
                None, node.properties["completion_conditions"]
            )
            if hint:
                entry["completion_hint"] = hint
        summaries.append(entry)
    return summaries
```

#### 2g: 修改 `enter_area()` — 删除 `initialize_events_from_chapter` 调用

L488-511 的事件初始化块整块删除（事件在 WorldGraph 中，不在 AreaRuntime）。

#### 2h: 修改 `_restore_area()` — 删除 `initialize_events_from_chapter` 调用

L299-320 的事件初始化块整块删除。

---

### Step 3: `app/services/admin/pipeline_orchestrator.py` — 切换到 BehaviorEngine

#### 3a: A4 阶段 (L96-100) 替换

```python
# 旧:
pre_area_updates = session.current_area.check_events(session) if session.current_area else []
pre_tick_result = self._run_behavior_tick(session, "pre", pre_area_updates)

# 新:
# A4: BehaviorEngine pre-tick (C8: 唯一事件系统)
pre_tick_result = session.run_behavior_tick("pre")
```

#### 3b: narrative_hints 注入 (L109-111) — 保持不变

#### 3c: C1 阶段 (L181-186) 替换

```python
# 旧:
post_updates = session.current_area.check_events(session) if session.current_area else []
post_tick_result = self._run_behavior_tick(session, "post", post_updates)

# 新:
# C1: BehaviorEngine post-tick (C8: 唯一事件系统)
post_tick_result = session.run_behavior_tick("post")
```

#### 3d: C1b 章节转换 (L188-193) 替换

```python
# 旧:
chapter_transition = (session.current_area.check_chapter_transition(session) if session.current_area else None)

# 新:
# C1b: 章节转换（从 WorldGraph GATE 边）
chapter_transition = session.check_chapter_transitions()
```

#### 3e: 修复 post_updates 引用

搜索 `post_updates` 的其他引用（约 L328），替换为从 `post_tick_result` 提取：

```python
# 旧: for u in post_updates: ...
# 新:
if post_tick_result:
    for nid, changes in post_tick_result.state_changes.items():
        if changes.get("status") == "completed" and nid not in all_event_ids:
            node = session.world_graph.get_node(nid) if session.world_graph else None
            if node and node.type == "event_def":
                all_event_ids.append(nid)
```

#### 3f: 删除 3 个方法

- `_run_behavior_tick()` (L413-433)
- `_log_dual_write_comparison()` (L435-453)
- `_sync_event_completions()` (L455-468)

---

### Step 4: `app/services/admin/v4_agentic_tools.py` — 切换到 WorldGraph

#### 4a: 重写 `activate_event()` (L1178-1266)

核心逻辑：
1. `wg.get_node(event_id)` 查找（非 `area_rt.events` 遍历）
2. 若 locked → 尝试 `engine.tick()` 刷新
3. `wg.merge_state(event_id, {"status": "active"})`
4. `engine.handle_event(WorldEvent("event_activated", ...))` 传播
5. 返回 `node.properties["narrative_directive"]`

#### 4b: 重写 `complete_event()` (L1268-1342)

核心逻辑：
1. `wg.get_node(event_id)` 查找
2. `wg.merge_state(event_id, {"status": "completed"})`
3. 从 `node.properties["on_complete"]` 读取副作用 → 直接应用（XP/物品/同伴）
   - `add_xp`: `session.player.xp += amount`
   - `add_items`: `inventory.append(item)`（沿用旧代码风格）
   - `unlock_events`: 由 tick cascade 自动处理
4. 更新 `narrative.events_triggered`
5. `engine.tick(ctx)` → cascade 解锁 + `_sync_tick_to_narrative` + `_apply_tick_side_effects`
6. 分发 CompactEvent 到同伴
7. 收集 `newly_available_events` 返回

#### 4c: navigate() — 已在 C7c 中完成，保持不变

#### 4d: 新增辅助方法

- `_apply_on_complete_from_graph(on_complete, event_id, node)` — 副作用应用
- `_dispatch_event_to_companions_from_graph(event_id, node)` — 同伴分发

#### 4e: 删除 2 个旧方法

- `_wg_sync_event_status()` (L59-68) — 吸收到主逻辑
- `_wg_emit_event()` (L70-92) — 吸收到主逻辑

---

### Step 5: `app/runtime/context_assembler.py` — 章节转换

#### 5a: `_get_chapter_context()` (L157-162) 替换

```python
# 旧:
area = getattr(session, "current_area", None)
if area and hasattr(area, "check_chapter_transition"):
    transition_ready = area.check_chapter_transition(session)
    if transition_ready:
        ctx["chapter_transition_available"] = transition_ready

# 新:
if hasattr(session, "check_chapter_transitions"):
    transition_ready = session.check_chapter_transitions()
    if transition_ready:
        ctx["chapter_transition_available"] = transition_ready
```

---

### Step 6: `app/runtime/area_runtime.py` — 剥离事件逻辑

#### 6a: `get_area_context()` 事件段 (L583-602) 替换

```python
# 旧: for event in self.events: ...
# 新:
event_summaries = []
if session and hasattr(session, "get_event_summaries_from_graph"):
    event_summaries = session.get_event_summaries_from_graph(self.area_id)
```

#### 6b: 删除事件相关方法

- `check_events()` (L376-456)
- `check_chapter_transition()` (L462-527)
- `_parse_condition_group()` (L715-729)
- `_evaluate_conditions()` (L731-774)
- `_eval_single()` (L776-798)
- 9 个条件处理器 (L802-940)
- `_CONDITION_HANDLERS` (L1010-1020)
- `_apply_on_complete()` (L1026-1076)
- `_dispatch_event_to_companions()` (L1077-1108)
- `initialize_events_from_chapter()` (L248-277)

#### 6c: 保留（非事件功能）

- `get_area_context()` (修改后), `get_location_context()`, `record_action()`
- `load()`, `persist_state()`, `unload()` — 删除事件相关部分
- `_summarize_completion_conditions()`, `_summarize_group()`, `_summarize_single_condition()` — 保留为工具方法

#### 6d: 清理 `__init__` / `load()` / `persist_state()`

- `__init__`: 保留 `self.events = []`（兼容，不再使用）
- `load()`: 删除 `_load_events()` 相关
- `persist_state()`: 删除事件持久化循环

---

### Step 7: `app/config.py` — 删除 dual_write 开关

删除 `world_graph_dual_write` (L127)。保留 `world_graph_enabled` (L126) 作为安全开关。

---

### Step 8: 测试

#### 8a: `tests/test_c8_migration.py` — 新测试 (9 个)

| 测试 | 验证 |
|------|------|
| test_tick_replaces_check_events | run_behavior_tick 返回 TickResult，事件状态正确转换 |
| test_activate_event_worldgraph | 工具从 WorldGraph 节点操作，不依赖 area_rt.events |
| test_complete_event_side_effects | on_complete 的 add_xp/add_items 正确应用到 player |
| test_chapter_transition_from_graph | GATE 条件满足后 check_chapter_transitions 返回正确目标 |
| test_event_summaries_from_graph | get_event_summaries_from_graph 输出格式匹配旧 get_area_context 事件段 |
| test_narrative_sync | tick 产出 completed 事件 → narrative.events_triggered 更新 |
| test_cascade_unlock | 完成 A（unlock_events: [B]）→ cascade tick → B 变 available |
| test_companion_dispatch | 完成事件 → CompactEvent 分发到同伴 |
| test_world_graph_disabled_fallback | WORLD_GRAPH_ENABLED=false → 管线不崩溃 |

#### 8b: 更新 `tests/test_c7_integration.py` — 移除双写测试

双写比较测试不再适用，更新为 C8 单写模式。

#### 8c: 回归测试

```bash
pytest tests/test_c8_migration.py -v
pytest tests/test_graph_builder.py tests/test_behavior_engine.py tests/test_event_propagation.py tests/test_snapshot.py -v
pytest tests/ -v -k "not fastapi_to_mcp"
```

---

## 实施顺序

```
1. graph_builder: 补充 evt_props                    (低风险，纯增)
2. session_runtime: 6 个新方法 + 2 处修改           (中风险，新代码)
3. pipeline_orchestrator: 切换 + 删除双写           (高风险，核心管线)
4. v4_agentic_tools: 重写事件工具                   (高风险，工具行为变化)
5. context_assembler: 章节转换切换                  (低风险，小改)
6. area_runtime: 剥离事件逻辑                       (中风险，大删除)
7. config: 删除 dual_write                          (低风险)
8. 测试 + 全量回归                                  (验收)
```

---

## 关键文件

| 文件 | 变更类型 |
|------|----------|
| `app/world/graph_builder.py` | 增：evt_props 补充 |
| `app/runtime/session_runtime.py` | 增+改：6 新方法，删 2 处事件初始化 |
| `app/services/admin/pipeline_orchestrator.py` | 改+删：A4/C1/C1b 切换，删 3 方法 |
| `app/services/admin/v4_agentic_tools.py` | 重写：activate/complete_event，删 2 方法，增 2 方法 |
| `app/runtime/context_assembler.py` | 改：章节转换 5 行 |
| `app/runtime/area_runtime.py` | 大删：~600 行事件逻辑，改 get_area_context |
| `app/config.py` | 删：1 行 |
| `tests/test_c8_migration.py` | 新：9 测试 |
| `tests/test_c7_integration.py` | 改：移除双写测试 |

## 已确认的 API

- `WorldGraph.find_events_in_scope(area_id) → List[str]` ✓
- `WorldGraph.get_edges_between(src, tgt) → List[Tuple[str, Dict]]` ✓
- `WorldGraph.get_by_type(type_str) → List[str]` ✓
- `SessionRuntime.mark_player_dirty()` ✓
- `SessionRuntime._dirty_player` ✓
- `AreaRuntime._summarize_completion_conditions()` — 保留为工具方法 ✓
- `player.inventory.append()` — 沿用旧代码风格（无 add_item 方法）✓
