# P31：Planner 运行时崩溃与体验缺陷排查

## 背景

P30 接通了 `current_target_milestone` 链路后，live 测试暴露一系列运行时问题。
经排查，**致命根因是 `narrators.py` 的 context formatter 字段不匹配**——
所有 Planner 子系统（quest_manager / npc_director / world_builder / narrative_weaver / blackboard）
在格式化上下文时全部 crash，导致 **Planner 从未成功运行过一次**。

这解释了为什么之前 P22-P29 的功能看不到效果：不仅 `current_target_milestone` 链路断裂，
Planner 本身也因为 formatter 崩溃而从未执行过任何 directive。

---

## 问题清单

### 1. [致命/已修] `area_cluster['has_capacity']` KeyError

**文件**: `app/narrators.py:1152-1154`

**根因**: `narrative_planner.py` 在 A5a 中把 `area_cluster` 结构从 `{has_capacity: bool}` 改为
`{remaining_permanent: int, remaining_total: int}`，但 `narrators.py` 的 `_format_planner_context()`
没有同步更新，仍用 `area_cluster['has_capacity']` 硬索引访问。

**影响**: 所有 Planner 子系统在 evaluate() 时调用 `_format_planner_context()` → KeyError →
子系统异常被 `_run_and_drain` 捕获但 **不重试** → 该轮所有 directive 丢失。
每次 settlement tick 都会重复崩溃。

**状态**: ✅ 已修复（改为 `.get()` 防御性访问 + 正确字段名）

---

### 2. [高] NPC 私聊触发器不检查子地点共位

**文件**: `app/game_core/orchestration/hooks/private_chat_trigger.py:216-222`

**根因**: `_collect_reachable_npcs()` 返回两个集合：
- `colocated_npc_ids` — 与玩家同子地点的 NPC
- `area_npc_locations` — 整个 area 所有 NPC

但 execute() 中用的是 `all_area_npc_ids = set(area_npc_locations.keys())`（line 218），
即整个 area 的 NPC 都参与触发判定。非共位 NPC 也会生成 `npc_wants_to_chat` SSE 事件，
只是标记 `colocated=False`。

**表现**: 玩家收到不在同一子地点的 NPC 的私聊请求 → 响应时被 `utterance_orchestration.py`
的 `validate_presence()` 拒绝 → 报错 "npc_not_present"。用户体验差：NPC 发来请求但无法响应。

**修复方案**: 将 line 218 改为 `all_area_npc_ids = set(colocated_npc_ids)`，
只有共位 NPC + 队友才参与触发。非共位 NPC 的私聊请求不应该发给玩家。

---

### 3. [中] `update_quest` 指令大面积被拒

**日志**: `planner_update_quest only supports active quests`

**文件**:
- `app/game_core/rules/handlers/planner.py:161-162` — validate 检查 `status != "active"` 时拒绝
- `app/narrators.py:1033-1039` — formatter 把所有状态的 quest 混在一起展示

**根因**: `_build_planner_context()` 中 `dynamic_quests` 包含所有状态（available/active/completed）的任务，
`_format_planner_context()` 也不做区分，全部列为 `Dynamic quests: dq_xxx(available), dq_yyy(active)`。
LLM 看到 `available` 状态的任务也尝试 `update_quest`，但 handler 只接受 `active` 状态。

**修复方案**: 在 `_format_planner_context()` 中，将 quest 分为"可更新任务"（active）和
"可接取任务"（available）两个独立区间，并在 active 区间旁注明"只有 active 状态的任务可以 update_quest"。

---

### 4. [中] `dynamic sub-area capacity exceeded` 大面积拒绝

**日志**: `dynamic sub-area capacity exceeded (kind=fill_area/plant_environmental)`

**文件**:
- `app/game_core/rules/handlers/planner.py:1252-1256` — permanent ≥ 8 或 total ≥ 15 时拒绝
- `app/game_core/state/slices/area.py:672-683` — `count_dynamic_sub_areas()` 计数逻辑

**根因**: 容量上限 permanent=8 / total=15 。如果 Planner 在多轮 settlement 中反复 fill_area
（因为之前 crash 后重试），可能快速堆积 temporary_sub_areas 达到上限。
也可能是 Planner 不读 `area_cluster.remaining_total` 就盲目发指令。

