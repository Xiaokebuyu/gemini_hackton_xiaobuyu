# P32 — Planner 导演模式与冒险体验深化

## 背景

NPC 工具调用链路已修复（context_layers 注入 + ContextWindow 结构化 parts + registry 过滤 + sell_to_player 工具），NPC 现在能正常调用工具执行游戏机制。

当前遗留的核心体验问题：
1. **Planner 运行频率过低** — 冷却门控 + settlement 门控双重限制，玩家约 24 次对话 planner 才跑一次
2. **世界缺乏内容密度** — planner 只按里程碑推主线，不会根据玩家日常生成社交事件、NPC 个人剧情、冒险线索
3. **战斗极难进入** — 遭遇点很少被生成，动态遭遇阈值偏高
4. **NPC"有话对你说"功能失效** — directive topic 未注入 NPC prompt，导致 NPC 不知道该说什么

## 设计愿景

Planner 不是"里程碑推进器"，而是**世界导演**。它观察玩家的日常行为（和谁聊天、去了哪里、做了什么），基于这些积累自然地生成事件：

- 和铁匠聊了很多次 → 铁匠某天托你帮忙采矿石（专属支线）
- 和柜台小姐好感度到了 → 她下班后约你去酒馆聊天（社交事件）
- 在某区域探索多次 → 发现隐藏洞穴入口（探索事件）
- 某个 NPC 信任度够高 → 透露一个秘密，开启专属剧情线
- 接下讨伐任务 → planner 布置一系列地点，通过调查线索链逐步接近怪物所在地，最终进入战斗

**关键词：内容密度**。不是每隔很久才来一个大任务，而是持续不断的小事件（NPC 主动搭话、发现线索、偶遇旅人、收到委托）让世界感觉活着。

---

## 调查结论

### 1. Planner 触发频率分析

**当前链路**：
```
玩家对话（time_cost = 1/6）
  → _finalize_dialogue_turn() → accumulate(1/6)
  → 约 6 次对话后 accumulated >= 1.0 → 触发 settlement
  → settlement 中 NarrativePlannerHook.execute() 检查门控：
    ├─ quiet_rest_slot → 跳过
    ├─ triggered=False AND ticks_since_last_run < FALLBACK_INTERVAL(4) → 跳过（冷却）
    └─ 通过 → 运行 planner
```

**问题**：`FALLBACK_INTERVAL = 4` 意味着 planner 每 4 个 settlement tick 才跑一次。每个 settlement ≈ 6 次对话，所以 planner 实际 **~24 次对话才跑一次**。

**已有的 bypass**：如果 `_has_trigger_change(change_log)` 检测到 `_TRIGGER_SLICES`（flags/quests/player/areas/events/relations/party/time）中有变化，`triggered=True` 会绕过冷却。但对话改变的 flag（`talked_to_{npc_id}`）属于 flags slice，所以**大多数对话其实会触发 planner**——问题出在没有 flag 变化的对话（如与同一 NPC 连续聊多轮）。

**关键代码**：`narrative_planner.py:230` `FALLBACK_INTERVAL = 4` 和 `narrative_planner.py:310-317` 冷却门控。

### 2. Planner 能看到的数据（33+ 字段）

**已有**（planner context 中）：
- `area_npc_summaries` — 每个 NPC 的 `{id, name, tags, approval, trust}`
- `strategy_notes` — planner 的跨轮私人笔记（上轮写→本轮读，是唯一的有状态记忆）
- `play_style_tags` — 玩家行为风格（DIALOGUE_HEAVY / COMBAT_FOCUSED / EXPLORER / TRADER）
- `active_quests` + `planner_feedback` — 任务进度和目标完成率
- `exploration_summary` — 遭遇/线索/发现统计
- `area_events` — 区域最近 10 条事件
- `previous_directive_results` — 上轮指令执行反馈
- `hostile_config_locations` — 可种植遭遇的位置列表
- `milestone_outline` — 当前里程碑大纲和进度
- `player_level`, `player_xp`, `danger_level` 等

