# Planner 职能完成度审计与 TODO

记录日期：2026-03-16
审计范围：NarrativePlannerHook + 4 个 PlannerSubSystem + AgenticNarrativePlanner + Clue + Quest 追踪

---

## 一、Planner 架构总览

### 运行时结构

```
NarrativePlannerHook (priority=66)
  ├── 确定性预处理（GC / despawn / auto-escalation / quest_completed 奖励服务）
  ├── 语义事件收集 → collect_planner_events()
  ├── blackboard.plan() → AgenticNarrativePlanner (LLM)
  ├── 指令执行 → PlannerDispatcher → 4 个 SubSystem
  │     ├── QuestManagerSubSystem      — create_quest / publish_bulletin / retire_quest / update_quest / advance_milestone / set_task_monitor
  │     ├── NpcDirectorSubSystem       — direct_npc / spawn_quest_npc / assign_capability / revoke_capability / assign_service / revoke_service / curate_shop / create_rumor / modify_location
  │     ├── WorldBuilderSubSystem      — plant_environmental / fill_area / fill_location / plant_encounter / discover_room / fill_room
  │     └── NarrativeWeaverSubSystem   — schedule_event / escalate / adjust_pacing
  ├── story_facts → NarrativePlanSlice
  ├── outline_updates → milestone_outline 同步
  └── SSE 事件发射
```

### 支持的 23 种 Directive Kind

全部在 `directive_contracts.py` 注册且有完整的合约校验 + handler 实现：

| Kind | SubSystem | Handler | 合约校验 | 测试 |
|------|-----------|---------|---------|------|
| create_quest | QuestManager | planner_create_quest | ✅ | ✅ |
| publish_bulletin | QuestManager | planner_publish_bulletin | ✅ | ✅ |
| retire_quest | QuestManager | planner_retire_quest | ✅ | ✅ |
| update_quest | QuestManager | planner_update_quest | ✅ | ✅ |
| advance_milestone | QuestManager | planner_advance_milestone | ✅ | ✅ |
| set_task_monitor | QuestManager | planner_set_task_monitor | ✅ | ✅ |
| direct_npc | NpcDirector | planner_direct_npc | ✅ | ✅ |
| spawn_quest_npc | NpcDirector | planner_spawn_quest_npc | ✅ | ✅ |
| assign_capability | NpcDirector | planner_assign_capability | ✅ | ✅ |
| revoke_capability | NpcDirector | planner_revoke_capability | ✅ | ✅ |
| assign_service | NpcDirector | planner_assign_service | ✅ | ✅ |
| revoke_service | NpcDirector | planner_revoke_service | ✅ | ✅ |
| curate_shop | NpcDirector | planner_curate_shop | ✅ | ✅ |
| create_rumor | NpcDirector | create_rumor (WorldStateHandler) | ✅ | ✅ |
| modify_location | NpcDirector | modify_location (WorldStateHandler) | ✅ | ✅ |
| plant_environmental | WorldBuilder | planner_plant_environmental | ✅ | ✅ |
| fill_area | WorldBuilder | planner_fill_area | ✅ | ✅ |
| fill_location | WorldBuilder | planner_fill_location | ✅ | ✅ |
| plant_encounter | WorldBuilder | planner_plant_encounter | ✅ | ✅ |
| discover_room | WorldBuilder | planner_discover_room | ✅ | ✅ |
| fill_room | WorldBuilder | planner_fill_room | ✅ | ✅ |
| schedule_event | NarrativeWeaver | schedule_event (WorldStateHandler) | ✅ | ✅ |
| escalate | NarrativeWeaver | planner_escalate | ✅ | ✅ |
| adjust_pacing | NarrativeWeaver | planner_set_pacing_frozen | ✅ | ✅ |

**结论：23 种指令的合约校验 → 子系统路由 → Command handler → StateDelta 全链路均已实现。单元测试全部通过。**

---

## 二、Planner ↔ NPC 任务发布与执行流程

### 任务发布流程（Planner → NPC）

