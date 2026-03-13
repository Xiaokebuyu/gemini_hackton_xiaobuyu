# 叙事层 + 叙事规划施工记录

**设计文档**：叙事层设计规范 + 叙事规划子系统设计规范
**代码路径**：`app/game_core/narrative/` + `app/game_core/planning/`
**Phase**：4C（Agent 工具）+ 4B-P35（NarrativePlannerHook）

## 模块状态

### 叙事层（narrative/）

| 组件 | 状态 | 说明 |
|------|------|------|
| `AgentTool` ABC | [完成] | name/description/parameters/allowed_roles/execute |
| `AgentContext` | [完成] | role + world + state + scene_entries + execute_command |
| `AgentContextBuilder` | [完成] | 7 层上下文 + 角色可见性矩阵 + 系统提示构建（D-N13） |
| `AgenticExecutor` | [更新] | 单轮 `run()` + 多轮 `run_agentic()` + LlmPort 注入（D-N10）；P29-A1 NPC 纯文本优雅降级 |
| `RoleToolRegistry` | [完成] | gm/npc/teammate 分桶 + 同名工具按序覆盖 |
| `ToolResult` | [完成] | success/message/commands/metadata |
| **GM 工具（5 个）** | [完成] | describe_environment / narrate / comment / pass_turn / suggest_options（D-N08） |
| **NPC 工具（8 个）** | [更新] | speak / emote / update_feeling / remember / offer_quest / offer_trade / refuse / reveal_secret（D-N09）；P29-A2 OfferQuestTool/AcceptQuestTool 改用 role_data 验证 |
| **Teammate 工具（6 个）** | [完成] | speak / emote / express_opinion / suggest_tactic / share_memory / request_action（D-N09） |

### 叙事规划（planning/）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlanner` | [完成] | L0-L5 升级阶梯 + 区域填充 + 任务播种 + 解冻（D-N07） |
| `DynamicSubAreaManager` | [完成] | AreaSlice-backed create/expire/list_active |
| `PlanningDirective` 系列 | [完成] | 9 种指令类型 dataclass |
| `PlannerSubSystem` | [完成] | Protocol：name/handles/accepts_event/evaluate/apply_directive（D-P20a） |
| `PlannerDispatcher` | [完成] | 忙碌锁 + bounded queue + drain 模式（D-P20a） |
| `LegacyDirectiveSubSystem` | [已删除] | D-P20a 创建，D-P20b 删除（全部 handles 迁移到 4 个子系统） |
| `QuestManagerSubSystem` | [完成] | create_quest/publish_bulletin/retire_quest/update_quest（D-P20b/D-P34） |
| `NpcDirectorSubSystem` | [完成] | direct_npc/spawn_quest_npc（D-P20b） |
| `WorldBuilderSubSystem` | [完成] | plant_environmental/fill_area（D-P20b） |
| `PacingControllerSubSystem` | [完成] | escalate/adjust_pacing（D-P20b） |
| `planning/utils.py` | [完成] | coerce_non_empty_string/string_or_empty/normalize_mapping（D-P20b） |
| `NarrativeWeaverSubSystem` | [完成] | Directive GC + 任务过期 + NPC 清理 + 自动升级保障网（D-P20c） |
| `ItemDesignerSubSystem` | [部分完成] | curate_shop 已激活（D-P36）；design_reward 仍 deferred |

### 适配器（adapters/）

| 组件 | 状态 | 说明 |
|------|------|------|
| `DesignSkillPort` / `NullDesignSkillPort` | [完成] | Planner 设计模板检索边界协议（D-P20a） |

### 叙事规划工具（narrative/planner_tools.py）

| 组件 | 状态 | 说明 |
|------|------|------|
| `ReadDesignSkillTool` | [完成] | 读取 data/{world_id}/planner_skills/{cat}/{name}.md（D-P20a） |
| `ListDesignSkillsTool` | [完成] | 列出可用设计模板（D-P20a） |

### App 层实现

| 组件 | 状态 | 说明 |
|------|------|------|
| `LocalDesignSkillProvider` | [完成] | 文件系统读取 + 路径遍历防护（D-P20a） |
| `AgenticNarrativePlanner` | [更新] | 新增 executor/design_skill_port/world_id 参数；plan() 走多轮 Agent（D-P20a） |

### Hook 边界状态（编排层联动）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlannerHook` | [更新] | D-P20a：dispatcher 参数 + context 注入；D-P20b：删除 _apply_directive + 辅助方法，移除 instance_manager/sub_area_manager 参数；D-P20c：删除 _expire_dynamic_quests/_despawn_expired_quest_npcs，激活 dispatch() 调用链 |
| `GmNarrationHook` | [完成] | narrator 注入边界 + SceneBus 受控写入 |
| `MilestoneCompletionHook` | [新增] | P25-14 Phase 1：自动完成 ACTIVE 里程碑（P54，在 MilestoneUnlockHook 之前） |
| `QuestObjectiveTrackingHook` | [新增] | P25-14 Phase 2：自动追踪有结构化条件的任务目标（P56，在 MilestoneUnlockHook 之后） |

## 决策记录

### [D-P29-A10] 动态子地点背景图生成集成（A10a + A10b）

**变更文件：**
- `app/image_prefetch.py` — **新文件**：`AssetResolver` 模块级单例（`get_asset_resolver()`）、`prefetch_sub_area_backgrounds(result, time_period, resolver)` 扫描 `PipelineResult.delta` 中的 `temporary_sub_areas` 变更并以 `asyncio.create_task()` 非阻塞触发背景图生成、`get_cached_background_url(sub_area_id, time_period, resolver)` 同步缓存查询（仅读 LRU，不触发生成）
- `app/scene_views.py` — `build_scene_change()` 新增 `asset_resolver` 可选参数；玩家在动态子地点时调用 `_lookup_cached_background()` 查缓存；命中则在 payload 追加 `background_url`（`data:<mime>;base64,<b64>` 格式）
- `app/routers/gameplay.py` — `navigate` 路由的 `_execute()` 闭包：执行成功后调用 `_schedule_sub_area_prefetch(result, session)`；`build_scene_change()` 调用改为传 `asset_resolver=resolver`；新增 `_schedule_sub_area_prefetch()` helper（读取 time_period + 调用 prefetch）

**关键设计决策：**
- 非阻塞设计：`prefetch_sub_area_backgrounds()` 同步返回，每个生成任务独立 asyncio Task；`asyncio.create_task()` 要求在 running event loop 内调用，在 navigate 路由的 async 上下文中满足此条件
- 缓存键格式：`temp/{sub_area_id}/background/{time_period}`（与 disk cache key 独立，避免路径混淆）
- `build_scene_change()` 只做同步缓存查询（`_cache_get`），不触发生成，保持 sync 函数语义不变
- `is_dynamic_sub_location` flag 仅在 maps registry 存在且 `current_location_id` 不在静态 `sub_locations` 时为 `True`，避免误判静态子地点
- fallback result（`image_base64=None`）不返回 URL（返回 None），与 AssetResolver 的不缓存 fallback 约定一致

**新增测试（11 个，`tests/test_a10_image_prefetch.py`）：**
- `prefetch_sub_area_backgrounds`：新子地点触发 task / 已缓存跳过 / 空 delta noop / 非 areas slice noop / 无 id 的 sub_area 跳过
- `get_cached_background_url`：命中返回 data URL / 未命中返回 None / fallback 结果返回 None
- `build_scene_change` 集成：动态子地点有缓存 → `background_url` 在 payload / 缓存未命中 → 无 `background_url` / 无 resolver → 无 `background_url`

---

### [D-P29-A2] NPC 任务工具权限修复

**变更文件：**
- `app/game_core/narrative/character_tools.py` — `OfferQuestTool.execute()`：删除 `state.quests` fallback（line 419-423），不在 bulletins 就直接拒绝；`AcceptQuestTool.execute()`：整段替换为 `role_data` 验证（bulletins 检查 + active_quests 去重检查），去除所有 `state.quests` 直接访问

**关键设计决策：**
- `role_data` 是通过 `_extract_receptionist_data()` 注入的规范真相源，`state.quests` 直接访问违反 `RoleStateProxy` 边界
- `OfferQuestTool` 无 role_data 时跳过验证（backward compat for non-receptionist usage）
- `AcceptQuestTool` 无 role_data 或非 receptionist 角色时返回 `missing_role_data` 错误（强制要求角色数据）

**新增测试：**
- `tests/test_p29_a1_a2.py` — `TestOfferQuestTool`（3 个）：bulletin 匹配成功、不在 bulletin 拒绝、无 role_data 跳过验证
- `tests/test_p29_a1_a2.py` — `TestAcceptQuestTool`（6 个）：bulletin 匹配成功、不在 bulletin 拒绝、已 active 拒绝、无 role_data 拒绝、非 receptionist 角色拒绝、缺 quest_id

**更新已有测试：**
- `tests/test_agentic_loop.py` — `test_npc_text_only_response_is_protocol_error` 改名并更新为新行为
- `tests/test_p28_building_tools.py` — `TestAcceptQuestTool` 两个测试更新为新语义
- `tests/test_s504_tool_constraints.py` — `test_offer_quest_dynamic_quest_available_fallback` 改为验证新的拒绝行为

---

### [D-P29-A1] Private Chat NPC 优雅降级

**变更文件：**
- `app/game_core/narrative/executor.py` — `run_agentic()` NPC 路径 `text_without_tool` 分支：改为把纯文本包装成 `ToolResult(ok=True, message=text, metadata={"event_type": "speech", "synthetic": True})`，返回 `finish_reason="text_fallback"` 的 `AgentResult`，而非 `protocol_error`；NPC 空文本仍然保持 `empty_response` 错误；teammate 路径不变

**关键设计决策：**
- 下游 `_extract_visible_reply_text()` 按 `event_type == "speech"` 提取，无需修改 coordinator
- `synthetic: True` 字段标记降级来源，供日志分析区分
- 仅影响 NPC 角色；teammate 仍按原有协议处理（text-only = `protocol_error`）

**新增测试：**
- `tests/test_p29_a1_a2.py` — `test_a1_npc_plain_text_wrapped_as_synthetic_speech`：验证 text_fallback 和 synthetic 标记
- `tests/test_p29_a1_a2.py` — `test_a1_npc_plain_text_extractable_by_visible_reply`：验证下游提取正常
- `tests/test_p29_a1_a2.py` — `test_a1_npc_empty_text_still_returns_protocol_error`：空文本仍报错
- `tests/test_p29_a1_a2.py` — `test_a1_teammate_plain_text_still_protocol_error`：teammate 不受影响

---

### [D-P31 Phase 1] 开场体验修复（问题 6 + 11a）

**背景：** P30 接通 `current_target_milestone` 链路后，live 测试发现开场体验存在两类空白：
1. Bootstrap 种子任务使用英文生成标题（`_display_name()` 产物），且因 `delivery_method` 字段导致任务不可见
2. 开场叙述（LLM 路径和确定性 fallback 路径）均无故事背景，LLM 生成通用叙述而非基于剧情

**变更文件：**

- `app/game_core/runtime.py` — `bootstrap_opening_planner()` 后处理：`hook.bootstrap()` 完成后、`persist` 前，遍历 `state.quests.dynamic_quests`，对有 `metadata.source_milestone` 的任务从 `QuestRegistry.get_milestone()` 读取 `title`/`description`，覆写任务的 `title`/`summary` 字段，并 `pop("delivery_method")` 使任务对玩家可见

- `app/game_core/narrative/context_builder.py` — `build_gm_opening_context()`：读取 `narrative_plan.current_target_milestone`，注入 `opening_milestone`（title/narrative_context/key_elements/involved_npcs）和 `opening_chapter`（title/description）到 `l7_engine_result`；`GM_OPENING_PROMPT`：在 `## Style` 前新增 `## Story context` 块，指示 LLM 利用 `opening_milestone` 和 `opening_chapter` 叙事

- `app/agent_orchestration.py` — `_build_opening_user_message()`：从 `narrative_plan.current_target_milestone` 读取里程碑叙事，构建 `opening_narrative` dict（milestone_title/narrative_context/key_elements/chapter_title/chapter_description），注入 payload

- `app/opening_views.py` — `build_opening_narration()`：在 `lines` 构建后、`return` 前，从 `world.quests` 读取章节描述和里程碑 `narrative_context`，用 `insert(0, ...)` 前置注入，让确定性 fallback 叙述也包含故事背景

**关键设计决策：**
- `delivery_method` 字段被移除：这是种子任务被隐藏的根因，`pop()` 是最简修复
- `world.has_registry("quests")` + `state.has_slice("narrative_plan")` 双重保护：确保无 quests registry 或 narrative_plan slice 时静默跳过
- `opening_narrative` 为 `None` 时原有 payload 结构不变（向后兼容）
- `context_builder.py` 位于 `game_core/`，不得 import 应用层 — 通过 `_world.quests` 访问（WorldInstance 是 game_core 内部模块），完全合规

**无新测试：** Phase 1 改动为数据注入路径，通过 live 验证（新 session 开场包含章节叙事背景、任务面板显示中文标题）。全量测试基线无变化。

**更新已有测试（5 个文件）：**
- `tests/test_agentic_loop.py` — `test_npc_text_only_response_is_protocol_error` → 改名/改义
- `tests/test_npc_interaction.py` — `test_text_only_npc_response_is_reported_as_invalid_agent_response` → 改名/改义
- `tests/test_private_chat.py` — `test_text_only_npc_response_is_invalid_agent_response` → 改名/改义
- `tests/test_agent_orchestration.py` — 3 个相关测试改为验证新的成功行为

---

### [D-P25-5b] Phase 5b：任务目标追踪（P25-14 Phase 2）

**变更文件：**
- `app/game_core/rules/handlers/planner.py` — `_compute_create_quest()` objectives 处理：从逐个 `if isinstance` 改为 normalize 循环，string → `{"description": s, "completed": False}`，dict → 保留全部原有 key（向后兼容 type/target 等遗留字段）+ 确保 `description`/`completed` 存在 + 验证并保留合法 `condition` 字段（需含 `type` 键），去除无效 condition
- `app/game_core/orchestration/hooks/quest_objective_tracking.py` — **新文件**：`QuestObjectiveTrackingHook`（priority=56），遍历 active 动态任务，对有 `condition` 字段的 objective 调用 `BasicEventConditionEvaluator._condition_met()`，满足时 in-place 标记 `completed=True`，全部完成时按 `requires_report` 推进 status 为 `ready_to_report` 或 `completed`
- `app/game_core/orchestration/hooks/__init__.py` — 导出 `QuestObjectiveTrackingHook`
- `app/game_core/orchestration/defaults.py` — 导入并注册到 `DEFAULT_SETTLEMENT_HOOK_TYPES`（位于 MilestoneUnlockHook 之后）

**关键设计决策：**
- objectives normalize 保留原有 key（向后兼容 `_build_objective_event_changes` 的 type/target 读取逻辑）
- `condition` 字段独立于 objective 的遗留 `type` 字段，两者语义不同：遗留 `type` 用于 EventEngine 自动绑定，`condition` 用于 QuestObjectiveTrackingHook 直接评估
- Hook 直接操作 `context.state.quests.dynamic_quests` dict（受控例外，等同 NarrativePlanSlice 直接写入），设置 `_dirty = True` 确保持久化

**新增测试：**
- `tests/test_phase5_quest_tracking.py` — `TestObjectiveConditionSchema`（3 个）：condition 保留、无效 condition 剔除、string objective 规范化
- `tests/test_phase5_quest_tracking.py` — `TestQuestObjectiveTrackingHook`（8 个）：满足条件自动完成、未满足不变、无 condition 不触发、全完成推进 completed/ready_to_report、混合追踪、跳过非 active 任务、无 quests slice noop

### [D-P25-5a] Phase 5a：面板 + Skills（P25-12 + P25-11）

**变更文件：**
- `app/routers/panels.py` — `_quest_response()` 重构：不再构建 `enriched_milestones`，`milestone_states={}` 不暴露给前端（P25-12）；filter `raw_dynamic` 去除 status 为 `retired`/`expired` 的任务，只传 `visible_quests` 给 `normalize_dynamic_quest_panel()`
- `app/quest_views.py` — `normalize_dynamic_quest_view()` 新增 `completion_hint` 字段：status=active 且所有 objectives 的 `completed=True` 时，`requires_report=True` → "所有目标已完成，请返回汇报"，否则 → "所有目标已完成"
- `app/narrators.py` — `_SYSTEM_PROMPT` 在"## 设计模板工具"段落之后追加"## 设计模板使用规则"（强制 create_quest/plant_encounter 前必须 read_design_skill）；5 个 subsystem prompt 中的 `（可选）可通过...` 改为 `创建新内容前，建议先通过 list_design_skills / read_design_skill 查阅...`

**关键设计决策：**
- `milestone_states` 隐藏是 frontend-layer 决定：内部状态不应直接暴露给 UI，frontend 只需要 dynamic_quests 视图
- `completion_hint` 是只读派生字段（display-only），不持久化，仅在 quest_views 中计算
- Skills prompt 的 `强制要求` 措辞限定于 create_quest 和 plant_encounter（内容生成密集型指令）；其余 directive 类型仅"建议"

**新增测试：**
- `tests/test_phase5_quest_tracking.py` — `TestQuestPanelFiltering`（2 个）：milestone_states 为空、retired/expired 过滤
- `tests/test_phase5_quest_tracking.py` — `TestCompletionHint`（5 个）：全完成 hint、requires_report hint、部分完成无 hint、非 active 无 hint、无 objectives 无 hint
- `tests/test_phase5_quest_tracking.py` — `TestSkillsPromptMandatory`（2 个）：prompt 含强制规则、subsystem prompt 已更新

### [D-P25-4] Phase 4：知识积累链路（P25-17-1 + P25-05 + P25-15-1）

**变更文件：**
- `app/game_core/narrative/context_window.py` — 删除 deprecated `pop_oldest_for_graphize()` 方法；`export_messages()` 返回 `dict[str, Any]`（`{"messages": [...], "graphize_counter": N}`，原返回 list）；`import_messages()` 接受 `dict | list` — dict 还原 graphize_counter，list（旧格式）fallback 为 0
- `app/game_core/orchestration/npc_interaction.py` — `collect_for_graphize()` 替换 `pop_oldest_for_graphize()`（line 476）
- `app/game_core/orchestration/private_chat.py` — 同上（line 263）
- `app/game_core/narrative/instance_manager.py` — `collect_for_graphize()` 替换 `pop_oldest_for_graphize(fraction=1.0)`（line 237）
- `app/game_core/narrative/context_builder.py` — `build_npc_context()` 改为 `await self._build_l6(npc_id, memory_retriever, "npc")`（不再返回 stub recall_tool）；`build_teammate_context()` 同理；`_build_l6()` 和 `_extract_scene_keywords()` 加 `has_slice("player")` 守卫避免无 player slice 时异常
- `app/narrators.py` — `export_history()` 返回类型改为 `dict[str, Any]`；两处 `import_history()` 接受 `dict | list`，dict 直接传 `import_messages()`，list 走原有 legacy 检测路径
- `app/game_core/runtime.py` — `_restore_context_windows()` 中 `isinstance(messages, list)` 改为 `isinstance(messages, (list, dict))`；GM/planner history 恢复也同步改为 `isinstance(..., (list, dict))`

**关键设计决策：**
- `export_messages()` 改为 dict 是破坏性变更，需要同步更新所有 callers 和 tests
- `import_messages()` 实现向后兼容（接受旧 list 格式），方便存量数据迁移
- L6 注入（4-3）使 NPC/Teammate context 从 `{"hits": [], "source": "recall_tool"}` stub 变为实际 retriever 调用；retriever=None 时返回 `{"hits": [], "source": "null"}`
- `_build_l6()` 和 `_extract_scene_keywords()` 加 player slice 守卫，兼容无 player slice 的测试 state

**新增/更新测试：**
- `tests/test_context_window.py` — 删除 `TestPopOldestForGraphize`（5 个），新增 `TestCollectForGraphize`（5 个）
- `tests/test_p19_phases_bcde.py` — 更新 `test_context_window_export_import_messages`（新 dict 格式），新增 `test_context_window_graphize_counter_persisted`（counter 持久化），新增 `test_context_window_legacy_list_import`（旧 list 格式向后兼容），更新 `test_planner_history_export_import`（新 dict 格式）
- `tests/test_context_builder.py` — 更新 l6 source 断言（`"recall_tool"` → `"null"`），更新两个 `test_no_double_retriever_call` → `test_retriever_called_once_in_full_context`（call_count 0 → 1）
- `tests/test_s107_gm_graphize.py` — 更新 `test_gm_export_returns_empty_when_no_history`（新 dict 格式）
- `tests/test_s107_planner_graphize.py` — 更新 `test_export_format_has_content_key`（新 dict 格式）

### [D-P25-3b] Phase 3b：体验质量 — Osiris 局部性 + 检定约束（P25-08/P25-06）

**变更文件：**
- `app/evaluators.py` — `OSIRIS_SYSTEM_PROMPT` 在 `## Constraints` 之后追加 `## Locality constraints` 段落，明确 modify_disposition 只能目标在场 NPC
- `app/game_core/orchestration/hooks/ai_osiris.py` — `_build_nearby_npcs()`：当 `normalized_location is not None` 时，只返回同 sub_location 的 NPC（`area_local` + `static_local`），不返回 `area_other` + `static_other`；`execute()`：提取 `present_ids`，在 validation loop 中对 `modify_disposition` 执行局部性检查
- `app/game_core/orchestration/npc_interaction.py` — 新增 `_format_check_constraint()` 方法（30 行），根据检定结果生成中文约束文本（成功/失败 × persuasion/intimidation/deception + fallback）；`execute_interaction()` 在 `_write_skill_check_observation()` 之后注入约束到 `system_prompt`

**关键设计决策：**
- 局部性检查在 `execute()` 而非 `_passes_minimal_semantic_validation`（classmethod 无法访问 context）
- `present_ids` 从 `snapshot["scene_presence"]["present_character_ids"]` 获取（snapshot 已在 execute() 最开始构建）
- 局部性只在 `present_ids` 非空时生效，空列表时跳过（避免误屏蔽无场景数据的情况）
- `_format_check_constraint()` 返回 str，直接追加到 system_prompt；check_result=None 时不追加
- 更新 `tests/test_ai_osiris_hook.py` 中 5 个测试以匹配新的 sub_location 过滤行为
- 新增 `tests/test_p25_phase3b.py`（22 个测试）

### [D-P25-3a] Phase 3a：里程碑生命周期 + 进程引导（P25-14）

**变更文件：**
- `app/game_core/rules/handlers/navigation.py` — `_compute_move_area()` 追加 `visited_area_{area_id}` FlagSlice StateChange（路径格式 `flags.visited_area_X`，只在 flags slice 存在时写入）
- `app/game_core/orchestration/event_engine.py` — `_check_location_entered()` 优先检查持久 flag `visited_area_{area_id}`，fallback 到实时位置
- `app/game_core/orchestration/hooks/milestone_completion.py` — 新文件：`MilestoneCompletionHook`（priority=54），扫描 ACTIVE 里程碑，条件满足时通过 advance_quest 命令自动 COMPLETED
- `app/game_core/orchestration/hooks/__init__.py` + `defaults.py` — 注册 MilestoneCompletionHook 到 DEFAULT_SETTLEMENT_HOOK_TYPES
- `app/narrators.py` — `_SYSTEM_PROMPT` 追加"## 进程引导原则"段落

**关键设计决策：**
- `MilestoneCondition` 是 dataclass 而非 dict，`_condition_to_dict()` 转换为 `{"type": .type, "params": .params}` 供 EventEngine 评估
- FlagSlice.apply_state_change 要求路径 `flags.key_name`，不是 `key_name`
- navigation handler 加条件判断 `has_slice("flags")`，避免老测试中无 flags slice 时 KeyError
- MilestoneCompletionHook priority=54 < MilestoneUnlockHook priority=55，确保同一 tick 内完成→解锁级联