**缺失但可补**：
- `relationship_stage`（当前只有 approval/trust 数值，没有阶段名如 friend/close_friend）
- 全局 NPC 交互历史（只看当前区域的 NPC）

### 3. 现有 NPC 关系机制（可复用）

**关系阶段**：stranger → acquaintance(>10) → friend(>30 approval, >20 trust) → close_friend(>60 trust) → intimate(>80 trust, >60 romance)

**已有 Hook**：
- `RelationshipHook(65)` — 自动检测阶段跃迁，发出 `relationship_stage_changed` SSE
- `PrivateChatTriggerHook(75)` — 休息时基于 romance/trust/intimate 触发 `npc_wants_to_chat`
- `CampfireHook(63)` — 长休时队友回忆共同经历
- `SharedExperienceHook(62)` — 记录 combat/quest/rest 共同经历

**关键复用点**：Planner 已能看到 approval/trust 数值，只需在 prompt 中教它"观察关系变化 → 安排对应事件"。

### 4. NPC"有话对你说"Bug 分析

**完整链路**：
```
Planner 生成 direct_npc(talk, topic="防守方式")
  → PlannerNpcHandler 存储到 npc_directives（含 topic）
  → DirectiveTriggerHook 发出 SSE "npc_wants_to_chat"
  → 玩家点击与 NPC 对话
  → NpcInteractionCoordinator.execute_interaction()
  → ❌ topic 未注入 NPC 的 system prompt
  → NPC 不知道该说什么 → 空内容/错误
```

**根因**：`npc_interaction.py` 中没有消费 pending directive 并将 topic 注入 NPC prompt 的代码。Directive 被触发了但 NPC 完全不知道自己"有话要说"以及要说什么。

**"agent 字段错误"**：来自 `agent_orchestration.py` 错误处理 — NPC agent 因无内容可说而报错，返回 `{"code": "agent_failed"}` 或类似结构。

### 5. 战斗进入链路（完整但被动）

**三条路径**：
- **路径 A（动态遭遇）**：EncounterHook，基于 `danger_level * time_coeff >= 0.75` 触发 → 阈值偏高
- **路径 B（种植遭遇）**：Planner 的 `plant_encounter` directive → 但 planner 很少主动种植
- **路径 C（导航检测）**：进入子区域时检测 planted 遭遇 → 正常工作

链路本身完整（navigation → encounter detection → stealth check → start_combat），问题在于**遭遇不被生成**。

### 6. 动态子地点没有上锁机制

**现状**：`NavigationHandler._validate_enter_sub_location()` 对动态子区域只检查"是否存在"，没有 locked 字段。玩家发现子区域后可以直接进入，无法做"调查线索 → 解锁下一个区域"的递进设计。

**已有的相关机制**：
- 房间有 `discoverable` 标记（需 `discover_room` 指令才能进入）
- Clue 效果里有 `unlock_sub_location`，但它实际是**创建新子区域**而不是解锁已有子区域
- Container 有 `locked/unlocked` 状态，但那是容器不是地点

**缺失**：动态子区域的 `locked` 字段 + NavigationHandler 的锁检查 + planner 设置/解除锁的能力。

### 7. plant_environmental 名字和描述混用

**现状**：`plant_environmental` 创建临时子区域时把 `description` 同时赋给 `label` 和 `description`，导致出现 `label:"恶臭的遗迹入口林道"` 这种"描述当名字"的情况。

**对比**：`fill_area` 正确分开了 `label`（短名称）和 `description`（详细描述），但 `plant_environmental` 没有。

**实际 save 数据示例**：
```json
{
  "id": "clue_16",
  "label": "恶臭的遗迹入口林道",    // ← 应该是短名称如"林道入口"
  "description": "恶臭的遗迹入口林道", // ← 重复
  "interactables": [],                // ← 空
  "type": "discovery"
}
```

### 8. NPC 位置上下文 Bug

**现状**：`build_npc_context()` 调用 `_resolve_location()` 解析的是**玩家位置**，NPC 的 L2（区域环境）和 L3（位置详情）层基于玩家位置构建。NPC 不知道"自己在哪"。