```
1. Planner LLM 输出 create_quest directive
   → QuestManager._apply_create_quest()
   → planner_create_quest Command
   → QuestSlice.dynamic_quests 新增任务（status=available）
   → SSE: quest_created + quest_status_changed

2. Planner LLM 输出 publish_bulletin directive
   → QuestManager._apply_publish_bulletin()
   → planner_publish_bulletin Command → 任务张贴到 board
   → 自动 notify_resident_npcs → NpcDirector.direct_npc(bulletin_awareness)
   → 相关 NPC 获得 directive，blackboard 写入 goals + pending_topic

3. Planner LLM 输出 direct_npc directive
   → NpcDirector._apply_direct_npc() → planner_direct_npc Command
   → NarrativePlanSlice.npc_directives 追加指令
   → RelationSlice.npc_blackboards[npc_id] 写入 goals + pending_topic
   → DirectiveTriggerHook 检测到待消费 directive → SSE npc_has_directive
   → 前端显示 NPC 有话对你说，玩家点击后触发 npc_interaction
   → npc_interaction.py 注入 pending_topic 到 NPC system prompt
   → NPC 对话完成后清除 pending_topic
```

### 任务完成识别机制（三层冗余）

**第一层：QuestObjectiveTrackingHook (priority=56)**
- 每 settlement tick 遍历所有 active 动态任务的 objectives
- 对有 `condition` 字段的 objective，调用 `BasicEventConditionEvaluator._condition_met()` 检测
- 条件满足 → `obj["completed"] = True`
- 所有 objective 完成 → quest.status 改为 `completed`（或 `ready_to_report` 如果 `requires_report=True`）

**第二层：TaskMonitorHook (priority=57)**
- 检查 quest.task_monitor.conditions（planner 通过 set_task_monitor 设置的监控条件）
- 全部满足 + on_complete="auto" → advance_quest Command → 直接完成 + 发放奖励
- 全部满足 + on_complete="notify" → SSE task_monitor_triggered

**第三层：NarrativePlannerHook 确定性预处理**
- 检测 quest_completed 语义事件 → 自动为 receptionist NPC 分配 reward service
- 玩家找 receptionist 领取报酬

### 支持的 11 种任务完成条件

| 条件类型 | 参数 | 检测方式 |
|----------|------|---------|
| flag_set | key, value | flags[key] == value |
| npc_talked | npc_id | flags["talked_to_{npc_id}"] |
| item_obtained | item_id | player.inventory 检查 |
| kill_count | monster_type, count | flags["kills_{type}"] >= count |
| level_reached | level | player.level >= level |
| location_visited | area_id, location_id | flags["visited_{area}_{loc}"] |
| encounter_cleared | area_id, encounter_id | hostile_tracking[id].cleared |
| clue_investigated | area_id, clue_id | interactable_states[id].resolved |
| all_encounters_cleared | area_id | 全部 hostile_tracking.cleared |
| danger_below | area_id, threshold | danger_level < threshold |
| location_entered | area_id | player.current_area 匹配 |

---

## 三、Planner ↔ Clue 系统

### Clue 创建链路

```
Planner LLM → plant_environmental directive
  → WorldBuilder._apply_plant_environmental()
    ├── Path A (翻译路径): 满足条件时翻译为 fill_location + investigate_clue interactable
    │   条件：无 interactables/resident_npcs + area_id + clue_id + location_id 可解析
    │   产出：interactable.functional.type = "investigate_clue"
    └── Path B (子区域路径): 创建独立 temporary_sub_area（discovery 类型）
        产出：动态子区域，需要玩家前往探索

Path A 产出的 clue 包含：
- clue_id, name, description, topic
- options: [{id, label, check?: {skill, dc}}]（2~4 个选项）
- outcomes: {option_id: {always: [...], on_pass: [...], on_fail: [...]}}
- on_first_inspect: [...] 效果列表
- party_prompt_hints: [...] 队伍讨论提示
```

### Clue 调查链路

```
玩家选择 investigate_clue → ClueHandler._compute_investigate()
  → 首次检查 → 应用 on_first_inspect 效果
  → 返回 options 列表供玩家选择

玩家选择选项 → resolve_clue_option
  → 如有 check → d20 + skill_bonus vs DC → passed/failed
  → select_option_effects(passed) → apply_clue_effects()
  → 效果类型：set_flag / remove_flag / advance_quest / add_knowledge / modify_approval / unlock_sub_location
  → 写入 interactable_states（resolved_option_id, outcome_text, check_passed, effects_applied）
  → 写入 area_events（调查记录）
  → hide_on_resolve → 从场景移除 interactable overlay
```