**诊断需求**: 需在 live 环境检查 `state.areas.count_dynamic_sub_areas(area_id)` 的实际值。
如果 total 已经接近 15，说明之前的 fill_area 指令虽然被执行了但过期清理没生效。

**修复方案**:
1. 在 `_format_planner_context()` 中明确告知 LLM 当前剩余容量（`remaining_total` 已在 area_cluster 中）
2. 在 Planner 的 system prompt 中加入约束："fill_area/plant_environmental 前必须检查 remaining_total > 0"
3. 检查 `tick_expiry()` 是否在 settlement 中被正确调用（timed sub-areas 是否自动过期清理）

---

### 5. [低] 图片生成 `broken data stream`

**文件**: `app/routers/images.py:308`

**堆栈**: `Image.open(ref_path)` → PIL `load()` → `OSError: broken data stream`

**根因**: 参考图片（base portrait 的 neutral.png）在磁盘上可能：
- 文件写入未完成就被读取（race condition between write and read）
- 文件因为 Gemini API 返回了部分数据导致写入不完整
- 磁盘空间不足

**修复方案**: 在 `_generate_and_save_with_ref()` 的 `Image.open(ref_path)` 前加防御：
```python
if not ref_path.exists() or ref_path.stat().st_size == 0:
    logger.warning("reference image missing or empty: %s", ref_path)
    return False
try:
    base_img = Image.open(ref_path)
    base_img.load()  # 强制完整加载，检测损坏
except Exception:
    logger.exception("reference image corrupted: %s", ref_path)
    return False
```

---

### 6. [高] Bootstrap 任务使用英文标题，破坏沉浸感

**文件**: `app/game_core/planning/opening_bootstrap.py:47, 101-102`

**根因**: `_display_name()` 用 `milestone_id.replace("_", " ").title()` 生成标题：
`ms_arrival` → `"Ms Arrival"`（英文），而非使用 `MilestoneTemplate.title`（`"初到边境"`）。

Bootstrap 没有访问 `WorldInstance` 的能力（它是纯确定性的），所以无法查询内容注册表。

**影响范围**:
1. `opening_views.py:78` 确定性开场评论直接引用 seed quest 标题：
   `"在你真正迈步之前，公会已经替你准备好了一条线索：「Lead: Ms Arrival」。"` ← 英文破坏沉浸
2. `dynamic_quests["dq_ms_arrival"]` 中 Planner LLM 看得到这个英文标题
3. `quest_history` 中的 `create_quest` 记录也保留英文

**修复方案**（两个方向）:
- **方向 A**: 在 `bootstrap_opening_planner()` 调用时，将 `MilestoneTemplate.title` 注入 bootstrap 上下文，
  让 `OpeningBootstrapQuestAgent` 使用中文标题
- **方向 B**: 删除 bootstrap 的 `create_quest` directive，改为纯 `StateChange` 激活里程碑，
  让 Planner LLM 在第一次 settlement 时基于 milestone outline 生成中文任务

---

### 7. [中] `_build_planner_context()` 重复 "maps" key

**文件**: `app/game_core/orchestration/hooks/narrative_planner.py`

**根因**: 返回的 dict 中有两处定义 `"maps"` key：
- line 1139: `"maps": getattr(context.world, "maps", None) if context.world.has_registry("maps") else None`（防御性）
- line 1206: `"maps": context.world.maps if context.world.has_registry("maps") else None`（直接访问）

第二个覆盖第一个，功能上无差异但属于代码质量问题（Python dict 后者覆盖前者）。
应删除 line 1139 的重复项。

---

### 8. [高] LLM Planner 各子系统 prompt 大面积约束缺失

**文件**: `app/narrators.py` — 各子系统 system prompt

**系统性排查**（逐一对比 handler 中 `ExecuteResult.error()` 与对应 prompt 的覆盖度）:

#### 8a. QUEST_MANAGER — `update_quest` 字段范围和状态约束不足

