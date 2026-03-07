# P9-B NarrativePlanner 投递系统完全对齐计划

## 问题定义

NarrativePlanner 的投递目标仍停留在 shell world 假设。默认 planner 硬编码 `board_id='board'`（5 处）和 `npc_id='guild_clerk'`（3 处），而 live world 中的真实对象是：
- 布告栏：`quest_board`（`adventurer_guild` 子地点下，tags 含 `quest_source`）
- 公会 NPC：`guild_girl`（`adventurer_guild` 驻留 NPC）

此外，设计文档（NarrativePlanner 设计规范 §3.1-§3.11 + 叙事规划子系统设计规范 §十一）定义了完整的投递体系，当前实现在上下文注入、指令结构、LLM prompt、任务生命周期、临时 NPC 注册、DynamicSubAreaManager、EventEngine 联动等多个维度存在差距。

本计划目标：**完全对齐设计文档**，不延后。

---

## 与设计文档的完整差距清单

| # | 设计规范要求 | 来源 | 现状 | 差距等级 |
|---|------------|------|------|---------|
| 1 | `publish_bulletin` 的 `board_id` 应为真实 interactable ID | §3.7 | 硬编码 `"board"` | **严重** |
| 2 | `direct_npc` 的 `npc_id` 应从里程碑/场景 NPC 选取 | §3.5 | 硬编码 `"guild_clerk"`（L2/L3 有 involved_npcs 消费但 fallback 仍是硬编码） | **严重** |
| 3 | `direct_npc` 的 `directive` 不能为空 | §3.5 隐含 | 允许 `directive={}` 落库，NPC prompt 产生空指令行 | **严重** |
| 4 | Planner 上下文 Part ④ 含 `area_npcs`/`factions`/`world_rules` | §3.1 | `area_npcs` 缺失；`factions`/`world_rules` 缺失 | 中等 |
| 5 | Planner 上下文 Part ② 含 `party` 队伍组成 | §3.1 | 缺失 | 中等 |
| 6 | Planner 上下文 Part ② 含 `play_style_tags` | §3.1 | Slice 有字段但未序列化到上下文 | 中等 |
| 7 | `publish_bulletin` 含 `location`（area + sub_location 二元定位） | §3.7 | 缺失 | 中等 |
| 8 | `publish_bulletin` 含 `notify_resident_npcs`（自动通知驻留 NPC） | §3.7 | 未实现 | 中等 |
| 9 | `publish_bulletin` 含 `urgency`/`posted_by`/`expiry_ticks` | §3.7 | 缺失 | 轻微 |
| 10 | LLM prompt 应含全部 9 种指令 | §八 | 只有 6 种（缺 spawn_quest_npc/plant_environmental/fill_area） | 中等 |
| 11 | LLM prompt 应含 7 个核心原则 + 升级等级表 | §八 | 极简 prompt，无原则无等级表 | 中等 |
| 12 | DynamicQuest 含 objectives/rewards/delivery/expiry | §6.2 | 仅 8/16 字段（缺 objectives/rewards/delivery_method/expiry_ticks/on_expire 等） | 中等 |
| 13 | 任务过期机制（expiry_ticks + on_expire: escalate/retire） | §6.1 | 未实现 | 中等 |
| 14 | `spawn_quest_npc` 应生成临时 NPC 实体（profile/dialogue_hook/despawn） | §3.4 | 仅 move_npc + 写 directive，不创建新 NPC | 中等 |
| 15 | Planner 上下文含 `area_boards`（当前区域可用布告栏列表） | §3.7 隐含 | 缺失 | 中等 |
| 16 | `spawn_quest_npc` 底层应复用 PasserbyService + 临时 NPC 在 context_builder 可被消费 | §3.4 | 临时 NPC profile 不可被 context_builder 消费 | 中等 |
| 17 | DynamicQuest.objectives 与 EventEngine 自动联动 | §6.3 | objectives 仅为记录，无自动完成判定 | 中等 |
| 18 | `plant_environmental`/`fill_area` 应委托 DynamicSubAreaManager | §十一 | 直接操作 AreaSlice，无三层持久性/簇容量管理 | 中等 |
| 19 | `play_style_tags` 应从行为窗口自动推导 | §7.3 隐含 | 需手动设置 | 轻微 |
| 20 | 完整 world_context（factions/world_rules）注入 planner | §3.1 Part ④ | 缺失 | 中等 |

---

## 分阶段方案

### Phase 1：基础设施（上下文补全 + 空值防护）

**目标**：为后续 Phase 提供正确的数据基础，同时堵住空 directive 漏洞。

**无外部依赖，可独立验收。**

#### Step 1-1：Planner 上下文补全

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_build_planner_context()` 方法（L324-426）

**改动内容**：在现有 `return` 字典中增加 5 个字段：

```python
# --- 在 area_cluster 构建逻辑之后（~L385 后）新增 ---