### [D-N01] AgentContext 与 SettlementContext 分离

`AgentContext`（叙事层）面向 Agent 工具，包含 role + character 视角。
`SettlementContext`（编排层）面向 SettlementHook，包含 rules engine 入口。
两者不互相引用，通过 SharedContext 在 Pipeline 中桥接。

### [D-N02] RoleToolRegistry 按 allowed_roles 注册

工具通过 `tool.allowed_roles` 声明可用角色列表，Registry 自动分桶。
`get_tools_for(role, traits)` 的 `traits` 参数预留但当前未使用。

### [D-N03] PlanningDirective 9 种类型

```
CreateQuestPlan      — 创建动态任务
SpawnQuestNpcPlan    — 生成任务 NPC
DirectNpcPlan        — 向 NPC 下达指令
PlantEnvironmentalPlan — 植入环境线索
PublishBulletinPlan  — 发布公告板
EscalatePlan         — 升级紧张度
AdjustPacingPlan     — 调整节奏
RetireQuestPlan      — 退休任务
FillAreaPlan         — 填充区域内容
```

### [D-N04] Hook 已稳定，但 provider 仍保持骨架

本轮把两层编排边界补到了“稳定扩展点”：

- `NarrativePlannerHook` 已具备稳定 planner 输入、bookkeeping 和最小 directive 执行子集
- `GmNarrationHook` 已具备稳定 narrator 输入、entry 标准化和 SceneBus 写入路径

但以下底层提供者仍保持骨架：

- `NarrativePlanner`：默认仍返回空列表
- `AgenticExecutor`：默认仍只是工具执行循环骨架

这意味着后续深化应优先替换 provider 实现，而不是再重做 hook 外层契约。

### [D-N05] 默认 provider 已进入最小真实实现，边界仍保持可注入

在 Hook 边界稳定之后，本轮继续把默认 provider 从“空骨架”推进到“最小真实”：

- `NarrativePlanner` 不再返回空列表，而是采用确定性规则梯子：
  - 首个可用里程碑的最小任务播种
  - 里程碑停滞时的加压与冻结
  - 进度恢复后的节奏解冻
- `GmNarrationHook` 默认 narrator 不再是空实现，而是模板化 narrator

但这些 provider 仍然保持“可注入替换”的边界：

- 可以继续显式注入自定义 planner / narrator
- `NullGmNarrator` 仍保留，用于明确的静默 no-op 场景

因此当前真正还未完成的，不是默认 provider 本身，而是：

- 更复杂的 planner 语义
- 具体 AgentTool 集合
- LLM 驱动的 agentic / narration 能力

### [D-N06] NarrativePlannerHook 全 9 指令闭合

**日期**：2026-02-28

原先 6/9 指令已实现（create_quest/direct_npc/publish_bulletin/escalate/adjust_pacing/retire_quest），3 个标记为 `_UNSUPPORTED_DIRECTIVES`。

**本轮补齐**：
- **spawn_quest_npc**：`move_npc` 注册位置 + `add_directive` 存储元数据（融入 NpcScheduleHook/InteractionService 的 npc_locations 消费链）。拒绝已存在的 npc_id。
- **plant_environmental**：写入 `area.properties.search_targets`（与 investigate handler 无缝集成，D-R26）。拒绝已存在的 clue_id。dc 防御非法值。
- **fill_area**：`add_temporary_sub_area`（与 DynamicSubAreaManager 共享底层）。容量检查 `has_cluster_capacity`。expiry=-1（permanent）。

**模式对齐**：
- 不记 history（仅 quest 生命周期事件记）
- 参数验证用 `_coerce_non_empty_string`
- 唯一性检查（spawn/plant 拒绝重复，fill 检查容量）
- `_UNSUPPORTED_DIRECTIVES` 清空

**测试**：4 新测试 + 1 现有测试更新

### [D-N07] NarrativePlanner L0-L5 升级阶梯 + 区域填充

**日期**：2026-02-28

原先 NarrativePlanner 只有 3 条规则（任务播种 / stalled≥6→escalate+freeze / thaw）。

**本轮深化**：

- **plan() 重构为优先级链**：seed → thaw → escalation → area_fill → noop（5 个 `_try_*` 方法）
- **L0-L5 升级阶梯**（替代旧的 stalled≥6 平坦检查）：
  - L0 (0-3 ticks): 不干预
  - L1 (4-6): publish_bulletin 暗示 + escalate(+1)
  - L2 (7-9): direct_npc 推荐 + escalate(+1)
  - L3 (10-12): create_quest(如需) + direct_npc + escalate(+1)
  - L4 (13-15): escalate(+1) + adjust_pacing(freeze) + plant_environmental(如有区域)
  - L5 (16+): escalate(+2) 最后警告
- **_pick_target_milestone**：优先 active > available，无 milestone 时阶梯不激活
- **区域填充**：area has_capacity + total_dynamic < 2 → fill_area 指令
- **Hook 扩展**：`_build_planner_context` 新增 `area_cluster` 字段（area_id + has_capacity + total_dynamic）

**行为变更**：
- stalled≥6 但无 milestone → 不再 escalate+freeze，改为 noop（无可引导目标）
- 升级阶梯需要 target_level > current_level 才触发（防止重复相同等级）

**测试**：5 新测试（L2/L3/L4/noop-without-milestone/area-fill）+ 2 现有测试更新

### [D-N08] GM AgentTool 5 个工具实现

**日期**：2026-02-28

**新建文件**：`app/game_core/narrative/gm_tools.py`

**设计约束**：GM 是零写入角色——不构造 Command、不写 SceneBus、不修改任何持久状态。

**5 个工具**：

| 工具 | 功能 | 输出 |
|------|------|------|
| `describe_environment` | 读取 ❶+❸ 组装环境参考材料 | reference dict（area/map/npcs/time） |
| `narrate` | 透传客观环境叙述文本 | event_type=gm_narration |
| `comment` | 透传毒舌旁白文本 | event_type=gm_comment |
| `pass_turn` | 空操作（本轮不叙述） | event_type=pass |
| `suggest_options` | 验证并结构化玩家选项 | event_type=dialogue_options + options list |

**模式**：
- `_GmTool` 基类预填 `allowed_roles=["gm"]`
- `register_gm_tools(registry)` 一次注册全部 5 个
- `suggest_options` 只验证 intent（check.skill / action），不计算 DC
- `describe_environment` 防御性检查 has_slice + area 存在性

**测试**：9 个新测试 in `tests/test_gm_tools.py`

### [D-N09] NPC + Teammate AgentTool 12 个工具实现

**日期**：2026-02-28

**新建文件**：`app/game_core/narrative/character_tools.py`

**设计约束**：NPC/Teammate 只能修改自身相关状态。SceneBus 写入属于受控例外 A（格级缓冲）。Command 构造走 `context.run_command()` → RulesEngine。`character_id` 从 `context.metadata["character_id"]` 获取。

**三类交互模式**：

| 模式 | 工具 | 机制 |
|------|------|------|
| SceneBus 写入 | speak / emote / refuse | 直接 `scene.add_entry(SceneEntry(...))` |
| Command 构造 | update_feeling / remember / offer_quest / reveal_secret / express_opinion | `Command(source="ai_osiris")` → `run_command()` |
| 只读/纯叙述 | offer_trade / suggest_tactic / share_memory / request_action | 读取 state 或透传文本 |

**共享工具**：speak + emote 的 `allowed_roles=["npc", "teammate"]`，自动注册到两个角色桶。

**特殊逻辑**：
- `update_feeling`：维度限 approval/trust/fear/romance，delta 限 [-50,50]
- `reveal_secret`：信任检查 `npc_dispositions[character_id].trust >= trust_required`，通过后 SceneBus + add_knowledge 双写
- `offer_quest`：to_state 默认 "AVAILABLE"，handler 负责验证状态转换
- `share_memory`：读取 `npc_impressions[character_id]` 全量返回

**测试**：17 个新测试 in `tests/test_character_tools.py`

### [D-N10] AgenticExecutor LLM 集成（Gemini Function Calling）

**日期**：2026-02-28

**架构设计**：

```
app/llm_gemini.py (imports google.genai)
        ↓ implements
app/game_core/adapters/llm.py (LlmPort Protocol, pure Python)
        ↓ injected into
app/game_core/narrative/executor.py (AgenticExecutor.run_agentic)
```

**新增组件**：

| 组件 | 位置 | 说明 |
|------|------|------|
| `LlmResponse` | adapters/llm.py | SDK-agnostic 响应（text + tool_calls + finish_reason） |
| `LlmPort` Protocol | adapters/llm.py | `generate(system_prompt, history, tool_declarations) -> LlmResponse` |
| `NullLlmProvider` | adapters/llm.py | 安全默认：返回空 LlmResponse |
| `AgentResult` | narrative/models.py | 多轮循环结果（text + tool_results + turns_used） |
| `run_agentic()` | narrative/executor.py | 多轮 agentic 循环方法 |
| `GeminiLlmAdapter` | app/llm_gemini.py | 具体 Gemini 实现（应用层，不在 game_core） |

**隔离约束**：
- `game_core/` 不 import `google.genai` — LlmPort Protocol 纯 Python
- `GeminiLlmAdapter` 在 `app/` 层，通过 Protocol 注入
- `NullLlmProvider` 在 `game_core/` 层作安全默认

**agentic 循环**：
1. 构建 tool declarations + 初始 history
2. LLM generate → 解析 tool_calls
3. 无 tool_calls → 返回 AgentResult（text + completed）
4. 有 tool_calls → 执行工具（复用 `run()` 单轮方法）→ 追加 history → 重复
5. 达到 max_turns → 返回 AgentResult（max_turns_reached）

**history 抽象格式**（不依赖 Gemini SDK types）：
```python
[
    {"role": "user",  "parts": [{"text": "..."}]},
    {"role": "model", "parts": [{"function_call": {"name": "...", "args": {}}}]},
    {"role": "user",  "parts": [{"function_response": {"name": "...", "response": {}}}]},
]
```

**向后兼容**：现有 `run()` 方法不变（单轮模式），`run_agentic()` 是新增方法。

**测试**：8 个新测试 in `tests/test_agentic_loop.py`（使用 RecordingLlmProvider mock）

### [D-N11] Settlement GM 叙述接通 LLM（Phase 1）

**日期**：2026-03-01

**目标**：格结算时 GM Agent 用 LLM（gemini-3-flash-preview）生成叙述，替代模板文本。

**改动清单**：

1. **GmNarrator Protocol 改 async**（`hooks/gm_narration.py`）
   - `GmNarrator.compose()` / `NullGmNarrator.compose()` / `TemplateGmNarrator.compose()` → `async def`
   - `GmNarrationHook.execute()` 中 `await self._narrator.compose(...)`

2. **AgenticGmNarrator**（**新建** `app/narrators.py`）
   - 实现 `GmNarrator` Protocol，包装 `AgenticExecutor.run_agentic(role="gm")`
   - 持有 session 级 world/state 引用（和 TickCoordinator 同生命周期）
   - `_agent_result_to_decision()` 提取 narrate/comment 工具输出 → `GmNarrationDecision.entries`
   - 含 `GM_SETTLEMENT_PROMPT` 系统指令（毒舌旁白人格 §2.3 + 格结算场景）

3. **注入连线**
   - `bootstrap.py`：`build_runtime_for_world()` / `build_restored_runtime_for_world()` 新增 `gm_narrator_factory` 参数（`Callable[[WorldInstance, StateContainer], GmNarrator]`）。factory 在 state 创建后调用，解决 narrator 需要 state 引用的时序问题。利用 `register_default_settlement_hooks` 的 name 去重机制跳过默认 TemplateGmNarrator
   - `runtime.py`：`GameRuntime.__init__` 新增 `llm_provider: LlmPort | None`。`_gm_narrator_factory()` 方法构建闭包，内部延迟导入 `app.narrators.AgenticGmNarrator` + 工具注册
   - `session_store.py`：`load_runtime_for_world()` 透传 `gm_narrator_factory` 到 bootstrap

**测试**：450 passed（基线不变 +0），已有测试中 mock narrator 改 async 兼容

### [D-N12] NPC 对话 + GM/Teammate 即时反应接通 LLM（Phase 2+3）

**日期**：2026-03-01

**目标**：NPC 被对话时用 LLM 生成回应；玩家每次动作后 GM 可选即时叙述/点评、队友可选发言。

**架构决策**：
- Agent 编排在应用层（`app/agent_orchestration.py`），不下沉到 game_core
- 单一 `AgentOrchestrationService` 复用一个 `AgenticExecutor`（19 工具全注册，按 role 自动筛选）
- NPC 工具的 Command 执行通过 `AgentContext.execute_command` 回调 → `rules_engine.execute()` → `state.apply(delta)`
- 玩家消息写入 SceneSlice（`source="player"`），NPC 工具已有 SceneSlice 写入逻辑
- `deps.py` 检测 `GOOGLE_API_KEY` / `GEMINI_API_KEY` 环境变量自动启用 LLM

**改动清单**：

1. **AgentOrchestrationService**（**新建** `app/agent_orchestration.py`）
   - `generate_npc_response(session, npc_id, player_message)` → NPC 对话 SSE 事件
   - `generate_post_action_reactions(session, result)` → GM + Teammate 反应 SSE 事件
   - 3 套 System Prompt：NPC（动态，含个性/好感/记忆）、GM 即时反应、Teammate（动态，含个性/审批值）
   - `_make_command_executor(session)` 回调：NPC 工具 Command → rules_engine → state.apply
   - SSE 转换器：`_npc_result_to_sse` / `_gm_result_to_sse` / `_teammate_result_to_sse`

2. **GameRuntime 暴露编排服务**（`runtime.py`）
   - `agent_orchestration` property（lazy，有 LLM 时创建 singleton）
   - `_build_agent_orchestration()` 注册全部 19 工具 + 创建 AgenticExecutor

3. **deps.py LLM 自动启用**
   - `_build_game_runtime()` 检测环境变量 → 创建 `GeminiLlmAdapter` → 注入 `GameRuntime`
   - `get_agent_orchestration()` helper

4. **InteractRequest 增 message 字段**（`api_models.py`）
   - `message: str | None = None` — 玩家对话文本

5. **Router 集成**（`routers/gameplay.py`）
   - `interact_stream`：InteractionService 之后，若有 NPC + message → 调 `generate_npc_response()`
   - `action_stream` / `input_stream`：PipelineResult 之后 → 调 `generate_post_action_reactions()`
   - 无 LLM 时优雅降级（`get_agent_orchestration()` 返回 None，跳过）

**SSE 事件映射**：
- NPC speak → `npc_response`（含 npc_id + content）
- NPC emote → `npc_emote`（含 npc_id + action）
- NPC refuse → `npc_response`（type="refuse"）
- GM narrate → `gm_narration`（含 content）
- GM comment → `gm_comment`（含 content）
- Teammate speak/emote → `teammate_response`（含 character_id + content/action）

**测试**：468 passed（+18），18 个新测试覆盖 prompt 构建、SSE 转换、Command 执行、集成流程、优雅降级

## 填充 TODO

- [x] `AgenticExecutor`：接入 LLM agentic 循环 — D-N10 完成
- [ ] `RoleToolRegistry`：traits 过滤逻辑
- [x] GM 工具 5 个 — D-N08 完成
- [x] NPC 工具 8 个 — D-N09 完成
- [x] Teammate 工具 6 个 — D-N09 完成
- [x] `NarrativePlanner`：更丰富的规划策略 — D-N07 完成
- [x] Settlement GM 叙述接通 LLM — D-N11 完成
- [x] NPC 交互对话接通 LLM（Phase 2）— D-N12 完成
- [x] 格内动作 GM + Teammate 反应接通 LLM（Phase 3）— D-N12 完成
- [x] AgentContextBuilder 集中化（7 层上下文 + 角色可见性矩阵）— D-N13 完成
- [ ] D-N13 收尾：将 7 层上下文真正注入 `AgenticExecutor` 的初始输入（当前主要用于测试与 prompt 局部取值）
- [ ] D-N13 收尾：为工具执行补角色级只读隔离（当前 `AgentContext` 仍携带完整 `world + state`）
- [ ] D-N13 收尾：补齐与 `ContextAssembler` 的 L2/L3 字段对齐（如动态子区域统计/列表）
- [ ] PrivateChatCoordinator（私聊管线）
- [ ] 对话选项生成（Agent 意图 + ❷ 填充 DC）
- [ ] MemoryGraph / ContextWindow 记忆系统接入
- [ ] InstanceManager NPC 实例池
- [x] 完整 7 层 AgentContextBuilder — D-N13 完成

---

### [D-N13] AgentContextBuilder 集中化（N-3，对齐叙事层设计规范 §3.1-3.3）

**问题**：LLM Agent 上下文组装散落在 `agent_orchestration.py` 的 10 个模块级函数中，无层级结构，无角色可见性过滤。`ContextAssembler`（编排层）已有 8 层 pipeline 上下文，但绑定 `SharedContext`，Agent 编排在 pipeline 外调用无法复用。

**决策**：
1. 新建 `app/game_core/narrative/context_builder.py`，实现设计文档 §3.1-3.3 的完整 `AgentContextBuilder`
2. 7 层 dict 输出（L0-L7）+ 角色可见性矩阵（GM/NPC/Teammate 各自过滤范围）
3. `agent_orchestration.py` 删除 10 个散落函数 + 2 个 prompt 常量（557→~200 行），改用 builder
4. 顺手修复 `_generate_teammate_reactions` 中 `isinstance(members, list)` 的 bug（members 实际是 dict）

**L4 角色差异化**：
- GM：全量 state snapshots（time/player/relations/flags/party）
- NPC：仅自身 disposition + stage + impressions（3 字段）
- Teammate：自身 disposition + party_members + companion_approval + time

**L5 可见性过滤**：
- GM：排除 `visibility="system"` 的条目
- NPC/Teammate：排除 system + 过滤 private 条目（按 `audience_token = f"{role}:{char_id}"`）

**L6/L7**：L6 stub（预留 MemoryGraph 参数），L7 仅 GM 接收 hints 参数。

**不重构 ContextAssembler**：两者服务不同消费场景（pipeline vs agent），避免跨层强耦合。

**文件变更**：
- `app/game_core/narrative/context_builder.py`（新建，~410 行）
- `app/agent_orchestration.py`（重构，~557→~200 行）
- `app/game_core/narrative/__init__.py`（添加 AgentContextBuilder 导出）
- `tests/test_context_builder.py`（新建，~190 行，26 个新测试）
- `tests/test_agent_orchestration.py`（更新 import）

**测试基线**：541 passed（+26 新测试，零回归）

---

### [D-N14] N-1 Phase 1：MemoryRetriever Protocol + ContextWindow + L6 改造（2026-03-01）

**问题**：`AgentContextBuilder._build_l6()` 硬编码返回 `{"hits": [], "source": "stub"}`，没有注入边界，不可替换。

**改动**：

#### 新建 `app/game_core/narrative/memory_retriever.py`
- `MemoryRetriever` Protocol（`@runtime_checkable`）：`async def retrieve(actor_id, keywords, context) -> {"hits", "source"}`
- `NullMemoryRetriever`：安全默认，无 IO，返回 `{"hits": [], "source": "null"}`

#### 新建 `app/game_core/narrative/context_window.py`
- `WindowMessage(slots=True)`：role / content / token_count / metadata / is_graphized
- `ContextWindow(slots=True)`：actor_id + max_tokens(200K) + overflow_threshold(0.9)
  - `add_message()` → 返回 `should_graphize`
  - `pop_oldest_for_graphize(fraction=1/3)` → 弹出并标记 is_graphized=True，供 Phase 2-3 MemoryGraphizer
  - `snapshot()` → JSON-serializable 快照（Phase 2 持久化钩子）

#### 修改 `app/game_core/narrative/context_builder.py`
- 新增 import：`MemoryRetriever`
- `_build_l6(memory)` → `async def _build_l6(self, actor_id, retriever)` — 去掉 @staticmethod
- 5 个公开方法改为 async，参数 `memory: Any = None` → `memory_retriever: MemoryRetriever | None = None`：
  - `build_npc_context`, `build_teammate_context`
  - `build_npc_system_prompt`, `build_teammate_system_prompt`
  - `build_teammate_interaction_prompt`

#### 修改调用方（加 await）
- `app/game_core/orchestration/npc_interaction.py`：2 处（build_npc_system_prompt + build_teammate_interaction_prompt）
- `app/agent_orchestration.py`：2 处（build_npc_system_prompt + build_teammate_system_prompt）

#### 更新 `app/game_core/narrative/__init__.py`
- 新增导出：`ContextWindow`, `MemoryRetriever`, `NullMemoryRetriever`, `WindowMessage`

**设计偏离**：无。Phase 1 严格对齐设计规范 L6 注入边界定义。

**测试**：
- `tests/test_context_window.py`（新建，15 个测试）
- `tests/test_memory_retriever.py`（新建，5 个测试）
- `tests/test_context_builder.py`（更新 13 个方法：asyncio.run + L6 source "stub"→"null"）

**测试基线**：595 passed（+24，零回归）

---

### [D-N15] N-1 Phase 2：WorldKnowledgeGraph + 扩散激活检索（2026-03-01）

**问题**：Phase 1 的 `_build_l6` 传入空 keywords/context，`KnowledgeGraphMemoryRetriever` 尚未实现，
L6 实际上仍为空命中。Phase 2 目标：实现静态世界知识图谱 + BFS 扩散激活检索，接通完整 L6 数据流。

**改动**：

#### 新建 `app/game_core/adapters/memory_graph_port.py`
- `MemoryGraphPort` Protocol（`@runtime_checkable`）：`async def query_spread(actor_id, keywords, context, *, max_depth, decay, top_k) -> list[dict]`
- `NullMemoryGraphPort`：安全默认，无 IO，返回 `[]`
- 导出到 `app/game_core/adapters/__init__.py`

#### 新建 `app/world_knowledge_graph.py`
- `WorldKnowledgeGraph`（实现 `MemoryGraphPort`）：NetworkX DiGraph
- 节点类型：character / faction / area / location / item / monster / skill / milestone
- 边类型（`EdgeType`）：located_in / belongs_to / has_class / carries / sells / faction_relation / drops / adjacent_to / contains / requires / leads_to
- `ensure_seeded(world)`：按 world_id 懒惰初始化，幂等
- `_seed_*` 7 个子方法：从各 Registry 提取节点和边
- `_find_seed_nodes(keywords)`：大小写不敏感匹配 label/tags/node_id
- `_spread_activation(seeds, max_depth, decay)`：BFS on undirected view，边权重参与衰减
- `query_spread`：seed → spread → top_k hits（排除 seed 节点自身）

#### 新建 `app/memory_retriever_impl.py`
- `KnowledgeGraphMemoryRetriever`：`__init__(graph: MemoryGraphPort)`
- `retrieve(actor_id, keywords, context)` → 透传到 `graph.query_spread` → 包装为 `{"hits", "source": "knowledge_graph"}`

#### 修改 `app/game_core/narrative/context_builder.py`
- `_build_l6`：补充 `_extract_scene_keywords(actor_id)` 调用 + `context={"world": self._world, "current_area": ...}`
- 新增 `_extract_scene_keywords(actor_id)`：从最近 5 条可见 scene 条目分词，去重，上限 20 个关键词

#### 修改注入链路
- `npc_interaction.py`：`NpcInteractionCoordinator` 添加 `memory_retriever: MemoryRetriever | None = None` 构造参数，传入 `build_npc_system_prompt`
- `runtime.py`：`GameRuntime.__init__` 添加 `memory_retriever` 参数；`_build_agent_orchestration` 传递给 `AgentOrchestrationService`
- `agent_orchestration.py`：`AgentOrchestrationService.__init__` 添加 `memory_retriever` 参数；两处调用（`build_npc_system_prompt` + `build_teammate_system_prompt`）补参；`run_npc_interaction` 传递给 `NpcInteractionCoordinator`
- `deps.py`：`_build_game_runtime` 实例化 `WorldKnowledgeGraph + KnowledgeGraphMemoryRetriever`，注入到 `GameRuntime`