代码中已有正确的 `_resolve_npc_area_and_location(npc_id, state, world)` 函数，但只在 guard/temple_keeper 角色数据中使用，未接入主上下文构建。

### 9. NPC 不知道周围有哪些其他 NPC

**现状**：NPC 对话上下文中，L2/L3 层包含区域环境和位置详情，但没有明确告诉 NPC "你旁边还有谁"。NPC 之间缺乏互相感知，无法自然地提到"隔壁的铁匠"或"刚才经过的旅人"。

### 10. 僻静处（private_chat）SSE 重复发送

**现状**：PrivateChat 创建的僻静处临时子区域，其 SSE 事件存在重复发送问题。需要排查 PrivateChatCoordinator 或 DirectiveTriggerHook 中的发送逻辑。

### 11. NPC 无对话外自主行动能力

**现状**：NPC 有 19 个对话工具，但**没有移动工具**。NPC 只在两个时机"活着"：
1. 与玩家对话时（通过工具）
2. Settlement hook 时（被系统驱动）

NPC 移动只能通过 Planner → `direct_npc(destination)` / `modify_location` → NpcScheduleHook 在时段切换时执行。即使给 NPC 加移动工具也无法在对话外使用（工具只在对话中可调用）。

**结论**：NPC 赴约/集合等移动行为必须由 Planner 编排，不需要给 NPC 加移动工具。但需要 Planner 足够频繁 + 足够聪明来安排这些。

---

## 实施计划

### Phase 1：Planner 频率提升

**改动**：`FALLBACK_INTERVAL = 4` → `1`

**文件**：`app/game_core/orchestration/hooks/narrative_planner.py:230`

**效果**：Planner 在每次 settlement 都运行（约每 6 次对话一次），同 tick 防重复执行。配合已有的 `triggered` 机制（有状态变化时立即运行），planner 能持续观察玩家行为。

**保留**：quiet_rest_slot 跳过（休息时不干扰）。

### Phase 2：Planner Context 补充 relationship_stage

**改动**：在 `_build_planner_context()` 的 `area_npc_summaries` 中补充 `stage` 字段

**文件**：`app/game_core/orchestration/hooks/narrative_planner.py` `_build_planner_context()` 中构建 `area_npc_summaries` 的位置

**原因**：Planner 当前只能看到 approval/trust 的数值，不知道 NPC 处于哪个关系阶段（stranger/friend/close_friend...）。补充 stage 后 planner 可以根据阶段变化安排对应事件。

```python
# 在 npc_entry 构建中追加：
"stage": context.state.relations.get_stage(npc_id) or "stranger",
```

### Phase 3：NPC"有话对你说"修复 + Directive 持久化

**问题**：
1. Directive topic 未注入 NPC prompt → NPC 不知道该说什么
2. Directive 是一次性消费的 → 玩家拒绝后 NPC 就忘了自己要说什么

**修复方案**：改为 **Planner 写黑板** 模式。Planner 发 `direct_npc` 时，topic/goal 同时写入 `npc_blackboards`（RelationSlice），NPC 通过黑板持久读取。即使玩家拒绝或忽略，NPC 下次对话还能再次提起。

**3a. Planner directive 写入 NPC 黑板**
- 文件：`app/game_core/planning/npc_director.py` 的 `_apply_direct_npc()`
- 逻辑：apply directive 时，将 topic/goal 写入 `relations.npc_blackboards[npc_id]["pending_topic"]`
- 这样 topic 持久存储在 state 中，不随 directive 消费消失

**3b. npc_interaction 读取黑板 topic 注入 prompt**
- 文件：`app/game_core/orchestration/npc_interaction.py`
- 位置：构建 NPC context 时
- 逻辑：检查 `npc_blackboards[npc_id]["pending_topic"]` → 注入 system prompt（"## 你有话想对玩家说\n话题：{topic}"）
- NPC 说完后清除 blackboard 中的 topic

**3c. DirectiveTriggerHook SSE payload 补充 topic**
- 文件：`app/game_core/orchestration/hooks/directive_trigger.py`
- 追加：`"topic": directive_entry.get("directive", {}).get("topic")`
- 前端可展示"XX 想和你聊聊关于 YY 的事"