- 规则 7 说"只能对 active 任务 update，字段增量合并"
- **缺失**：哪些字段可更新（objectives/description/summary）？哪些不行（status）？
- **缺失**：不能用 `update_quest` 改 status（应该用 `retire_quest`/`advance_quest`）
- **加上** context 中 quest 混合展示，LLM 无法快速定位哪些是 active

#### 8b. WORLD_BUILDER — 容量约束完全缺失 + interactables 结构缺失

- **完全没提容量约束**：不知道检查 `remaining_total` / `remaining_permanent`
- **容量双层语义不清**：permanent≤8 和 total≤15 是两个独立限制，需要同时检查
- `fill_area` 规则 3 只列了 `id/label/description`，**没提 `interactables` 字段**
  但 `_format_planner_context()` 中有 `fill_area_guidance` 说 interactables 要含 `id/name/description/type/tags/checks`
  → prompt 规则和格式化上下文的指导不一致
- `discover_room` / `fill_room` 两个指令在 handler 中存在（line 1345-1401），
  但 WORLD_BUILDER prompt 中**完全没有文档**
- `discoverable_rooms_hidden` 和 `dynamic_location_capacity` 字段在上下文中存在但 prompt 未说明如何使用

#### 8c. NARRATIVE_WEAVER — escalate/adjust_pacing 参数约束缺失

- `escalate` 的 `delta` 范围是 `[-3, 3]` 整数（handler line 1670），但 prompt **完全没提**
  → LLM 可能输出 `delta: 5` → 被拒
- `adjust_pacing` 要求 `frozen` 必须是**布尔值**（handler line 1674），
  但 prompt 只说"必须是 `{frozen: true|false}`"，LLM 可能输出字符串 `"true"` → 被拒

#### 8d. NPC_DIRECTOR — 临时 NPC 生命周期缺失

- `spawn_quest_npc` 的规则 5 说"优先复用已有 NPC"
- **缺失**：临时 NPC 默认 24 ticks 后自动清理（handler line 934-941 的 `despawn_in_ticks` 默认值）
- LLM 不知道临时 NPC 会消失，可能布局依赖临时 NPC 长期存在的剧情

#### 8e. 通用 — `_format_planner_context()` quest 展示混乱

- 所有状态的 quest 混在一行：`Dynamic quests: dq_x(active), dq_y(available), dq_z(completed)`
- LLM 无法快速定位 active quest → 频繁对非 active quest 执行 update_quest
- 应分为 "可 update 的任务(active)"、"可接取的任务(available)"、"已完成(readonly)" 三区间

#### 8f. 通用 — 容量展示格式易误导

当前格式：`remaining_permanent=7 | remaining_total=13`
LLM 可能误解 "remaining_permanent=7" 为"已有7个permanent"。
应改为：`permanent=7/8 available, total=13/15 available`

**完整拒绝原因清单**:

| 拒绝原因 | 触发条件 | 频率 | prompt 覆盖 |
|----------|---------|------|------------|
| `planner_update_quest only supports active quests` | 对 non-active quest 用 update_quest | **极高** | ⚠️ 弱 |
| `dynamic sub-area capacity exceeded` | permanent≥8 或 total≥15 | **极高** | ❌ 无 |
| `dynamic quest not found: {id}` | quest_id 拼错或已 retire | 中 | ⚠️ 弱 |
| `unknown area_id` | fill_area 的 area_id 不在允许列表 | 中 | ❌ 无 |
| `delta must be integer between -3 and 3` | escalate delta 超范围 | 低-中 | ❌ 无 |
| `frozen must be a bool` | adjust_pacing frozen 非布尔 | 低 | ⚠️ 弱 |
| `quest_id collides with milestone_id` | 新 quest_id 与 milestone ID 同名 | 低 | ❌ 无 |
| `shop not initialized` | curate_shop 对无 shop 的 NPC | 低 | ❌ 无 |
| `npc already present` | spawn 已存在的 NPC | 低 | ⚠️ 中 |
| interactables 格式错误 | fill_area 的 interactables 缺字段 | 中 | ❌ 完全缺失 |
| discover_room/fill_room 使用 | handler 存在但 prompt 无文档 | 低 | ❌ 完全缺失 |

---