# 当前区域 NPC 列表
area_npcs: list[str] = []
# 当前区域布告栏列表（带 quest_source tag 的 interactable）
area_boards: list[dict[str, str]] = []  # [{"id": "quest_board", "sub_location": "adventurer_guild"}]

if context.state.has_slice("areas") and context.state.has_slice("player"):
    area_id = context.state.player.current_area
    if area_id and area_id in context.state.areas.areas:
        area_state = context.state.areas.areas[area_id]
        # NPC 列表：当前区域所有 NPC
        area_npcs = list(area_state.npc_locations.keys())
        # 布告栏列表：从内容层 MapRegistry 查 quest_source interactable
        if context.world.has_registry("maps"):
            area_tmpl = context.world.maps.get_area(area_id)
            if area_tmpl is not None:
                for sub_id, sub_tmpl in area_tmpl.sub_locations.items():
                    for ia in sub_tmpl.interactables:
                        if "quest_source" in ia.tags:
                            area_boards.append({
                                "id": ia.id,
                                "sub_location": sub_id,
                            })

# 队伍组成
party_members: list[dict[str, str]] = []
if context.state.has_slice("party"):
    for member_id in context.state.party.member_ids:
        party_members.append({"id": member_id})

# play_style_tags
play_style_tags = list(context.state.narrative_plan.play_style_tags)
```

在 return dict 中追加：

```python
"area_npcs": area_npcs,
"area_boards": area_boards,
"party": party_members,
"play_style_tags": play_style_tags,
```

**验证**：
- 单测：构造含 MapRegistry + AreaSlice + PartySlice 的 context，调用 `_build_planner_context()`，验证 4 个新字段存在且正确
- 单测：无 MapRegistry 时 area_boards 为空（不报错）

#### Step 1-2：Directive 空值防护

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `direct_npc` 分支（L597-636）

**改动内容**：在 L602 之后增加空 directive 拒绝逻辑：

```python
# 现有代码（L601-603）:
directive = payload.get("directive")
if directive is not None and not isinstance(directive, Mapping):
    return False

# --- 新增：拒绝空 directive ---
if not directive:  # None 或 {} 均拒绝
    return False
```

**同步改动**：`_normalize_directive()` 中 `DirectNpcPlan` 分支（L535-539）

```python
# 改为：拒绝空 directive
if isinstance(raw, DirectNpcPlan):
    if not raw.directive:
        return None  # 会被 _apply_normalized_decision 跳过
    return "direct_npc", {
        "npc_id": raw.npc_id,
        "directive": dict(raw.directive),
    }
```

注意 `_apply_normalized_decision()` 中需要处理 `_normalize_directive()` 返回 `None` 的情况（检查现有 L451-476 是否已有 None guard）。

**验证**：
- 单测：`directive={}` → `_apply_directive()` 返回 False
- 单测：`directive=None` → 返回 False
- 单测：`directive={"kind": "present_quest", "quest_id": "dq_1"}` → 返回 True
- 回归：现有 planner 测试全部通过

#### Step 1-3：LLM Prompt 上下文格式化补全

**改动文件**：`app/narrators.py`

**改动位置**：`_format_planner_context()` 函数（L221-322）

**改动内容**：

Part ② 玩家行为画像增加 party 和 play_style_tags：

```python
# ~L289 后新增：
party = ctx.get("party", [])
if party:
    party_strs = [m.get("id", "?") for m in party]
    lines.append(f"Party members: {', '.join(party_strs)}")

style_tags = ctx.get("play_style_tags", [])
if style_tags:
    lines.append(f"Play style: {', '.join(style_tags)}")
```

Part ④ 世界上下文增加 area_npcs 和 area_boards：

```python
# ~L311 后新增：
area_npcs = ctx.get("area_npcs", [])
if area_npcs:
    lines.append(f"Area NPCs: {', '.join(area_npcs)}")

area_boards = ctx.get("area_boards", [])
if area_boards:
    board_strs = [f"{b['id']}@{b['sub_location']}" for b in area_boards]
    lines.append(f"Quest boards: {', '.join(board_strs)}")
```

**验证**：
- 现有 _format_planner_context 相关测试通过
- 新单测：含新字段的上下文能正确格式化

---

### Phase 2：核心修复（消除 shell world 常量 + publish_bulletin 对齐）

**依赖 Phase 1**（需要上下文中的 `area_boards` 和 `area_npcs`）。

#### Step 2-1：Planner 动态解析 board_id

**改动文件**：`app/game_core/planning/planner.py`

**改动位置**：`_normalize_context()` 方法（L428-469）和所有消费 board_id 的地方

**改动内容**：

`_normalize_context()` 增加 `area_boards` 解析：

```python
# ~L468 后新增：
raw_boards = context.get("area_boards", [])
area_boards = []
if isinstance(raw_boards, list):
    for b in raw_boards:
        if isinstance(b, Mapping) and b.get("id"):
            area_boards.append(dict(b))
