# Direction A 完成度与修复清单

> 创建时间：2026-02-18
> 基于：代码审计 + Codex 深度调查 + 设计手册对照（2026-02-19）
> 状态：F01-F25 全部完成（147/147 测试通过），Direction A 整体完成度 ~97%
> 前置文档：`图世界活物化方向手册.md` 方向 A

---

## 当前总评

| 子方向 | 完成度 | 生产有效度 | 说明 |
|--------|--------|-----------|------|
| A.1 场景总线基础设施 | **~90%** | 激活中 | 瞬时层 + 持久层 + 图谱化 + 召回全部就位。偏差：post-tick hints 写入总线后无人消费（下回合走 metadata 旁路） |
| A.2 引擎作为意图执行器 | **~95%** | 激活中 | 6 类意图覆盖 + 动态工具排除 + 安全网审计。BehaviorEngine 与 IntentExecutor 是两个独立系统 |
| A.3 各主体自产信息 | **~95%** | 激活中 | NPC Reactor 被动反应 + NPC 主动对话自产(F23) + 队友 Agent 化(F21) 全部就位 |
| A.4 GM 角色重定义 | **~95%** | 激活中 | prompt 层旁观者定位 + 导航裁剪 + TALK 后 npc_dialogue 排除(F24) 完成 |
| **整体** | **~97%** | **激活** | F01-F25 全部完成。剩余偏差(D1/D5/D6)为有意设计选择，不修 |

---

## 修复清单

### P0：基础可运行（错误记录 + 审计）

#### F01 ✅ 将 SCENE_BUS_ENABLED 默认值改为 true

- **文件**: `app/config.py:129`
- **修复**: `os.getenv("SCENE_BUS_ENABLED", "false")` → `"true"`
- **完成日期**: 2026-02-18（第四批）

#### F02 ✅ 引擎执行失败时记录错误而非静默回退

- **文件**: `app/services/admin/pipeline_orchestrator.py:196`
- **修复**: 引擎失败时 `logger.warning` + 注入 `engine_attempted` 到 context_dict
- **完成日期**: 2026-02-18（第一批）

#### F03 ✅ 安全网短路时记录审计日志

- **文件**: `app/services/admin/v4_agentic_tools.py:136`
- **修复**: 短路前调用 `_record()`，标记 `success=True, note="blocked_by_engine_filter"`
- **完成日期**: 2026-02-18（第一批）

---

### P1：双份代码合并（消除分叉源头）

#### F04 ✅ 统一 period 词表

- **文件**: `app/world/intent_executor.py` + `app/services/admin/v4_agentic_tools.py`
- **修复**: 统一使用 `TimePeriod` 枚举英文值（dawn/day/dusk/night）
- **完成日期**: 2026-02-18（第一批）

#### F05 ✅ execute_move() 补齐 HOSTS 边同步

- **文件**: `app/world/intent_executor.py` + `app/services/admin/v4_agentic_tools.py`
- **修复**:
  - 新增模块级函数 `update_hosts_edges()` / `update_party_hosts_edges()`
  - `execute_move()` 重构为 EXIT → HOSTS → ENTER 顺序
  - `v4_agentic_tools.py` 同名方法改为薄委托（消除重复代码）
- **测试**: `TestHostsEdgeHelpers` + `TestExecuteMoveHostsEdges`（全部通过）
- **完成日期**: 2026-02-18（第二批）

#### F06 ✅ execute_move() 补齐旅行时间归一化

- **文件**: `app/world/intent_executor.py`
- **修复**: 新增 `_normalize_advance_minutes()` 静态方法，桶 `[5,10,15,30,60,120,180,240,360,480,720]`，在 `_parse_travel_time()` 之后调用
- **测试**: `test_normalize_advance_minutes` + `test_move_uses_normalized_time`（全部通过）
- **完成日期**: 2026-02-18（第二批）

#### F07 ✅ execute_rest() 修复 player.hp 字段访问