### Clue 完成条件联动

```
Planner 创建任务时可设 objective.condition.type = "clue_investigated"
  → QuestObjectiveTrackingHook 每 tick 检查
  → interactable_states[clue_id].resolved_option_id 非空 → objective completed
```

**结论：Clue 创建→调查→效果→任务联动全链路闭合。**

---

## 四、Planner 子地点生成能力

### 三种子地点生成指令

| 指令 | 用途 | 产出 |
|------|------|------|
| fill_area | 在 area 下新增子地点 | temporary_sub_area（可设 locked=true） |
| fill_room | 在 sub_location 下新增房间 | dynamic_room（可设 discoverable=true） |
| discover_room | 解锁已有的 discoverable 房间 | 标记 discovered_dynamic_rooms |

### fill_location（不创建新地点）

- 在已有 sub_location/room 中添加 interactable overlay
- 支持 clue interactable（investigate_clue functional type）
- 支持普通 inspect/interact 类 interactable

### plant_environmental（双路径智能）

- Path A：clue_id + location_id 可解析 → 翻译为 fill_location（精确放置到现有位置）
- Path B：否则创建独立 temporary_sub_area（discovery 类型）

### DynamicSubAreaManager 容量控制

- 每个 area 有 temporary_sub_area 容量上限
- Planner context 中提供 `remaining_capacity` 信息
- 超出容量时 handler 拒绝执行

**结论：子地点生成三层（area 子区域 / sub_location 房间 / 场景内 interactable）全部实现，有容量控制。**

---

## 五、Planner 职能完成度评估

### ✅ 已完成且工作正常（单元测试全部通过）

1. **23 种 directive 全链路**：合约校验 → 子系统路由 → handler → StateDelta（全部通过）
2. **任务生命周期**：创建 → 发布 → 追踪 → 完成 → 奖励 → 退役
3. **任务自动完成**：11 种 condition 类型 + QuestObjectiveTrackingHook + TaskMonitorHook
4. **NPC 指令系统**：direct_npc + pending_topic 注入 + blackboard 写入
5. **Clue 系统**：创建 → 调查 → 效果 → 任务联动
6. **子地点生成**：fill_area / fill_room / discover_room / fill_location / plant_environmental
7. **NPC 能力/服务管理**：assign/revoke capability/service + 过期清理
8. **里程碑管理**：advance_milestone（80% 条件验证）+ outline 同步
9. **节奏控制**：escalate + adjust_pacing + auto-escalation safety net
10. **遭遇种植**：plant_encounter + 怪物 ID 列表 + 地图分类
11. **商店策展**：curate_shop（add/remove/restock items）
12. **谣言系统**：create_rumor
13. **位置修改**：modify_location
14. **事件调度**：schedule_event
15. **设计模板查阅**：read_design_skill / list_design_skills（LLM agentic 工具）

### ⚠️ 功能实现但运行时效果待验证（需要实际 LLM 调用验证）

1. **LLM 输出质量**：Planner prompt 能否稳定产出格式正确且语义合理的 directive
2. **story_facts 三元组**：WKG 写入实现了，但实际使用中 planner 是否输出有效三元组
3. **outline_updates 同步**：代码实现了，但 LLM 是否正确使用 completed_steps/new_steps/remove_steps
4. **strategy_notes 跨轮记忆**：写入/读取都实现了，但 LLM 是否有效利用
5. **play_style_tags 适应**：行为窗口推导实现了，LLM prompt 中有指引但实际效果未知

### ❌ 已知缺陷（from known_issues.md + P32 分析）

1. **Planner 运行频率**：FALLBACK_INTERVAL 已降为 1（P32-1 修复），但仍依赖 settlement tick 触发（6 次对话 ≈ 1 settlement），实际约每 6 次对话运行一次
2. **area_situation 汇总缺失**：AreaSlice.area_situation 字段已有但 planner 未写入汇总，NPC 看不到区域态势
3. **NPC 自主行为缺失**：NpcAutonomyHook 在设计中但未实现（known_issues.md 阶段 4-A）
4. **NPC 黑板初始化**：blackboard 功能实现了但空跑消化旧印象的逻辑未实现
5. **任务奖励无 rewards 的情况**：Planner prompt 已强调必须指定 rewards，但 LLM 可能仍遗漏