default_board_id = area_boards[0]["id"] if area_boards else None

# 加入返回 dict：
"area_boards": area_boards,
"default_board_id": default_board_id,
```

`_try_seed_quest()`（L70-82）：

```python
# 替换 L73 的 "board_id": "board"
board_id = ctx.get("default_board_id")
# 只在有 board 时才发 publish_bulletin
if board_id:
    directives.append({
        "kind": "publish_bulletin",
        "payload": {
            "board_id": board_id,
            ...
        },
    })
```

`_l1_hint()`（L199-224）：同理，方法签名增加 `ctx` 参数，从 `ctx["default_board_id"]` 获取。无 board 时跳过 publish_bulletin，只做 escalate。

`_level_response()`（L171-197）：传递 `ctx` 到 `_l1_hint()`。

**验证**：
- 单测：上下文含 `area_boards=[{"id": "quest_board", "sub_location": "adventurer_guild"}]` → bulletin board_id 为 `"quest_board"`
- 单测：上下文无 area_boards → planner 不发 publish_bulletin（降级为只 escalate）
- 回归：全部现有 planner 测试（需调整 fixture 提供 area_boards）

#### Step 2-2：Planner 动态解析 npc_id

**改动文件**：`app/game_core/planning/planner.py`

**改动位置**：`_normalize_context()` + `_try_seed_quest()` + `_l2_recommend()` + `_l3_urgent()`

**改动内容**：

`_normalize_context()` 增加：

```python
"area_npcs": self._normalize_strings(context.get("area_npcs", [])),
```

NPC 选择优先级链（抽取为辅助方法）：

```python
def _pick_npc(self, ctx: dict[str, Any], target_detail: dict[str, Any] | None = None) -> str | None:
    """选择投递 NPC：在场 involved > 不在场 involved > 场景 NPC > None。"""
    involved = (target_detail or {}).get("involved_npcs", [])
    area_npcs = set(ctx.get("area_npcs", []))
    for npc_id in involved:
        if npc_id in area_npcs:
            return npc_id
    if involved:
        return involved[0]
    if area_npcs:
        return next(iter(area_npcs))
    return None