- **文件**: `app/world/intent_executor.py`
- **修复**: 对齐到 `current_hp` / `max_hp` 接口
- **完成日期**: 2026-02-18（第一批）

#### F08 ✅ execute_rest() 增加战斗中禁止检查

- **文件**: `app/world/intent_executor.py`
- **修复**: 检查 `game_state.combat_id`，战斗中返回 error
- **测试**: `test_rest_blocked_during_combat`（通过）
- **完成日期**: 2026-02-18（第一批）

---

### P2：NPC Reactor 修正

#### F09 ✅ 调整 NPC Reactor 执行顺序：移到 GM 之前

- **文件**: `app/services/admin/pipeline_orchestrator.py`
- **修复**:
  - NPC Reactor 块从 C 阶段移到 `agentic_process_v4` 之前
  - 反应写入总线后刷新 `bus_summary`，GM 可消费 NPC 反应
  - C 阶段原位置已清除
- **测试**: 无独立集成测试（依赖重量级异步上下文，属 E2E 级验证）
- **完成日期**: 2026-02-18（第二批）

#### F10 ✅ NPC Reactor 复用 LLMService 单例

- **文件**: `app/world/npc_reactor.py`, `app/services/admin/pipeline_orchestrator.py`
- **修复**:
  - `__init__` 新增 `llm_service` 参数 + `_llm_reaction()` 延迟初始化
  - `pipeline_orchestrator.py:249` NPCReactor 构造注入 `llm_service=self.flash_cpu.llm_service`（Codex 审计发现遗漏，2026-02-19 补齐）
- **测试**: `test_llm_service_reused` + `test_injected_llm_service_used` + `test_lazy_init`（全部通过）
- **完成日期**: 2026-02-18（第二批），2026-02-19 补注入点

#### F11 ✅ NPC Reactor LLM 失败累积告警

- **文件**: `app/world/npc_reactor.py`
- **修复**: `_llm_consecutive_failures` 计数器，成功归零，>=3 升级 `logger.error`
- **测试**: `test_consecutive_failures_tracked` + `test_consecutive_failures_reset_on_success`（全部通过）
- **完成日期**: 2026-02-18（第二批）

---

### P3：工具过滤精细化

#### F12 ✅ MOVE 排除策略区分 area vs sublocation

- **文件**: `app/world/intent_executor.py`, `app/services/admin/v4_agentic_tools.py`, `app/prompts/flash_agentic_system.md`, `tests/test_tool_filtering.py`, `tests/test_intent_executor.py`
- **修复**:
  - `execute_move()` 返回 `intent_type="move_area"`，`execute_sublocation_enter()` 返回 `"move_sublocation"`
  - `_ENGINE_TOOL_EXCLUSIONS` 拆分：`"move_area"` 排除全部导航，`"move_sublocation"` 只排除 `enter_sublocation`
  - prompt 拆为两条独立说明
- **测试**: `TestMoveAreaFiltering` + `TestMoveSublocationFiltering` + SafetyNet 更新 + 断言更新（全部通过）
- **完成日期**: 2026-02-18（第三批）

#### F13 ✅ REST 排除策略放宽为仅排除 update_time

- **文件**: `app/services/admin/v4_agentic_tools.py`, `app/prompts/flash_agentic_system.md`, `tests/test_tool_filtering.py`
- **修复**: `"rest"` 排除集从 `{"heal_player", "update_time"}` 改为 `{"update_time"}`，prompt 明确 `heal_player` 仍可用于额外治疗
- **测试**: `TestRestFiltering.test_rest_removes_only_update_time`（通过）
- **完成日期**: 2026-02-18（第三批）

---

### P4：信息重复清理

#### F14 ✅ 队友 bus_summary 去重玩家输入和 GM 叙述

- **文件**: `app/world/scene_bus.py`, `app/services/admin/pipeline_orchestrator.py`, `tests/test_scene_bus.py`
- **修复**:
  - `get_round_summary()` 新增 `exclude_actors: Optional[set]` 参数
  - 队友专用 bus_summary 调用传入 `exclude_actors={"player", "gm"}`
  - GM 用的调用不变（需要完整总线）