**设计偏离**：无。Phase 2 严格对齐计划：静态图 + 扩散激活，动态边（disposition/事件）留 Phase 3。

**架构决策**：
- BFS 在无向视图（`to_undirected()`）上传播，激活双向流动（找 merchant_tom → 也能激活其所在区域）
- `context["world"]` 传入 retriever 用于懒惰 seeding，无需引入全局 WorldInstance 引用
- `write_episode`（ContextWindow 溢出图谱化）有意推迟至 Phase 3
- `NullMemoryGraphPort` + `NullMemoryRetriever` 保持分层安全默认

**测试**：
- `tests/test_world_knowledge_graph.py`（新建，28 个测试）：节点/边构建、BFS 激活、decay 数值、top_k、幂等性
- `tests/test_memory_retriever_impl.py`（新建，11 个测试）：Protocol 满足、空关键词短路、hit 格式透传

**测试基线**：634 passed（+39，零回归）

**验收备注（2026-03-01）**：

---

## [D-N16] N-1 Phase 3a：InstanceManager + LRU 实例池（2026-03-01）

**问题**：NPC 完全无实例——每次交互创建临时 context，调用完即销毁，对话历史不跨轮次保留。
ContextWindow 数据结构（Phase 1）从未被任何调用方使用。

**目标**：
1. InstanceManager — per-NPC ContextWindow 的 LRU 实例池（最多 200 个 NPC）
2. 对话历史注入 run_agentic() — NPC 能"记得"与同一玩家的历史对话
3. 溢出检测并打通 write_episode 接口 — 溢出时弹出旧消息，调用图谱化方法（stub）

#### 新建 `app/game_core/narrative/instance_manager.py`
- `InstanceManager`：`OrderedDict` + LRU 淘汰策略
- `get_or_create(actor_id)` → 返回/创建 ContextWindow，访问即促进到 MRU
- `get(actor_id)` → 返回现有实例或 None（不创建）
- `contains(actor_id)` / `instance_count()` 查询方法
- 构造参数：`max_instances=200`、`max_tokens_per_instance=200_000`、`overflow_threshold=0.9`

#### 修改 `app/game_core/narrative/executor.py`
- `run_agentic()` 新增 `conversation_history: list[dict] | None = None` 参数
- 若提供，用已有历史 + 追加 user_message；否则行为与原完全一致（零回归）

#### 修改 `app/game_core/adapters/memory_graph_port.py`
- `MemoryGraphPort` Protocol 新增 `write_episode(actor_id, messages, context)` 方法
- `NullMemoryGraphPort` 同步添加 stub（返回 None）

#### 修改 `app/world_knowledge_graph.py`
- `WorldKnowledgeGraph.write_episode()` stub：Phase 3a no-op，Phase 3b 填充 LLM 三元组提取

#### 修改 `app/game_core/orchestration/npc_interaction.py`
- `NpcInteractionResult` 新增 `graphize_candidates: list[WindowMessage]` 字段
- `execute_interaction()` 新增 `context_window: ContextWindow | None = None` 参数
- Step 2 前调用 `_window_to_history(context_window)` 构建历史
- Step 2 后更新 ContextWindow，检测溢出 → 填入 `graphize_candidates`
- 新增纯函数：`_window_to_history()`、`_approx_tokens()`

#### 修改注入链路
- `agent_orchestration.py`：`AgentOrchestrationService` 添加 `instance_manager` 参数；
  `run_npc_interaction()` 和 `generate_npc_response()` 均接入 InstanceManager；
  溢出时调用 `graph.write_episode()`（通过 getattr 访问 _graph，Phase 3b 改为正式接口）
- `runtime.py`：`GameRuntime.__init__` 添加 `instance_manager: Any = None` 参数，传递给 `_build_agent_orchestration`
- `deps.py`：`_build_game_runtime` 实例化 `InstanceManager()`，注入 `GameRuntime`

**设计偏离**：无。Phase 3a 严格对齐计划：InstanceManager + LRU + write_episode stub。
write_episode 实现（LLM 三元组提取）留 Phase 3b。

**架构决策**：
- InstanceManager 在 game_core/narrative/ 层，零外部依赖（只依赖 ContextWindow）
- 历史转换（WindowMessage → Gemini format）分别在 npc_interaction.py 和 agent_orchestration.py 各维护一份，不跨层 import
- token 估算 `len(content)//4`，Phase 3b 接真实 token counter
- LRU 不持久化（Phase 3c 接 save_store）

**测试**：
- `tests/test_instance_manager.py`（新建，21 个测试）：init/create/get、LRU 淘汰逻辑、MRU 晋升、独立窗口、重建清空

**测试基线**：655 passed（+21，零回归）

**验收备注（2026-03-01）**：

- 本条验收备注由 Codex（GPT-5 编码代理）根据当前仓库实现与测试结果补记。
- D-N13 可按“主体完成”验收：`AgentContextBuilder` 已落地，`agent_orchestration.py` 已切换到 builder 路径，相关测试通过。
- 当前 7 层 context 仍未成为 LLM 的主输入源：`AgenticExecutor` 初始 history 仍主要使用 `scene_entries + user_message`，未直接注入 `build_gm_context()` / `build_npc_context()` / `build_teammate_context()` 的完整结果。
- 当前角色可见性属于“软约束”：L5 和 prompt 构建已做过滤，但 `build_agent_context()` 交给工具的仍是完整 `world + state`，尚未做角色级只读快照或硬隔离。
- 当前 `L2/L3` 与 `ContextAssembler` 不是完全同构：Agent 侧尚未补齐部分动态子区域相关字段（如 `dynamic_sub_area_counts`、`dynamic_sub_areas`）。
- 因此 D-N13 的状态应理解为”集中化完成、设计闭环未完全收口”；剩余差距已转入上方 TODO。

---

## [D-N17] N-1 Phase 3b：write_episode LLM 图谱化（2026-03-01）

**问题**：Phase 3a 完成的 `write_episode` 是 no-op stub；`ensure_lore_enriched` 缺失；
知识图谱只有静态边，对话内容无法沉淀为图谱关系。

**目标**：
1. `write_episode` — NPC 对话溢出 → LLM 提取三元组 → 动态边插入图谱
2. `ensure_lore_enriched` — 世界书 lore/角色描述 → LLM 提取语义关系 → 丰富静态图谱
3. 两条链路共享同一套 function calling 基础设施（`RECORD_TRIPLE_TOOL`）

#### 修改 `app/world_knowledge_graph.py`
- `__init__` 新增 `llm: LlmPort | None = None` 参数；新增 `_lore_graphized: set[str]`
- 5 个新 `EdgeType` 常量：`knows_about / interacted_with / made_promise / related_to / has_opinion_of`
- 模块级常量：`RECORD_TRIPLE_TOOL` function calling 声明 + `_DIALOGUE_EXTRACTION_PROMPT` + `_LORE_ENRICHMENT_PROMPT`
- `write_episode` 替换 stub → 完整实现：`_extract_triples_via_llm` + `_apply_triple`
- 新方法 `ensure_lore_enriched(world)` — 懒惰触发，先标记后调用，幂等
- `query_spread` 追加 `await self.ensure_lore_enriched(world)` 调用
- 新私有方法：`_extract_triples_via_llm` / `_apply_triple` / `_collect_lore_texts`
- 模块级纯函数 `_format_dialogue(messages)` — WindowMessage list → 可读对话串

#### 修改 `app/deps.py`
- `WorldKnowledgeGraph(llm=llm_provider)` — 传入 LLM，无 API key 时 llm=None 静默降级

**设计决策**：
- **Function calling 而非文本解析**：`RECORD_TRIPLE_TOOL` 声明，LLM 通过 `record_triple` 调用返回三元组，避免正则脆弱性
- **名称→ID 映射用 `_find_seed_nodes`**：LLM 返回自然语言名称，大小写不敏感匹配，找不到则静默跳过
- **ensure_lore_enriched 先标记后执行**：`_lore_graphized.add()` 在 LLM 调用前，防止并发重入；失败不重试
- **批量单次 LLM 调用**：lore 文本拼接后一次调用（cap=10），避免延迟爆炸
- **零回归保证**：`WorldKnowledgeGraph()` 无参构造保持兼容（llm=None 时两条链路均 no-op）

**测试**：
- `tests/test_write_episode.py`（新建，15 个测试）：
  - `TestWriteEpisode`（8）：no_llm、empty_messages、inserts_edge、skips_unknown_subject/object、llm_exception、multiple_triples、weight_propagated
  - `TestEnsureLoreEnriched`（4）：no_llm、idempotent、inserts_lore_edges、no_lore_registry
  - `TestApplyTriple`（3）：creates_edge、skips_unknown_subject、skips_self_loop

**测试基线**：670 passed（+15，零回归）

---

## [D-N18] N-1 Phase 4：L6 Memory Hits 注入 NPC/Teammate System Prompt（2026-03-01）

**问题**：整条记忆图谱管线（Phase 1-3b）打通后，`build_npc_system_prompt()` 和 `build_teammate_system_prompt()`
只读 L4 数据，L6 hits 被计算后丢弃，NPC/Teammate 看不到知识图谱查出的相关世界信息。

**目标**：将 L6 memory hits 注入 NPC/Teammate system prompt，打通记忆管线最后一公里。

#### 修改 `app/game_core/narrative/context_builder.py`（4 处）

**`_build_npc_prompt_text()`**：
- 新增 `knowledge_hits: list[dict[str, Any]] | None = None` 参数（默认 None，向后兼容）
- 从 hits[:5] 构建 `knowledge_block`：格式 `- label (node_type)` 或 `- label (node_type): description`
- f-string 中追加到 `{memories_block}` 之后，新增 `## Relevant world knowledge` 段落

**`build_npc_system_prompt()`**：
- `l6 = layers["l6_memory_recall"] or {}`
- `knowledge_hits=l6.get("hits", [])` 传入 `_build_npc_prompt_text()`

**`_build_teammate_prompt_text()`**：
- 新增 `knowledge_hits` 参数（默认 None）
- `base = TEAMMATE_PROMPT_TEMPLATE.format(...)` 后追加 knowledge block

**`build_teammate_system_prompt()`**：
- 同 NPC 提取 L6 并传入 `_build_teammate_prompt_text()`

**设计决策**：
- cap=5 hits（query_spread 限 top_k=10，进 prompt 再减半避免 token 膨胀）
- `## Relevant world knowledge` 区别于 `## Your memories of the player`（后者是 L4 impressions，主观记忆；前者是客观世界事实）
- GM 的 L6 设计为 None，不需改动
- 不动 executor.py：L6 是背景知识，属于 system prompt 而非对话 history

**测试**：
- `tests/test_context_builder.py` 扩展：新增 `TestL6Injection`（8 个测试）：
  - NPC prompt contains block / empty hits / capped at 5 / with description / without description
  - Teammate prompt contains block / empty hits
  - `_build_npc_prompt_text` 向后兼容（不传 knowledge_hits 不报错）

**测试基线**：678 passed（+8，零回归）

---

## [D-N19] N-2 Phase A：PrivateChatCoordinator MVP（2026-03-01）

**问题**：设计规范 §7.4 私聊机制（romance/深层信任场景）完全缺失。NpcInteractionCoordinator 是 6 步管线，私聊需要去掉 Step 3（GM 旁观）和 Step 4（队友反应），SceneEntry 改为 `visibility="private"`。

**目标**：实现玩家主动发起的 4 步私聊 MVP（Phase A），无触发条件限制。Phase B（romance/trust 阈值 + NPC 主动发起）留后续。

#### 新建 `app/game_core/orchestration/private_chat.py`（~175 行）

- `PrivateChatResult` dataclass（slots=True）：success/npc_id/npc_result/dialogue_options/time_cost/error/graphize_candidates
  - 有意不含 `gm_result` 和 `teammate_results`（私聊不可被第三方观察）
- `PrivateChatCoordinator` class：
  - Step 1 Setup：`build_npc_system_prompt()` + SceneEntry(`visibility="private"`, `audience=["player", f"npc:{npc_id}"]`)
  - Step 2 NPC Agent：`executor.run_agentic()` + ContextWindow overflow 检测
  - Step 3 Dialogue Options：`_build_static_dialogue_options()`（import from npc_interaction）
  - Step 4 Return：time_cost=1/6
- 模块级辅助函数：`_window_to_history()`、`_approx_tokens()`（各模块自持，不共用）
- 直接 import `_build_static_dialogue_options`、`_extract_speech_text` from npc_interaction（纯函数，无副作用）

#### 修改 `app/agent_orchestration.py`（~65 行）

- 新增 import：`PrivateChatCoordinator, PrivateChatResult` from `private_chat`
- 新增 `run_private_chat()` 方法：InstanceManager.get_or_create() + coordinator.execute() + write_episode overflow 回写 + 错误降级
- 新增 `_private_chat_result_to_sse()` 模块级函数：复用 `_npc_result_to_sse()` + dialogue_options 事件，无 GM/Teammate 事件

#### 修改 `app/api_models.py`（+5 行）

- 新增 `PrivateChatRequest(BaseModel)`：`npc_id: str`, `message: str`

#### 修改 `app/routers/gameplay.py`（~45 行）

- import `PrivateChatRequest` from `api_models`
- 新增 `POST /api/game/{world_id}/sessions/{session_id}/private_chat/stream` 端点
  - 与 `interact_stream` 同模式（简单 `_generate()` 协程，无 asyncio.Queue）
  - `agent_svc is None` → 优雅降级返回 `no_llm` 错误

**设计决策**：
- `_window_to_history` 和 `_approx_tokens` 各模块自持（与现有 npc_interaction.py 的模式一致，不共享）
- `_private_chat_result_to_sse` 复用 `_npc_result_to_sse()`（已在 agent_orchestration.py 定义）
- Phase B（触发条件 + NPC 主动）留后续，不在本次范围
- `PrivateChatRequest` 放入 `api_models.py` 遵循现有所有 Request 模型的组织模式

**测试**：新建 `tests/test_private_chat.py`（17 个测试）：
- `TestPrivateChatCoordinator`（9）：npc_not_found / no_llm / scene_is_private / audience / no_gm_teammate_fields / dialogue_options / window_updated / overflow_graphize / time_cost
- `TestPrivateChatResultToSSE`（5）：npc_speech_event / options_event / no_gm_teammate / empty_options / none_npc_result
- `TestWindowHelpers`（3）：excludes_graphized / min_one_token / proportional

**测试基线**：695 passed（+17，零回归）

---

## [D-N20] N-2 Phase B：PrivateChatTriggerHook（NPC 主动发起私聊）（2026-03-07）

**问题**：Phase A 完成了玩家主动发起的 4 步私聊管线，但 §7.4 的另一侧（NPC 根据 disposition 主动发起信号）缺失，且现有触发行为未按“只在场景可达 NPC、休息场景、非受限关系阶段、私聊中防重入、概率化”对齐。

**目标**：在结算时检测 NPC 的 romance/trust/stage 阈值，并按回归顺序触发私聊意图信号，避免异常触发。

#### 版本 1（2026-03-01）

- **触发条件**：romance ≥ 60 OR trust ≥ 50 OR stage == "intimate"（任一满足）
- **冷静期**：`absolute_tick + COOLDOWN_TICKS(=6)` 写入 FlagSlice，下次检查时比较

#### 版本 2（2026-03-07 回正）

- **场景过滤**：仅扫描当前玩家区域内 `areas.npc_locations` 中 NPC + `party.members`（始终可达），避免跨场景误触发。
- **阶段过滤**：`stranger` / `cold` / `hostile` / `nemesis` / `enemy` 直接跳过，不再触发。
- **时机过滤**：只在 `SceneBus` 当前 tick 带 `REST/LONG_REST` 标签时检查。
- **概率化**：满足条件后引入 Bernoulli 抑制，`romance=40%`、`trust=30%`、`intimate=60%`。
- **私聊重入防护**：`player.current_location` 以 `_private_` 开头时直接跳过。
- **阈值说明**：`TRUST_THRESHOLD` 保持 `50`，并在代码注释中对比 orchestration layer §3.2 与 NPC 规范不一致说明（仅记录，不改变逻辑）。

#### 模块更新

- 更新 `app/game_core/orchestration/hooks/private_chat_trigger.py`
- 保持 `PrivateChatTriggerHook` 在 `NpcScheduleHook` 后执行（priority 75）
- `tests/test_private_chat_trigger.py`：补充场景/关系阶段/时机/概率化/防重入覆盖
- 更新 `app/game_core/orchestration/hooks/__init__.py` + `app/game_core/orchestration/defaults.py` 注册（沿用原 D-N20 变更）

**设计决策**：
- 冷静期仍用 `TimeSlice.absolute_tick()` = `(day-1)*24+slot`，无须新建时钟状态
- 依旧采用直接 `FlagSlice` mutation（与现有 hooks 风格一致）
- Trust 阈值保持 50（与 Phase B 现有实现一致），优先以系统回归一致性为准
- 阶段过滤与概率化均保持 Hook 端完成，降低下游复杂度

**测试**：`tests/test_private_chat_trigger.py`（27 个测试）：
- `TestBasicPrivateChatTriggerEvaluator`（5）：romance/trust/intimate 各触发 + 无触发 + 优先级
- `TestNullEvaluator`（1）：从不触发
- `TestPrivateChatTriggerHook`（21）：场景过滤 / 非可达过滤 / 休息时机 / 关系阶段 / 概率抑制 / 私聊重入跳过 + 既有阈值、冷静期、metadata、Name 回退、无 FlagSlice 降级等

**测试基线**：预计可达 727（已补齐 D-N20 Phase B 回正用例，需执行回归）

---

## [D-O21] AIOsirisHook LLM 链路接通（2026-03-01）

见 `orchestration.md` [D-O21]。

---

## [D-A02] A-2：LLM 叙事文本真流式 + thought_signature 修复（2026-03-01）

**问题**：

1. `AgenticExecutor.run_agentic()` 最终轮（无工具调用）通过 `generate()` 非流式返回，客户端无法收到逐字文本，体验不连贯。
2. `_parse_response()` 丢弃 Gemini 3 的 `thought_signature`，多轮 function-calling history 重构时部分 context 损坏。

**目标**：最终轮真流式推送 + thought_signature 保留。

**决策**：双调用模式（double-call）：先 `generate()` 检测最终轮（无 tool_calls），再 `generate_stream()` 流式出文本。注 TODO：未来优化为单次流式检测。`generate_stream()` 使用 `tool_config=NONE` 强制禁用 function calling，确保纯文本输出。

#### 修改 `app/game_core/adapters/llm.py`

- `LlmResponse` 新增 `raw_model_parts: list[dict[str, Any]] | None = None`
- `LlmPort` Protocol 新增 `generate_stream()` → `AsyncIterator[str]`
- `NullLlmProvider` 新增空 stub（`return; yield` 模式）

#### 修改 `app/llm_gemini.py`

- 新增 `generate_stream()` 使用 `generate_content_stream()` + `tool_config=NONE`
- 修复 `_to_content()`：新增 `thought_signature` part 分支
- 修复 `_parse_response()`：填充 `raw_model_parts`（含 thought_signature/text/function_call）

#### 修改 `app/game_core/narrative/executor.py`

- `run_agentic()` 新增 `text_chunk_sink: Callable[[str], Awaitable[None]] | None = None`
- 最终轮：若 sink 且 LLM 有 `generate_stream`，重打流式调用逐 chunk 推送
- `_model_turn()` 优先使用 `response.raw_model_parts` 构建 history（保留 thought_signature）

#### 修改 `app/game_core/orchestration/npc_interaction.py` + `private_chat.py`

- `execute_interaction()` / `execute()` 新增 `text_chunk_sink` 参数，透传给 Step 2 NPC `run_agentic()`

#### 修改 `app/agent_orchestration.py`

- 5 个方法新增 `text_chunk_sink` 参数并透传：
  - `generate_npc_response()`, `_generate_gm_reaction()`, `generate_post_action_reactions()`, `run_npc_interaction()`, `run_private_chat()`

#### 修改 `app/routers/gameplay.py`

- `action_stream` + `input_stream`：在 `generate_post_action_reactions()` 调用前创建 `text_chunk_sink = async def(chunk) → queue.put(SSEEvent("text_chunk", {...}))`
- `interact_stream` + `private_chat_stream`：重构为 task+queue 模式（与 action_stream 对齐），创建 `text_chunk_sink` 并透传

**SSE 事件**：`{"event_type": "text_chunk", "payload": {"text": "<chunk>"}}`

**隔离约束**：`text_chunk_sink: Callable[[str], Awaitable[None]]` 在 game_core 层纯抽象，SSEEvent 构造在应用层。

**测试基线**：719 passed（零回归，A-2 为架构改动，现有测试覆盖接口契约）

---

## [D-N21] N-7：7 层上下文接入 AgenticExecutor 初始 History（2026-03-01）

**问题**：`AgenticExecutor._build_initial_history()` 只消费裸 `context.scene_entries`，`AgentContextBuilder` 构建的 L0/L2/L3 完全丢弃，L5 可见性过滤也被绕过（见待办 N-7）。NPC 无空间感知，GM 叙述缺乏环境锚点，设计规范 §3.2 可见性规则在 executor 层失效。

**解决方案**：新增 `NpcFullContext` dataclass + `build_npc_full_context()` 单次 retrieve，`run_agentic()` 新增 `context_layers` 参数，首轮注入序列化后的 L0/L2/L3/L5（GM 追加 L7）。

#### 改动清单

**`app/game_core/narrative/context_builder.py`**

- 新增 `NpcFullContext` dataclass（`system_prompt: str` + `layers: dict[str, Any]`）
- 新增 `build_npc_full_context()` async 方法：内部调用 `build_npc_context()` 一次，同时构建 system_prompt，避免 double-retrieve
- `build_npc_system_prompt()` 原样保留（向后兼容）

**`app/game_core/narrative/executor.py`**

- `run_agentic()` 新增 `context_layers: dict[str, Any] | None = None` 参数
- `_build_initial_history()` 新增 `include_scene: bool = True` 参数；当 L5 由 context_layers 提供时，抑制原始 `scene_entries` 重复注入
- `run_agentic()` 中：仅在 `conversation_history is None`（首轮）时注入 layers 文本，避免多轮上下文膨胀
- 新增 `_serialize_context_layers(role, layers)` 静态方法：L0 世界常量 + L2 区域 + L3 地点 + L5 场景（cap 10）；GM 额外追加 L7 hints

**`app/game_core/orchestration/npc_interaction.py`**

- Step 1：`build_npc_system_prompt()` → `build_npc_full_context()`（避免 double-retrieve）
- Step 2 NPC `run_agentic()`：追加 `context_layers=npc_layers`
- Step 3 GM `run_agentic()`：追加 `gm_layers = builder.build_gm_context()` + `context_layers=gm_layers`

**`app/game_core/orchestration/private_chat.py`**

- Step 1 同上改用 `build_npc_full_context()`
- Step 2 NPC `run_agentic()`：追加 `context_layers=npc_layers`

**`app/agent_orchestration.py`**

- `generate_npc_response()`：`build_npc_system_prompt()` → `build_npc_full_context()`；`run_agentic()` 追加 `context_layers=npc_full.layers`
- `_generate_gm_reaction()`：追加 `gm_layers = builder.build_gm_context(hints=...)` + `context_layers=gm_layers`
- `_generate_teammate_reactions()`：暂不改（Teammate double-retrieve 问题 defer 到 N-7 Phase 2）

**`app/game_core/narrative/__init__.py`**：导出 `NpcFullContext`

#### 设计决策