### 9. [中] 图片生成并发竞态条件

**文件**: `app/routers/images.py:256-326`

**根因深层分析**:

当多个 emotion variant 请求并发到达时：
1. 请求 A: 生成 base portrait → `path.write_bytes(data)` (line 283)
2. 请求 B: 尝试读取 base portrait 作为 ref → `Image.open(ref_path)` (line 308)

`_GENERATION_SEMAPHORE` 只保护 API 调用（line 279/309），不保护磁盘 I/O。
如果请求 B 在 A 写入完成前读取，PIL 得到部分 PNG → `broken data stream`。

**修复方案**: 用原子写入（先写 `.tmp` 再 `rename`）+ 读取前验证文件完整性。

---

### 10. [低] Bootstrap 占位任务的里程碑激活路径

**排查确认**: `opening_bootstrap.py` 的 create_quest directive 包含
`metadata.source_milestone = milestone_id`，`planner_create_quest` handler 正确读取
`target_milestone = coerce_non_empty_string(metadata.get("source_milestone"))`，
所以 **里程碑激活链路（AVAILABLE→ACTIVE）是正确的**。P30 新增的 `current_target_milestone`
StateChange 也在同一路径中工作。

**残留问题**: bootstrap 创建的英文 quest（`dq_ms_arrival`、`"Lead: Ms Arrival"`）
虽然 `delivery_method=internal` 不出现在公告板，但仍然：
- 出现在 `dynamic_quests` 中，Planner LLM 看得到
- 出现在 `quest_history` 中
- 标题/描述是硬编码英文

**可选优化**: 删除 bootstrap 的 create_quest directive，改为只发
`StateChange("quests", "set", "milestone_states.{ms_id}", {"state": "ACTIVE"})` +
`StateChange("narrative_plan", "set", "current_target_milestone", ms_id)`。
让 Planner LLM 在第一次 settlement 时基于 milestone outline 生成中文任务。
但需要确认：没有 seed quest 时，milestone 的 AVAILABLE→ACTIVE 转换需要另一个触发点。

---

### 11. [致命] 开场无剧情背景、Planner 未运行导致空白开局

**这是当前最影响玩家体验的问题。**

#### 11a. 开场叙述缺少故事背景

**文件**:
- `app/game_core/narrative/context_builder.py:634-638` — `build_gm_opening_context()`
- `app/game_core/narrative/context_builder.py:170-200` — `GM_OPENING_PROMPT`

**根因**: 内容注册表中有丰富的叙事数据：
- `ChapterMeta.description`: "以边境小镇为核心，玩家从新手冒险者成长为可信赖的队伍成员..."
- `MilestoneTemplate.narrative_context`: "冒险者公会是边境小镇的心脏。柜台小姐用专业而温和的态度接待每一位来访者..."
- `MilestoneTemplate.key_elements`: ["公会登记", "柜台小姐的温柔关切", "任务板上密密麻麻的哥布林委托"]

但 `build_gm_opening_context()` 只是在标准 GM 上下文上加了 `opening=True` 标记。
L1 层（`_build_l1_full()`）包含 milestone_states 和 chapter_completion 等**结构数据**，
但**没有**注入 `ChapterMeta.description`、`MilestoneTemplate.narrative_context`、`key_elements` 等**叙事内容**。

**结果**: LLM 开场叙述只能基于物理场景（L2/L3 的地点、NPC、出口）生成泛泛描述，
不知道"这是第二卷边境的暗流"、不知道"柜台小姐的温柔关切"、不知道"任务板上密密麻麻的哥布林委托"。
确定性 fallback 更差："你站在Guild Hall，新的旅程就从这里开始。"——完全没有故事感。

**补充发现**: `agent_orchestration.py:2033-2051` 的 `_build_opening_user_message()` 也有问题：
- `opening_quest` 字段只传了 `.title`（即英文 "Lead: Ms Arrival"），没传 summary/narrative_context
- `present_npcs` 只传了名字，没传 NPC 的 backstory/personality
- `GM_OPENING_PROMPT` 声称 "You have the full opening context... the first quest hook..."，
  但实际传给 LLM 的只有 ID 级别的数据 → 提示词与现实不符

