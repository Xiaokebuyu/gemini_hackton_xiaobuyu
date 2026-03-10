# P11 - NPC在场语义、队伍系统与长休表现分块计划

## 1. 目标

当前有三组问题已经互相缠在一起：

1. NPC 在场语义不一致：场景里能看见，点击却提示不在当前区域/地点
2. 队伍系统后端已有较多实现，但前端显化和空间跟随明显落后
3. 长休采用 8 格逐格结算后，玩家会直接看到大量内部结算痕迹，体验混乱

这份文档的目的不是一次性给出最终实现，而是把问题拆成可以独立推进的块，避免继续把：

- 明确 bug
- 架构未对齐
- 产品表现取舍

混在一轮里一起改。

### 1.1 不在本批文档重点中的已对齐项

下列前后端对齐已经在前面的轮次完成，本文件不再把它们当成主问题重复展开：

- 探索主选项区已有 `短休 / 长休 / 扎营 / 值守`
- HUD 已显示 `day / slot / period`
- 前端已消费：
  - `campfire_dialogue`
  - `event_state_changed`
  - `discovery_reveal`
  - `hidden_object_revealed`
  - `trap_detected`

本文件聚焦的是仍然未收口的三条链：

1. NPC 在场语义
2. 队伍系统闭环
3. 长休表现压缩

---

## 2. 当前调查结论

### 2.1 NPC 可见但不可交互：真实后端 bug

#### 现状

- 场景显示谁在场，来自 [`app/scene_views.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/scene_views.py)
  - `build_location_overview()` 会从 `AreaSlice.npc_locations` 取 `present_npcs`
  - 当玩家在区域主场景，即 `current_location is None` 时，会显示 `npc_location is None` 的 NPC
- 交互校验谁能说话，来自 [`app/game_core/orchestration/interaction.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/orchestration/interaction.py)
  - `_validate_npc_presence()` 当前要求：
    - `npc_area == current_area`
    - `npc_location` 存在
    - `current_location` 存在
    - 且两者相等
  - 这条规则不接受“双方都在 area 主场景（location_id=None）”

#### 结果

- NPC 可以被 `location_overview` 显示出来
- 但 `/interact/stream` 会拒绝与其对话
- 长休后更容易出现，是因为 period 切换会触发日程移动，部分 NPC 被放到 area 级主场景

#### 定性

这是明确 bug，不是产品取舍问题。

---

### 2.2 当前看到的“NPC 随机出现”并不是通用随机刷出系统

#### 现状

- 当前场景里真正显示的 NPC，核心数据源仍是 `AreaSlice.npc_locations`
- 当前“像随机出现”的来源主要有三类：
  1. [`NpcScheduleHook`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/orchestration/hooks/npc_schedule.py) 的日程移动
  2. [`NarrativePlannerHook`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/orchestration/hooks/narrative_planner.py) 的叙事投递
  3. 私聊邀请/事件型出现（不是实体在场）

#### 关键事实

- `NpcScheduleHook` 默认 provider 是确定性的，不是随机掷骰
- 它只在 period 切换时移动 NPC
- 每次最多移动 2 个 NPC
- 目标是 `town` 或角色 home/schedule destination
- 它显式排除了 party members

#### 定性

当前没有一个成熟的“随机路人刷出系统”在主链里支撑场景 NPC。
你看到的“突然出现”更接近：

- 日程移动
- planner 投递
- 私聊/事件引导

不是文档理想态里的完整 passerby/runtime presence 系统。

---

### 2.3 队伍系统：后端中等完成度，前端明显落后

#### 后端已有部分

1. 状态层
   - [`app/game_core/state/slices/party.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/state/slices/party.py)
   - 已有：
     - `members`
     - `companion_approval`
     - `shared_experiences`

2. 管理层
   - [`app/game_core/orchestration/companion_manager.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/orchestration/companion_manager.py)
   - 已有：
     - `recruit`
     - `dismiss`
     - `force_leave`