- **L4/L6 不序列化**：L4（关系数据）和 L6（记忆召回）已通过 `system_prompt` 体现，不重复注入
- **L1 不序列化**：GM 通过 L7 hints 获得足够上下文；L1 章节数据若需注入，由 Phase 2 补充
- **Teammate 推迟**：`build_teammate_system_prompt()` 同样有 double-retrieve 问题，但 Teammate 不是当前核心路径，defer 到 N-7 Phase 2

#### 新增测试

- `tests/test_context_builder.py::TestNpcFullContext`（4 个测试）：七层 keys 完整、未知 NPC→None、call_count==1 验证、system_prompt 内容正确
- `tests/test_agentic_loop.py::TestSerializeContextLayers`（5 个测试）：L2/L3 序列化、L5 场景注入、NPC 不含 L7、GM 含 L7、空 layers→空串
- `tests/test_agentic_loop.py::TestContextLayersInjection`（5 个测试）：首轮注入验证、有历史时跳过、L5 抑制原始 scene_entries、context_layers=None 向后兼容、GM L7 hints 进 history

**测试基线**：741 passed（新增 14 个测试，零回归）

---

## [D-N22] N-7 Phase 2：Teammate double-retrieve 修复 + context_layers 注入（2026-03-01）

**问题**：`_generate_teammate_reactions()` 调用 `build_teammate_system_prompt()`，内部调用 `build_teammate_context()`（含 retriever.retrieve()）。若再单独获取 layers，会 double-retrieve。`npc_interaction.py` Step 4 的队友路径也缺少 `context_layers`。

**解决方案**：与 NpcFullContext 完全对称，新增 `TeammateFull` + `build_teammate_full_context()`。`npc_interaction.py` Step 4 无 retriever，直接调 `build_teammate_context()` 无 IO 成本。

#### 改动清单

**`app/game_core/narrative/context_builder.py`**

- 新增 `TeammateFull` dataclass（`system_prompt: str` + `layers: dict[str, Any]`）
- 新增 `build_teammate_full_context()` async 方法（紧跟 `build_npc_full_context()` 之后）

**`app/game_core/narrative/__init__.py`**：导出 `TeammateFull`

**`app/agent_orchestration.py` `_generate_teammate_reactions()`**

- `build_teammate_system_prompt()` → `build_teammate_full_context()`（避免 double-retrieve）
- `run_agentic()` 追加 `context_layers=tm_full.layers`

**`app/game_core/orchestration/npc_interaction.py` Step 4**

- 追加 `tm_layers = await builder.build_teammate_context(member_id)`（无 retriever，无 IO）
- `run_agentic()` 追加 `context_layers=tm_layers`

#### 新增测试

- `tests/test_context_builder.py::TestTeammateFull`（4 个测试）：七层 keys、未知角色→None、call_count==1、system_prompt 内容正确

**测试基线**：745 passed（新增 4 个测试，零回归）

---

## [D-N23] P1-A：Planner LLM 上下文补全（2026-03-05）

**问题**：`AgenticNarrativePlanner.plan()` 通过 `_format_planner_context()` 格式化上下文，但该函数只输出 5 个字段，而 Hook 已构建 15+ 字段完整上下文（time, location, recent_changes, strategy_notes, area_cluster, behavior_window 等）。

**改动**：
- `narrative_planner.py` `_build_planner_context()`：`behavior_window_size` → `behavior_window: list(...)`
- `narrators.py` `_format_planner_context()`：完全重写，按设计规范四部分格式化

**范围说明**：设计规范 §3.1 还要求 `player.level`、`guild_rank`、`party`、`play_style_tags`、`area_npcs`、`relevant_factions` 等字段。这些字段 Hook 的 `_build_planner_context()` 本身不构建（只输出 15 个固定字段），`narrators.py` 无法格式化没有的数据。补齐这些字段需要先扩展 Hook 的 context 构建（读 PlayerSlice、CharacterRegistry、AreaSlice.npc_locations 等），属于独立的 Hook 扩展任务，不在本轮 P1-A 范围内。

**测试基线**：1051 passed（零回归）

---

## [D-N24] P1-B：NPC 指令消费链路（2026-03-05）

**问题**：`direct_npc` 指令写入 `NarrativePlanSlice.npc_directives`，但 NPC Agent 对话时完全不读取。

**改动**：

| 文件 | 改动 |
|------|------|
| `app/game_core/narrative/context_window.py` | +`directive_queue` 字段 + `consume_directive()` 方法 |
| `app/game_core/narrative/instance_manager.py` | `get_or_create()` 新增 npc_directives/current_tick 参数，注入未消费指令 |
| `app/agent_orchestration.py` | 3 处 get_or_create 调用注入 directives + current_tick |
| `app/game_core/orchestration/npc_interaction.py` | Step 1 消费 directive → 传给 build_npc_full_context |
| `app/game_core/narrative/context_builder.py` | `_build_npc_prompt_text` 加 active_directive；`build_npc_full_context` 透传 |
| `app/game_core/narrative/character_tools.py` | `OfferQuestTool` source `"ai_osiris"` → `"npc"` |

**设计关键**：directive_queue 存 state dict 直接引用，consume_directive() 标记 consumed=True 直接写回 state，snapshot() 时自动带出。过期 lifetime 默认 24 ticks（1 游戏天）。

**已接受偏差（对照设计规范 + P1 文档）**：

1. **active instance 直接注入未实现**（NPC规范 §8.3 step 2）：设计规范要求当 directive 写入时，若目标 NPC 有活跃实例则直接注入 directive_queue。当前实现仅在 `get_or_create()` 新建窗口时注入，pool 内已有窗口不感知新 directive。根本原因：NarrativePlannerHook 在 game_core 层，InstanceManager 在 app 层，架构隔离红线不允许跨层调用。影响：新 directive 加入时若目标 NPC 窗口在 pool，最多延迟 1 次 LRU 淘汰周期后才注入（下下次交互）。游戏中可接受。

2. **flush_to_state() 未实现**（P1 文档 Step 2）：P1 文档要求 LRU 淘汰前显式回写 consumed 状态。当前实现通过直接引用代替：directive_queue 存 state dict 引用而非拷贝，consume_directive() 原地 `consumed=True` 自动传播，NarrativePlanSlice.snapshot() 做 `dict(item)` 浅拷贝时捕获。仅在 session 生命周期内引用链有效（restore 只在 session 加载时发生，创建新 dict 对象不影响）。功能等价，实现更简洁。

3. **priority 比较修复**：设计规范 priority 为 str（"high"/"medium"/"low"），原 `max(eligible, key=lambda d: d.get("priority", 0))` 字母序错误（"medium">"low">"high"）。已修复为 `_directive_priority()` 函数，支持 str→int 映射 + int 两种格式。

**测试基线**：1051 passed（零回归）

---

## [D-N25] P1-C：任务生命周期闭环（2026-03-05）

**Phase 1：create_quest → EventSlice**：`NarrativePlannerHook` 新增 `_create_milestone_condition_events()`，为每条 success/failure condition 注册 dormant 事件（on_trigger → advance_quest）。

**Phase 2：BasicEventConditionEvaluator 支持 on_trigger**：`_evaluate_event()` 返回类型加 commands list。dormant 条件满足且有 on_trigger → 转 "resolved"（一次性）+ 返回命令；无 on_trigger → 原有 "available" 路径。`evaluate()` 聚合所有事件的命令供 EventConditionHook 统一执行。

> P1 文档说明 "dormant→triggered→active" 路径；实际实现改为 dormant→resolved（one-shot），见 D-O27 偏差说明。

**Phase 3：MilestoneUnlockHook (P55)**：新建文件，HOOK_PRIORITY=55（EventConditionHook=50 之后）。`should_skip=False`（原因：EventConditionHook 的 execute_command 走 _apply_delta 不写 change_log）。`execute()` 扫描所有 COMPLETED milestone，检查 next_milestones prerequisites，满足则 advance_quest AVAILABLE + emit "milestone_unlocked" SSE。

注册到 `hooks/__init__.py` + `defaults.py`。

**附带修复**：`EventSlice._VALID_STATES` 补入 `"available"`（D-O27 遗漏 1）。

**已接受偏差——MilestoneUnlock 1-tick 延迟**：P1 文档要求"同一 settlement 内完成解锁，Planner 直接看到 AVAILABLE"（P1 文档中称 P20→P25→P35 顺序）。实际 HOOK_PRIORITY 值：NarrativePlannerHook=35、EventConditionHook=50、MilestoneUnlockHook=55。执行顺序为 Planner(35)→EventCondition(50)→MilestoneUnlock(55)，MilestoneUnlock 反而排在 NarrativePlanner 之后。P1 文档的 P20/P35 是概念编号，不是实际优先级数值，文档对 hook 顺序的假设有误。实际效果：Tick T 的 EventCondition+MilestoneUnlock 解锁下游里程碑，Tick T+1 的 NarrativePlanner 才能看到新 AVAILABLE。1-tick 延迟，游戏中无感。若要同 tick 可见需将 NarrativePlannerHook 移至 P>55，影响大，不做。见 D-O27 同步更新。

**测试基线**：1051 passed（零回归）

---

## [D-N26] P2/P5 Round 1：Prompt 层丰富化 + Secrets Schema 升级（2026-03-05）

### 背景

NPC/Teammate 系统提示只消费了 `name/personality/dialogue_style/tags`，`CharacterTemplate` 的 `backstory/speech_pattern/character_class/faction/secrets` 完全未注入，导致对话质量不佳。

### Phase 1b：SecretEntry dataclass（characters.py）

- 新增 `SecretEntry(content, trust_threshold=50, tags=[])` dataclass（`NpcAttack` 之后，`ShopEntry` 之前）
- `CharacterTemplate.secrets` 类型 `list[str]` → `list[SecretEntry]`
- `_build_template()` 向后兼容解析：`str` → `SecretEntry(content=s, threshold=50)`；`Mapping` → 完整 SecretEntry

### Phase 1：Prompt 丰富化（context_builder.py + executor.py）

**新增模块级常量/函数**：
- `_STAGE_GUIDES: dict[str, str]` — 8 种关系阶段对应行为指引
- `_trust_hint(trust) -> str` — 5 档信任描述
- `_romance_hint(romance) -> str` — 4 档浪漫描述（<20 返回空）
- `_resolve_stage(state, char_id) -> str` — 读 RelationSlice.relationship_stages
- `_filter_secrets(secrets_raw, trust_val) -> list[str]` — str 兼容 + SecretEntry 门槛

**`_build_npc_prompt_text()` 新增参数 `time_info` 和段落**：backstory / speech_pattern / identity / behavior（stage+trust+romance）/ time / secrets（trust-gated）

**`_build_l4_npc()` 新增 `"time"` 键**，上游 `build_npc_full_context()` + `build_npc_system_prompt()` 传递 `time_info`

**`_build_teammate_prompt_text()` 动态化**：新增 `stage` + `time_info` 参数，复用行为指引函数

**executor.py**：L0 lore cap 5→3；每条目加 description

### 测试

新增 `tests/test_prompt_enrichment.py`（39 个测试）。已有测试小修：`test_content_registries.py`（secrets 检查 SecretEntry 对象）、`test_context_builder.py`（L4 keys 改 issubset）。

**测试基线**：1084 passed（零回归）

---

## [D-N27] P2/P5 Round 2：私聊上下文差异化 + 场景生成 + GM 内心旁白（2026-03-05）

### 背景

Phase 2：私聊对话与普通对话完全一致，NPC 无法感知"私下交谈"的语境。Phase 2b：私聊中缺少玩家内心独白维度。

### Phase 2：私聊上下文差异化 + 场景生成

**context_builder.py**：
- `_build_npc_prompt_text()` 新增 `is_private: bool = False` 参数
  - is_private=True 时注入 `## Private conversation context` 段
  - secrets 门槛降低 20：`effective_trust = trust + (20 if is_private else 0)`
- `build_npc_full_context()` 新增 `is_private: bool = False` 参数，透传给 `_build_npc_prompt_text()`

**private_chat.py**：
- 新增 `_PRIVATE_CHAT_SCENES` 模块常量（4 种 area 标签 × 若干模板）
- `PrivateChatResult` 新增 `scene_id: str | None = None` + `scene_name: str = ""`
- 新增 `_create_private_scene()` 方法：幂等（先清旧 → 再建新）+ 按 area tags 匹配模板
- `execute()` 调用改为 `is_private=True`，Step 1 后创建私聊场景

**agent_orchestration.py**：
- `_private_chat_result_to_sse()` 前置 `scene_change` 事件（`location_id/name/background/transition`）

### Phase 2b：GM 内心旁白

**context_builder.py**：
- 新增 `GM_PRIVATE_CHAT_INTROSPECTIVE_PROMPT` 常量（玩家内心独白，非毒舌 GM）
- `AgentContextBuilder.build_gm_private_chat_prompt()` 返回该常量

**private_chat.py**：
- `PrivateChatResult` 新增 `gm_result: AgentResult | None = None`（docstring 注明是内心独白非第三方观察）
- `execute()` Step 2.5：GM 内心旁白，`max_turns=1`，默认 pass_turn

**agent_orchestration.py**：
- `_private_chat_result_to_sse()` 处理 `gm_result` → `gm_comment` SSE（`tone="introspective"`）

### 测试

新增 `tests/test_private_chat_enrichment.py`（14 个测试）。已有测试小修：`test_private_chat.py`（`gm_result` 字段存在但允许为 None）。

**测试基线**：1098 passed（零回归）

## [D-N28] P2/P5 Round 3：SceneBus 事件标签 + SharedExperience Hook + Teammate 场景修正（2026-03-05）

### 背景

Phase 0：SceneBus 缺少语义标签，Phase 3/4 的战斗/危机检测无法工作。Phase 3：PartySlice 有 `record_experience()` API，但没有 Hook 调用它。Phase 4：Teammate 反应概率固定，无法感知"战斗刚结束"或"刚开过口"等上下文。三者依赖链：Phase 0 写 COMBAT_END → Phase 3 检测到战斗经历 → Phase 4 Teammate 感知。

### Phase 0：SceneBus ENGINE 标签注入（tick_coordinator.py）

- 新增模块级常量 `_SEMANTIC_TAGS`（7 种 action_type → tag 列表）
- 新增 `_emit_action_tags(result)` 方法：写入 `source="ENGINE"` + `visibility="system"` 的 SceneBus 条目
- `process()` 在 `_record_action()` 后立即调用 `_emit_action_tags()`

### Phase 3：SharedExperienceHook（新建 hooks/shared_experience.py）

- `HOOK_PRIORITY = 62`（NpcScheduleHook=60 之后，RelationshipHook=65 之前）
- `_detect_experience()` 优先级：combat > quest > rest，从 action_log 和 SceneBus ENGINE tags 双重检测
- 命中后调用 `context.state.party.record_experience(experience)`
- `hooks/__init__.py` 导入 + `__all__` 追加；`defaults.py` 注册到 `DEFAULT_SETTLEMENT_HOOK_TYPES`

### Phase 4：Teammate 场景感知修正（npc_interaction.py）

- `_should_teammate_respond()` 新增 `scene_entries` 参数（可选，向后兼容）
- 场景调整：`COMBAT_END+0.3 / CRISIS+0.4 / TRIVIAL-0.2 / recent_speaks×-0.15`
- `execute_interaction()` Step 4 在队友循环前提取 `scene_entries` 并传入

### 测试

新增 `tests/test_round3_engine_tags.py`（12 个）+ `tests/test_round3_shared_experience.py`（19 个），共 31 个新测试。

**测试基线**：1129 passed（零回归）

## [D-N29] P2/P5 Round 4：CampfireHook + 负面关系跃迁 + Directive GC（2026-03-05）

### 背景

Phase 5：SharedExperienceHook 已录入战斗/任务/休息经历，但没有任何消费端——长休后队友沉默。Phase 6a：RelationshipHook 只处理正面跃迁，好感度暴跌不会触发 cold/hostile。Phase 6b：NarrativePlanSlice.npc_directives 无限增长，消费/过期的 directive 不清理。

### Phase 5：CampfireHook（新建 hooks/campfire.py，P63）

- 触发条件：LONG_REST tag + party 成员 + 今天有重大经历（必触发）/ 无重大经历 30% 概率
- 队友资格：stage 不在 stranger/cold/hostile/nemesis + approval ≥ 0
- 经历选择：优先今天的重大经历（+100）、critical_moment（+30）、major 类型（+10）
- 输出：`campfire_dialogue` SSE event（teammate_id / content / memory_type / memory_summary）
- 附带 +5 approval（`RelationSlice.modify_disposition`）
- 注册：`hooks/__init__.py` + `defaults.py`（P63，在 SharedExperienceHook=62 与 RelationshipHook=65 之间）

### Phase 6a：负面关系跃迁（relationship.py）

- 新增 `_NEGATIVE_ENTRY_THRESHOLD`：acquaintance(-20) / friend(-30) / close_friend(-40) / intimate(-50) → cold
- 新增模块级函数 `_next_negative_stage(current_stage, dispositions)`
- `execute()` 循环内：正面检测返回 None 时改查负面方向，最后统一执行 `set_relationship_stage`
- 负面渐进（cold→hostile→nemesis）及恢复路径延迟到轮5

### Phase 6b：Directive GC（narrative_plan.py + narrative_planner.py）

- `NarrativePlanSlice.prune_consumed_and_expired(current_tick)` 过滤 `consumed==True` 或 `expires_at_tick < current_tick` 的 directive，返回清除数量
- `NarrativePlannerHook.execute()` 在最终 `return HookResult(...)` 前调用 GC

### 测试

新增 `tests/test_round4_campfire.py`（22 个）+ `tests/test_round4_negative_stages.py`（13 个）+ `tests/test_round4_directive_gc.py`（9 个），共 44 个新测试。

**测试基线**：1173 passed（零回归）

## [D-N30] P2/P5 Round 5：CompanionManager + 负面关系深化 + EventEngine 条件扩展（2026-03-05）

### 背景

Phase 7（CompanionManager 招募/离队）从未实现，RelationshipHook 的 cold→hostile→enemy 渐进跃迁 deferred 到本轮。Phase 8（EventEngine 缺 npc_talked / item_obtained / kill_count 条件类型）限制了事件触发能力。Phase 9 (InstanceManager tiering) 因单模型偏好跳过。

### Phase A：CompanionManager + 负面关系深化

**新建 `app/game_core/orchestration/companion_manager.py`**：
- `RecruitResult` dataclass（success / reason 字段）
- `CompanionManager(world, state)` 类，不是 Hook，被 RelationshipHook 和（未来）InteractionService 调用
- `recruit(npc_id)` 前置条件：has "recruitable" tag + stage != stranger + approval > 0 + party not full + not already member
- `dismiss(npc_id)` 直接调 `state.party.remove_member()`
- `force_leave(npc_id, reason)` 包装 dismiss，reason 改为 `force_leave:{reason}`

**扩展 `hooks/relationship.py`**：
- 新增 `_NEGATIVE_PROGRESSION`：`"cold" → ("hostile", {approval:-50, trust:-30})`，`"hostile" → ("enemy", {trust:-60})`
- `_next_negative_stage()` 扩展：在 `_NEGATIVE_ENTRY_THRESHOLD` 查不到时，改查 `_NEGATIVE_PROGRESSION`，所有维度均需满足阈值
- `execute()` 补丁：当 new_stage 为 hostile/enemy 且 npc_id 在 party.members 时，实例化 CompanionManager 调 force_leave()，emit `companion_dismissed` SSE + record_change

### Phase B：EventEngine 3 个新条件类型

**扩展 `event_engine.py`**：
- `_check_npc_talked(state, params)` → 读 FlagSlice `talked_to_{npc_id}` flag
- `_check_item_obtained(state, params)` → 查 PlayerSlice.snapshot()["inventory"] 中是否有 item_id
- `_check_kill_count(state, params)` → 读 FlagSlice `kill_count_{monster_type}` flag，比较 count

**flag 写入**：
- `npc_interaction.py` Step 1：每次交互后写 `talked_to_{npc_id}=True`（npc_full != None 后）
- `private_chat.py` Step 1：私聊也写同样 flag
- `kill_count_{type}` 写入侧：✅ **已完成**（见 D-P5-78 below）

### 测试

新增 `tests/test_round5_companion.py`（21 个）+ `tests/test_round5_event_conditions.py`（17 个），共 38 个新测试。

**测试基线**：1194 passed（零回归）

---

## D-P5-78: Phase 7/8 收尾（2026-03-05）

### Phase 8：kill_count 写入侧

**改动文件**：`rules/handlers/combat.py`

在 `_compute_attack_resolution()` 的 `if combat_cleared:` 块内（XP 分发之后），遍历所有参与者，为死亡（非逃跑）的怪物写入 `kill_count_{monster_id}` flag：
- 只计 `alive=False AND fled=False` 的怪物
- 使用 FlagSlice "set" 操作（读-改-写，因 FlagSlice 不支持 "add"）
- 与 XP StateChange 一起放入 `extra_changes`，原子应用

闭合链路：`CombatHandler → kill_count flag → EventEngine._check_kill_count() → MilestoneCondition`

### Phase 7：CompanionManager API 端点

**改动文件**：`api_models.py` + `routers/gameplay.py`

新增 `CompanionRequest(npc_id: str)` model 和两个 streaming 端点：
- `POST .../companion/recruit` — 调用 `CompanionManager.recruit()`，成功 emit `companion_recruited` SSE
- `POST .../companion/dismiss` — 调用 `CompanionManager.dismiss()`，成功 emit `companion_dismissed` SSE

两端点均遵循 `_stream_with_lock` 模式，成功后 save + `location_overview` + `stream_end`。

### 测试

新增 `tests/test_combat_kill_count.py`（4 个测试）：
- `test_kill_count_incremented_on_defeat`: 击杀后 flag 递增
- `test_kill_count_not_incremented_on_flee`: 逃跑不计数
- `test_kill_count_accumulates`: 多次击杀累积
- `test_kill_count_different_monster_types`: 不同怪物分开计数

CompanionManager 单元测试已在 `test_round5_companion.py` 中完备（21 个），无需新增。

---

## [D-N13] board_id 动态解析与发布目标统一（2026-03-07）

### 背景

- 早期 `NarrativePlanner` 在发布任务公告时硬编码 `board_id="board"`，与地图内真实可配置 `quest_source` ID 不一致。
- 同期旧链路也存在 `NarrativePlanSlice` 持有公告内容的遗留认知，导致 BoardHandler 与 AreaSlice 读取口径不统一。

### 决策

1. 在 `NarrativePlanner` 的上下文归一化中携带当前 `location` 与 `maps`。
2. 新增 `_resolve_quest_board()` 动态解析器：
  - 优先从 `MapRegistry` 的当前 `area_id` 下所有 `sub_location.interactables` 找带有 `quest_source` tag 的交互物；
  - 动态解析 `board_id` 作为 `publish_bulletin` 指令入参。
3. 如果缺失可解析目标，`_try_seed_quest()` 返回 `stable` 并带 `reason="quest_board_unresolved"`，避免抛错误中断。
4. `publish_bulletin` 指令执行链统一写入 `AreaSlice` 的 `board_bulletins`（见 D-S07），不再在 `NarrativePlanSlice` 持久化公告。

### 变更与验收

- 文件：`app/game_core/planning/planner.py`  
- 文件：`app/game_core/planning/models.py`（发布指令 payload）
- 文件：`app/game_core/orchestration/hooks/narrative_planner.py`（执行阶段消费 `area_id`）
- 文件：`tests/test_narrative_executor.py`
- 文件：`tests/test_narrative_planner_hook.py`

### 收益

- `action/stream` 下 `browse_board` 的 `board_id` 不再受固定字符串影响，能随地图配置切换。

---

## D-P18a — §1.5 关键词提取重写 + §1.3a story_facts 持久化（2026-03-09）

### 问题根因

P18 审计发现两处断点：
1. `_extract_scene_keywords()` 对中文内容做空格 tokenize（`content.split()`），中文无空格分词导致关键词全部失效，知识图谱查询 L6 命中率为零。
2. `NarrativePlanSlice` 无 `story_facts` 字段，`write_episode` 提取的三元组无法跨 session 持久化。