### Phase 4：UNIFIED_PLANNER_PROMPT 重写 — 世界导演化

这是最核心的改动。重新定位 planner 的角色，从"里程碑推进器"变成"世界导演"。

**文件**：`app/narrators.py` UNIFIED_PLANNER_PROMPT（约 1709-1960 行）

**改动方向**：

#### 4a. 新增"导演思维框架"

在 prompt 开头重新定义 planner 的角色：

```
你是世界导演。你的职责不只是推进主线——你要让这个世界活起来。

你有三个层次的工作：
1. 日常层 — 基于玩家与 NPC 的互动，安排社交事件、NPC 主动搭话、日常小插曲
2. 关系层 — 追踪每个重要 NPC 的关系进展，在关系达到关键节点时触发专属剧情
3. 冒险层 — 设计完整的冒险线路（线索链 → 探索 → 战斗），而不只是发一个任务

你应该像一个桌游 DM 那样思考：玩家最近在做什么？哪个 NPC 和玩家的关系有变化？
现在是安排一个小事件的好时机，还是该推进主线了？
```

#### 4b. 新增"NPC 关系驱动事件"规则

```
## NPC 关系事件触发
观察 area_npc_summaries 中每个 NPC 的 approval、trust 和 stage：

- stage 从 stranger → acquaintance：NPC 开始主动打招呼（direct_npc: approach）
- stage 从 acquaintance → friend：安排一个小忙（create_quest: NPC 个人委托）
- stage 到达 close_friend：触发 NPC 个人剧情线（create_quest + 专属冒险）
- stage 到达 intimate：安排亲密事件（direct_npc: talk + 特殊话题）
- approval 突然下降：NPC 表现冷淡或质问（direct_npc: react）

用 strategy_notes 记录每个重要 NPC 的剧情进度，例如：
"guild_girl: 已完成初次委托，approval=35，下次到 friend 阶段时安排她的个人委托"
"goblin_slayer: 共同战斗过 2 次，trust=45，接近 friend 阈值，准备一个他的过去相关的线索"
```

#### 4c. 新增"冒险设计"规则

```
## 冒险内容设计（重要）
创建战斗/探险类任务时，必须同时布置完整的冒险线路。空壳任务（只有 create_quest 没有配套内容）是被禁止的。

一个完整的冒险应包含：
1. 线索入口 — fill_location: 在已有地点放置可调查的线索（脚印、目击报告、遗留物品）
2. 中间地点 — fill_area / fill_location: 布置通往目标的路径，放置更多线索和环境叙事
3. 最终遭遇 — plant_encounter: 在目标地点放置怪物遭遇
4. 任务目标链 — create_quest: objectives 依次引用上述内容（clue_investigated → location_visited → encounter_cleared）

示例 — "清剿哥布林巢穴"：
directives:
1. fill_location(area_id="frontier_wilderness", location_id="forest_path",
   interactables=[{id: "clue_goblin_tracks", name: "可疑的足迹", type: "inspect",
   tags: ["clue"], functional: {type: "investigate_clue"}}])
2. fill_area(area_id="frontier_wilderness", id="goblin_camp", label="哥布林营地",
   description="树林深处的简陋营地")
3. plant_encounter(area_id="frontier_wilderness", sub_area_id="goblin_camp",
   monster_ids=["goblin","goblin","goblin_archer"], description="哥布林巡逻队")
4. create_quest(quest_id="dq_goblin_nest", title="密林中的哥布林",
   objectives=[
     {description: "调查森林小径的可疑足迹", condition: {type: "clue_investigated", params: {area_id: "frontier_wilderness", clue_id: "clue_goblin_tracks"}}},
     {description: "清剿哥布林营地", condition: {type: "encounter_cleared", params: {area_id: "frontier_wilderness", encounter_id: "goblin_camp"}}}
   ],
   rewards: {xp: 400, gold: 100})
```

#### 4d. 新增"日常事件"引导