- **测试**: `TestRoundSummaryExcludeActors`（3 个测试全部通过）
- **完成日期**: 2026-02-18（第三批）

#### F15 ✅ 清理 prompt 中开关关闭时的无效段落

- **文件**: `app/prompts/flash_agentic_system.md`, `app/services/admin/flash_cpu_service.py`, `tests/test_tool_filtering.py`
- **修复**:
  - Section 15-16 用 `<!-- SECTION:ENGINE_BUS_START/END -->` 标记包裹
  - `_load_agentic_prompt()` 新增 `scene_bus_enabled` 参数，False 时裁剪标记之间内容
  - 调用点传入 `settings.scene_bus_enabled`
- **测试**: `TestPromptStripping`（2 个测试全部通过）
- **完成日期**: 2026-02-18（第三批）

---

### P5：翻开关 + GM 角色重定义 + 意图扩展

#### F01 ✅ 将 SCENE_BUS_ENABLED 默认值改为 true

- **文件**: `app/config.py:129`
- **改动**: `os.getenv("SCENE_BUS_ENABLED", "false")` → `"true"`
- **工程量**: 1 行代码，需配合全面回归测试
- **风险**: 整个 Direction A 从休眠变为激活，需确保无回归
- **完成日期**: 2026-02-18（第四批）

#### F16 ✅ 改造 GM prompt 开头定位

- **文件**: `app/prompts/flash_agentic_system.md:1-10`, `app/services/admin/flash_cpu_service.py`
- **修复**:
  - 开头用 `<!-- SECTION:GM_ROLE_BUS_ON_START/END -->` 包裹旁观者定位文本
  - 用 `<!-- SECTION:GM_ROLE_BUS_OFF_START/END -->` 包裹现有全知 GM 定位文本
  - `_load_agentic_prompt()` 增加双段条件替换（复用并扩展 F15 的 `_strip_section` / `_unwrap_section` 辅助函数）
- **完成日期**: 2026-02-18（第四批）

#### F17 ✅ 总线开启时缩减 GM 工具指导文本

- **文件**: `app/prompts/flash_agentic_system.md`（3 处段落）
- **修复**:
  - Section 2 映射表中 `navigate` / `enter_sublocation` 两行用 `<!-- SECTION:NAV_GUIDE_START/END -->` 包裹
  - Layer 2 `connections`/`sub_locations` 说明行用同标记包裹
  - Layer 3 `leave_sublocation` 离开提示行用同标记包裹
  - `_load_agentic_prompt()` 总线开启时循环清除所有 NAV_GUIDE 段落
- **完成日期**: 2026-02-18（第四批）

#### F20 ✅ A.2 补齐 EXAMINE / USE_ITEM 意图

- **文件**: `app/world/intent_resolver.py`, `app/world/intent_executor.py`, `tests/test_intent_resolver.py`, `tests/test_intent_executor.py`
- **修复**:
  - `IntentType` 枚举新增 `EXAMINE = "examine"` / `USE_ITEM = "use_item"`
  - 新增 `_EXAMINE_KEYWORDS` / `_USE_ITEM_KEYWORDS` 关键词常量
  - 新增 `_try_examine()` 方法：匹配子地点/NPC，优先级在 REST 之前
  - 新增 `_try_use_item()` 方法：优先从 `session.player.inventory` 匹配（兼容旧 `session.state.player_character`）
  - `execute_examine()`：聚合 WorldGraph + LocationContext 细节并写入总线条目
  - `execute_use_item()`：执行 heal 类物品效果（含骰值计算）并消耗 1 个物品，失败时回退 GM 兜底
  - `dispatch()` 增加两个分支