### §1.5 — context_builder.py `_extract_scene_keywords()` 重写

**文件**：`app/game_core/narrative/context_builder.py`（L1097-1139）

改动策略：完全放弃文本 tokenize，改用实体 ID 直接提取：
- `actor_id` 作为第一个关键词
- `player.snapshot()` 中的 `current_area`、`current_location`
- `quests.get_active_quests()` 中每个任务的 `target_milestone`（仅 status in_progress/active/accepted）
- 最近 5 条场景条目 metadata 中的 `npc_id`、`speaker_id`、`character_id`
- 去重保序，上限 20 个

对比旧实现：旧实现把中文 content 按空格分词再 lower/strip，对中文几乎完全失效。新实现不依赖任何 NLP，完全使用英文 ID，与知识图谱节点 ID 直接匹配。

### §1.3a — NarrativePlanSlice 新增 story_facts 字段

**文件**：`app/game_core/state/slices/narrative_plan.py`

- `__init__`：新增 `self.story_facts: list[dict[str, Any]] = []`
- `restore()`：`self.story_facts = [dict(item) for item in payload.get("story_facts", []) if isinstance(item, Mapping)]`
- `snapshot()`：`"story_facts": [dict(f) for f in self.story_facts]`
- 新增方法 `add_story_facts(facts)`：`extend(dict(f) for f in facts)` 存防御性拷贝 + `_dirty = True`

注意：`add_story_facts([])` 仍会 set `_dirty = True`（与 `record_behavior` 一致的语义）。

### 测试

新建 `tests/test_p18_phase1.py`（22 个测试，全部通过）：
- `TestExtractSceneKeywords`（10 个）：actor_id 优先、area/location ID、quest milestone、过滤 completed、中文内容不产生 ASCII 外关键词、metadata npc_id/character_id、去重、上限、空 area 安全
- `TestNarrativePlanStoryFacts`（12 个）：初始化、append 防御性拷贝、dirty 标记、空列表调用、多次累积、snapshot 含字段、snapshot 防御性拷贝、restore 加载、restore 缺省空列表、restore 过滤非 Mapping、序列化轮回、restore 清 dirty

测试基线：1604 passed（+22，3 pre-existing failures 不变）

---

## [D-P18-1.1] escalate 指令落世界状态（2026-03-09）

**问题**：`escalate` 指令仅更新内部计数器 `narrative_plan.escalation_level`，不落世界状态，导致下游系统（战斗难度、NPC 感知）无法读取。

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**实现**：
- 新增 import `Command`（`from app.game_core.rules.models import Command`）
- `_apply_directive("escalate", ...)` 主处理（原 L1038-1045）：
  - 保留 `adjust_escalation(delta)` 内部计数器更新
  - 追加 `execute_command(Command(type="adjust_danger", params={"area_id": area_id, "delta": 0.05*delta}))` —— 以 `context.state.player.current_area` 获取区域；无 player 切片时跳过
  - 追加 `execute_command(Command(type="set_flag", params={"key": "narrative_escalation_level", "value": escalation_level}))` —— 无 flags 切片时命令静默失败（validation 返回 ok=False）
- `_expire_dynamic_quests()` on_expire="escalate" 路径（原 L1557-1558）：同样追加两个 execute_command，delta 固定 0.05

**注意**：`RulesEngine()` 裸构造不含 handlers，测试中必须调用 `register_default_rules_handlers(rules_engine)`。

---

## [D-P18-1.4] plant_environmental / fill_area SSE + SceneBus 通知（2026-03-09）

**问题**：`plant_environmental` 和 `fill_area` 成功执行后不发 SSE，前端和下游 Hook 无法感知环境变化。

**改动文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**实现**：
- `NarrativePlannerHook.__init__`：新增实例变量 `self._pending_sse: list[SSEEvent] = []`（per-execute scratch buffer）
- `execute()` 开头：`self._pending_sse = []`（重置）
- `execute()` 构建 `sse_events` 时：`sse_events: list[SSEEvent] = list(self._pending_sse)` 后立即 `self._pending_sse = []`（drain）
- `_apply_directive("plant_environmental", ...)` 成功后（`return True` 前）：
  - `self._pending_sse.append(SSEEvent(event_type="environment_changed", payload={...change_type="plant_environmental"}))`
  - `context.scene_bus.add_entry({"source": "ENGINE", "content": "[ENGINE:environment_changed] New discovery point appeared: ...", "visibility": "system", "tags": ["environment_changed", "narrative_planner"]})`
- `_apply_directive("fill_area", ...)` 成功后：同样模式，`change_type="fill_area"`

**设计决策**：`_apply_directive` 是同步方法，无法直接返回 SSE。选择 instance-level scratch buffer 而非改方法签名，减少改动面。每次 `execute()` 调用时重置，不会跨调用污染。

## [D-P18-1.3b] WorldKnowledgeGraph.inject_story_facts（2026-03-09）

**问题**：`write_episode` 提取的三元组只存活在内存 graph 中，session 重启后丢失，知识图谱每次从空白开始。

**改动文件**：`app/world_knowledge_graph.py`

**实现**：新增 `inject_story_facts(facts: list[dict[str, Any]])` 方法，在 `ensure_seeded` 附近。

逻辑：
- 遍历 facts，跳过 subject/relation/object 任意一个为空的条目
- subject/object 若不存在则创建 `node_type="story_fact"` 的新节点
- 使用 `self._graph.add_edge(subj, obj, relation=rel, weight=weight, source="story_fact")` 插入边（NetworkX 幂等覆盖）

**设计决策**：不新增 `_seeded` 状态跟踪（inject 是幂等的），不走 `_apply_actor_triple`（story_facts 是全局事实，不是某个 NPC 私有知识）。

**测试**：9 个新测试 in `tests/test_p18_phase1.py::TestInjectStoryFacts`

## [D-P18-1.3c] Session 加载时注入 story_facts（2026-03-09）

**问题**：graph 在内存中，session 重启后 story_facts 三元组丢失。需在 session 加载时重新注入。

**改动文件**：`app/game_core/runtime.py`（`resume_session` 方法）

**注入点**：`CompanionManager.sync_to_player()` 之后，`phase` 变量赋值之前。

**实现**：
```python
if (
    self._agent_orchestration is not None
    and runtime.state.has_slice("narrative_plan")
):
    graph = getattr(
        getattr(self._agent_orchestration, "_memory_retriever", None),
        "_graph",
        None,
    )
    if graph is not None:
        facts = runtime.state.narrative_plan.story_facts
        if facts:
            graph.inject_story_facts(facts)
```

**设计决策**：通过 `getattr` 安全取 `_memory_retriever._graph`（不引入新 Protocol），`game_core/runtime.py` 用局部导入模式访问应用层组件（与 CompanionManager 注入相同模式）。

## [D-P18-1.2] write_episode 改为主动触发（2026-03-09）

**问题**：`write_episode` 只在 ContextWindow overflow 时触发（200K token 门槛，实际不可达），导致对话知识从不被提取到图谱。

**改动文件**：`app/agent_orchestration.py`

**实现**：
- 新增模块级纯函数 `_collect_npc_interaction_exchange(result: NpcInteractionResult) -> list[WindowMessage]`：
  - 从 `result.round_messages` 中取第一条 player 消息和第一条 npc 消息
  - NPC 无回复时返回空列表（不触发 write_episode）
- 新增模块级纯函数 `_collect_private_chat_exchange(player_message: str, result: PrivateChatResult) -> list[WindowMessage]`：
  - 用 `_extract_visible_reply_text(result.npc_result)` 取 NPC 回复
  - NPC 无回复时返回空列表
- `run_npc_interaction()`：在现有 overflow 路径 `_write_episode(session, npc_id, result.graphize_candidates)` 之后追加主动路径
- `run_private_chat()`：同样模式

**触发条件**：`if not result.graphize_candidates` — overflow 路径已覆盖时不重复提取（避免对同一段对话提取两次）。

**测试**：8 个新测试 in `tests/test_p18_phase1.py`（4 × `TestCollectNpcInteractionExchange` + 4 × `TestCollectPrivateChatExchange`）

---

## [D-P18b] Phase 2：删除确定性 Planner + LLM 化（2026-03-09）

**目标**：删除 `NarrativePlanner`（807 行确定性 planner），只保留 LLM planner。Hook 无 LLM 时优雅降级（noop）。

### 核心改动

**删除**：`app/game_core/planning/planner.py`（整文件，807 行）

**更新 `app/game_core/planning/__init__.py`**：移除 `NarrativePlanner` 导出。

**`app/game_core/orchestration/hooks/narrative_planner.py`**：
- `__init__`：`self.planner = planner`（不再 fallback 到 `NarrativePlanner()`）
- `execute()` 入口：`planner is None` → 提前 return noop，reason="no_planner"
- `_bootstrap_decision()`：内联最小确定性逻辑（~45 行），只做 milestone 播种 + quest_source board 公告，不再依赖旧 planner 的任何方法
- `_build_planner_context()`：追加 `story_facts` 和 `danger_level` 字段
- `_get_area_danger()`：新增 `@staticmethod`，从 AreaSlice 读取当前区域危险度
- `_apply_story_facts()`：在 `execute()` 中 `_apply_normalized_decision()` 后处理 story_facts 写入

**`app/narrators.py`**：
- `AgenticNarrativePlanner.__init__`：删除 `NarrativePlanner` fallback，纯 LLM
- `_SYSTEM_PROMPT`：重写为中文创作指令，含 story_facts 说明、升级阶梯、可用指令（移除 spawn_quest_npc）
- `plan()` 方法：LLM 失败/解析失败时 → 返回空 directives noop（不再 fallback 到确定性 planner）
- `_format_planner_context()`：Part 2 后追加"世界中已确立的事实"section（`story_facts`），Part 3 追加 `danger_level`

### 测试改动

**删除（共 11 个）**：
- `tests/test_narrative_executor.py`：删除 8 个 `test_default_narrative_planner_*` + `_map_context_with_board` helper
- `tests/test_narrative_planner_hook.py`：删除 3 个测试旧 planner 内嵌逻辑的测试（`test_l2_uses_involved_npc_from_milestone`、`test_l2_no_directive_when_no_npc_available`、`test_l3_create_quest_metadata_includes_key_elements`）

**修改（共 12 个）**：
- `test_narrative_planner_hook.py`：将调用 `NarrativePlannerHook()` 无 planner 的测试改为传入 `RecordingPlanner`（quest expiry × 3、spawn despawn × 1、escalation L2/L3/L4/noop × 4、play_style_tags × 1、fallback bookkeeping × 1、duplicate skip × 1）
- `test_bootstrap_noops_when_seeded_quest_already_exists`：修正断言 `"reason"` 由 `"bootstrap_stable"` → `"stable"`

**新建**：`tests/test_p18_phase2.py`，15 个新测试覆盖：
- `test_no_planner_returns_noop`
- bootstrap 逻辑 × 3（seed / skip / bulletin）
- story_facts 写入与过滤 × 2
- `_format_planner_context` story_facts + danger_level × 3
- `_build_planner_context` 字段注入 × 2
- AgenticNarrativePlanner 故障路径 × 3

---

## [D-P18c] Phase 3：指令深化（2026-03-09）

**目标**：3 个指令消费端闭环深化。

### 3.1 plant_environmental Tier 1-3

**Tier 1：PassivePerceptionHook 动态子地点感知**（已在 Phase 3 前完成，本次追加测试验证）

`app/game_core/orchestration/hooks/passive_perception.py` Phase 4 逻辑：遍历 `areas.list_temporary_sub_areas(area_id)`，过滤 `discovery_mode="check"`，按 `discovery_dc` 执行被动感知，发现时写入 `mark_discovery()` + 记录 `discovered_items.{sub_id}` StateChange + 发射 `discovery_reveal` SSE。

**Tier 2：InteractableHandler 动态子地点 fallback**（已在 Phase 3 前完成，本次追加测试验证）

`app/game_core/rules/handlers/interactable.py`：静态模板找不到时，调 `_find_dynamic_interactable()` 在 `temporary_sub_areas` 中扫描匹配 `location_id` 的子地点，再查 `interactables` 列表。`_compute_dynamic()` 兼容 raw dict checks（`.get()` 访问）。

**Tier 3：context_builder L2/L3 content_hints**（已在 Phase 3 前完成，本次追加测试验证）

`app/game_core/narrative/context_builder.py`：
- `_build_l2()`：汇总动态子地点 `content_hints`/`description` 为 `content_hints` 列表（含 `discovered` 标志）
- `_build_l3()`：`is_dynamic=True` 时追加 `content_hints` 和 `interactables` 字段

### 3.2 retire_quest 级联清理

**Bug 修复**：`app/game_core/orchestration/hooks/narrative_planner.py` L1210：`area_state._dirty = True` → `context.state.areas._dirty = True`（`AreaState` 是 `@dataclass(slots=True)`，不含 `_dirty` 属性，`_dirty` 在 `AreaSlice` 上）。

**级联逻辑**（已在 Phase 3 前完成）：
1. 扫描 `quest_history` 中 `kind="spawn_quest_npc"` + `linked_quest_id==quest_id` 的条目，从所有 area 中移除 NPC 位置，并调 `remove_temporary_npc()`
2. 遍历所有 area 的 `board_bulletins`，调 `remove_board_bulletin(area_id, board_id, quest_id)`
3. 遍历所有 area 的 `temporary_sub_areas`，对 `linked_quest_id==quest_id` 的子地点调 `remove_temporary_sub_area()`
4. 过滤 `narrative_plan.npc_directives` 中 `linked_quest_id==quest_id` 的条目

### 3.3 publish_bulletin 默认通知

**已在 Phase 3 前完成**：`notify_resident_npcs` 默认值为 `True`（L1102）。

### 测试

**新建**：`tests/test_p18_phase3.py`，24 个测试：

- `TestPassivePerceptionDynamicSubArea` × 4（detect / skip auto / skip discovered / fail low passive）
- `TestInteractableHandlerDynamicFallback` × 5（find dynamic / unknown location / unknown interactable / no checks / with checks metadata）
- `TestContextBuilderContentHints` × 6（L2 has hints / L2 discovered flag / L2 no hints when empty / L3 interactables / L3 content_hints / L3 static is_dynamic=False）
- `TestRetireQuestCascade` × 6（despawn NPC / remove bulletin / remove sub_area / remove directives / no crash on no links / preserve unlinked bulletins）
- `TestPublishBulletinDefaultNotify` × 3（default True notifies / explicit False no-op / explicit True notifies）

**测试基线**：1667 passed（3 预已知失败不变）

**测试基线**：1643 passed（3 个预已知 flaky 不变）

---

## [D-P18d] Phase 4：知识图谱动态边持久化（2026-03-09）

**目标**：`WorldKnowledgeGraph` actor-private 图（`write_episode` + `remember` 产出的 NPC 私有知识）跨 session 持久化。

**已有情况**：
- `story_facts`（NarrativePlanner 产出）已通过 `NarrativePlanSlice.story_facts` 持久化 ✅
- `ensure_lore_enriched()` / `ensure_seeded()` 每 session 幂等重建，无需持久化 ✅
- `_actor_graphs`（NPC 私有知识）session 重启后丢失 ❌

**持久化范围**：`_actor_graphs` + `_actor_memory_counts`

### 4.1 WorldKnowledgeGraph — export/import_actor_state（app/world_knowledge_graph.py）

新增两个方法（`_is_memory_node` 之后）：

**`export_actor_state() -> dict[str, Any]`**：
- 遍历 `_actor_graphs`，跳过空图
- 每个 actor 序列化 nodes（含 id 字段）和 edges（含 src/dst 字段）
- 含 `memory_counts`
- 无数据时返回 `{}`

**`import_actor_state(data: dict[str, Any]) -> None`**：
- 幂等（覆盖已有 actor graph）
- 对每个 node dict 做 `dict(node)` 拷贝后 pop "id"（避免污染入参）
- 对每个 edge dict 做 `dict(edge)` 拷贝后 pop "src"/"dst"
- 跳过无 id 的 node、跳过 src/dst 为空的 edge
- 恢复 `_actor_memory_counts`

### 4.2 NarrativePlanSlice — actor_knowledge 字段（app/game_core/state/slices/narrative_plan.py）

- `__init__`：新增 `self.actor_knowledge: dict[str, Any] = {}`
- `restore()`：`raw_ak = payload.get("actor_knowledge")`；`Mapping` 则 `dict(raw_ak)` else `{}`
- `snapshot()`：新增 `"actor_knowledge": dict(self.actor_knowledge)`
- 新增方法 `set_actor_knowledge(data)`：`dict(data) if data else {}` + `_dirty = True`

### 4.3 GameRuntime — save/load 集成（app/game_core/runtime.py）

**`save_session()` 前同步**：
- 新增 `self._sync_knowledge_graph_state(session.runtime)` 调用（在 `_save_store.save_runtime()` 之前）
- 新增 `_sync_knowledge_graph_state(runtime)` 方法：getattr 取 `tick_coordinator.knowledge_graph`，调 `export_actor_state()`，非空则调 `narrative_plan.set_actor_knowledge()`

**`_restore_story_facts()` → `_restore_knowledge_graph()` 重命名并扩展**：
- 步骤 1（已有）：注入 `story_facts` 到主图
- 步骤 2（新增）：取 `narrative_plan.actor_knowledge`，调 `import_actor_state()`
- `resume_session()` 调用处同步更新

**设计决策**：
- 使用 `getattr` 安全取图谱引用（与现有 `_resolve_knowledge_graph()` 风格一致）
- 方法重命名为 `_restore_knowledge_graph` 更准确，旧名 `_restore_story_facts` 仅做故事事实
- `import_actor_state` 内部拷贝 dict 避免污染入参，符合防御性编程约定

### 测试（tests/test_p18_phase4.py，新建，30 个测试）

- `TestExportActorState`（5）：empty / empty graph / with triple / memory counts / multiple actors
- `TestImportActorState`（6）：restore nodes+edges / memory counts / idempotent / no mutation / invalid data / skip no-id nodes
- `TestExportImportRoundTrip`（1）：端到端 export→import 保留 edges
- `TestNarrativePlanActorKnowledge`（9）：init / set+dirty / empty clears / snapshot含字段 / snapshot防御拷贝 / restore / restore缺省 / restore非mapping / round-trip
- `TestSyncKnowledgeGraphState`（4）：有actor时写入 / no_wkg / no_slice / 无actor不写入
- `TestRestoreKnowledgeGraph`（4）：restore actor / 仍恢复story_facts / no_wkg / no_slice
- `TestFullRoundTrip`（1）：write triple → save(sync) → resume(restore) → query 有边

**测试基线**：1697 passed（+30，3 预已知失败不变）

---

### [D-P19a] P19 Phase A — Planner ↔ NPC 交互修复（2026-03-09）

**问题**：Planner 的 `direct_npc` 指令存入 `NarrativePlanSlice.npc_directives` 后，只有玩家主动对话时才被消费。无主动触发路径导致指令永远 `consumed=false`，Planner 也因不感知 pending 指令而重复下发。

**修改文件**：

| 文件 | 改动 |
|------|------|
| `app/game_core/state/slices/narrative_plan.py` | `add_directive()` 添加同 NPC 去重：新 pending 指令替换旧的，已消费保留 |
| `app/game_core/orchestration/hooks/directive_trigger.py` | **新建** `DirectiveTriggerHook`（priority=76），扫描 pending directives → reachable NPC 检查 → cooldown 检查 → 概率门控 → `npc_wants_to_chat` SSE，每 tick 最多触发 1 个 |
| `app/game_core/orchestration/hooks/__init__.py` | 注册 `DirectiveTriggerHook` |
| `app/game_core/orchestration/defaults.py` | 加入 `DEFAULT_SETTLEMENT_HOOK_TYPES`（PrivateChatTriggerHook 之后） |
| `app/game_core/orchestration/hooks/narrative_planner.py` | `_build_planner_context()` → `narrative_plan` 子字典加 `npc_directives`（未消费 + 未过期的汇总） |
| `app/narrators.py` | `_format_planner_context()` 添加"未消费指令"段；`_SYSTEM_PROMPT` 中 `direct_npc` 加 kind 枚举和 topic 字段；规则段加第 6、7 条 |

**关键设计决策**：
- `DirectiveTriggerHook` 复用 `_collect_reachable_npcs` / `_get_npc_name`（private_chat_trigger 模块级函数）
- 概率门控：high=0.90 / medium=0.60 / low=0.30（区别于关系驱动的 0.30-0.60）
- cooldown key：`directive_trigger_cooldown_{npc_id}`，4 ticks
- 每 tick 最多 1 个 NPC 邀请（`break` after first triggered）
- 与 `PrivateChatTriggerHook` 职责分离：关系驱动 vs 叙事驱动

**测试**：`tests/test_p19_phase_a.py`（新建，27 个测试）

- `TestAddDirectiveDedup`（4）：dedup同NPC / 保留consumed / 不同NPC共存 / marks_dirty
- `TestDirectiveTriggerHookGuards`（5）：no_slice / no_player / private_chat / no_pending / no_reachable
- `TestDirectiveTriggerHookCore`（9）：高优先级触发 / SSE结构 / cooldown / 跳过consumed / 跳过expired / 每tick最多1 / priority=76 / NPC不可达 / 已注册到defaults
- `TestPlannerContextIncludesPendingDirectives`（5）：context含pending / 排除consumed / 排除expired / format显示 / 无pending不显示
- `TestSystemPromptConstraints`（4）：kind枚举 / topic字段 / 规则6 / 规则7

**测试基线**：1724 passed（+27，3 预已知失败不变）

---

## D-P19bcde — P19 Phase B+C+D+E（2026-03-09）

### Phase E：story_facts relation 约束 + BFS deque 优化

**文件**：`app/narrators.py`、`app/world_knowledge_graph.py`

- `_SYSTEM_PROMPT` 规则 4：relation 枚举明确为 `knows_about / interacted_with / made_promise / related_to / has_opinion_of`
- `_spread_activation_in_graph()`：frontier 从 `list` 改为 `collections.deque`，`pop(0)` → `popleft()`（O(1) vs O(n)）

### Phase B：RecallTool + L6 改造

**文件**：`app/game_core/narrative/character_tools.py`、`app/game_core/narrative/context_builder.py`、`app/game_core/orchestration/npc_interaction.py`、`app/game_core/orchestration/private_chat.py`、`app/agent_orchestration.py`

- **新增 `RecallTool`**：NPC/Teammate 共用工具，执行时从 `context.metadata["memory_retriever"]` 主动查询知识图谱。注册到 `_NPC_TOOLS` 和 `_TEAMMATE_TOOLS`。
- **L6 改为空占位**：`build_npc_context()` 和 `build_teammate_context()` 的 L6 不再调用 `_build_l6()`，改为 `{"hits": [], "source": "recall_tool"}`
- **删除 knowledge_block 预注入**：`_build_npc_prompt_text()` 和 `_build_teammate_prompt_text()` 中删除 knowledge_block 构建逻辑
- **注入 metadata**：`NpcInteractionCoordinator` 和 `PrivateChatCoordinator` 在构建 `AgentContext` 时注入 `memory_retriever` + `world`；`generate_npc_response` 同步更新
- **tool_rules 新增 recall 说明**：NPC 和 Teammate prompt 的 tool_rules 段加 recall 使用提示

### Phase C：32K FIFO 滑动窗口 + 图谱化计数器 + 持久化

**文件**：`app/game_core/narrative/context_window.py`、`app/game_core/narrative/instance_manager.py`、`app/game_core/state/slices/narrative_plan.py`、`app/game_core/runtime.py`、`app/agent_orchestration.py`