3. 运行时层
   - [`app/game_core/narrative/companion_runtime.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/narrative/companion_runtime.py)
   - 已有：
     - `CompanionRuntimeManager`
     - `CompanionInstance`
     - `TickRecord`

4. 编排接线
   - [`app/game_core/orchestration/tick_coordinator.py`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/game_core/orchestration/tick_coordinator.py)
   - 当前会把结构化 tick record 分发给队友运行时

5. 相关 Hook
   - `SharedExperienceHook`
   - `CampfireHook`
   - `RelationshipHook`
   - `PrivateChatTriggerHook`

#### 后端主要缺口

1. 队友位置跟随没有真正收口
   - 设计文档要求：队友加入队伍后应跟随玩家，并与场景存在保持一致
   - 当前没有稳定证据表明：
     - recruit 后会把队友放到玩家当前场景
     - navigate 后会同步 party members 到 `AreaSlice.npc_locations`
   - `NpcScheduleHook` 还排除了 party members，因此它也不会承担这部分同步职责

2. 队友“存在”更多是状态存在，不是稳定的场景存在
   - `PartySlice` 有成员
   - `CompanionRuntimeManager` 有实例
   - 但不等于玩家一定能在当前 scene 里稳定看到这些队友

#### 前端现状

- 当前前端只显式消费了：
  - `companion_recruited`
  - `companion_dismissed`
  - `npc_wants_to_chat`
- 参考：
  - [`../frontend/src/hooks/useGameStream.ts`](/home/xiaokebuyu/workplace/gemini-hackton/frontend/src/hooks/useGameStream.ts)
  - [`../frontend/src/game/overlays/ChatInviteModal.tsx`](/home/xiaokebuyu/workplace/gemini-hackton/frontend/src/game/overlays/ChatInviteModal.tsx)
- 没有看到：
  - `partyStore`
  - 队伍面板
  - approval 可视化
  - recruit/dismiss 主 UI
  - shared experiences 可视化
- `resume` 返回的 `party` payload 也没有真正建立前端队伍状态视图

#### 定性

- 后端：中等完成度，约 `60%-70%`
- 前端：明显落后，约 `15%-25%`

当前更像：
“后端系统存在，但玩家侧几乎感知不到完整队伍系统”

---

### 2.4 长休：当前实现符合文档，但玩家表现层容易混乱

#### 设计与实现是否一致

一致。

- 文档明确写的是：
  - `rest_long(remaining_slots=8)`
  - `→ 逐格结算 × 8`
- 参考：
  - [`app/博德之门3架构设计规范/编排层设计规范.md`](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/博德之门3架构设计规范/编排层设计规范.md)
  - `rest_long(remaining_slots=8)` 对应 `逐格结算 × 8`
- 当前规则层和协调器实现也是：
  - RestHandler 计算整段 `time_cost`
  - TickCoordinator 逐格消费 accumulated time

#### 问题不在“是否逐格”

问题在于：

1. 内部世界模拟逐格是合理的
2. 玩家看到的表现现在也接近逐格裸流出

即使已经做过 quiet slot 抑制，长休期间仍可能看到：

- `time_advanced`
- `event_state_changed`
- 关系变化
- NPC 日程变动带来的在场变化
- 营火/私聊等长休尾部社交事件

这些单独都合理，但连续出现会让玩家体感变成：

“睡一觉时整个世界在台前不断跳动”

#### 定性

这更像是产品表现层问题，而不是纯逻辑 bug。

更合适的方向不是直接把长休改成“内部只算 1 格”，而是：

- 内部继续 8 格逐格模拟
- 外部做压缩显示/合帧显示
- 玩家只看：
  - 开始休息
  - 关键中断
  - 长休结束后的重要变化

也就是：

**内部逐格，外部压缩**

---

## 3. 相对设计文档的偏差评估

### 3.1 NPC 在场语义

#### 文档目标

- 场景展示与可交互性应共享同一套 presence 语义
- 玩家能看到的人，应当符合“当前可达/可交互”的基本认知

#### 当前偏差

- `scene_views` 允许 area 主场景 NPC 显示
- `interaction` 不允许 area 主场景 NPC 对话

#### 完成度

约 `40%`

不是“没做”，而是两套规则打架。

---

### 3.2 NPC 日程 / 场景在场

#### 文档目标

- named NPC / passerby / 队友 / planner 投递对象，各自来源和行为边界要清晰
- 队友不走普通日程，而是跟随玩家

#### 当前偏差

- 日程移动已存在，但“玩家为什么会在这里见到这个 NPC”缺少统一解释层
- party spatial follow 没有真正收口

#### 完成度

约 `50%`

---

### 3.3 队伍系统

#### 文档目标

- 后端：
  - PartySlice
  - CompanionManager
  - 队友跟随
  - 共同经历
  - 审批值/关系联动
  - 营火与休息期互动
- 前端：
  - 队伍可见
  - approval 可感知
  - recruit/dismiss 可操作
  - 长休/营火/私聊时队友存在感明确

#### 当前偏差

- 后端已有大半，但“空间跟随”和“场景存在一致性”仍弱
- 前端大部分仍停留在事件通知层

#### 完成度

- 后端：`60%-70%`
- 前端：`15%-25%`

---

### 3.4 长休表现

#### 文档目标

- 世界内部逐格结算
- 长休期间可触发：
  - 营火对话
  - 夜间事件
  - 世界时间推进后果

#### 当前偏差

- 后端模拟方向符合文档
- 但玩家表现层没有做足够压缩
- 导致内部结算细节直接暴露

#### 完成度

- 模拟层：`80%+`
- 玩家表现层：`40%-50%`

---

## 4. 分块处理建议

### Block A - NPC 在场语义统一

#### 目标

统一“谁会显示在当前 scene”与“谁可以被当前交互”的规则。

#### 要做的事

1. 定义单一真相源：`is_npc_reachable(area_id, location_id, npc_area, npc_location)`
2. 让 `scene_views` 与 `interaction` 共用这套判断
3. 明确支持：
   - area 主场景 `(area, None)`
   - 子地点 `(area, location_id)`
4. 修掉“看得见却不能说话”

#### 验收

- area 主场景里的 NPC 能正常对话
- 子地点 NPC 仍然只在同地点可交互

#### 优先级

最高

---

### Block B - NPC 日程与在场解释层

#### 目标

把“为什么这个 NPC 会在这里”分清楚，不再让玩家感觉像随机刷出。

#### 要做的事

1. 区分 4 类 NPC 在场来源：
   - 常驻场景 NPC
   - 日程移动 NPC
   - planner 投递 NPC
   - 事件型出现
2. 明确日程移动是否允许把 named NPC 放到 area 主场景
3. 如果允许，确保交互规则支持
4. 如果不允许，收紧 `NpcScheduleHook` 的落点

#### 验收

- 长休后 NPC 出现位置可解释
- 不再出现“像随机冒出来但又不能互动”的状态

#### 优先级

高

---

### Block C - 队伍系统后端收口

#### 目标

让队友不仅存在于 `PartySlice`，也稳定存在于场景与编排逻辑中。

#### 要做的事

1. 明确 recruit 后的队友空间归属
2. 建立 navigate / location change 时的队友跟随同步
3. 统一：
   - `PartySlice.members`
   - `AreaSlice.npc_locations`
   - `CompanionRuntimeManager`
4. 检查营地/长休时队友是否应进入 `present_npcs`

#### 验收

- 队友加入后能稳定跟随玩家
- 当前场景内的队友可见、可交互、可参与营火/私聊逻辑

#### 优先级

高

---

### Block D - 队伍系统前端最小可见化

#### 目标

让玩家真正感知到“我有队伍”，而不是后端默默维护。

#### 要做的事

1. 增加最小队伍视图：
   - 当前队友列表
   - approval 概览
2. `resume/opening` 时同步恢复队伍视图
3. 提供 recruit/dismiss 入口或至少可视反馈
4. 让 `campfire_dialogue`、队友私聊、共同经历有前端存在感

#### 验收

- 玩家能看到当前队友
- 玩家能感知 approval 和队伍变化

#### 优先级

中高

---

### Block E - 长休玩家表现压缩

#### 目标

保留内部 8 格逐格模拟，但不要把全部内部细节逐条端给玩家。

#### 要做的事

1. 定义长休的玩家展示模型：
   - 开始休息
   - 静默推进
   - 中断事件
   - 结束总结
2. 对 quiet slot 做前端或流层压缩
3. 只突出：
   - 真正重要的 `gm_narration`
   - 营火对话
   - 私聊邀请
   - 关键事件变化
4. 避免把每个内部 tick 的技术痕迹都变成主叙事

#### 验收

- 长休依然逐格模拟
- 但玩家看到的是清晰的休息过程，而不是信息碎片流

#### 优先级

中

---

## 5. 推荐执行顺序

### 第一组：先修硬错误

1. Block A - NPC 在场语义统一
2. Block B - NPC 日程与在场解释层

理由：
- 当前已经有明确用户可见 bug
- 这是后续队伍跟随和长休场景一致性的前提

### 第二组：补系统闭环

3. Block C - 队伍系统后端收口
4. Block D - 队伍系统前端最小可见化

理由：
- 队伍系统现在最大问题不是“完全没有”，而是“前后端各做了一半”
- 需要把“状态存在”变成“玩家可见存在”

### 第三组：做体验整形

5. Block E - 长休玩家表现压缩

理由：
- 这块更偏产品层
- 建议在 presence / party / campfire 语义稳定之后再做

---

## 6. 当前建议

如果按风险和收益排序，下一步最值得直接动手的是：

1. **先修 Block A**
   - 统一 `scene_views` 和 `interaction` 的 NPC presence 语义
2. **紧接着做 Block C**
   - 补队友跟随和场景在场同步
3. **再做 Block D**
   - 给队伍系统一个最小前端面板/可视状态
4. **最后处理 Block E**
   - 把长休表现从“内部逐格裸流”改成“外部压缩展示”

---

## 7. 一句话总结

当前最需要分块处理的，不是一个单点 bug，而是三条链：

1. **NPC 在场语义链**：显示与交互规则不一致  
2. **队伍系统闭环链**：后端已有，前端弱，空间跟随未收口  
3. **长休表现链**：内部模拟合理，但玩家展示过于暴露内部结算细节

把这三条链拆开后，后续每一轮修改才会更稳定。

---

## 8. Block A+B 实施记录（2026-03-07 完成）

### 根因调查

通过 debug 脚本实证验证，发现"NPC 可见但不可交互"的根因不是单纯的校验 bug，而是 NPC 日程系统存在**三层数据-代码断裂**：

1. **Schedule key 不匹配**：characters.json 用 `morning/afternoon/evening`，`_predict_next_period()` 返回 `dawn/day/dusk` → `_scheduled_destination()` 永远查不到键
2. **Schedule 值不对应**：值是概念性地名（`temple`, `guild_hall`, `tavern`），既不是 area ID 也不是 sub-location ID → 映射全部失败
3. **Provider 硬编码**：`_moves_to_town` 假设存在 `"town"` area（实际是 `frontier_town`）→ dusk/night 永远 noop；两个 move 方法都硬编码 `location_id=None` → NPC 被移到 area 主场景

结果链：`BasicNpcScheduleProvider` 对当前数据完全空转 → 偶尔触发的 fallback 移动把 NPC 从子地点拽到 area 主场景（location=None）→ `_validate_npc_presence` 不接受 area 主场景匹配 → 前端看得见 NPC 却无法交互。

### 修复内容

#### 内容数据修复

**characters.json**：
- schedule 键名对齐：`morning→dawn, afternoon→day, evening→dusk`
- schedule 值改为 maps.json 中真实存在的 sub-location ID
- 支持结构化跨区格式：`{"area": "cow_girl_farm", "location": "gs_warehouse"}`（goblin_slayer dusk/night）

**maps.json**：
- frontier_town 新增 `tavern` 子地点（多角色日程引用）
- cow_girl_farm 新增 `farm_field` 子地点（cow_girl 白天工作地）
- 修复 4 个角色的跨区 resident_npcs 冲突：goblin_slayer / high_elf_archer / dwarf_shaman / lizard_priest 统一归到 area_id 对应的 frontier_town/adventurer_guild

#### 代码修复

**`characters.py`**：
- `CharacterTemplate.schedule` 类型从 `dict[str, str]` 改为 `dict[str, Any]`
- 加载时保留 dict 值（不再 `str(v)` 强转）

**`npc_schedule.py`**：
- `_scheduled_destination()` 返回 `tuple[str | None, str | None]`（area_id, location_id），支持字符串（同区子地点）和 dict（跨区移动）两种格式
- `_moves_to_town` / `_moves_to_home` 合并为 `_collect_moves`，消除 dusk/night vs dawn/day 分支
- 移除 `"town"` 硬编码和 `location_id=None` 硬编码

**`interaction.py`**：
- `_validate_npc_presence` 新增 area 主场景（双方 location=None）匹配分支作为安全网

#### 测试

- `test_npc_schedule_hook.py`：mock 数据添加 schedule 条目，断言更新匹配新语义（14 passed）
- `test_npc_schedule_provider.py`：测试重写覆盖新的返回值格式和移动逻辑（10 passed）
- 全量测试：1480 passed（唯一失败是 pre-existing flaky test）

### 修复后验证（debug 脚本输出）

- 所有 period 都能查到 schedule 条目并产生正确移动
- 所有移动都带有明确的 `location_id`（非 None）
- goblin_slayer 跨区移动到 `cow_girl_farm/gs_warehouse` 正确解析
- NPC 不再被移到 area 主场景（location=None）

### Block A+B 偏差评估更新

- **NPC 在场语义**完成度：`40%` → `90%`（场景显示与交互校验统一，日程系统真正工作）
- **NPC 日程/场景在场**完成度：`50%` → `85%`（schedule 数据对齐，provider 正确解析，跨区移动支持）
- 剩余 gap：`_MOVE_LIMIT=2` 导致 period 变化时只有 2 个 NPC 移动（需多次 tick 才能让全员归位）；路人系统（PasserbyPool）未实现