- **测试**: `TestExamineIntent`（5 个）+ `TestUseItemIntent`（6 个）+ `TestExecuteExamine`（4 个）+ `TestExecuteUseItem`（5 个），20/20 全部通过
- **完成日期**: 2026-02-18（第四批，回归补丁）

---

### P6：持久层新功能

#### F18 ✅ A.1 持久层：回合末总线图谱化

- **设计**: 方向手册 L220-222
- **内容**: 回合结束 → flash 模型从总线条目提取话题/关联 → 写入 `GraphScope.location()` 持久会话图
- **依赖**: F01（开关开启）
- **文件**:
  - `app/services/scene_bus_graphizer.py`
  - `app/services/memory_graphizer.py`
  - `app/services/admin/pipeline_orchestrator.py`
  - `app/services/admin/admin_coordinator.py`
- **修复**:
  - 新增 `graphize_scene_bus_round()`，将 `SceneBus.entries` 适配为 `GraphizeRequest`
  - `MemoryGraphizer.graphize()` 增加 `target_scope` + `mode="scene_bus"`，支持写入 location scope
  - SceneBus 图谱模式落库 `topic`/`utterance` 节点，补 `ABOUT`/`BY`/`RESPONDS_TO` 边
  - 在 `PipelineOrchestrator` 的 `scene_bus.clear()` 前接入图谱化，异常 fail-open（仅 warning）
- **测试**: `tests/test_scene_bus_graphizer.py`（通过）
- **完成日期**: 2026-02-18（第五批）

#### F19 ✅ A.1 持久层：进入子地点时扩散激活召回

- **设计**: 方向手册 L222
- **内容**: 进入子地点 → 用玩家上下文做 SA → 命中话题节点 → 注入总线
- **依赖**: F18（需要先有持久节点可供激活）
- **文件**:
  - `app/services/admin/recall_orchestrator.py`
  - `app/world/intent_executor.py`
  - `app/services/admin/pipeline_orchestrator.py`
- **修复**:
  - `RecallOrchestrator.recall()/recall_v4()` 增加 `location_id` 参数并加载 `GraphScope.location(...)`
  - `IntentExecutor` 支持注入 `recall_orchestrator`，`execute_sublocation_enter()` 成功后触发 recall
  - 召回结果写入 `BusEntryType.SYSTEM`（优先翻译记忆，兜底 top activated nodes）
  - recall 超时/异常 fail-open，不阻断移动主流程
- **测试**:
  - `tests/test_recall_orchestrator.py`（通过）
  - `tests/test_intent_executor.py` 新增 sublocation recall 场景（通过）
- **完成日期**: 2026-02-18（第五批）

---

### P7：清除旧管线 fallback + 废弃组件（上下文去污）

> 系统已全面切换到总线驱动架构，`scene_bus_enabled` 恒为 `true`。所有 fallback 到旧管线的分支和废弃组件只增加维护负担和上下文污染。

#### F25 ✅ 移除 BUS_OFF fallback 分支 + 废弃 LEGACY 组件

- **原则**: 我们在快速迭代，保留旧路径只会增加混乱。一刀切删除。
- **清除清单**:

  **A. prompt 层** ✅
  - `flash_agentic_system.md`: 删除 `GM_ROLE_BUS_OFF` 整段 + `GM_ROLE_BUS_ON` 标记（保留内容）+ 3 处 `NAV_GUIDE` 段落 + `ENGINE_BUS` 标记（保留内容）。源 prompt 零 `SECTION:` 标记残留
  - `flash_analysis.md`: **已删除**

  **B. flash_cpu_service.py** ✅
  - `_load_agentic_prompt()`: 从 62 行（3 helper + 双分支）简化为 5 行（read_text + fallback）
  - 删除 `analyze_and_plan()` + `_parse_analysis_result()` + `_load_analysis_prompt()` + `self.analysis_prompt_path`（~215 行 LEGACY 代码）

  **C. pipeline_orchestrator.py** ✅
  - 9 处 `settings.scene_bus_enabled and session.X` → `session.X`

  **D. session_runtime.py** ✅
  - `_init_scene_bus()`: 删除 `settings.scene_bus_enabled` 早退
  - `enter_area()`: 删除 `_cfg.scene_bus_enabled` 条件

  **E. config.py** — 不改（保留紧急开关）

  **F. tests/test_tool_filtering.py** ✅
  - 删除 `TestPromptStripping` + `TestPromptRoleAndNavStripping`（~78 行，测试已删除的 strip/unwrap 逻辑）

