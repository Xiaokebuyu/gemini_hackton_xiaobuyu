# Planner 叙事规划

## 模块概要

Planner 是游戏的"导演"，观察玩家行为，通过 23 种 directive 编排任务/地点/NPC 来推进叙事。

### 核心模块

| 模块 | 位置 | 职责 |
|------|------|------|
| NarrativePlannerHook | `orchestration/hooks/narrative_planner.py` | Planner 主入口 hook（priority=66），每 settlement tick 执行：确定性预处理 → 语义事件收集 → LLM 决策 → 指令执行 |
| AgenticNarrativePlanner | `app/narrators.py` | LLM 驱动的 Planner 实现，multi-turn agent（可调用 design_skill 工具），输出 JSON directive 列表 |
| PlannerDispatcher | `planning/subsystem.py` | 路由 PlannerEvent 到注册的 SubSystem，管理 busy-lock 和事件队列 |
| NarrativePlanSlice | `state/slices/narrative_plan.py` | Planner 专属状态：章节进度、NPC 指令、任务历史、行为窗口、策略笔记、里程碑大纲、NPC 能力/服务 |

### 四个 SubSystem

| SubSystem | 位置 | 处理的 directive |
|-----------|------|-----------------|
| QuestManager | `planning/quest_manager.py` | create_quest, publish_bulletin, retire_quest, update_quest, advance_milestone, set_task_monitor |
| NpcDirector | `planning/npc_director.py` | direct_npc, spawn_quest_npc, assign_capability, revoke_capability, assign_service, revoke_service, curate_shop, create_rumor, modify_location |
| WorldBuilder | `planning/world_builder.py` | plant_environmental, fill_area, fill_location, plant_encounter, discover_room, fill_room |
| NarrativeWeaver | `planning/narrative_weaver.py` | schedule_event, escalate, adjust_pacing |

### 辅助模块

| 模块 | 位置 | 职责 |
|------|------|------|
| directive_contracts | `planning/directive_contracts.py` | 23 种 directive 的合约校验和归一化 |
| semantic_events | `planning/semantic_events.py` | 从 change_log/action_log 收集语义事件供 Planner 决策 |
| opening_bootstrap | `planning/opening_bootstrap.py` | 开场序列确定性 planner 组装 |
| pacing_controller | `planning/pacing_controller.py` | 已废弃，合并入 NarrativeWeaver |
| dynamic_sub_area | `planning/dynamic_sub_area.py` | 动态子区域管理器（创建/过期/容量） |
| capabilities | `planning/capabilities.py` | NPC 动态能力描述符 |
| service_descriptors | `planning/service_descriptors.py` | NPC 动态服务定义 |
| planner_tools | `narrative/planner_tools.py` | Planner agent 的 LLM 工具（read_design_skill, list_design_skills） |
| PlannerQuestHandler | `rules/handlers/planner.py` | Planner 内部 Command 处理器（planner_create_quest 等） |
| PlannerWorldHandler | `rules/handlers/planner.py` | Planner 世界构建 Command 处理器（planner_fill_area 等） |
| PlannerNpcHandler | `rules/handlers/planner.py` | Planner NPC Command 处理器（planner_direct_npc 等） |