- **ContextWindow FIFO**：`max_tokens` 默认 32_768，`add_message()` 自动驱逐最旧消息（FIFO）
- **graphize_counter**：累积 token 计数，`should_graphize` 基于 `graphize_counter >= graphize_threshold`（默认 32_768）
- **collect_for_graphize()**：收集未图谱化消息 + 标记 `is_graphized=True` + 重置计数器；不移除消息
- **pop_oldest_for_graphize() 保留**：兼容 InstanceManager eviction 调用（标记 deprecated）
- **export_messages/import_messages**：完整序列化/反序列化
- **InstanceManager**：`max_tokens_per_instance` 默认改为 32_768；新增 `iter_instances()`
- **NarrativePlanSlice**：新增 `context_windows_data` 字段 + `set_context_windows_data()` 方法
- **runtime.py**：`save_session()` 调用 `_save_context_windows(session)`；`resume_session()` 调用 `_restore_context_windows(runtime)`
- **删除 proactive extraction**：`agent_orchestration.py` 删除 `_collect_npc_interaction_exchange` / `_collect_private_chat_exchange`；proactive path 改为基于 `should_graphize` 触发

### Phase D：Planner 100K 滑动历史窗口

**文件**：`app/narrators.py`

- `AgenticNarrativePlanner` 新增 `_history: list`、`_history_tokens: int`、`_max_history_tokens=100_000`
- `plan()` 将 `_history + [current_msg]` 作为 history 传入 `llm.generate()`；成功解析后调用 `_append_history()`
- `_append_history()`：FIFO 驱逐（每轮 = user+model 2 条），超出 budget 时弹最老轮
- `export_history()` / `import_history()`：供 runtime.py 通过 NarrativePlanSlice 持久化

**关键设计决策**：
- context_windows 持久化走 `NarrativePlanSlice.context_windows_data`（受控例外，与 `actor_knowledge` 同模式），而非独立文件。原因：PersistencePort 接口不暴露文件路径，此方式兼容所有持久化后端
- `graphize_counter` 独立于 FIFO eviction：eviction 减少 `current_tokens`，但不影响 `graphize_counter`，确保图谱化触发不依赖窗口是否满
- `collect_for_graphize()` 不从窗口移除消息，只标记 `is_graphized=True`；FIFO eviction 才移除消息

**测试**：`tests/test_p19_phases_bcde.py`（新建，16 个测试）

| 测试 | Phase |
|------|-------|
| `test_recall_tool_returns_hits` | B |
| `test_recall_tool_no_retriever` | B |
| `test_recall_tool_registered_for_npc_and_teammate` | B |
| `test_npc_prompt_no_knowledge_block` | B |
| `test_context_window_default_32k` | C |
| `test_context_window_fifo_eviction` | C |
| `test_context_window_graphize_counter` | C |
| `test_context_window_collect_for_graphize` | C |
| `test_context_window_export_import_messages` | C |
| `test_graphize_triggered_after_threshold` | C |
| `test_planner_history_append` | D |
| `test_planner_history_fifo_eviction` | D |
| `test_planner_history_export_import` | D |
| `test_planner_plan_includes_history` | D |
| `test_story_facts_relation_enum_in_prompt` | E |
| `test_bfs_uses_deque` | E |

**更新既有测试**：
- `test_character_tools.py`：NPC 工具数 9→10，Teammate 工具数 7→8（+recall）
- `test_context_builder.py`：L6 期望值 `source: "null"` → `"recall_tool"`；knowledge_block 注入测试改为验证无注入行为；retriever call_count 1→0
- `test_context_window.py`：`should_graphize` 相关测试改为用 `graphize_threshold` 参数
- `test_p18_phase1.py`：`TestCollectNpcInteractionExchange` / `TestCollectPrivateChatExchange` 标记 `@pytest.mark.skip`（函数已删除）
- `test_private_chat.py`：`test_overflow_populates_graphize_candidates` 改用 `graphize_threshold=1`

**测试基线**：1732 passed（+16 新，8 skipped，3 预已知失败不变）

---

### [D-P20a] Planner 子系统化基础设施（Phase 1a + 1b）

**日期**：2026-03-09

**目标**：将单体 NarrativePlannerHook 向多子系统架构迁移的第一步：建立调度框架 + read_skill 工具。

#### Phase 1a：调度框架

**新建**：
- `app/game_core/planning/subsystem.py` — `PlannerEvent`、`SubSystemResult` dataclass + `PlannerSubSystem` Protocol + `PlannerDispatcher`（忙碌锁 + _MAX_QUEUE_DEPTH=3 bounded queue + drain）
- `app/game_core/planning/legacy_subsystem.py` — `LegacyDirectiveSubSystem`，包装 `hook._apply_directive` callable，只接受 `tick_settlement` 事件

**修改**：
- `app/game_core/orchestration/hooks/narrative_planner.py`：
  - `__init__` 新增 `dispatcher: PlannerDispatcher | None = None`
  - `_apply_normalized_decision` 用 `apply_fn = dispatcher.apply_directive if dispatcher else self._apply_directive`
  - `execute()` 注入 `__world__`、`__state__`、`__world_id__` 到 planner_context
- `app/game_core/planning/__init__.py`：导出新类型
- `app/game_core/bootstrap.py`：两处 `NarrativePlannerHook` 构建处自动创建 `PlannerDispatcher` + `LegacyDirectiveSubSystem`

**设计决策**：
- dispatcher 接线放在 `bootstrap.py`（而不是 deps.py），原因：factory 只返回 planner 对象，hook 在 bootstrap 中构建；bootstrap 可自由 import game_core/planning/
- `world_id` 通过 planner_context 的 `__world_id__` key 在 plan() 调用时动态解析（`context.world.world_id`），不在 factory 时固定

#### Phase 1b：read_skill 工具

**新建**：
- `app/game_core/adapters/design_skill.py` — `DesignSkillPort` Protocol + `NullDesignSkillPort`
- `app/design_skill_provider.py` — `LocalDesignSkillProvider`（读取 `data/{world_id}/planner_skills/{cat}/{name}.md`；路径遍历防护拒绝含 `..`/`/`/`\` 的段）
- `app/game_core/narrative/planner_tools.py` — `ReadDesignSkillTool`、`ListDesignSkillsTool`、`register_planner_tools()`

**修改**：
- `app/game_core/adapters/__init__.py`：导出 `DesignSkillPort`、`NullDesignSkillPort`
- `app/narrators.py` — `AgenticNarrativePlanner` 新增 `executor`/`design_skill_port`/`world_id` 参数；`plan()` 有 executor 时走多轮 agent，否则降级单轮
- `app/deps.py` — `_build_narrative_planner()` 创建 planner executor + design_skill_port 并注入

**AgenticExecutor planner 角色处理**：text-only response 在 planner 角色下不触发 protocol_error（executor 对 npc/teammate 做强制工具检查，planner 不在此列），最终文本直接返回给 plan() 解析 JSON。

**测试新增**：`tests/test_p20_planner_subsystem.py`，23 个测试，覆盖 1a 7 项 + 1b 9 项

**测试基线**：1755 passed（+23 新，8 skipped，3 预已知失败不变）

### [D-P20b] Directive 子系统剥离（Phase 1c + 1d）

**日期**：2026-03-09

**目标**：将 Hook 的 `_apply_directive()` 中 9 个 directive handler 剥离为 4 个独立子系统，删除 LegacyDirectiveSubSystem。

#### Phase 1c：QuestManagerSubSystem

**新建**：
- `app/game_core/planning/utils.py` — 共享工具函数（coerce_non_empty_string/string_or_empty/normalize_mapping）
- `app/game_core/planning/quest_manager.py` — `QuestManagerSubSystem`（handles: create_quest/publish_bulletin/retire_quest）

**迁移的辅助方法**：_create_milestone_condition_events、_create_objective_events、_objective_to_condition_type、_objective_target_to_params、_coerce_optional_bool、_resident_npcs_for_board

**关键设计**：publish_bulletin 的跨系统 direct_npc 调用通过 `self._dispatcher.apply_directive()` 路由，不再递归调用 hook

#### Phase 1d：剩余 3 子系统 + Legacy 清除

**新建**：
- `app/game_core/planning/npc_director.py` — `NpcDirectorSubSystem`（handles: direct_npc/spawn_quest_npc；持有 instance_manager 引用）
- `app/game_core/planning/world_builder.py` — `WorldBuilderSubSystem`（handles: plant_environmental/fill_area；通过 sse_collector 引用写入 Hook 的 _pending_sse）
- `app/game_core/planning/pacing_controller.py` — `PacingControllerSubSystem`（handles: escalate/adjust_pacing；escalate 走 context.execute_command）

**删除**：
- `app/game_core/planning/legacy_subsystem.py` — 全部 9 种 handles 已分配到 4 个子系统

**修改**：
- `app/game_core/orchestration/hooks/narrative_planner.py`：
  - 删除 `_apply_directive()` 方法及所有已迁移的辅助方法（~500 行净删除）
  - `__init__` 移除 instance_manager/sub_area_manager 参数
  - `_apply_normalized_decision` 移除 self._apply_directive fallback，dispatcher=None 时降级为 noop + 日志
  - 保留：execute()、生命周期方法（_expire_dynamic_quests/_despawn_expired_quest_npcs）、编排方法
- `app/game_core/bootstrap.py`：两处构建点创建 4 个子系统替代 Legacy；注册顺序 QuestManager > NpcDirector > WorldBuilder > PacingController
- `app/game_core/planning/__init__.py`：导出 4 个新类型，移除 LegacyDirectiveSubSystem

**测试新增**：`tests/test_p20_directive_subsystems.py`，31 个测试（QuestManager 8 + NpcDirector 6 + WorldBuilder 4 + PacingController 4 + 集成 3 + 元检查 6）

**测试基线**：1783 passed（+28 新，8 skipped，3 预已知失败不变）

### [D-P20c] NarrativeWeaver + ItemDesigner + 测试修复（Phase 2a-2f）

**日期**：2026-03-09

**目标**：新增 NarrativeWeaverSubSystem 和 ItemDesignerSubSystem，激活 Hook 的 dispatch() 调用链，修复受影响的已有测试，并补齐完整测试覆盖。

#### Phase 2a：NarrativeWeaverSubSystem

**新建**：
- `app/game_core/planning/narrative_weaver.py` — `NarrativeWeaverSubSystem`（纯 evaluate 驱动，handles=frozenset()）
  - `_HANDLES = frozenset()`（不接受任何 directive namespace）
  - `_AUTO_ESCALATION_THRESHOLDS = [4, 7, 10, 13, 16]`，`_FALLBACK_ESCALATION_INTERVAL = 6`
  - `evaluate()` 执行：① Directive GC、② 动态任务过期、③ 临时 NPC 清理、④ 自动升级保障网
  - `_expire_dynamic_quests()` — 从 Hook 迁移（原 lines 974-1073），返回 void，SSE 写入 `self._sse_collector`
  - `_despawn_expired_quest_npcs()` — 从 Hook 迁移（原 lines 1075-1119），同上
  - `_check_auto_escalation()` — 新方法，读 ticks_since_milestone_progress 和 escalation_level，超阈值且未冻结时返回 escalate directive dict

#### Phase 2b：ItemDesignerSubSystem

**新建**：
- `app/game_core/planning/item_designer.py` — `ItemDesignerSubSystem` stub
  - `_HANDLES = frozenset({"design_reward", "curate_shop"})`
  - `evaluate()` 返回空 SubSystemResult
  - `apply_directive()` 记录 debug log，返回 False

#### Phase 2c：Hook 修改

**修改**：
- `app/game_core/orchestration/hooks/narrative_planner.py`：
  - 删除 `_expire_dynamic_quests()` 方法（~100 行）
  - 删除 `_despawn_expired_quest_npcs()` 方法（~45 行）
  - 删除 Hook.execute() 中的 Directive GC 调用 + 两个生命周期调用（~16 行）
  - 新增 dispatch() 调用块（~15 行）：在 ticks_since_milestone_progress 更新之前触发，以防双重升级
  - dispatch 循环取出各子系统 SubSystemResult.directives，通过 `dispatcher.apply_directive()` 路由
  - 末尾再次 drain `self._pending_sse`（SSE 从 weaver evaluate 写入后经此汇入 sse_events）

**关键设计决策**：dispatch 在 ticks 更新前执行，NarrativeWeaver 读到的是原始计数器，PacingController 执行 escalate 后 level 已变，再检查阈值时不会二次触发。

#### Phase 2d：Bootstrap + __init__ 更新

**修改**：
- `app/game_core/bootstrap.py`：在 PacingControllerSubSystem 注册之后追加 NarrativeWeaverSubSystem 和 ItemDesignerSubSystem
- `app/game_core/planning/__init__.py`：导出 NarrativeWeaverSubSystem、ItemDesignerSubSystem

#### Phase 2e：已有测试修复

**修改**：
- `tests/test_p18_phase1.py`：
  - 新增 import：`from app.game_core.planning import NarrativeWeaverSubSystem, ItemDesignerSubSystem`
  - `TestOnExpireEscalateProducesWorldStateChanges` 的 2 个测试：由 `hook._expire_dynamic_quests()` 改为 `NarrativeWeaverSubSystem()._expire_dynamic_quests()`
  - `_make_full_hook_p18()` helper：新增注册 NarrativeWeaverSubSystem + ItemDesignerSubSystem，与 bootstrap.py 保持一致

#### Phase 2f：新测试

**新建**：`tests/test_p20_narrative_weaver.py`，44 个测试，分 9 类：

| 类别 | 测试数 | 覆盖 |
|------|--------|------|
| 协议合规 | 5 | name/handles/accepts_event/apply_directive |
| retire 策略 | 2 | 状态变更 + SSE |
| escalate 策略 | 4 | escalation_level + flag + danger + SSE |
| ignore 策略 | 2 | status=expired，不 escalate |
| 未到期保护 | 2 | tick 不足不过期 + completed 跳过 |
| NPC despawn | 4 | 过期清理 + 临时 NPC 清理 + 未到期保留 + 非 spawn 条目跳过 |
| Directive GC | 2 | consumed/expired 指令清理 |
| 自动升级保障网 | 12 | fires/frozen/below threshold/level1/level3/fallback |
| evaluate 返回 | 2 | directive 包含 escalate / 不含 |
| dispatch 集成 | 2 | Hook.execute 触发生命周期 + 自动升级 directive 应用 |
| ItemDesigner stub | 6 | 协议 + evaluate 空结果 + apply_directive false |
| SSE 到达 HookResult | 2 | event_type 存在 + payload 正确 |

**测试基线**：1827 passed（+46 新：44 新建 + 2 修复，8 skipped，3 预已知失败不变）

---

### [D-P34] update_quest 指令 — 任务面板动态 GPS 导航

**日期**：2026-03-09

**背景**：设计文档 3.4 要求任务面板从"静态状态查询"升级为"动态 GPS 导航"。Planner 在关键节点完成后推送步骤指引（current_step / next_steps / hints）。

**核心决策**：不新建独立 slice，直接在现有 `dynamic_quests` dict 中存储追踪字段（增量合并语义）。

**改动清单**：

| 文件 | 改动 |
|------|------|
| `app/game_core/planning/quest_manager.py` | `_HANDLES` 加 `update_quest`；`__init__` 新增 `sse_collector` 参数；`apply_directive` 路由；新增 `_apply_update_quest` 方法 |
| `app/game_core/orchestration/hooks/narrative_planner.py` | `_SUPPORTED_DIRECTIVES` 加 `update_quest` |
| `app/narrators.py` | 系统 prompt 新增 update_quest 指令说明；`QUEST_MANAGER_AGENT_PROMPT` 更新 |
| `app/game_core/bootstrap.py` | `QuestManagerSubSystem` 构造传 `sse_collector=planner_hook._pending_sse`（两处） |
| `tests/test_34_update_quest.py` | 新建，14 个测试 |

**_apply_update_quest 语义**：
- 前置检查：quest_id 非空、任务存在、status == "active"（非 active 不更新）
- 增量合并：只更新 payload 中提供的字段，不覆盖已有值
- 字段：current_step（str）、next_steps（list[str]）、hints（list[str]）、completed_objectives（list[str]）
- 副作用：`quests._dirty = True` + quest_history 追加 update_quest 记录 + SSE `quest_progress_updated`

**SSE payload 格式**：
```json
{"quest_id": "dq_x", "current_step": "...", "next_steps": ["..."], "hints": ["..."]}
```

**测试覆盖**：

| 分类 | 数量 | 内容 |
|------|------|------|
| _HANDLES / _SUPPORTED_DIRECTIVES 成员 | 2 | 不变量验证 |
| 字段写入 | 3 | current_step / next_steps+hints / completed_objectives |
| 守卫条件 | 4 | non-active / retired / unknown quest / 缺 quest_id |
| 增量合并 | 1 | 只提供 current_step 不覆盖已有 hints |
| SSE | 2 | collector 有内容时 sse 正确 / collector=None 无报错 |
| 副作用 | 2 | quest_history 记录 / dirty flag 置位 |

**测试基线**：1862 passed（+14 新：test_34_update_quest.py）

---

### [D-P35] NPC 职责约束 — tag 通用化 + 真理源注入

**日期**：2026-03-09

**背景**：设计文档 3.5 要求按 NPC tag 注入职责约束 + 真理源数据到 system prompt，防止 LLM 编造不存在的任务/商品。

**核心决策**：在 ContextBuilder 层（`build_npc_full_context` + `build_npc_system_prompt`）提取真理源数据，以 `role_data` 参数传给 `_build_npc_prompt_text`，在 `{directive_block}` 后插入 `{role_block}`。

**改动清单**：

| 文件 | 改动 |
|------|------|
| `data/goblin_slayer/v2/characters.json` | guild_girl tags 加 `"receptionist"` |
| `data/goblin_slayer/v2/tags.json` | general 分类加 `"receptionist"` tag |
| `app/game_core/state/slices/area.py` | 新增 `get_all_board_bulletins(area_id)` 方法 |
| `app/game_core/narrative/context_builder.py` | 新增 `_extract_receptionist_data` / `_extract_merchant_data` / `_extract_role_data` / `_format_role_constraint_block`；`_build_npc_prompt_text` 新增 `role_data` 参数；`build_npc_full_context` + `build_npc_system_prompt` 注入 role_data |
| `tests/test_35_npc_role_constraints.py` | 新建，28 个测试 |

**关键设计决策**：
- `_extract_role_data` 以 tag_set 为分发键，当前支持 `receptionist` 和 `merchant`（预留）
- receptionist 提取：当前区域公告板（`areas.get_all_board_bulletins`）+ 玩家已接任务（`quests.dynamic_quests` status=="active"）
- merchant 提取：`relations.get_shop_state(npc_id)` 库存列表（预留，当前无商人 NPC 数据）
- `role_block` 置于 `directive_block` 之后、`tool_rules` 之前，体现"职责约束比行为指令更优先但比工具规则晚"

**测试覆盖**：

| 分类 | 数量 | 内容 |
|------|------|------|
| 数据验证 | 1 | guild_girl 有 receptionist tag |
| AreaSlice API | 3 | get_all_board_bulletins 返回结构、空 area、防御性拷贝 |
| _extract_receptionist_data | 4 | 结构正确、公告板填充、仅 active 任务、空 area |
| _extract_role_data 分发 | 4 | receptionist / merchant / 无特殊 tag / 空 tag |
| _format_role_constraint_block | 8 | 各 role 格式化、空公告板占位、未知 role 空字符串 |
| _build_npc_prompt_text 注入 | 3 | 无 role_data 无块、有 role_data 有块、顺序（约束块在工具规则前） |
| build_npc_full_context 集成 | 5 | 公告板注入、空公告板占位、非特殊 NPC 无块、active 任务、约束文本 |
| build_npc_system_prompt 集成 | 1 | 柜台 NPC 也注入 role 块 |

**测试基线**：1896 passed（+28 新：test_35_npc_role_constraints.py，+修复 4 个 test_game_data_loader_v2.py 因 tag 缺失导致的失败）

### [D-P36] 边境小镇商店激活

**日期**：2026-03-09

**背景**：设计文档 3.6 要求商店系统跑通（EconomyHandler 已完整，但数据层全空）。主线目标：铁匠铺数据 + _extract_merchant_data bug 修复 + curate_shop 指令激活。

**改动清单**：

| 文件 | 改动 |
|------|------|
| `data/goblin_slayer/v2/characters.json` | 新增 blacksmith 角色（含完整 shop_inventory：base_pool 12 项，rotating_pool 3 项） |
| `data/goblin_slayer/v2/maps.json` | `blacksmith_shop.resident_npcs: []` → `["blacksmith"]` |
| `data/goblin_slayer/v2/tags.json` | general 分类新增 `"craftsman"` 和 `"merchant"` tag |
| `app/game_core/narrative/context_builder.py` | `_extract_merchant_data` bug 修复：`"items"` → `"current_stock"`、`"price"` → `"base_price"`、`"stock"` → `"remaining"`；`_format_role_constraint_block` merchant 分支：stock=None 显示为"无限" |
| `app/game_core/planning/item_designer.py` | 移除 stub，添加构造函数（sse_collector）；实现 `_apply_curate_shop`：add/remove/restock 三操作 + StateChange 写回 + SSE 通知 |
| `app/game_core/orchestration/hooks/narrative_planner.py` | `_SUPPORTED_DIRECTIVES` 新增 `"curate_shop"` |
| `app/game_core/bootstrap.py` | 两处 `ItemDesignerSubSystem()` → `ItemDesignerSubSystem(sse_collector=planner_hook._pending_sse)` |
| `app/narrators.py` | Planner system prompt 新增 curate_shop 指令说明 |
| `tests/test_36_shop_activation.py` | 新建，13 个测试 |

**关键设计决策**：
- `_apply_curate_shop` 只修改已由 EconomyHandler 初始化的 shop state，不主动创建（职责边界）
- NPC 存在性验证：仅在 `has_registry("characters")` 时才检查，否则跳过（测试无注册表场景兼容）
- add_items 的 price_override 优先，其次 item registry base_price，最后 0（防崩溃）
- remaining=None（无限库存）和 remaining=int（有限库存）语义区分保持与 EconomyHandler 一致

**测试覆盖（13 个）**：
- 数据验证：blacksmith_data_loads / blacksmith_in_maps / blacksmith_has_merchant_tag
- curate_shop 操作：add_items / remove_items / restock_items
- 守卫条件：unknown_npc / no_shop_state
- SSE：sse_emitted / no_sse_when_collector_none
- 指令注册：curate_shop_in_supported_directives
- bug 修复验证：merchant_prompt_reads_current_stock / unlimited_stock_display

**测试基线**：1912 passed（+16，含 13 个新测试）

### [D-P23-B] P23 轨道 B：奖励 + NPC 数据 + Views

**日期**：2026-03-11

**问题清单**：W1-1（奖励发放）、W2-4（NPC 真理源深化）、W5-1（面板初始导航）、W5-6（已完成任务摘要）、W6-5（story_facts NPC 注入）、W6-7（receptionist 接取放宽）

**改动清单**：

| 文件 | 改动 |
|------|------|
| `app/game_core/rules/handlers/board.py` | B-1: `_build_reward_changes()` 模块级函数；`_compute_board_complete_quest()` 新增 gold/xp/items StateChange + rewards_claimed + reward_summary metadata |
| `app/game_core/rules/handlers/receptionist.py` | B-1: `_compute_report_quest()` 新增奖励 StateChange + rewards_claimed 防重机制；B-6: validate 放宽 receptionist_accept_quest：quest 不在公告板但 status==available 时仍可接取 |
| `app/game_core/narrative/context_builder.py` | B-2: `_extract_receptionist_data` 追加 objectives/rewards/difficulty；`_extract_merchant_data` 新增 world 参数，追加 name/type/rarity/description；新增 `_danger_label()` 函数；`_extract_guard_data` 追加 danger_label；`_format_role_constraint_block` 对应更新展示格式；B-5: `build_npc_full_context` 注入 story_facts → `_build_npc_prompt_text` 新增 story_facts 参数 → 渲染为 `## Relevant world knowledge` 块 |
| `app/quest_views.py` | B-3: active quest 无 current_step 时从 objectives/summary 自动生成初始导航；B-4: completed quest 注入 completed_summary（rewards + completed_objectives） |