```
## 日常事件（保持世界活力）
不是每次都需要发大任务。以下是低成本但高感知度的日常干预：

- direct_npc(talk): NPC 主动找玩家聊天（分享趣事、评论天气、讨论最近的冒险）
- direct_npc(inform): NPC 分享有用信息（听说南边出现了奇怪的东西、隔壁酒馆有打折）
- direct_npc(react): NPC 对玩家最近的行为做出反应（"听说你打败了哥布林？厉害啊！"）
- publish_bulletin: 在任务板贴新通告（不一定是任务，可以是新闻、警告、悬赏）
- plant_environmental: 在区域放置氛围元素（篝火痕迹、远处的狼嚎、神秘旅人）

每次规划至少考虑是否需要一个日常事件来保持世界活力。即使不推进主线，也可以让 NPC 动起来。
```

#### 4e. 调整"strategy_notes 使用规则"

```
## strategy_notes — 导演笔记本
strategy_notes 是你的私人笔记本，每次规划时会看到上次写的内容。用它来：

1. 记录每个重要 NPC 的剧情进度和下一步计划
2. 记录玩家的行为模式（偏好社交？偏好战斗？经常去哪里？）
3. 规划未来 2-3 轮的事件安排（"下次 guild_girl 到 friend 时安排个人委托"）
4. 记录哪些地方已经填充了内容，避免重复

格式建议：
---
NPC 进度:
  guild_girl: acquaintance, approval=25, 计划: 到friend时触发"她的烦恼"支线
  goblin_slayer: stranger, approval=8, 最近共同战斗, 准备让他主动搭话

世界状态:
  frontier_wilderness: 已放置哥布林巡逻遭遇, 线索已放
  ancient_ruins: 空, 计划下次放置探索内容

下一步:
  - 如果玩家继续和guild_girl聊 → 安排她分享烦恼
  - 如果玩家去wilderness → 确保有遭遇可触发
---
```

### Phase 5：动态子地点上锁机制

**问题**：冒险区域的子地点/房间无法上锁，无法做"调查线索 → 解锁下一区域"的递进设计。

**改动**：

**5a. 动态子区域增加 locked 字段**
- 文件：`app/game_core/planning/dynamic_sub_area.py` + `app/game_core/rules/handlers/planner.py`
- `fill_area` 和 `plant_environmental` 创建子区域时支持 `locked: true` 参数
- 默认 `locked: false`（向后兼容）

**5b. NavigationHandler 增加锁检查**
- 文件：`app/game_core/rules/handlers/navigation.py`
- `_validate_enter_sub_location()` 对动态子区域检查 `locked` 字段
- locked=true 时返回 `ValidationResult(ok=False, reason="location_locked")`

**5c. 解锁机制**
- 复用已有的 `unlock_sub_location` clue 效果，改为：查找目标子区域 → 设置 `locked=false`
- 也可由 Planner 通过新 directive 或 flag 变化触发解锁
- 链路：调查线索 → clue effect `unlock_sub_location` → 目标子区域 locked=false → 玩家可进入

**5d. Planner prompt 引导**
- 在 Phase 4 prompt 的"冒险设计"部分增加锁定用法：
  ```
  fill_area(id="goblin_inner", locked=true)  // 初始锁定
  fill_location 放置 clue，clue 的 effect 包含 unlock_sub_location(sub_area_id="goblin_inner")
  ```

### Phase 6：plant_environmental 名字/描述分离

**问题**：`plant_environmental` 把 description 同时赋给 label 和 description。

**改动**：
- 文件：`app/game_core/rules/handlers/planner.py` 的 `_compute_plant_environmental()`
- 新增 `label` 参数（短名称），`description` 保持原义
- 向后兼容：如果没提供 label，从 description 截取前 N 字符
- 同步更新 `directive_contracts.py` 的 plant_environmental 校验

### Phase 7：NPC 位置上下文修复

**问题**：`build_npc_context()` 的 L2/L3 层基于玩家位置构建，NPC 不知道自己在哪。

**改动**：
- 文件：`app/game_core/narrative/context_builder.py`
- `build_npc_context(npc_id)` 改用 `_resolve_npc_area_and_location(npc_id, state, world)` 解析 NPC 真实位置
- `build_teammate_context()` 同理
- 已有 helper 函数，对接即可