- **文件**: `flash_agentic_system.md`, `flash_analysis.md`(已删除), `flash_cpu_service.py`, `pipeline_orchestrator.py`, `session_runtime.py`, `test_tool_filtering.py`
- **工程量**: ~250 行删除 + ~20 行简化
- **测试**: 138/138 通过（Direction A 全量测试套件）
- **完成日期**: 2026-02-19

---

### P8：设计手册对照修复（2026-02-19 审计产出）

> 基于设计手册 A.1-A.4 期望回合流程 vs 实际 `pipeline_orchestrator.py` 执行流程的逐步对照。

#### F22 ✅ post-tick hints 总线写入无消费者

- **问题**: `post_tick_result.narrative_hints` 在队友之后写入总线，紧接着被 `scene_bus.clear()` 清除。无消费者。
- **修复**: 删除 post-tick hints 的总线写入（6 行），仅保留 metadata 旁路（跨回合传递路径不变）
- **文件**: `app/services/admin/pipeline_orchestrator.py`
- **完成日期**: 2026-02-19（第七批，随 F25 一起完成）

#### F23 ✅ NPC 对话自产（IntentExecutor 直接调用 NPC AI）

- **问题**: 手册 A.3 期望"谁的发言谁自己产，GM 不代言"。TALK 意图由 GM 通过 `npc_dialogue` 代理。
- **修复**:
  - `IntentExecutor.__init__` 新增 `flash_cpu` 可选注入
  - `execute_talk_setup()` → `execute_talk()`，新增 `player_message` 参数
  - 成功时通过 `flash_cpu.execute_request(NPC_DIALOGUE)` 生成 NPC 回复，写入 `BusEntryType.SPEECH`
  - 降级策略：`flash_cpu` 为 None 或调用失败 → 只写 ACTION 条目，GM 兜底
  - `pipeline_orchestrator.py` IntentExecutor 构造注入 `flash_cpu`
- **文件**: `app/world/intent_executor.py`, `app/services/admin/pipeline_orchestrator.py`
- **测试**: `TestExecuteTalk`（4 个测试：SPEECH 生成、降级、异常容错、消息传递）全部通过
- **完成日期**: 2026-02-19（第八批）

#### F24 ✅ GM 工具集在 TALK 后收窄

- **问题**: 引擎成功执行 TALK 后 GM 仍持有 `npc_dialogue` 工具，可能重复调用。
- **修复**: `_ENGINE_TOOL_EXCLUSIONS` 增加 `"talk": {"npc_dialogue"}`
- **逻辑**: 引擎 TALK 成功 → NPC 已在总线中回复 → GM 不需要再调 `npc_dialogue`。引擎失败 → `engine_executed` 未设置 → GM 仍持有工具兜底。
- **文件**: `app/services/admin/v4_agentic_tools.py`
- **测试**: `TestTalkFiltering.test_talk_removes_npc_dialogue`（通过）
- **完成日期**: 2026-02-19（第八批）

---

### P9：队友 Agent 化（独立大项）

#### F21 ✅ A.3 队友 Agent 化

- **设计**: 方向手册 L301, L307-314
- **内容**: 队友拥有独立工具集（update_disposition / choose_combat_action / recall_memory），自主读总线决策
- **实现**:
  - `TeammateAgenticToolRegistry`（3 工具）: `teammate_agentic_tools.py`
  - 队友 agentic 系统提示: `teammate_agentic_system.md`
  - `_run_agentic_generation_payload()` 接入 `agentic_generate()` + 工具注册
  - agentic→simple 降级容错
  - `teammate_tool_call` 事件发射 + 流式 yield
  - `_build_teammate_context()` 注入 `_runtime_session` + tools