**关键设计决策**：
- 奖励 StateChange 路径：gold/xp 用 `StateChange("player", "set", "gold/xp", new_value)`（增量计算在 handler 层），items 用整个 inventory 快照替换（与 InventoryHandler 一致）
- `_build_reward_changes` 暴露为模块级函数，便于 receptionist handler 复用
- receptionist report 双重防重：检查 `rewards_claimed` flag，成功发奖后设置该 flag
- B-3 fallback 仅在 `current_step is None` 时生效（不覆盖 LLM 设置的值）
- B-5 story_facts cap = 5 条（取最近 N 条），如无 narrative_plan slice 静默跳过

**测试覆盖（21 个新测试 in `tests/test_track_b_rewards_npc_views.py`）**：
- B-1: board_complete grants gold/xp/items/reward_summary/rewards_claimed（4 个）
- B-1: receptionist_report grants rewards / prevents double grant / sets flag（3 个）
- B-2: receptionist prompt objectives/rewards（2 个）；merchant name/type/no-world（2 个）；guard label thresholds（2 个）
- B-3: objectives → current_step；summary fallback；existing not overridden（3 个）
- B-4: completed_summary / active no summary（2 个）
- B-5: story_facts injected / empty state no block（2 个）
- B-6: off-board available accepted（1 个）

**测试基线**：2221 passed（+21 新测试；9 pre-existing failures from Track A changes in working tree）

---

## P23 轨道 C — 商店 + Prompt + 兜底（2026-03-11）

**问题来源**：P23 二次审计 W1-2、W2-3、W5-3、W5-4、W6-2、W6-3

### C-1（W1-2）商店 Bootstrap 初始化

**问题**：`shop_state` 是 lazy init，首次浏览商店时不存在，导致商人无库存。

**修改 `app/game_core/runtime.py`**：
- `bootstrap_opening_planner()` 方法末尾（在 persist 保存之前）调用 `self._bootstrap_shops(session)`
- 新增 `_bootstrap_shops(session)` 方法：遍历 world.characters 所有有 `shop_inventory` 的角色，对无 shop_state 的角色执行 `refresh_shop` Command，走 rules_engine.execute() + state.apply()
- 已有 shop_state 的角色跳过（restored session 保护）

**注意**：直接调 rules_engine.execute() + state.apply() 而非走 tick_coordinator，因为 bootstrap 阶段是受控例外。

### C-2（W2-3）+ C-4（W5-4）Planner Prompt 扩展

**修改 `app/narrators.py`**：
- `AgenticNarrativePlanner._SYSTEM_PROMPT`：新增「设计模板工具」章节（list_design_skills / read_design_skill 说明 + 推荐用法）
- `AgenticNarrativePlanner._SYSTEM_PROMPT`：新增「玩法风格解读」章节（5 种 play_style_tags 行为指引）
- 所有 5 个子系统 prompt（QUEST_MANAGER / NPC_DIRECTOR / WORLD_BUILDER / NARRATIVE_WEAVER / ITEM_DESIGNER）末尾追加「（可选）可通过 list_design_skills / read_design_skill 查阅设计模板。」

### C-3（W5-3）无 LLM 确定性降级

**问题**：无 LLM 时整个 Planner 静默空转，bootstrap 也无法工作。

**修改 `app/deps.py`**：
- 新增 `_build_fallback_planner_system_factory()` 函数（在 `_build_game_runtime()` 之前定义）
- 当 `llm_provider is None` 时，`else` 分支赋值 `planner_system_factory = _build_fallback_planner_system_factory()`
- Fallback Assembly 包含：`OpeningBootstrapQuestAgent`（确定性，处理 bootstrap 事件）+ `_FallbackBlackboard` + 4 个 `_FallbackAgent`（返回空 directives + `reason: "no_llm"`）

**关键设计**：bootstrap 路径不依赖 LLM，所有规划降级为 noop，但不报错。

### C-5（W6-2）旋转库存受控随机

**问题**：旋转库存用 `tick % len(eligible)` 选择，会简单循环，同 NPC 不同 tick 相邻可预测。

**修改 `app/game_core/rules/handlers/economy.py`**：
- 新增 `import hashlib` + `import logging; logger = logging.getLogger(__name__)`
- `_select_rotating_entries()` 新增 `*, npc_id: str = ""` keyword 参数
- 调用方 `_refresh_shop_state()` 传入 `npc_id=npc_id`
- 选择起始位从 `tick % len` 改为 `int(md5(f"{npc_id}:{tick}".encode())[:8], 16) % len`
- 同 npc_id + tick → 结果一致（确定性），不同 tick → 不简单循环

### C-6（W6-3）design_reward SSE + 价格告警

**修改 `app/game_core/planning/item_designer.py`**：
- `_apply_design_reward()` 成功后，当 `_sse_collector is not None` 时追加 `SSEEvent("reward_designed", {"quest_id": ..., "items": [...]})`
- 方法返回值从直接返回 `result.executed` 改为 failure fast（`return False` 后提前退出），再 emit SSE，再 `return True`

**修改 `app/game_core/rules/handlers/economy.py`**：
- `_base_price_for_item()` 回退到 0 之前调用 `logger.warning("item %r has no base_price, defaulting to 0", item_id)`

### 额外修复（非 Track C 范围但必要）

- `narrative_planner.py`：将 `get_approval(npc_id)` / `get_trust(npc_id)` 修正为 `get_disposition(npc_id, "approval")` / `get_disposition(npc_id, "trust")`（RelationSlice 无 get_approval 方法）
- `test_track_a_planner_fullchain.py`（Track A 测试文件）：修正 3 处 bug：RelationSlice.restore 键名错误、冗余 local import 导致 UnboundLocalError、result.error → result.errors
- `test_p20_directive_subsystems.py`：`publish_bulletin` 测试移除 metadata 中的 quest_id（Track A 新增了 quest 存在性校验）

**关键文件**：
| 文件 | 改动 |
|------|------|
| `app/game_core/runtime.py` | C-1: `bootstrap_opening_planner` + `_bootstrap_shops` |
| `app/narrators.py` | C-2/C-4: 主 prompt + 5 个子系统 prompt |
| `app/deps.py` | C-3: `_build_fallback_planner_system_factory` + else 分支 |
| `app/game_core/rules/handlers/economy.py` | C-5: hashlib 种子；C-6: 价格告警 |
| `app/game_core/planning/item_designer.py` | C-6: reward_designed SSE |

**测试覆盖（15 个新测试 in `tests/test_p23_track_c.py`）**：
- C-1: bootstrap 初始化 shop_state / 跳过已有 / 无 characters registry noop（3 个）
- C-2+C-4: prompt 含设计工具说明 / play_style_tags 解读 / 子系统 prompt 含工具注（3 个）
- C-3: fallback assembly 结构 / agent noop / blackboard noop / bootstrap 仍工作（4 个）
- C-5: hash 种子验证 / 同 tick 一致性（2 个）
- C-6: design_reward SSE 发射 / collector=None 不崩 / base_price 告警（3 个）

**测试基线**：2280 passed（Track C +15；Track A bug fix +4；p20 fix +2；7 pre-existing failures）

---

## [S1-07 Phase 1] Planner ContextWindow 迁移（2026-03-11）

**问题**：`AgenticNarrativePlanner` 用 `_history: list[dict]` + `_history_tokens` + `_max_history_tokens` 手动维护 FIFO 历史。这套自建机制与 NPC/Teammate 已有的 ContextWindow + graphize_counter 图谱化机制完全分离，导致：
1. Planner 历史在驱逐后永久丢失（无图谱化沉淀）
2. export/import 用 `{role, text}` legacy 格式，与 ContextWindow 标准格式不兼容

**目标**：将 Planner 的 `_history` 替换为 `ContextWindow` 实例，统一图谱化触发路径（graphize_callback）。

### 改动清单

#### `app/narrators.py` — AgenticNarrativePlanner 重构
- 新增 import：`asyncio`、`ContextWindow`、`WindowMessage`（from `game_core.narrative.context_window`）
- **构造函数**：移除 `_history`、`_history_tokens`、`_max_history_tokens`；新增 `graphize_callback: Callable | None = None` 参数、`_context_window = ContextWindow(actor_id=history_key, max_tokens=100_000, graphize_threshold=100_000)`
- **`_append_history()`**：改用 `_context_window.add_message()` 写入 WindowMessage；`triggered=True` 时 `asyncio.create_task(_graphize_callback(...))`
- **`export_history()`**：返回 `_context_window.export_messages()`（新格式：content/token_count/is_graphized）
- **`import_history()`**：检测 legacy 格式（有 `text` key 且无 `content` key）→ 自动转换；再调 `_context_window.import_messages()`
- **`plan()`**：`conversation_history` 参数改用 `_window_to_planner_history(self._context_window)`（单轮和多轮均适用）

#### 新增 `_window_to_planner_history(window)` 模块级 helper
- 将 ContextWindow 转换为 Gemini history 格式 `[{role, parts:[{text}]}]`
- 与 `agent_orchestration._window_to_history()` 的关键差异：**不跳过** `is_graphized=True` 的消息——Planner 需要完整 FIFO 窗口内容做对话连续性，图谱化标记不影响可见性

#### `app/deps.py` — graphize_callback 注入
- `knowledge_graph = WorldKnowledgeGraph(...)` 独立变量（原来直接传给 `KnowledgeGraphMemoryRetriever`）
- 新增 `_make_graphize_callback(graph)` 工厂函数：返回 async 闭包，调用 `graph.write_episode()`；graph=None 时返回 None
- `_build_planner_system()` 内：`graphize_callback = _make_graphize_callback(knowledge_graph)`，传给全部 6 个 `AgenticNarrativePlanner` 实例

### 兼容设计

- **Legacy 格式向后兼容**：`import_history` 检测 `{role, text}` 格式并自动转换（旧持久化数据无缝迁移）
- **graphize 失败不阻断**：callback 内 `try/except Exception: pass`，图谱化失败不影响规划执行
- **_graphize_callback 为 None 时静默跳过**：无 LLM 时（deps.py 无 graphize_callback）完全无副作用

### 修复的现有测试（3 个）

`tests/test_p19_phases_bcde.py` 中 3 个测试引用了旧 API（`_history`、`_history_tokens`、`export[]["text"]`），已更新为新 ContextWindow API：
- `test_planner_history_append` → 改用 `_context_window.messages`
- `test_planner_history_fifo_eviction` → 改用 `_context_window.max_tokens`
- `test_planner_history_export_import` → 改用 `exported[0]["content"]`（新格式）

### 新增测试（14 个）：`tests/test_s107_planner_graphize.py`

1. `_append_history` 写入 ContextWindow（role + content 正确）
2. token_count 最小值为 1
3. plan() 成功后 ContextWindow 有 2 条消息
4. parse 失败时 ContextWindow 不写入
5. export/import round-trip（新格式）
6. export 格式有 `content` key（无 `text` key）
7. legacy `{role, text}` 格式 import 转换正确
8. import([]) 不清空已有消息
9. graphize_callback 在阈值触发时被调用
10. callback=None 时无报错
11. plan() 第二轮时传入上一轮 history
12. `_window_to_planner_history` 包含 is_graphized=True 的消息
13. FIFO eviction 在超 max_tokens 时驱逐最旧消息
14. AgenticNarrativePlanner 的 ContextWindow 使用 100K 限制

**测试基线**：2330 passed（+14 新测试，+3 修复现有测试，零回归）

---

## [D-S107-P2P3] GM ContextWindow + Planner 长期记忆检索（Phase 2+3）

**日期**：2026-03-12
**计划文件**：`/home/xiaokebuyu/.claude/plans/cozy-dazzling-locket.md`

### Phase 2：AgenticGmNarrator ContextWindow 集成

**改动文件**：`app/narrators.py`、`app/deps.py`、`app/game_core/runtime.py`

**narrators.py** — `AgenticGmNarrator` 扩展：

- 构造函数新增 `graphize_callback: Callable | None = None` 参数
- 新增字段：`_graphize_callback`，`_context_window = ContextWindow(actor_id="__gm__", max_tokens=32_768, graphize_threshold=32_768)`
- `compose()` 修改：
  - 构建 `user_message` 后立即写入 ContextWindow（user 消息）
  - 调用 `run_agentic()` 时传入 `conversation_history=_window_to_planner_history(self._context_window)`（复用 Phase 1 helper）
  - LLM 返回后写入 model 消息；若触发 threshold → `collect_for_graphize()` → `asyncio.create_task(callback("__gm__", messages))`
- 新增 `export_history() / import_history()` 持久化接口（无需 legacy 转换——GM 历史是全新格式）

**deps.py** — `_build_gm_narrator()` 传入 `graphize_callback`：

- 在 `gm_narrator_factory` 闭包内捕获 `_gm_graph = knowledge_graph`
- 每次 `_build_gm_narrator()` 调用时构造新的 `graphize_callback = _make_graphize_callback(_gm_graph)` 传给 `AgenticGmNarrator`

**runtime.py** — GM 历史持久化：

- 新增静态方法 `_find_gm_narration_hook(session)` — 遍历 `settlement_hooks` 找 `GmNarrationHook` 实例（与 `_find_narrative_planner_hook` 同模式）
- 新增 `GmNarrationHook` import（从 `app.game_core.orchestration.hooks.gm_narration`）
- `_save_context_windows()`：在 planner history 保存后追加 GM narrator history（key `"__gm__"`），通过 `gm_hook._narrator.export_history()` 读取
- `_restore_context_windows()`：从 `history_payloads["__gm__"]` 取出 GM history，找 `GmNarrationHook` 后调用 `hook._narrator.import_history(payload)`；主循环里跳过 `"__gm__"` key 防重复

### Phase 3：AgenticNarrativePlanner 长期记忆检索

**改动文件**：`app/narrators.py`、`app/deps.py`

**narrators.py** — 新增 helper + 修改 `plan()` + formatter：

- 新增 `_extract_planner_keywords(context)` 模块级 helper：
  - 从 `narrative_plan.current_target_milestone` 提取里程碑 ID
  - 从 `area_npcs` 提取 NPC ID（dict 用 `id` key，其他 str 化）
  - 从 `quests.dynamic_quests`（dict 或 list 两种格式）提取 quest ID
  - 上限 10 个关键词
- `AgenticNarrativePlanner.__init__()` 新增 `memory_retriever: Any = None` 参数
- `plan()` 在调用 `_context_formatter` 之前：
  - 若有 `memory_retriever` 且关键词非空 → await `retrieve(actor_id=history_key, keywords, context={"world": ...})`
  - 取 `hits[:5]`（cap=5）写入 `context["__long_term_memory__"]`
  - retrieve 失败 → debug log + 跳过（不影响 plan 主流程）
- `_format_planner_context()` 新增 `## 长期记忆` 区块：当 `ctx["__long_term_memory__"]` 存在时渲染 label + activation + description

**deps.py** — `_build_planner_system()` 对全部 6 个 `AgenticNarrativePlanner` 实例追加 `memory_retriever=memory_retriever`

### 关键设计决策

- **retrieve() 是 async** — 用 `await` 直接调用（plan() 已是 async）
- **context 浅拷贝** — `context = dict(context)` 保护原始 dict，不修改调用方的数据
- **graphize 不影响 FIFO 可见性** — Phase 1 已确立：graphize 仅触发图谱写入，不从 conversation_history 中过滤消息
- **GM history 的 key 命名**：`"__gm__"` 以 `__` 开头，与 planner 系列（`__planner_blackboard__` 等）对齐，不与 NPC window（无 `__` 前缀）冲突
- **_restore_context_windows 跳过 `"__gm__"`**：防止 GM key 被误投递给 NarrativePlannerHook 的 `participants.get("__gm__")` 返回 None 而静默跳过

### 新增测试（22 个）：`tests/test_s107_gm_graphize.py`

**Phase 2（GM，10 个）**：
1. GM narrator 有 ContextWindow（32K 限制）
2. compose() 写入 user + model 消息
3. 多次 compose() 累积 history
4. 第二次 compose() 传入 conversation_history 给 executor
5. export/import round-trip
6. 无 history 时 export 返回 []
7. import([]) 是 noop
8. threshold 触发时 graphize_callback 被调用
9. callback=None 时无报错
10. executor 抛异常时返回 status=llm_error

**Phase 3（Planner 记忆，12 个）**：
11. `_extract_planner_keywords` 提取里程碑 ID
12. `_extract_planner_keywords` 提取 NPC ID
13. `_extract_planner_keywords` 提取 quest ID
14. `_extract_planner_keywords` 上限 10
15. `_extract_planner_keywords` 空 context 返回 []
16. plan() 调用 retrieve() 并注入 `__long_term_memory__`
17. 无关键词时不调用 retrieve()
18. memory_retriever=None 时正常工作
19. retrieve 结果 cap=5
20. `_format_planner_context` 渲染 `## 长期记忆`
21. 无 `__long_term_memory__` 时不渲染该区块
22. retrieve() 抛异常时 plan() 仍返回正常结果

**测试基线**：2352 passed（+22 新测试，零回归）

---

### [D-P25-Phase1] P25 稳定性基础（P25-03/04/07）

**日期**：2026-03-12

#### P25-03: Gemini 空响应防护

**文件**：`app/llm_gemini.py` — `_parse_response()`

- 在 `for candidate in response.candidates:` 前新增守卫：`if not response.candidates:` → 立即返回 `LlmResponse(finish_reason="error", metadata={"error": "empty_candidates"})`
- 追加内层守卫：`if not candidate.content or not candidate.content.parts: continue`（跳过无内容候选）
- 覆盖场景：安全过滤、服务过载、超时等 Gemini 返回空 candidates 的情况

#### P25-04: 诊断日志增强

**文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

- `missing_dispatcher` 警告：改用 `%s` 格式参数（`kind=%s, subsystem=%s`）替代 `extra=` dict，确保 kind/subsystem 出现在消息文本中
- `dispatcher_rejected` 警告：同上

#### P25-07: Planner 语言约束

**文件**：`app/narrators.py` — `AgenticNarrativePlanner._SYSTEM_PROMPT`

- 在 `## 可用指令` 之前追加 `## 语言与格式` 段落
- 约束：面向玩家的文本（title/summary/objective 描述/bulletin）必须使用中文；内部标识符（quest_id/npc_id 等）保持英文 snake_case

**测试**：3 个新测试（`tests/test_p25_phase1_and_2_1.py`）

---

### [D-P25-Phase2-1] P25-01: area_ids 补全

**日期**：2026-03-12

#### Step 1: 数据收集

**文件**：`app/game_core/orchestration/hooks/narrative_planner.py` — `_build_planner_context()`

- 新增 `all_area_ids: list[str]` — 在有 areas slice 时收集 `context.state.areas.areas.keys()`
- 新增 `current_sub_area_ids: list[str]` — 收集当前 area 的静态子地点（from `MapRegistry.get(area_id).sub_locations.keys()`）和动态临时子地点（from `area_state.temporary_sub_areas`）
- 两个字段追加到返回 dict

#### Step 2: 渲染到 prompt

**文件**：`app/narrators.py` — `_format_planner_context()`

- 在 "Allowed board ids:" 之后渲染 "Allowed area_ids: ..." 和 "Sub-areas for {area_id}: ..." 两行
- 两者缺失时不渲染（空列表条件保护）

**关键设计决策**：
- `all_area_ids` 在外层 `if context.state.has_slice("areas")` 块内收集（利用已有的 areas slice guard）
- `current_sub_area_ids` 与 MapRegistry 交互复用已有的 `context.world.has_registry("maps")` guard，避免重复守卫
- 动态子地点遍历兼容 Mapping 类型（与 AreaState.temporary_sub_areas 的 `list[dict[str, Any]]` 类型契约一致）

**测试**：6 个新测试（`tests/test_p25_phase1_and_2_1.py`）

**测试基线**：2380 passed（+9 新测试，零回归）

---

### [D-P25-Phase2-2] P25-02: 反馈链修复

**日期**：2026-03-12

#### Step 1: Dispatcher 返回拒绝原因

**文件**：`app/game_core/planning/subsystem.py`

- `PlannerSubSystem` Protocol：`apply_directive` 返回类型从 `bool` 改为 `bool | str`（str = 拒绝原因字符串）
- `PlannerDispatcher.apply_directive`：返回类型同步改为 `bool | str`，具体原因：
  - `"cyclic_call_blocked"` — 循环调用防护
  - `"depth_overflow"` — 调用深度超限
  - `"no_handler_for_kind"` — 无子系统处理该 kind
  - 子系统返回值透传（可能是 `True` 或 reason string）

**5 个子系统文件同步**（`app/game_core/planning/`）：

- `quest_manager.py`：`apply_directive` + `_apply_create_quest` / `_apply_publish_bulletin` / `_apply_retire_quest` / `_apply_update_quest` 均改为 `bool | str`；失败时返回 `"; ".join(result.errors) or "command_failed"`；`_apply_publish_bulletin` 的 board/area 缺失返回 `"missing_board_or_area_id"`
- `npc_director.py`：`apply_directive` + `_apply_direct_npc` / `_apply_spawn_quest_npc` 改为 `bool | str`；`_apply_direct_npc` 的 npc/directive 元数据缺失返回 `"missing_npc_or_directive_in_metadata"`
- `world_builder.py`：`apply_directive` + 3 个 handler 改为 `bool | str`；area/sub_area 元数据缺失返回 `"missing_area_or_sub_area_id"` 或 `"missing_area_or_sub_area_or_entry"`
- `pacing_controller.py`：`apply_directive` + `_apply_escalate` / `_apply_adjust_pacing` 改为 `bool | str`
- `item_designer.py`：`apply_directive` + `_apply_design_reward` / `_apply_curate_shop` 改为 `bool | str`；日志记录改为先提取原因字符串再记录

**注意**：`narrative_weaver.py` 的 `apply_directive` 仍返回 `False`（无 directive 命名空间，`bool` 是 `bool | str` 的子集，保持语义一致）

#### Step 2: 捕获拒绝原因到 audit

**文件**：`app/game_core/orchestration/hooks/narrative_planner.py` — `_apply_directive_batch()`

- 改 `if not applied:` 为 `if applied is not True:`（修复空字符串误判）
- 新增 `reason_code = applied if isinstance(applied, str) else "dispatcher_rejected"`
- audit entry 的 `reason_code` 字段从硬编码 `"dispatcher_rejected"` 改为实际原因

#### Step 3: 渲染 previous_directive_results

**文件**：`app/narrators.py` — `_format_planner_context()`

- 在 story_facts 段落之后追加 `## 上轮指令执行反馈` 区块
- 渲染格式：`  - {kind}: {status}` 可选追加 `({reason_code})`
- 末尾追加 `请根据以上反馈调整本轮指令，避免重复相同错误。`
- `previous_directive_results` 为空时不渲染该区块

**关键设计决策**：
- 返回 `str` 而不是 `False` 使 planner audit 能捕获具体失败原因，下一轮 planner 可据此调整
- `applied is not True` 判断正确处理字符串（非空字符串是 truthy）和 `False` 两种拒绝情形
- 不破坏 `narrative_weaver.py` 的 `False` 返回——`False` 仍然不是 `True`，逻辑正确

**测试**：6 个新测试 + 15 个现有测试更新（`tests/test_p25_phase1_and_2_1.py` + 8 个已有测试文件）

**测试基线**：2386 passed（+6 新测试，零回归）

### [D-P29-A9a] NarrativePlanSlice 里程碑大纲扩展

**日期**：2026-03-13

**设计背景**：P29-A9 里程碑大纲子系统。Planner 在发布任务前先为当前里程碑生成 5-10 步叙事大纲，通过持久化存储在 NarrativePlanSlice 中，后续 A9b/A9c/A9d/A9e 在此基础上构建生成逻辑和上下文注入。