**修复方案**: 在 `build_gm_opening_context()` 中注入章节叙事：

```python
def build_gm_opening_context(self) -> dict[str, Any]:
    context = self.build_gm_context(hints=["opening_scene"])
    context["l7_engine_result"]["opening"] = True
    # 注入章节/里程碑叙事
    if self._world.has_registry("quests"):
        target_ms_id = None
        if self._state.has_slice("narrative_plan"):
            target_ms_id = self._state.narrative_plan.current_target_milestone
        if target_ms_id:
            ms = self._world.quests.get_milestone(target_ms_id)
            if ms:
                context["l7_engine_result"]["opening_milestone"] = {
                    "title": ms.title,
                    "narrative_context": getattr(ms, "narrative_context", ""),
                    "key_elements": ms.key_elements,
                    "involved_npcs": ms.involved_npcs,
                }
                if ms.chapter_id:
                    ch = self._world.quests.get_chapter(ms.chapter_id)
                    if ch:
                        context["l7_engine_result"]["opening_chapter"] = {
                            "title": ch.title,
                            "description": ch.description,
                        }
    return context
```

同时更新 `GM_OPENING_PROMPT`，在指令中加入：
```
If opening_chapter and opening_milestone are provided in context, weave their
narrative_context and key_elements into your narration naturally. This is the
story the player is stepping into — establish it without exposition-dumping.
```

#### 11b. Planner 未在开局运行 → 无可见任务

**根因**:
1. `opening_bootstrap.py` 只创建一个 `delivery_method: "internal"` 的 seed quest（标题是英文 "Lead: Ms Arrival"）
2. Planner（NarrativePlannerHook，priority=66）只在 settlement tick 中运行
3. 第一次 settlement 需要玩家做一个消耗时间的动作才会触发
4. 在那之前：**零可见任务、零 NPC 行为指令、零世界建设**

**结果**: 玩家开局后看到的是一个空壳世界——有场景描述但没有任务方向、没有 NPC 行为、
没有动态子地点。必须先做一个动作（比如和 NPC 说话）→ settlement 触发 → Planner 第一次运行 →
才开始生成任务和世界内容。这个"冷启动"期间玩家体验极差。

**修复方案**（两个互补方向）:

**方向 A: 在 opening stream 中触发一次 Planner settlement**

在 `gameplay.py:opening_stream` 的开场叙述之后、`stream_end` 之前，
插入一次 NarrativePlannerHook 的 execute()，让 Planner 在开局就生成：
- 第一个可见的中文任务（基于 milestone outline）
- NPC 行为指令
- 动态子地点填充

这样玩家看完开场叙述后，任务面板就有内容了。

```python
# opening_stream 中，narration 之后
# 触发 Planner 冷启动
planner_hook = session.runtime.resolve_planner_hook()
if planner_hook is not None:
    planner_context = _build_settlement_context(session)  # 需要构建
    planner_results = await planner_hook.execute(planner_context)
    for sse_event in planner_results:
        await queue.put(sse_event)
```

**可行性已验证**：
- `SettlementContext` 构造只需 `change_log=[]`、`state`、`world`、`scene_bus=SceneBus(SceneSlice())`、
  `rules_engine`、`apply_delta`（已在 `bootstrap_opening_planner()` line 629-638 中有先例）
- `NarrativePlannerHook.execute()` 不依赖 TickCoordinator 状态或累计时间机制
- `_ensure_milestone_outline()` 100% 安全：可选依赖 outline_generator，异常优雅降级
- 现有 `bootstrap_opening_planner()` 已经在 opening 中构建过 SettlementContext → 可直接复用模式

**方向 B: 确定性 fallback 开场叙述注入故事背景**

即使 LLM 不可用，`build_opening_narration()` 也应该从章节/里程碑数据中提取故事背景：