```

`_try_seed_quest()`、`_l2_recommend()`、`_l3_urgent()` 中替换所有 `"guild_clerk"` 为 `self._pick_npc(ctx, target_detail)`。无 NPC 时条件性跳过 direct_npc 指令。

**验证**：
- 单测：`area_npcs=["guild_girl"]`, `involved_npcs=["guild_girl"]` → npc_id 为 `"guild_girl"`
- 单测：`area_npcs=["merchant"]`, `involved_npcs=["guild_girl"]` → npc_id 为 `"guild_girl"`
- 单测：`area_npcs=["merchant"]`, 无 involved → npc_id 为 `"merchant"`
- 单测：`area_npcs=[]`, 无 involved → seed 不发 direct_npc，L2 返回 None

#### Step 2-3：publish_bulletin 结构扩展

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `publish_bulletin` 分支（L638-652）

**改动内容**：

- 增加 `location`（area + sub_location）
- 增加 `urgency` / `posted_by` / `expiry_ticks`
- 增加 `notify_resident_npcs`：为 true 时，查 MapRegistry 获取 sub_location 的 resident_npcs，为每个 NPC 递归调用 `_apply_directive("direct_npc", ...)` 注入 `bulletin_awareness` 指令

**同步改动**：`planner.py` 的 `_try_seed_quest()` 和 `_l1_hint()` payload 增加 location + notify_resident_npcs 字段。

**验证**：
- 单测：含 location + notify_resident_npcs=True → 驻留 NPC 自动收到 directive
- 单测：无 MapRegistry → notify 逻辑不报错
- 单测：expiry_ticks 正确存储

---

### Phase 3：LLM Prompt 对齐

**依赖 Phase 1**。

#### Step 3-1：重写 AgenticNarrativePlanner System Prompt

**改动文件**：`app/narrators.py`

**改动位置**：`AgenticNarrativePlanner._SYSTEM_PROMPT`（L179-194）

**改动内容**：替换为设计文档 §八 的完整 prompt 模板：
- 身份说明：叙事规划器，不是 GM
- 7 个核心原则（不偏离主线、自然融入、尊重节奏、递进不跳跃、适应风格、避免重复、保持紧凑）
- L0-L5 升级等级表
- 全部 9 种指令类型及简要参数说明
- JSON 输出格式约束
- **关键约束**：npc_id 必须来自 Area NPCs，board_id 必须来自 Quest boards

**验证**：
- AgenticNarrativePlanner 现有测试通过
- 人工审查 prompt 内容与设计 §八 一致

---

### Phase 4：DynamicQuest 结构补全 + 任务过期

**建议在 Phase 2 之后执行**。

#### Step 4-1：扩展 DynamicQuest 字段

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `create_quest` 分支（L572-595）

**改动内容**：quest_payload 增加设计文档 §6.2 的缺失字段：

```python
quest_payload = {
    # --- 现有字段 ---
    "quest_id": quest_id,
    "status": status,
    "title": ...,
    "summary": ...,
    "source": "narrative_planner",
    "created_at_tick": current_tick,
    # --- 新增字段（§6.2）---
    "target_milestone": ...,     # 从 metadata.source_milestone 提取
    "urgency": ...,              # 从 metadata.urgency 或默认 "medium"
    "objectives": [...],         # list[dict]，可为空
    "rewards": {...},            # dict，可为空
    "delivery_method": ...,      # 投递方式标识
    "expiry_ticks": ...,         # int | None
    "on_expire": ...,            # "ignore" | "escalate" | "retire"
    "generated_by_escalation": ...,  # int
    "planner_reasoning": ...,    # str
    "metadata": ...,
}
```

**同步改动**：`planner.py` L3 的 create_quest payload 增加 objectives / rewards / expiry_ticks / on_expire / generated_by_escalation。

**验证**：
- 单测：带 objectives/rewards 的 create_quest → 正确存储
- 单测：不带新字段的 create_quest → 默认值填充（向后兼容）

#### Step 4-2：任务过期机制

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`execute()` 方法中 Directive GC 之后（~L224）

**改动内容**：新增任务过期检测逻辑：
- 遍历 `dynamic_quests`，跳过 completed/retired/expired
- 检查 `current_tick - created_at_tick >= expiry_ticks`
- 按 `on_expire` 执行策略：
  - `"retire"` → `retire_dynamic_quest()` + history 记录
  - `"escalate"` → `adjust_escalation(1)` + retire + history
  - `"ignore"` → 仅标记 expired
- 发出 SSE `dynamic_quest_expired` 事件

**验证**：
- 单测：quest created tick=5, expiry=10, current=16 → expired
- 单测：on_expire="escalate" → escalation +1
- 单测：on_expire="retire" → retired + history
- 单测：无 expiry_ticks → 不过期

---

### Phase 5：spawn_quest_npc 基础实现

**建议在 Phase 4 之后**。

#### Step 5-1：扩展 spawn_quest_npc 指令

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `spawn_quest_npc` 分支（L683-710）

**改动内容**：
- 自动生成 npc_id（`_temp_npc_{tick}`）
- 存储 NPC profile（name/appearance/personality/dialogue_hook/tags/linked_quest_id）到 history
- 写入 directive（含 dialogue_hook/personality）
- 记录 despawn_tick 到 history

#### Step 5-2：临时 NPC 过期清理

**改动位置**：`execute()` 方法中任务过期检测之后

**改动内容**：遍历 quest_history 中 `kind=spawn_quest_npc` 的记录，到达 despawn_tick 时从 `area.npc_locations` 移除。

**验证**：
- 单测：spawn → NPC 出现在指定 area + location
- 单测：despawn 到期 → NPC 移除
- 单测：重复 spawn 同一 ID → 返回 False

---

### Phase 6：Planner 输入完整化（world_context + play_style_tags 推导）

**依赖 Phase 1**。解决差距 #4（factions/world_rules）、#19（play_style_tags 推导）、#20（world_context）。

#### Step 6-1：world_context 完整注入

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_build_planner_context()` 方法

**改动内容**：在现有 return dict 中增加 `world_context` 子结构：

```python
# --- world_context 构建 ---
world_context: dict[str, Any] = {}

# area_description
if context.world.has_registry("maps"):
    area_id = location.get("area_id", "")
    area_tmpl = context.world.maps.get_area(area_id) if area_id else None
    if area_tmpl:
        world_context["area_description"] = area_tmpl.description

# factions（当前区域相关势力）
if context.world.has_registry("factions"):
    area_id = location.get("area_id", "")
    if area_id:
        factions = context.world.factions.get_factions_in_area(area_id)
        world_context["relevant_factions"] = [
            {"id": f.id, "name": f.name} for f in factions
        ]

# world_rules（当前上下文相关规则）
if context.world.has_registry("lore"):
    chapter = context.state.narrative_plan.current_chapter
    area_id = location.get("area_id", "")
    faction_ids = [f["id"] for f in world_context.get("relevant_factions", [])]
    rules = context.world.lore.get_relevant_rules(
        chapter_id=chapter,
        area_id=area_id,
        faction_ids=faction_ids or None,
    )
    world_context["world_rules"] = [
        {"id": r.id, "title": r.title, "description": r.description}
        for r in rules[:5]  # cap at 5 to limit token cost
    ]
```

在 return dict 中追加：`"world_context": world_context,`

**同步改动**：`narrators.py` `_format_planner_context()` Part ④ 增加：