- **测试**: 13/13 全部通过（test_teammate_agentic_tools + test_pipeline_teammate_stage + test_teammate_response_service）
- **完成日期**: 2026-02-19（第八批，确认已完成）

---

## 建议修复顺序

```
第一批（基础可运行 + 消除错误掩盖）— ✅ 已完成:
  F02 ✅ 引擎失败记录错误
  F03 ✅ 安全网记审计
  F04 ✅ 统一 period 词表
  F07 ✅ execute_rest HP 字段修复
  F08 ✅ execute_rest 战斗检查
    ↓
第二批（消除分叉 + 修正顺序）— ✅ 已完成:
  F05 ✅ execute_move 补 HOSTS 边
  F06 ✅ execute_move 时间归一化
  F09 ✅ NPC Reactor 移到 GM 之前
  F10 ✅ LLMService 复用（+2026-02-19 补注入点）
  F11 ✅ LLM 失败累积告警
    ↓
第三批（精细化 + 去重）— ✅ 已完成:
  F12 ✅ MOVE 排除区分 area/sublocation
  F13 ✅ REST 排除放宽
  F14 ✅ 队友 bus_summary 去重
  F15 ✅ prompt 无效段落裁剪
    ↓
第四批（翻开关 + GM重定义 + 意图扩展）— ✅ 已完成:
  F01 ✅ SCENE_BUS_ENABLED=true
  F16 ✅ GM prompt 开头改造（旁观者/全知双段条件切换）
  F17 ✅ 工具指导文本裁剪（3处 NAV_GUIDE 标记）
  F20 ✅ 补齐 EXAMINE/USE_ITEM 意图（18/18 测试通过）
    ↓
第五批（持久层新功能）— ✅ 已完成:
  F18 ✅ 回合末总线图谱化             [SceneBus -> location scope 图谱化已接入]
  F19 ✅ 进入子地点时 SA 召回         [location scope 参与激活 + SYSTEM 注入总线]
  验证: 52/52 测试通过（test_scene_bus_graphizer + test_intent_executor + test_recall_orchestrator）
    ↓
第七批（清除旧管线 + 废弃组件 — 上下文去污）— ✅ 已完成:
  F25 ✅ 移除 BUS_OFF fallback + LEGACY 组件  [~250 行删除，138/138 测试通过]
  F22 ✅ post-tick hints 冗余总线写入删除      [6 行删除]
    ↓
第八批（NPC 对话自产 + GM 收窄 + 队友确认）— ✅ 已完成:
  F21 ✅ 队友 Agent 化（确认已完成）    [13/13 测试通过，零改动]
  F23 ✅ NPC 主动对话自产              [IntentExecutor 直接调用 NPC AI，4 个新测试]
  F24 ✅ GM 工具集 TALK 后收窄          ["talk": {"npc_dialogue"} 排除，1 个新测试]
  验证: 147/147 Direction A 全量测试通过
```

---

---

## 设计手册对照分析（2026-02-19）

> 对照 `图世界活物化方向手册.md` A.1 回合执行顺序（L228-238）与 `pipeline_orchestrator.py:process()` 实际执行流程。

### 实际执行流程

```
A0. BehaviorEngine pre-tick                        ← 在总线之前执行
A1. ContextAssembler.assemble()
A2. 构建 context_dict

── 总线流程 ──

S1.  玩家输入 → 总线                               ← L176   ✅ 步骤 1
S1b. pre-tick hints → 总线（engine/SYSTEM）         ← L188
S2.  IntentResolver → IntentExecutor → 总线         ← L196   ✅ 步骤 2
S2b. bus_summary → context_dict                    ← L234
S3.  NPCReactor → 总线，刷新 bus_summary            ← L240   ✅ 步骤 3
S4.  GM agentic_process_v4()                       ← L271   ✅ 步骤 4
S4b. GM 工具结果 + NPC 对话 + 叙述 → 总线           ← L288

── C 阶段 ──

C0.  BehaviorEngine post-tick                      ← L337
S5.  队友 process_round_stream()                   ← L374   ✅ 步骤 5
S5b. 队友响应 + post-tick hints → 总线              ← L411
     历史记录                                       ← L429
S6.  graphize_scene_bus_round()                    ← L460   ✅ 步骤 6
S7.  scene_bus.clear()                             ← L478   ✅ 步骤 7
     persist()                                      ← L482
```