### Phase 8：NPC 互相感知

**问题**：NPC 不知道周围有哪些其他 NPC，无法自然提到同场景的角色。

**改动**：
- 文件：`app/game_core/narrative/context_builder.py`
- 在 NPC 的 L3 或 L4 层中注入"同场景其他 NPC"列表
- 来源：`presence.get_area_npcs()` + `get_npc_room()` 过滤同位置/同房间的 NPC
- 格式：`nearby_npcs: [{id, name, tags}]`（排除自己）

### Phase 9：僻静处 SSE 重复发送修复

**问题**：PrivateChat 的僻静处临时子区域 SSE 事件重复发送。

**改动**：排查 PrivateChatCoordinator 中 SSE 发送逻辑，确保僻静处创建/进入事件只发一次。

### Phase 10：动态遭遇阈值微调（可选）

**改动**：`_PROBE_THRESHOLD = 0.75` → `0.5`

**文件**：`app/game_core/orchestration/hooks/encounter.py:43`

**效果**：降低动态遭遇的触发阈值，作为 planner 主动种植遭遇的补充。

---

## Phase 总览

| Phase | 标题 | 改动量 | 说明 |
|-------|------|--------|------|
| 1 | Planner 频率提升 | 1 行 | `FALLBACK_INTERVAL = 4` → `1` |
| 2 | Context 补充 relationship_stage | ~5 行 | `area_npc_summaries` 追加 NPC 关系阶段 |
| 3a | Directive 写入 NPC 黑板 | ~15 行 | Planner 的 topic 持久写入 `npc_blackboards` |
| 3b | NPC 对话读取黑板 topic | ~20 行 | `npc_interaction` 读取黑板注入 prompt，说完后清除 |
| 3c | SSE payload 补充 topic | ~3 行 | `npc_wants_to_chat` 携带话题 |
| **4** | **Planner Prompt 世界导演化** | **prompt 文本** | **最核心改动。重写 UNIFIED_PLANNER_PROMPT** |
| 5 | 动态子地点上锁机制 | ~60 行 | `locked` 字段 + 导航检查 + `unlock_sub_location` 真正解锁 |
| 6 | plant_environmental 名字/描述分离 | ~15 行 | 新增 `label` 参数，与 `description` 分开 |
| 7 | NPC 位置上下文修复 | ~15 行 | `build_npc_context()` 改用 NPC 真实位置 |
| 8 | NPC 互相感知 | ~20 行 | NPC 上下文注入同场景其他 NPC 列表 |
| 9 | 僻静处 SSE 重复发送修复 | 待查 | 排查 PrivateChat SSE 发送逻辑 |
| 10 | 动态遭遇阈值微调 | 1 行 | `_PROBE_THRESHOLD = 0.75` → `0.5` |

## 实施顺序

**第一批（快速修复 + NPC 基础）**：Phase 1 + 2 + 3(a/b/c) + 7 + 8 + 9 + 10
- 频率提升、NPC 对话/位置/感知修复、遭遇阈值
- 纯代码改动，可测试验证

**第二批（冒险机制）**：Phase 5 + 6
- 子地点上锁 + 名字/描述分离
- 为 Planner 冒险设计能力提供机制支撑

**第三批（Planner Prompt 重写）**：Phase 4
- 世界导演化 prompt 重写（由实施者基于调查理解亲自完成）
- 需要第一批和第二批的机制先就位

## 已完成的前置工作

- [x] NPC 工具调用修复（context_layers + ContextWindow 结构化 parts）
- [x] Registry trait 过滤 bug 修复
- [x] SellToPlayerTool 交易元工具
- [x] WorldKnowledgeGraph session 隔离
- [x] Room 级 NPC 可见性修复
- [x] Path A 移除 + Path B（blackboard）修复
- [x] NPC prompt 主动性引导 + 能力边界修复
- [x] assign_capability "quest_accept" 从 prompt 移除
- [x] tool_config=AUTO 显式设置
- [x] 四轮深度调查（planner context / 事件感知 / 关系机制 / NPC对话Bug）