```python
world_ctx = ctx.get("world_context", {})
if world_ctx.get("area_description"):
    lines.append(f"Area description: {world_ctx['area_description'][:200]}")
factions = world_ctx.get("relevant_factions", [])
if factions:
    lines.append(f"Factions: {', '.join(f['name'] for f in factions)}")
rules = world_ctx.get("world_rules", [])
if rules:
    rule_strs = [f"{r['title']}: {r['description'][:80]}" for r in rules]
    lines.append("World rules:\n  " + "\n  ".join(rule_strs))
```

**验证**：
- 单测：含 FactionRegistry + LoreRegistry → world_context 正确填充
- 单测：缺 registry → world_context 空 dict（不报错）
- 单测：`_format_planner_context` 正确格式化 world_context

#### Step 6-2：play_style_tags 自动推导

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`execute()` 方法中记录 behavior_window 之后（~L188）

**改动内容**：从 behavior_window 分析行为频率，推导标签：

```python
# --- play_style_tags 推导 ---
window = context.state.narrative_plan.behavior_window
if len(window) >= 6:  # 至少 6 条记录才有意义
    # 统计最近行为中的 changed_slices 频次
    # 也可以从 TickCoordinator 记录的 action_type 统计
    # 当前 behavior_window 记录的是 planner 自身运行记录
    # 真正的玩家行为需要从 change_log 积累
    pass  # 见下方设计说明
```

**设计说明**：当前 `behavior_window` 记录的是 **planner 自身运行记录**（tick/changed_slices/reason/directive_count），不是玩家行为记录。设计 §7.3 要求由 `TickCoordinator.process()` 在每次 Pipeline 处理时记录玩家行为（action_type: dialogue/combat/trade/explore/rest）。

因此需要**两处改动**：

**改动文件 2**：`app/game_core/orchestration/tick_coordinator.py`

在 `process()` 返回前，记录玩家行为到 NarrativePlanSlice：

```python
# 在 process() 末尾，settlement 之前或之后
if state.has_slice("narrative_plan"):
    state.narrative_plan.record_behavior({
        "tick": current_tick,
        "action_type": result.action_type,  # 需要 PipelineResult 暴露
        "location": state.player.current_location if state.has_slice("player") else None,
    })
```

**改动文件 3**：`narrative_planner.py` 增加推导逻辑：

```python
def _derive_play_style_tags(self, window: list[dict[str, Any]]) -> list[str]:
    """从行为窗口推导玩家风格标签。"""
    if len(window) < 6:
        return []
    type_counts: dict[str, int] = {}
    for entry in window:
        action = entry.get("action_type", "")
        if action:
            type_counts[action] = type_counts.get(action, 0) + 1
    total = sum(type_counts.values()) or 1
    tags: list[str] = []
    if type_counts.get("dialogue", 0) / total > 0.35:
        tags.append("DIALOGUE_HEAVY")
    if type_counts.get("combat", 0) / total > 0.35:
        tags.append("COMBAT_FOCUSED")
    if type_counts.get("explore", 0) / total > 0.25:
        tags.append("EXPLORER")
    if type_counts.get("trade", 0) / total > 0.2:
        tags.append("TRADER")
    return tags
```

在 `execute()` 中调用并更新：

```python
derived_tags = self._derive_play_style_tags(
    context.state.narrative_plan.behavior_window
)
if derived_tags != context.state.narrative_plan.play_style_tags:
    context.state.narrative_plan.play_style_tags = derived_tags
    context.state.narrative_plan._dirty = True
```

**验证**：
- 单测：10 条 dialogue 行为 → tags 含 DIALOGUE_HEAVY
- 单测：混合行为 → tags 反映实际比例
- 单测：< 6 条记录 → 空 tags
- 回归：TickCoordinator 测试

---

### Phase 7：spawn_quest_npc 完整生命周期（临时 NPC 注册 + context_builder 消费）

**依赖 Phase 5**。解决差距 #16。

#### Step 7-1：NarrativePlanSlice 增加 temporary_npcs 注册

**改动文件**：`app/game_core/state/slices/narrative_plan.py`

**改动内容**：增加 `temporary_npcs` 字段：

```python
def __init__(self) -> None:
    ...
    self.temporary_npcs: dict[str, dict[str, Any]] = {}  # npc_id → profile

def add_temporary_npc(self, npc_id: str, profile: dict[str, Any]) -> None:
    self.temporary_npcs[npc_id] = dict(profile)
    self._dirty = True

def remove_temporary_npc(self, npc_id: str) -> None:
    self.temporary_npcs.pop(npc_id, None)
    self._dirty = True

def get_temporary_npc(self, npc_id: str) -> dict[str, Any] | None:
    raw = self.temporary_npcs.get(npc_id)
    return dict(raw) if raw else None
```

同步更新 `restore()`、`serialize()`、`snapshot()`、`validate()`。

#### Step 7-2：spawn_quest_npc 写入 temporary_npcs

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：Step 5-1 的 spawn_quest_npc handler

**改动内容**：在写入 directive 之后，额外调用：