---

## 六、TODO 清单

### P0 — 必须修（阻塞核心体验）

- [ ] **TODO-01**：验证 Planner LLM 端到端运行 — 启动实际 session，确认 AgenticNarrativePlanner.plan() 返回有效 directive 并被成功执行。当前只有单元测试（mock blackboard），没有集成级 LLM 验证。
- [ ] **TODO-02**：验证 quest 完成 → 奖励发放全链路 — 创建带 condition 的 quest → 满足条件 → QuestObjectiveTrackingHook 标记完成 → reward service 分配到 receptionist → 玩家领取。
- [ ] **TODO-03**：验证 clue 创建 → 调查 → 任务联动 — plant_environmental(Path A) → 玩家 investigate_clue → resolve_clue_option → clue_investigated condition 满足 → quest objective 完成。

### P1 — 应该修（影响体验质量）

- [ ] **TODO-04**：Planner area_situation 汇总 — NarrativePlannerHook.execute() 末尾应将 area_events + danger_level + 遭遇/线索统计汇总写入 area_situation，供 NPC 读取。（known_issues 3-B）
- [ ] **TODO-05**：NPC context_builder 注入 area_situation — `_build_l2()` 应包含 area_situation 和 recent_area_events，减少 NPC 编造不存在事件。（known_issues 3-A）
- [ ] **TODO-06**：NPC 任务联动移动 — npc_schedule.py 应优先检查 npc_directives 中的 destination 字段，任务相关 NPC 应移动到任务区域。（known_issues 3-D）
- [ ] **TODO-07**：retire_quest 先发奖励 — QuestManager._apply_retire_quest() 已实现，但需验证：all_objectives_completed + rewards 未领取 → 自动分配 reward service。（known_issues 3-E）

### P2 — 增强（提升内容密度）

- [ ] **TODO-08**：NpcAutonomyHook 实现 — NPC 空跑（同 room NPC 自主更新 blackboard + 队友探索线索），是世界"活着"感觉的核心。（known_issues 4-A）
- [ ] **TODO-09**：NPC 提示词精简 — 用 blackboard 替代旧的 impressions/stage_guides 注入，减少 token 浪费。（known_issues 3-C）
- [ ] **TODO-10**：Osiris 机械化 — 去 LLM，纯规则表驱动因果引擎。（known_issues 2-A）
- [ ] **TODO-11**：Hook 整合（Osiris 协调模式）— EncounterHook + PassivePerceptionHook + EventConditionHook 统一到 Osiris 协调。（known_issues 2-B）

### P3 — 打磨（非阻塞但提升品质）

- [ ] **TODO-12**：Planner prompt 针对 create_quest rewards 的强制校验 — 虽 prompt 已说明，可在 directive_contracts 中对 create_quest 添加 rewards 非空校验。
- [ ] **TODO-13**：behavior_window → play_style_tags 验证 — 确认 24 tick 滑动窗口正确推导 combat_heavy/dialogue_heavy/exploration_heavy/quest_focused/idle。
- [ ] **TODO-14**：milestone_outline LLM 使用验证 — 确认 LLM 能正确使用 outline_updates（completed_steps/new_steps/remove_steps）。
- [ ] **TODO-15**：write_episode（WKG 三元组提取）— 当前不会自然触发（ContextWindow 200K 永远不溢出），需要手动触发或降低阈值验证。

---

## 七、测试通过情况快照

```
test_r4_planner_capabilities.py           — 19 passed ✅
test_track_a_planner_fullchain.py         — 27 passed ✅
test_narrative_planner_hook.py            — 59 passed ✅
test_clue_fix.py                          — passed ✅
test_33_quest_completion.py               — passed ✅
test_34_update_quest.py                   — passed ✅
test_phase5_quest_tracking.py             — passed ✅
test_p28_task_monitor.py                  — passed ✅
test_assign_quest_tool.py                 — passed ✅
test_p32_phase6_plant_environmental_label — passed ✅
test_p32_parent_location.py              — passed ✅
test_p32_phase3_pending_topic.py          — passed ✅
```

全部 planner 相关测试 **247+ tests passed, 0 failed**。