**变更文件**：
- `app/game_core/state/slices/narrative_plan.py` — 新增 `milestone_outline: dict[str, Any]` 字段 + 3 个方法 + `apply_state_change` 两条路径 + `restore()`/`snapshot()` 覆盖

**关键 API**：
- `set_milestone_outline(outline)` — 完整替换大纲，对 `steps` 列表做深度防御性拷贝，忽略非 Mapping 输入
- `mark_outline_step_completed(step_index)` — 按 `index` 字段查找步骤并标记 `completed=True`，静默忽略未知 index
- `get_current_outline_step()` — 返回第一个 `completed=False` 步骤的防御性拷贝，全部完成或无大纲时返回 None

**大纲结构**：
```json
{
    "target_milestone_id": "str",
    "chapter_id": "str",
    "computed_at_tick": 0,
    "steps": [
        {
            "index": 0,
            "description": "str",
            "type": "str",
            "condition": {"type": "...", ...},
            "related_npcs": [],
            "related_locations": [],
            "completed": false,
            "quest_id": null
        }
    ]
}
```

**`apply_state_change` 路径**：
- `path="milestone_outline"` + value 为 Mapping → 调用 `set_milestone_outline()`
- `path="milestone_outline.step_completed"` + value 为整数 → 调用 `mark_outline_step_completed(int(value))`

**防御性设计**：
- `snapshot()` 通过 `_snapshot_milestone_outline()` 对 steps 做深拷贝，避免外部 dict 泄漏内部引用
- `restore()` 对非 Mapping 值 fallback 为 `{}`（注意：steps 内的子 dict 目前仅做浅拷贝 `dict(raw_outline)`；如需完整深拷贝可在后续深化中升级 restore 路径）

**关键设计决策**：
- `_snapshot_milestone_outline()` 作为私有辅助方法而非直接内联，是为了保持 `snapshot()` 方法的可读性，与现有模式一致
- `mark_outline_step_completed` 通过 `index` 字段匹配（而非列表下标），因为步骤可能被过滤/重排，用 index 字段更健壮
- `apply_state_change` 中 `milestone_outline.step_completed` 不检查 Mapping 类型（value 是整数），与 `milestone_outline` 路径的 Mapping 检查不同

**新增测试**：
- `tests/test_p29_a9a_milestone_outline.py` — 22 个测试：初始状态、set/mark/get 各场景（含边界）、snapshot/restore 往返、apply_state_change 两条路径

**测试基线**：2801 passed（+22 新测试，零回归）

---

### [D-P29-A9b] MilestoneOutlineGenerator — LLM 大纲生成 + 确定性降级

**实施日期**：2026-03-13

**设计背景**：P29-A9b（含 A9f）。Planner 在发布任务前先为当前里程碑生成 5-10 步叙事大纲，大纲结构兼容 NarrativePlanSlice.set_milestone_outline()。包含无 LLM 确定性降级：从 key_elements 直接生成简化大纲。

**修改文件**：
- `app/narrators.py` — 新增 `_MILESTONE_OUTLINE_SYSTEM_PROMPT` 常量 + `_build_fallback_outline()` 模块级辅助函数 + `MilestoneOutlineGenerator` 类

**MilestoneOutlineGenerator 设计**：
- 构造：`MilestoneOutlineGenerator(llm=None)` — llm 可选注入，None 时走降级
- 主方法：`async generate(*, milestone_template, target_milestone_id, chapter_id, current_tick, game_state_summary=None, supported_condition_types=None)` → `dict`
- LLM 调用：单射 `llm.generate(system_prompt, history, [])` — 无工具，纯 JSON 输出
- `_build_user_message()` 构建结构化 prompt（里程碑信息 + supported_condition_types）
- `_parse_response()` 解析 + 归一化，自动剥去 markdown 代码块包装

**确定性降级（A9f）**：`_build_fallback_outline()`：
- 每个 key_element → 一个 step（max 10，min 1；空时填"完成里程碑目标"）
- 第一步注入 involved_npcs[:2] + involved_locations[:1]
- 条件类型：flag_set，flag_name = step_{i}_done

**关键设计决策**：
- 无状态类 — 每次 generate() 独立，无历史 window，与 AgenticNarrativePlanner 有状态模式不同
- llm=None 直接返回 fallback，零外部依赖
- `_build_fallback_outline` 为模块级函数（非私有方法），方便后续 A9c/A9d 独立调用
- LLM 失败路径：exception 或 JSON 解析失败 → debug log + 返回 fallback，调用方始终得到可用大纲

**新增测试**：
- `tests/test_p29_a9b_milestone_outline_generator.py` — 25 个测试：fallback 逻辑 8个 / 无 LLM 2个 / LLM 成功 3个 / LLM 失败路径 5个 / 用户消息内容 5个 / NarrativePlanSlice 兼容性 2个

**测试基线**：2876 passed（+25 新测试，零回归）

---

### [D-P29-A7] A7：私聊 Co-location 修复

**问题**：NPC 可跨子地点/房间发起私聊请求；玩家也可对不在同一位置的 NPC 发起私聊。

**变更文件**：
- `app/game_core/orchestration/hooks/private_chat_trigger.py`
  - `_collect_reachable_npcs()` 返回类型从 `set[str]` 改为 `tuple[set[str], dict[str, str | None]]`：
    - 第一项 `colocated_npc_ids`：真正与玩家同子地点（含 room 维度）的 NPC；party 成员始终算 colocated
    - 第二项 `area_npc_locations`：所在区域全部 NPC 的 `npc_id → sub_location` 映射
  - `execute()` 中改为遍历 `all_area_npc_ids`（colocated + area 全集），不同位置的 NPC 触发兴趣度检查后仍发 SSE，但 payload 含 `colocated: false` + `npc_location` 字段
  - 同位置 NPC 的 SSE payload 含 `colocated: true`，无 `npc_location` 字段
- `app/game_core/orchestration/hooks/directive_trigger.py`
  - 修复 `_collect_reachable_npcs()` 返回类型变更导致的破坏性引用
  - 改为 `colocated_npc_ids, area_npc_locations = _collect_reachable_npcs(context)` + `all_area_npc_ids = colocated_npc_ids | set(area_npc_locations.keys())`
- `app/utterance_orchestration.py`
  - A7c：`scope == "private"` 分支在调用 `run_private_chat()` 之前加 `validate_presence()` 校验
  - 不共位 → 返回 `interaction_rejected` SSEEvent，`reason="interaction_rejected"`，agent 不调用

**关键设计决策**：
- `_collect_reachable_npcs` 拆分 colocated vs. area 两个集合，让 Hook 既能判断"是否能立即私聊"（colocated），又能让跨位 NPC 出现在兴趣度筛选中（所有区域 NPC 均有资格触发 npc_wants_to_chat，但带 colocated=false 提示）
- DirectiveTriggerHook 行为保持不变（接受区域内任意 NPC 的 directive），只修改调用接口适配新返回类型
- utterance_orchestration A7c 的 validate_presence 复用了已有的 `_validate_npc_presence` 路径，包含 area 匹配 + sub_location co-location 检查

**新增测试**：
- `tests/test_private_chat_trigger.py` — `TestA7CoLocationFilter`（5 个）：colocated=true / colocated=false+npc_location / party 始终 colocated / 混合场景 / 双 None 场景
- `tests/test_a7_private_chat_colocation.py` — `TestA7cPrivateChatPresenceValidation`（5 个）：不同区域被拦截 / 不同子地点被拦截 / 同位可聊 / 双 None 区域根可聊 / 拒绝事件含 npc_id

**测试基线**：2835 passed，8 pre-existing failures（A1/A2 相关，不在 A7 范围）

---

### [D-P29-A5e] interactable 数据管线修复

**实施日期**：2026-03-13

**问题背景**：Planner 生成的动态子地点包含 interactables 字段（dict 对象列表，含 id/name/description/type/tags/checks），但三处 string normalization 函数用 `str()` 或 `coerce_non_empty_string()` 把 dict 压扁成字符串表示，导致下游 `InteractableHandler._find_dynamic_interactable()` 无法按 id 匹配。

**变更文件**：

- `app/game_core/planning/dynamic_sub_area.py`
  - 新增 `_normalize_interactables()` 类方法：保留 dict 元素不 stringify，兼容 plain string 元素
  - `create()` 中 `interactables` 字段改用 `_normalize_interactables()` 替代 `_normalize_sequence()`
  - `tags`、`resident_npcs` 等字段仍使用 `_normalize_sequence()`（不受影响）

- `app/game_core/planning/directive_contracts.py`
  - 新增模块级函数 `_normalize_interactable_list()`：同样保留 dict，兼容 string
  - `_normalize_legacy_fill_area_entry()` 中 `interactables` 字段改用 `_normalize_interactable_list()` 替代 `_normalize_string_list()`

- `app/game_core/rules/handlers/planner.py`
  - 新增模块级函数 `_normalize_interactable_list()`（与 dynamic_sub_area 保持同等逻辑）
  - `_compute_sub_area_command()` 中 `interactables` 字段改用 `_normalize_interactable_list()` 替代 `_normalize_string_list()`
  - `resident_npcs`、`tags` 等字段仍使用 `_normalize_string_list()`（只改 interactables）

- `app/scene_views.py`（`build_location_overview()`）
  - 已有逻辑：从 `area_template.sub_locations[current_location_id].interactables` 读取静态 interactables（dataclass 对象）
  - 新增 fallback 分支：若当前位置是 temporary sub-area（area_template 中无匹配静态子地点），则从 `area_state.temporary_sub_areas` 中找到对应 sub_area，读取其 `interactables` list
  - Dict 元素：按 id/name/description/tags/checks 提取为标准 overview 格式（`requires_check = bool(iact.get("checks"))`）
  - Plain string 元素：生成最小化 interactable 记录（向后兼容）

**关键设计决策**：
- fallback 分支设计为 `if not interactables`：只在静态模板没有 interactables 时（即玩家确实在动态子地点）触发，不会双重叠加
- `InteractableHandler._find_dynamic_interactable()` 已正确按 dict 格式处理，本次修复后 dict 能到达它

**新增测试**：`tests/test_p29_a5e_interactable_pipeline.py`（8 个）：
- `test_dynamic_sub_area_manager_preserves_interactable_dicts`：DynamicSubAreaManager 保留 dict
- `test_dynamic_sub_area_manager_preserves_string_interactables`：plain string 仍工作
- `test_dynamic_sub_area_manager_preserves_mixed_interactables`：混合列表
- `test_normalize_legacy_fill_area_entry_preserves_interactable_dicts`：directive_contracts 保留 dict
- `test_normalize_legacy_fill_area_entry_string_interactables_still_work`：legacy string 不回归
- `test_build_location_overview_reads_interactables_from_temporary_sub_area`：dict interactables 出现在 overview
- `test_build_location_overview_reads_string_interactables_from_temporary_sub_area`：string 形式也工作
- `test_build_location_overview_no_interactables_when_not_in_temp_sub_area`：不在动态子地点时列表为空

**测试基线**：8 passed（本次新增测试全部通过，pre-existing failures 与本次修改无关）

---

### [D-P29-A5a/b/d+A5e-hook+A6b/c+A8a/d+A9c/d] NarrativePlannerHook 上下文增强

**实施日期**：2026-03-13

**目标**：在 `_build_planner_context()` 中注入更丰富的上下文信息供 LLM Planner 决策，并接入里程碑大纲子系统。

**变更文件**：`app/game_core/orchestration/hooks/narrative_planner.py`

**A5a — area_cluster 容量细化**：
- 移除 `has_capacity: bool`，改为 `remaining_permanent: int`（最多 8 个永久动态子地点，当前剩余）+ `remaining_total: int`（最多 15 个总计，当前剩余）
- 保留 `total_dynamic` 字段不变

**A5b — 显式 NPC 列表**：
- 新增 `existing_npc_ids: list[str]` —— 当前区域的运行时 `npc_locations` 键 + 静态 `sub_locations.resident_npcs` 合并去重排序
- 与 `area_npcs`（运行时专属）并存，便于 Planner 不重复放置已存在 NPC

**A5d — 反馈回路增强**：
- `previous_directive_results` 每条记录新增 `entity_id` 字段：从 `payload_digest` 中提取 `entity_id / npc_id / quest_id`（首个非 None 值）

**A5e（hook 端）— interactable 示例注入**：
- 新增 `interactable_examples: list[dict]`：从当前区域静态子地点中提取至多 3 个完整 interactable 对象（id/name/description/type/tags/checks 序列化）
- 新增 `fill_area_guidance: str`：明确告知 Planner fill_area 的 interactables 需含完整结构

**A6b/A6c — hostile_config 上下文 + encounter 引导**：
- 新增 `hostile_config_locations: list[dict]`：遍历当前区域静态子地点的 `hostile_config` 字段，每条包含 sub_location_id/sub_location_name/monster_ids/stealth_dc/blocking/already_planted/already_cleared
- `already_planted` 判断：`get_hostile_state(sub_id)` 存在且 status != "cleared"
- `already_cleared` 判断：`get_hostile_state(sub_id)` 存在且 status == "cleared"
- 新增 `encounter_guidance: str`：引导 Planner 对 `already_planted=false, already_cleared=false` 条目使用 `planner_plant_encounter`

**A8a — 支持的 objective 条件类型**：
- 新增 `supported_objective_conditions: list[dict]`（5 种类型：npc_talked/item_obtained/kill_count/location_entered/flag_set，每个含 type/params/desc）
- 新增 `quest_objective_guidance: str`：强调每个 objective 必须含 condition 字段

**A8d — active_quests 详情**：
- 新增 `active_quests: list[dict]`：遍历状态为 in_progress/active/accepted 的动态任务，每条包含 quest_id/title/objectives（含 completed 和 condition）
- 对比旧 `dynamic_quests` 字段（全量），此字段专注 active 进度便于 Planner 判断任务进展

**A9c — 里程碑大纲触发（execute）**：
- 在 `execute()` 的冷却检查通过后、`_run_replay` 之前，调用 `_ensure_milestone_outline()`
- 检查 `current_target_milestone` 与 `milestone_outline.target_milestone_id` 是否一致
- 不一致时：若 `_outline_generator` 注入则调用其 `generate()`；否则调用 `_build_fallback_outline_from_template()`（A9f 确定性降级）
- 异常捕获：LLM 失败后回退到 deterministic fallback

**A9d — Planner Context 注入大纲**：
- 新增 `milestone_outline: dict | None`：当 `NarrativePlanSlice.milestone_outline` 存在时注入，包含 target/steps/current_step（第一个未完成步骤）
- 新增 `outline_guidance: str | None`：仅在有大纲时注入，引导 Planner 关联 step_index

**新增协议**：
- `MilestoneOutlineGeneratorPort(Protocol)` — 在 hook 内定义（避免跨层 import），签名匹配 `MilestoneOutlineGenerator.generate()`
- `NarrativePlannerHook.__init__` 新增 `outline_generator: MilestoneOutlineGeneratorPort | None = None` 可选参数

**新增私有方法**：
- `_ensure_milestone_outline(context, *, current_tick)` — A9c 触发逻辑
- `_build_outline_context(context, target_milestone_id, current_tick)` — 构建大纲生成输入
- `_build_fallback_outline_from_template(*, milestone_template, target_milestone_id, chapter_id, current_tick)` — A9f 确定性降级，one-step-per-key-element

**新增测试**（`tests/test_narrative_planner_hook.py::TestP29ContextEnhancements`，10 个）：
- `test_area_cluster_has_remaining_capacity_fields` — A5a
- `test_existing_npc_ids_combines_runtime_and_static_residents` — A5b
- `test_previous_directive_results_includes_entity_id` — A5d
- `test_interactable_examples_extracted_from_static_sub_locations` — A5e
- `test_hostile_config_locations_populated_for_area_with_hostile_sublocs` — A6b/c
- `test_supported_objective_conditions_present_with_required_types` — A8a
- `test_active_quests_contains_objectives_with_condition_status` — A8d
- `test_ensure_milestone_outline_generates_fallback_without_outline_generator` — A9c fallback
- `test_ensure_milestone_outline_skips_if_outline_already_matches` — A9c skip
- `test_build_planner_context_includes_milestone_outline_when_set` — A9d

**测试基线**：2908 passed, 8 skipped（pre-existing），全量通过

---

## D-P30：接通 current_target_milestone 完整链路（2026-03-13）

**根因**：`current_target_milestone` 从未被自动设置（始终为 null），导致 A9a-A9f 里程碑大纲子系统全部空转。

三层断裂：
1. `planner_create_quest` handler 激活 milestone 但不设置 `current_target_milestone`
2. `NarrativePlannerHook.execute()` 在 `_ensure_milestone_outline` 之前没有自动选目标
3. Bootstrap 路径同样缺少自动选目标逻辑

**Fix 1**（`app/game_core/rules/handlers/planner.py`）：
- `_compute_create_quest()` 在 milestone AVAILABLE→ACTIVE 激活后，若 `narrative_plan.current_target_milestone` 为 null，追加 `StateChange("narrative_plan", "set", "current_target_milestone", target_milestone)`
- 只在 null 时设置，避免覆盖 Planner 正在追踪的其他 milestone

**Fix 2**（`app/game_core/orchestration/hooks/narrative_planner.py`）：
- 新增 `_auto_select_target_milestone(context)` 方法：
  - 当前 target 仍然 ACTIVE → no-op
  - 否则，扫描所有 ACTIVE/AVAILABLE milestone，按 `(priority, sequence, ms_id)` 排序选最佳
  - ACTIVE 优先（priority=0），AVAILABLE 次之（priority=1），同类按 `MilestoneTemplate.sequence` 排
  - 直接调用 `state.narrative_plan.set_target_milestone(best_id)`（受控内部写入）
- `execute()` 在 `_ensure_milestone_outline()` 之前调用 `_auto_select_target_milestone(context)`
- `bootstrap()` 在 `_run_replay()` 完成后（replay 可能已 apply planner_create_quest）防御性调用一次

**新增测试**（`tests/test_p30_milestone_target_chain.py`，9 个）：
- `test_create_quest_sets_target_milestone` — Fix 1 正向路径
- `test_create_quest_does_not_override_existing_target` — Fix 1 保护既有 target
- `test_auto_select_picks_active_over_available` — ACTIVE 优先策略
- `test_auto_select_uses_sequence_ordering` — sequence 排序
- `test_auto_select_advances_after_completion` — 完成后推进到下一个 AVAILABLE
- `test_auto_select_noop_when_target_active` — 当前 target 仍 ACTIVE 时不变
- `test_auto_select_noop_when_no_candidates` — 无候选时保持 null
- `test_execute_calls_auto_select_and_sets_target` — execute() 调用链验证
- `test_bootstrap_sets_target_after_replay` — bootstrap() 调用链验证

**测试基线**：2917 passed, 8 skipped（pre-existing），全量通过

---

## D-P31 Phase 2+3：Planner prompt/context 修复 + 代码修复 + 图片竞态修复（2026-03-13）

**背景**：P30 接通 current_target_milestone 后，live 测试暴露大量运行时问题。本次修复 Phase 2（问题 2+3+7+8a-8f）和 Phase 3（问题 5+9）。

### 代码修复（问题 2）

**文件**: `app/game_core/orchestration/hooks/private_chat_trigger.py:218`

**根因**：`all_area_npc_ids = set(area_npc_locations.keys())` 将整个 area 所有 NPC 都纳入触发候选。非共位 NPC 会发出 `npc_wants_to_chat` SSE，但玩家无法响应（NPC 不在场），导致 "npc_not_present" 报错。

**修复**：改为 `set(colocated_npc_ids)`，只有与玩家同子地点的 NPC 和队友才参与触发判定。

**测试更新**（`tests/test_private_chat_trigger.py`）：
- 默认 `area_npc_locations` 从 `"npc_area_spot"` 改为 `player_location`（colocated）
- `test_noncolocated_npc_event_has_colocated_false_and_location` → 重命名为 `test_noncolocated_npc_does_not_trigger`（改为断言 0 个事件）
- `test_mixed_colocated_and_noncolocated_npcs` → 改为断言只有 colocated NPC 触发
- `test_npc_scene_filter_only_reachable_npcs` → 更新期望：`npc_local`（不同子地点）不触发，只有 `npc_party` 触发

### 代码修复（问题 7）

**文件**: `app/game_core/orchestration/hooks/narrative_planner.py`

删除 `_build_planner_context()` 返回字典中 line 1139 的重复 `"maps"` key（保留 line 1206 的那个，两者值不同但 Python dict 只保留最后一个）。

### `_format_planner_context()` quest 三区间分离（问题 3 + 8e）

**文件**: `app/narrators.py`

将单行 `"Dynamic quests: dq_x(active), ..."` 格式改为三区间展示：
- `## Quests you CAN update_quest (status=active):` — 带标题和 ID
- `## Quests available for acceptance (status=available, readonly):` — 带标题和 ID
- `## Completed/retired quests (readonly):` — 只显示 ID 和状态

**意义**：防止 Planner 对非 active 任务下发 update_quest（高频拒绝根因之一）。

### 容量展示格式优化（问题 8f）

**文件**: `app/narrators.py:_format_planner_context()`

area_cluster 展示从 `remaining_permanent=N` 改为 `permanent=N/8 available, total=N/15 available`，使 Planner 直接看到上限值，减少超容量指令。

### Prompt 约束补全（问题 8a-8d）

**QUEST_MANAGER**（规则 7）：明确 update_quest 只能推送 objectives/description/summary，禁止用 update_quest 改 status（应用 retire_quest）。

**WORLD_BUILDER**（规则 12-13 追加）：
- 容量约束警告：fill_area ≤ 8，plant_environmental 总数 ≤ 15，发出前必须检查 remaining_permanent/remaining_total
- interactables 字段完整定义要求：id / name / description / type / tags

**NARRATIVE_WEAVER**（规则 2 + 规则 8）：
- adjust_pacing payload 明确为 `{"frozen": true}` 或 `{"frozen": false}`（布尔值，不是字符串）
- 新增规则 8：escalate payload 格式 `{"delta": N}`，N 在 [-3, 3] 范围，超出被拒

**NPC_DIRECTOR**（规则 8 追加）：临时 NPC 默认 24 ticks 后 despawn，需要更长生命周期时使用 despawn_in_ticks 参数。

**测试修复**（`tests/test_p23_track_c.py`）：
- `test_subsystem_prompts_spell_out_runtime_contract_examples` 中 `'{"frozen": true|false}'` → `'{"frozen": true}'`（新提示词格式）

### Phase 3：图片生成竞态修复（问题 5 + 9）

**文件**: `app/routers/images.py`

1. `_generate_and_save_with_ref()` — 读取 ref_path 前增加验证：
   - `ref_path.exists()` 且 `stat().st_size > 0`，否则 return False + warning
   - `Image.open() + load()` 验证文件完整性，捕获损坏异常
2. 两个写入函数（`_generate_and_save` + `_generate_and_save_with_ref`）均改为原子写入：先写 `.tmp` 再 `rename`，防止并发读到半完成文件。

**测试基线**：2917 passed, 8 skipped（pre-existing），全量通过

---

## D-P31 11b：Planner 开局冷启动修复（2026-03-13）

**问题**：`NarrativePlannerHook.execute()` 的冷却检查（`FALLBACK_INTERVAL=4`）在开局后阻塞 Planner 直到第 4 个 tick。
`last_run_tick` 初始为 0，首次 settlement（tick 1）：`ticks_since_last_run = 1 - 0 = 1 < 4` → 跳过。

**修复**：在 `bootstrap_opening_planner()` 的标题修复之后，设置 `last_run_tick = -100`。
冷却检查 `max(0, 1 - (-100)) = 101 >= 4` → 首次 settlement 即通过。
Planner 运行后正常设置 `last_run_tick = current_tick`，后续冷却恢复正常。

**文件**：`app/game_core/runtime.py` — `bootstrap_opening_planner()` +2 行

**测试基线**：2917 passed, 8 skipped，全量通过