```python
def build_opening_narration(session: ManagedSession) -> str:
    # ... 现有场景描述逻辑 ...

    # 注入章节背景
    world = session.runtime.world
    if world.has_registry("quests"):
        chapters = world.quests.chapters()
        if chapters:
            lines.insert(0, chapters[0].description)
        # 注入第一个里程碑的叙事
        target_ms_id = None
        if session.runtime.state.has_slice("narrative_plan"):
            target_ms_id = session.runtime.state.narrative_plan.current_target_milestone
        if target_ms_id:
            ms = world.quests.get_milestone(target_ms_id)
            if ms and getattr(ms, "narrative_context", ""):
                lines.insert(1, ms.narrative_context)

    return " ".join(lines)
```

---

## 修复优先级

1. ✅ 问题 1 — 已修（`has_capacity` → `.get()` + 正确字段名）
2. ✅ **问题 11a** — 已修（开场叙述注入章节/里程碑叙事：LLM 上下文 + 确定性 fallback + user_message）
3. ✅ **问题 11b** — 已修（bootstrap 后设 `last_run_tick=-100`，首次 settlement 即通过冷却检查）
4. ✅ **问题 6** — 已修（runtime.py post-process：MilestoneTemplate.title 替换英文 + 移除 delivery_method）
5. ✅ 问题 2 — 已修（`colocated_npc_ids` 替换 `area_npc_locations.keys()`）
6. ✅ 问题 3+8 — 已修（Planner prompt 大修：quest 三区间 + 容量约束 + 各子系统约束补全）
7. ✅ 问题 7 — 已修（删除重复 maps key）
8. ✅ 问题 5+9 — 已修（图片原子写入 + 读取前验证）
9. 🔍 问题 4 — 待 live 诊断：capacity 上限是否需调整（当前 prompt 已告知 LLM 检查容量）
10. ✅ 问题 10 — 已被问题 6 的修复覆盖（bootstrap quest 现已中文可见，无需删除）

---

## 实施计划

### Phase A1（开场体验：最高优先级）

| 文件 | 改动 | 对应问题 |
|------|------|---------|
| `app/game_core/narrative/context_builder.py:634-638` | `build_gm_opening_context()` 注入 chapter.description + milestone.narrative_context + key_elements | #11a |
| `app/game_core/narrative/context_builder.py:170-200` | `GM_OPENING_PROMPT` 加入叙事上下文使用指令 | #11a |
| `app/agent_orchestration.py:2033-2051` | `_build_opening_user_message()` 注入 milestone narrative_context + key_elements | #11a |
| `app/opening_views.py:15-70` | `build_opening_narration()` 确定性 fallback 注入章节/里程碑叙事 | #11a |
| `app/routers/gameplay.py:736-813` | opening_stream 中 narration 后触发 Planner 冷启动 | #11b |
| `app/game_core/planning/opening_bootstrap.py:47` | 从 `_display_name()` 改为注入 `MilestoneTemplate.title` | #6 |

### Phase A2（Planner prompt 大修 — 消除高频拒绝）

| 文件 | 改动 | 对应问题 |
|------|------|---------|
| `app/game_core/orchestration/hooks/private_chat_trigger.py:218` | `area_npc_locations.keys()` → `colocated_npc_ids` | #2 |
| `app/narrators.py` `_format_planner_context()` | quest 三区间分离 + 容量展示格式优化 | #3, #8f |
| `app/narrators.py` QUEST_MANAGER prompt | 规则 7 加字段范围、禁止改 status | #8a |
| `app/narrators.py` WORLD_BUILDER prompt | 容量双层约束 + interactables 结构 + discover_room/fill_room | #8b |
| `app/narrators.py` NARRATIVE_WEAVER prompt | escalate delta[-3,3] + adjust_pacing 布尔值 | #8c |
| `app/narrators.py` NPC_DIRECTOR prompt | 临时 NPC 生命周期说明 | #8d |
| `app/game_core/orchestration/hooks/narrative_planner.py:1139` | 删除重复的 `"maps"` key | #7 |

### Phase A3（图片生成竞态修复）

| 文件 | 改动 | 对应问题 |
|------|------|---------|
| `app/routers/images.py:308` | 原子写入 + 读取前 `base_img.load()` 验证 | #5+9 |

### Phase B（需要 live 数据诊断后决定）

- 问题 4 的 capacity 上限是否需要调整
- 问题 10 是否删除 bootstrap 占位任务（改为纯 StateChange 激活）