```python
context.state.narrative_plan.add_temporary_npc(npc_id, npc_profile)
```

despawn 清理时同步调用：

```python
context.state.narrative_plan.remove_temporary_npc(temp_npc_id)
```

#### Step 7-3：context_builder 消费临时 NPC profile

**改动文件**：`app/game_core/narrative/context_builder.py`

**改动位置**：`_build_npc_prompt()` 或 `build_npc_full_context()` 中查询 NPC 模板的位置

**改动内容**：当 CharacterRegistry 查不到 NPC 时，fallback 到 NarrativePlanSlice.temporary_npcs：

```python
# 现有逻辑：
template = world.characters.get(npc_id)
# 新增 fallback：
if template is None and state.has_slice("narrative_plan"):
    temp_profile = state.narrative_plan.get_temporary_npc(npc_id)
    if temp_profile:
        # 从 temp_profile 构建轻量模板
        # name, personality, tags, dialogue_hook 等
        ...
```

具体实现需要读取 context_builder 的 NPC 查询链路确定最小改动点。核心目标是让临时 NPC 也能获得有效的 system prompt（personality + dialogue_hook 注入），而非通用 fallback。

**验证**：
- 单测：spawn 临时 NPC → temporary_npcs 注册
- 单测：context_builder 查询临时 NPC → 获得 personality + dialogue_hook
- 单测：despawn 后 → temporary_npcs 移除，context_builder 查不到
- 回归：现有 NPC 交互测试

---

### Phase 8：DynamicSubAreaManager 完整实现

**无强依赖**。解决差距 #18。

**当前状态**：`app/game_core/planning/dynamic_sub_area.py` 已有骨架（CRUD 代理 AreaSlice），`DynamicSubAreaExpiryHook`（P75）已存在并处理过期清理。

#### Step 8-1：TemporarySubArea 完整 schema

**改动文件**：`app/game_core/planning/dynamic_sub_area.py`

**改动内容**：扩展 `create()` 方法，增加设计 §十一 的完整字段验证：

```python
def create(self, area_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """创建动态子区域，返回 None 如果超出簇容量。"""
    # 簇容量检查
    if not self._areas.has_cluster_capacity(area_id):
        return None
    # 三层持久性检查
    tier = spec.get("tier", "temporary")
    if tier == "permanent":
        counts = self._areas.count_dynamic_sub_areas(area_id)
        if counts.get("permanent", 0) >= self._max_permanent(area_id):
            return None
    # 字段规范化
    sub_area = {
        "id": spec.get("id", f"dsa_{id(spec)}"),
        "label": spec.get("label", ""),
        "description": spec.get("description", ""),
        "tags": list(spec.get("tags", [])),
        "type": spec.get("type", "visit"),  # visit / discovery / dungeon
        "tier": tier,  # permanent / timed / temporary
        "discovery_mode": spec.get("discovery_mode", "auto"),
        "discovery_dc": spec.get("discovery_dc", 0),
        "hostile_config": spec.get("hostile_config"),
        "interactables": list(spec.get("interactables", [])),
        "resident_npcs": list(spec.get("resident_npcs", [])),
        "linked_quest_id": spec.get("linked_quest_id"),
        "linked_milestone": spec.get("linked_milestone"),
        "source": spec.get("source", "unknown"),
        "created_at_tick": spec.get("created_at_tick", 0),
        "expiry": spec.get("expiry_ticks", -1 if tier == "permanent" else 12),
        "status": "active",
    }
    return self._areas.add_temporary_sub_area(area_id, sub_area)
```

#### Step 8-2：plant_environmental 和 fill_area 改为委托

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `plant_environmental`（L712-738）和 `fill_area`（L740-763）分支

**改动内容**：

构造函数接受 `DynamicSubAreaManager`：

```python
def __init__(
    self,
    planner: NarrativePlannerProvider | None = None,
    *,
    instance_manager: InstanceManager | None = None,
    sub_area_manager: DynamicSubAreaManager | None = None,  # 新增
) -> None:
    ...
    self._sub_area_manager = sub_area_manager
```

`plant_environmental` 改为委托：

```python
if kind == "plant_environmental":
    if self._sub_area_manager is None:
        return False
    area_id = self._coerce_non_empty_string(payload.get("area_id"))
    if not area_id:
        return False
    spec = {
        "id": payload.get("clue_id", f"env_{current_tick}"),
        "label": self._string_or_empty(payload.get("description")),
        "description": self._string_or_empty(payload.get("description")),
        "type": "discovery",
        "tier": "temporary",
        "discovery_mode": payload.get("discovery_mode", "check"),
        "discovery_dc": payload.get("dc", 12),
        "linked_quest_id": payload.get("linked_quest_id"),
        "linked_milestone": payload.get("linked_milestone"),
        "source": "narrative_planner",
        "created_at_tick": current_tick,
        "expiry_ticks": payload.get("expiry_ticks", 12),
    }
    result = self._sub_area_manager.create(area_id, spec)
    if result is None:
        return False
    context.record_change(StateChange(
        slice="areas", operation="set",
        path=f"{area_id}.temporary_sub_areas.{spec['id']}", value=result,
    ))
    return True
```