### 结构性偏差

| # | 偏差 | 影响 | 对应修复 |
|---|------|------|---------|
| D1 | **pre-tick 在总线外执行**：BehaviorEngine pre-tick(A0) 在总线创建前运行，hints 后补写入总线 | 低 — pre-tick 评估上回合遗留条件，不依赖本轮玩家输入 | 不修 |
| D2 | **post-tick hints 无消费者**：post-tick hints 在队友之后写入总线(S5b)，紧接着被 clear(S7)。实际传递走 metadata 旁路 | 低 — 功能不受影响，总线写入纯冗余 | F22 |
| D3 | **GM 仍代理主动对话**：玩家说"和 NPC 说话" → IntentExecutor 只做交互计数 → GM 调 npc_dialogue → NPC AI 回复。GM 是中间人而非旁观者 | 高 — 与手册 A.3/A.4 核心理念冲突 | F23 |
| D4 | **GM 工具集过宽**：总线模式下 GM 仍持有完整工具集，只排除引擎已执行的对应工具 | 中 — prompt 层有约束但缺代码强制 | F24 |
| D5 | **队友信息混合路径**：队友 bus_summary 用 exclude_actors 去掉 player/gm，player/gm 通过模板变量注入。功能等价但非纯总线模型 | 低 — 有意为之的去重策略(F14)，信息完整性无损 | 不修 |
| D6 | **BehaviorEngine 与 IntentExecutor 独立**：手册"引擎"是统一概念，实际拆为条件状态机和意图执行两套系统 | 无 — 职责划分清晰，不需要统一 | 不修 |

---

## 变更日志

| 日期 | 操作 | 涉及 |
|------|------|------|
| 2026-02-18 | 创建文档，完成初始审计 | F01-F21 |
| 2026-02-18 | 第一批修复完成 | F02/F03/F04/F07/F08 ✅ |
| 2026-02-18 | 第二批修复完成（Codex 验证通过） | F05/F06/F09/F10/F11 ✅ |
| 2026-02-18 | 第三批修复完成（Codex 验证 19/19 100%） | F12/F13/F14/F15 ✅ |
| 2026-02-18 | 代码调查 + 工程量评估，重划第四~六批分组 | F01/F16-F21 工程量写入 |
| 2026-02-18 | 第四批修复完成（18/18 测试通过） | F01/F16/F17/F20 ✅ |
| 2026-02-18 | 第四批回归修正：USE_ITEM 真实链路 + EXAMINE/USE_ITEM 执行语义补齐（20/20） | F20 ✅ |
| 2026-02-18 | 第五批修复完成：回合末 SceneBus 图谱化 + 进入子地点召回注入（52/52） | F18/F19 ✅ |
| 2026-02-19 | Codex 审计（F01-F20: 97%，Direction A: 94%），F10 补注入点 | F10 补丁 |
| 2026-02-19 | 设计手册对照分析，识别 6 项结构性偏差（D1-D6），新增 F22/F23/F24 | P7 新增 |
| 2026-02-19 | 第七批修复完成：F25 清除旧管线 fallback + F22 删 post-tick hints（138/138 测试通过） | F22/F25 ✅ |
| 2026-02-19 | 第八批完成：F21 确认 + F23 NPC 对话自产 + F24 GM TALK 收窄（147/147 测试通过） | F21/F23/F24 ✅ |