`fill_area` 类似改造。

**同步改动**：`app/deps.py` 或 Hook 注册点注入 `DynamicSubAreaManager`。

**验证**：
- 单测：plant_environmental 通过 DynamicSubAreaManager 创建子区域
- 单测：簇容量满时返回 False
- 单测：permanent tier 超过上限时拒绝
- 单测：fill_area 正确委托
- 回归：现有 plant_environmental / fill_area 测试

---

### Phase 9：DynamicQuest 目标自动判定（EventEngine 联动）

**依赖 Phase 4**（objectives 字段就位）。解决差距 #17。

#### Step 9-1：create_quest 时为 objectives 创建 EventSlice 条件

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**改动位置**：`_apply_directive()` 中 `create_quest` 分支，在现有 `_create_milestone_condition_events()` 调用之后

**改动内容**：

```python
# 现有调用：
self._create_milestone_condition_events(quest_id, context, current_tick=current_tick)

# --- 新增：为 DynamicQuest objectives 创建 EventSlice 条件 ---
self._create_objective_events(quest_id, quest_payload, context, current_tick=current_tick)
```

新方法：

```python
def _create_objective_events(
    self,
    quest_id: str,
    quest_payload: dict[str, Any],
    context: SettlementContext,
    *,
    current_tick: int,
) -> None:
    """为动态任务的 objectives 创建 EventSlice 条件。"""
    if not context.state.has_slice("events"):
        return
    objectives = quest_payload.get("objectives", [])
    for idx, obj in enumerate(objectives):
        if not isinstance(obj, dict):
            continue
        if obj.get("optional", False):
            continue  # 可选目标不创建自动事件
        obj_type = obj.get("type", "")
        target = obj.get("target", {})
        if not obj_type or not target:
            continue
        # 映射 objective type → EventEngine condition type
        condition_type = self._objective_to_condition_type(obj_type)
        if condition_type is None:
            continue
        event_id = f"dq_{quest_id}_obj_{idx}"
        if context.state.events.get_event(event_id) is not None:
            continue
        context.state.events.activate(event_id, {
            "id": event_id,
            "event_id": event_id,
            "state": "dormant",
            "status": "dormant",
            "conditions": [{"type": condition_type, "params": dict(target)}],
            "on_trigger": [{
                "type": "complete_objective",
                "params": {
                    "quest_id": quest_id,
                    "objective_index": idx,
                },
            }],
            "source": "narrative_planner",
            "created_at_tick": current_tick,
        })
        context.record_change(StateChange(
            slice="events", operation="set",
            path=f"active_events.{event_id}",
            value={"state": "dormant"},
        ))

@staticmethod
def _objective_to_condition_type(obj_type: str) -> str | None:
    """映射 DynamicQuest objective type → EventEngine condition type。"""
    mapping = {
        "reach_location": "location_visited",
        "talk_to": "npc_talked",
        "collect": "item_obtained",
        "kill": "kill_count",
    }
    return mapping.get(obj_type)
```

#### Step 9-2：EventEngine 处理 complete_objective 触发动作

**改动文件**：`app/game_core/orchestration/event_engine.py`

**改动位置**：事件触发动作处理逻辑

**改动内容**：增加 `complete_objective` 触发动作类型：

```python
if action_type == "complete_objective":
    quest_id = params.get("quest_id")
    obj_idx = params.get("objective_index")
    if quest_id and obj_idx is not None:
        quest = state.quests.dynamic_quests.get(quest_id)
        if quest:
            objectives = quest.get("objectives", [])
            if 0 <= obj_idx < len(objectives):
                objectives[obj_idx]["completed"] = True
                state.quests._dirty = True
                # 检查是否所有必选目标完成
                all_required_done = all(
                    obj.get("completed", False)
                    for obj in objectives
                    if not obj.get("optional", False)
                )
                if all_required_done:
                    quest["status"] = "completed"
                    # 可选：检查关联里程碑
```

**验证**：
- 单测：create_quest 带 objectives → EventSlice 条件创建
- 单测：条件满足 → objective.completed = True
- 单测：所有必选目标完成 → quest.status = "completed"
- 单测：可选目标不影响完成判定
- 回归：现有 EventEngine 测试

---

## 各 Phase 改动汇总

| Phase | 核心改动 | 主要文件 | 估计测试数 |
|-------|---------|---------|----------|
| **Phase 1** | 上下文补全 + 空值防护 + LLM 格式化 | `narrative_planner.py`, `narrators.py` | ~8 |
| **Phase 2** | 消除硬编码 + bulletin 扩展 | `planner.py`, `narrative_planner.py` | ~12 |
| **Phase 3** | LLM prompt 重写 | `narrators.py` | ~2 |
| **Phase 4** | DynamicQuest 字段 + 任务过期 | `narrative_planner.py`, `planner.py` | ~8 |
| **Phase 5** | spawn_quest_npc 基础 | `narrative_planner.py` | ~6 |
| **Phase 6** | world_context + play_style_tags 推导 | `narrative_planner.py`, `narrators.py`, `tick_coordinator.py` | ~8 |
| **Phase 7** | 临时 NPC 注册 + context_builder 消费 | `narrative_plan.py`, `narrative_planner.py`, `context_builder.py` | ~8 |
| **Phase 8** | DynamicSubAreaManager 完整 | `dynamic_sub_area.py`, `narrative_planner.py` | ~10 |
| **Phase 9** | objectives + EventEngine 联动 | `narrative_planner.py`, `event_engine.py` | ~8 |
| **总计** | | ~8 个文件 | **~70** |

## 执行顺序与依赖

```
Phase 1（基础设施）                        ← 无依赖
  │
  ├──> Phase 2（核心修复）                 ← 依赖 P1 上下文
  │      │
  │      ├──> Phase 4（任务结构）           ← 建议在 P2 之后
  │      │      │
  │      │      ├──> Phase 9（EventEngine） ← 依赖 P4 objectives
  │      │      │
  │      │      └──> Phase 5（spawn 基础）  ← 共享 expiry 模式
  │      │             │
  │      │             └──> Phase 7（临时NPC完整） ← 依赖 P5
  │      │
  │      └──> Phase 8（SubAreaManager）    ← 建议在 P2 之后
  │
  ├──> Phase 3（LLM prompt）               ← 依赖 P1 上下文
  │
  └──> Phase 6（world_context + tags）     ← 依赖 P1
```

**建议的串行执行顺序**：P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9

## 验收标准

### Phase 1 完成后
- [ ] `_build_planner_context()` 输出含 `area_npcs`、`area_boards`、`party`、`play_style_tags`
- [ ] `directive={}` 或 `directive=None` 的 `direct_npc` 被拒绝，不落库
- [ ] `_format_planner_context()` 正确输出新增字段

### Phase 2 完成后
- [ ] planner 输出的 `publish_bulletin.board_id` 为真实 interactable ID（如 `quest_board`）
- [ ] planner 输出的 `direct_npc.npc_id` 为场景中真实 NPC（如 `guild_girl`）
- [ ] 无 board/NPC 时 planner 降级（只 escalate，不产生指向虚空的指令）
- [ ] bulletin 含 location 二元定位
- [ ] `notify_resident_npcs=true` 时驻留 NPC 自动收到 awareness directive

### Phase 3 完成后
- [ ] LLM prompt 含全部 9 种指令类型
- [ ] LLM prompt 含 7 个核心原则和升级等级表
- [ ] prompt 约束 npc_id/board_id 必须来自上下文真实列表

### Phase 4 完成后
- [ ] DynamicQuest 含 objectives、rewards、expiry_ticks、on_expire
- [ ] 过期任务自动标记 expired + 执行 on_expire 策略
- [ ] SSE `dynamic_quest_expired` 事件正确发出

### Phase 5 完成后
- [ ] spawn_quest_npc 将临时 NPC 放入指定位置
- [ ] 临时 NPC 携带 dialogue_hook 和 personality
- [ ] despawn_after_ticks 到期后临时 NPC 自动移除

### Phase 6 完成后
- [ ] planner 上下文含 area_description、relevant_factions、world_rules
- [ ] `_format_planner_context()` 正确格式化 world_context
- [ ] play_style_tags 从 behavior_window 自动推导
- [ ] TickCoordinator 每次 process 记录玩家行为到 behavior_window

### Phase 7 完成后
- [ ] spawn_quest_npc 将 NPC profile 注册到 `NarrativePlanSlice.temporary_npcs`
- [ ] context_builder 查不到 CharacterRegistry 时 fallback 到 temporary_npcs
- [ ] 临时 NPC 交互时获得正确的 personality + dialogue_hook system prompt
- [ ] despawn 时同步清理 temporary_npcs 注册

### Phase 8 完成后
- [ ] plant_environmental 和 fill_area 委托 DynamicSubAreaManager.create()
- [ ] 簇容量满时 create 返回 None，指令执行返回 False
- [ ] permanent tier 超过上限时拒绝
- [ ] TemporarySubArea 含完整 schema（discovery_mode、hostile_config 等）

### Phase 9 完成后
- [ ] create_quest 带 objectives 时自动创建 EventSlice 条件
- [ ] EventEngine 条件满足 → objective.completed = True
- [ ] 所有必选目标完成 → quest.status = "completed"
- [ ] 可选目标不影响完成判定
- [ ] complete_objective 触发动作类型在 EventEngine 中注册
