# Planner 系统重构：合并为单一决策者

记录日期：2026-03-15（第三版，基于代码实读 + Codex 验证 + 用户方向确认）
状态：待确认

## 设计原则

> "除非能带来十分显而易见的效果，否则不要增加架构复杂度增加多个 LLM。"

当前 5 个 LLM（4 子系统 + 1 Blackboard）没有带来显而易见的收益。合并为 1 个 high-thinking LLM，做好做透。

---

## 一、当前架构（实读代码确认）

```
deps.py 创建 5 个 LLM 实例：
  blackboard_llm  → AgenticNarrativePlanner（blackboard_registry，无 design_skill）
  quest_llm       → AgenticNarrativePlanner（quest_registry + design_skill）
  npc_llm         → AgenticNarrativePlanner（npc_registry + design_skill）
  world_llm       → AgenticNarrativePlanner（world_registry + design_skill）
  weaver_llm      → AgenticNarrativePlanner（weaver_registry + design_skill）

PlannerSystemAssembly:
  blackboard:             PlannerBlackboardPort（.plan() → story_facts + strategy_notes，directives=[]）
  quest_manager_agent:    PlannerAgentPort（.evaluate() → directives）
  npc_director_agent:     PlannerAgentPort（.evaluate() → directives）
  world_builder_agent:    PlannerAgentPort（.evaluate() → directives）
  narrative_weaver_agent: PlannerAgentPort（.evaluate() → directives）

bootstrap.py 连接：
  NarrativePlannerHook(blackboard=assembly.blackboard)
  QuestManagerSubSystem(agent=assembly.quest_manager_agent)
  NpcDirectorSubSystem(agent=assembly.npc_director_agent)
  WorldBuilderSubSystem(agent=assembly.world_builder_agent)
  NarrativeWeaverSubSystem(agent=assembly.narrative_weaver_agent)
  PacingControllerSubSystem()  ← 无 agent

每个子系统 agent 有"强制要求先 read_design_skill"→ 每次 evaluate 是 2+ 轮 roundtrip
Blackboard prompt 明确禁止产 directives（narrators.py:1555）
```

---

## 二、改造目标

```
改后：1 个 LLM，1 次调用/settlement，thinking_level="high"

UnifiedPlanner（新角色，替代 Blackboard + 4 子系统 agent）：
  - 拥有 design_skill_port + planner_tools
  - 可产出所有类型 directives + story_facts + strategy_notes
  - 输入：完整上下文 + 所有 events 一次性打包
  - 输出：最多 8 条 directives + story_facts + strategy_notes + outline_updates

子系统 → 纯 handler（只保留 apply_directive，去掉 agent）
确定性逻辑 → 搬到 hook 预处理中
```

---

## 三、具体改造

### 3a. 新建 UnifiedPlanner Prompt

合并 5 个 prompt 的精华为一个 prompt。基础框架是现有的 `_SYSTEM_PROMPT`（narrators.py:225-324），它已经列出了所有 directive 种类和规则。在此基础上补充：

```
追加内容（从子系统 prompt 提取的专业规则）：

# 来自 QUEST_MANAGER_AGENT_PROMPT：
- create_quest 必须有 objectives（含 condition）和 rewards
- publish_bulletin 必须有 board_id + metadata.quest_id
- update_quest 只能对 status=active 的任务
- objectives 优先用 encounter_cleared/clue_investigated 等条件类型

# 来自 NPC_DIRECTOR_AGENT_PROMPT：
- direct_npc payload 必须是 {npc_id, directive:{kind,..}, priority}
- directive.kind 只能是 talk/approach/react/inform
- 不把 behavior/topic/goal 放在 payload 根

# 来自 WORLD_BUILDER_AGENT_PROMPT：
- fill_area/fill_location/fill_room 的必填字段
- plant_encounter 的 monster_ids/sub_area_id 格式
- 线索必须用 fill_location + investigate_clue functional type
- 容量约束（fill_area ≤8，plant_environmental ≤15）

# 来自 NARRATIVE_WEAVER_AGENT_PROMPT：
- adjust_pacing 只用 {frozen: bool}
- escalate 只用 {delta: N}，N 在 [-3, 3]
```

**注意**：_SYSTEM_PROMPT 包含了大部分规则，但**缺少以下 directive kind**（需要补齐到"可用指令"部分）：
- `assign_service` / `revoke_service`（NPC 服务分配/移除）
- `schedule_event`（安排延迟事件）
- `create_rumor`（传播谣言）
- `modify_location`（修改 NPC 位置）
- `spawn_quest_npc`（生成临时任务 NPC）

这些在 `directive_contracts.py:48` 的 `SUPPORTED_PLANNER_DIRECTIVE_KINDS` 中已注册，但 _SYSTEM_PROMPT 的"可用指令"列表（334-352 行）未列出。需要补充格式示例和规则说明。

同时删除 `PLANNER_BLACKBOARD_PROMPT` 的"directives 必须为空"限制，改用补全后的 _SYSTEM_PROMPT。

### 3b. deps.py 精简

```python
# 改前：5 个 LLM 实例 + 5 个 registry
blackboard_llm = GeminiLlmAdapter(thinking_level="high", ...)
quest_llm = GeminiLlmAdapter(thinking_level="high", ...)
npc_llm = GeminiLlmAdapter(thinking_level="high", ...)
world_llm = GeminiLlmAdapter(thinking_level="high", ...)
weaver_llm = GeminiLlmAdapter(thinking_level="high", ...)

# 改后：1 个 LLM + 1 个 registry（含所有 planner tools）
unified_llm = GeminiLlmAdapter(thinking_level="high", profile_name="planner")
unified_registry = RoleToolRegistry()
register_planner_tools(unified_registry, roles=["planner"])

return PlannerSystemAssembly(
    blackboard=AgenticNarrativePlanner(
        llm=unified_llm,
        executor=AgenticExecutor(tool_registry=unified_registry, llm=unified_llm),
        design_skill_port=LocalDesignSkillProvider(),
        world_id="",
        role="planner",
        system_prompt=UNIFIED_PLANNER_PROMPT,   # ← 新 prompt
        provider_name="planner",
        history_key="__planner__",
        graphize_callback=graphize_callback,
        memory_retriever=memory_retriever,
        allowed_skill_categories=["quests", "npcs", "areas", "environments", "encounters", "narrative", "social"],
    ),
    quest_manager_agent=None,      # ← 不再需要
    npc_director_agent=None,
    world_builder_agent=None,
    narrative_weaver_agent=None,
)
```

### 3c. Blackboard prompt 改造

```python
# 改前（PLANNER_BLACKBOARD_PROMPT）：
# "directives 必须是空数组"

# 改后（UNIFIED_PLANNER_PROMPT）：
# 基于 _SYSTEM_PROMPT（已有所有 directive 规则）
# + 子系统专业规则（3a 中列出的）
# + 删除"directives 必须为空"限制
# + max directives 从 3 提高到 8
# + "强制 read_design_skill" 改为"建议查阅"（减少 roundtrip）
```

### 3d. hook execute() 简化

```python
# 改后伪代码（保留所有关键步骤，删除多轮 replay）：

async def execute(self, context):
    # === 前置检查（不变）===
    # 冷却检查、触发检查、quiet_rest_slot 检查（原样保留）

    # === 里程碑大纲（不变，从现有代码保留）===
    self._auto_select_target_milestone(context)       # 原 330 行
    await self._ensure_milestone_outline(context, ...) # 原 334 行

    # === 确定性预处理（从 NarrativeWeaver.evaluate 搬出）===
    context.execute_command(Command(type="planner_prune_npc_directives", ...))
    self._despawn_expired_quest_npcs(context, ...)
    escalate = self._check_auto_escalation(context)  # 加 cap=10
    pre_directives = [escalate] if escalate else []

    # === 确定性 quest_completed（从 NpcDirector 搬出）===
    for event in pending_events:
        if event.kind == "quest_completed":
            result = self._handle_quest_completed_deterministic(event, context)
            if result:
                pre_directives.extend(result.directives)

    # === 应用预处理 directives ===
    if pre_directives:
        self._apply_directive_batch(pre_directives, context, ...)

    # === 收集所有 events（一次性）===
    all_events = collect_planner_events(context, current_tick, ...)
    planner_event_summaries = [planner_event_snapshot(e) for e in all_events]

    # === 构建上下文（一次）===
    planner_context = self._build_planner_context(context, current_tick=current_tick)
    planner_context["planner_events"] = planner_event_summaries
    self._inject_runtime_refs(planner_context, context)

    # === UnifiedPlanner.plan() ← 唯一的 LLM 调用 ===
    decision = await self.blackboard.plan(planner_context)

    # === 应用 LLM directives（走现有 dispatcher）===
    apply_summary = self._apply_directive_batch(decision["directives"], context, ...)

    # === inline event check（从 _run_replay 保留）===
    run_inline_event_check(
        state=context.state, world=context.world,
        rules_engine=context._rules_engine,
        apply_delta=context._apply_delta,
        change_log=context.change_log,
        scene_bus=context.scene_bus,
        label="planner_unified",
        sse_collector=self._pending_sse,
    )

    # === 应用 story_facts ===
    self._apply_story_facts(decision.get("story_facts", []), context, ...)

    # === 检测 milestone transitions + cascade ===
    milestone_sse = _detect_failed_milestones_sse(context) + _detect_completed_milestones_sse(context)

    # === 更新 area_situation ===
    self._update_area_situation(context)

    # === 状态提交（不变）===
    # 更新 last_run_tick, strategy_notes, behavior_window, outline_updates 等
```

**关键保留项**（Codex 验证反馈指出的）：
- `_auto_select_target_milestone()` + `_ensure_milestone_outline()` — 里程碑大纲主链路
- `run_inline_event_check()` — 事件条件触发（flag 满足 → 事件激活）
- replay trace 语义 — 简化为 single-round trace（不再有多轮）

### 3e. bootstrap.py 和 Opening Bootstrap 兼容

**关键澄清**：`build_narrative_planner_hook()` 是一个通用 wiring 函数，Live 和 Bootstrap 用**不同的 PlannerSystemAssembly 实例**调用它。

```python
# Live planner（deps.py _build_planner_system）：
PlannerSystemAssembly(
    blackboard=UnifiedPlanner,     # ← 有 LLM
    quest_manager_agent=None,      # ← 改为 None
    npc_director_agent=None,       # ← 改为 None
    world_builder_agent=None,      # ← 改为 None
    narrative_weaver_agent=None,   # ← 改为 None
)

# Bootstrap planner（opening_bootstrap.py:129）：
PlannerSystemAssembly(
    blackboard=None,                                # ← 无 LLM
    quest_manager_agent=OpeningBootstrapQuestAgent(), # ← 确定性 agent，保留不动
)
```

**build_narrative_planner_hook() 本身不改**——它只是把传入的 agent 赋给子系统。Live 传 None，Bootstrap 传 OpeningBootstrapQuestAgent，各走各的。

**不需要分支逻辑**——因为两个 assembly 实例在不同代码路径创建，互不影响。

### 3g. PacingController 合并

```python
# NarrativeWeaver._HANDLES 扩展：
_HANDLES = frozenset({"schedule_event", "escalate", "adjust_pacing"})

# 新增 apply 方法（从 PacingController 搬过来）：
def _apply_escalate(self, payload, context, *, current_tick):
    ...
def _apply_adjust_pacing(self, payload, context, *, current_tick):
    ...

# PacingControllerSubSystem 从 defaults + bootstrap 中移除
```

### 3h. save/load 精简

```python
# 只保存 1 个 planner 历史（"__planner__"）
# 旧存档中的 __quest_manager_agent__ 等 key 加载时静默跳过
# runtime.py 的 _collect_context_windows / _restore_context_windows 精简
```

---

## 四、不变的部分

| 模块 | 为什么不变 |
|------|-----------|
| 所有 apply_directive handler（QuestManager/NpcDirector/WorldBuilder/Weaver 的 apply 方法） | 纯机械执行，不关心指令来源 |
| PlannerDispatcher 路由 | 指令分发不变 |
| directive_contracts 验证 | 验证不变 |
| 跨系统路由（publish_bulletin→direct_npc） | 在 apply 阶段 |
| InstanceManager 注入 | 在 _apply_direct_npc 中 |
| NPC 黑板写入 | 在 _apply_direct_npc 中 |
| SSE 事件产出 | 来源不变 |
| Opening Bootstrap | 独立路径，不受影响 |
| No-LLM Fallback | _FallbackBlackboard 仍可用 |
| NPC Agent/对话系统 | 完全独立于 Planner |
| 前端代码 | SSE 格式不变 |

---

## 五、修改文件清单

| 文件 | 改动 | 预估行数 |
|------|------|---------|
| `app/narrators.py` | 新建 UNIFIED_PLANNER_PROMPT（合并 5 个 prompt），删旧 prompt | ~200 行改动 |
| `app/deps.py` | 1 个 LLM 替代 5 个，1 个 registry 替代 5 个 | ~100 行删减 |
| `app/game_core/orchestration/hooks/narrative_planner.py` | execute() 简化（删 _run_replay，改为单次调用） | ~200 行改动 |
| `app/game_core/bootstrap.py` | 子系统 agent=None，删 PacingController | ~20 行 |
| `app/game_core/adapters/planner_system.py` | PlannerSystemAssembly 4 个 agent 字段改为可选 | ~5 行 |
| `app/game_core/planning/narrative_weaver.py` | 吸收 escalate/adjust_pacing handler | ~40 行 |
| `app/game_core/planning/pacing_controller.py` | 删除 | ~-110 行 |
| `app/game_core/runtime.py` | save/load 精简为 1 个历史 | ~20 行 |
| `app/game_core/orchestration/defaults.py` | 移除 PacingController | ~5 行 |

**净变化**：约 -100 行（删减多于新增）

---

## 六、风险与缓解

| 风险 | 缓解 |
|------|------|
| 单 prompt 覆盖 4 个领域，质量可能下降 | _SYSTEM_PROMPT 已包含所有规则；只需补充子系统的细节格式约束 |
| 单次输出 8 条 directives 可能不精准 | 保留"最多 N 条"限制 + directive_contracts 验证层 |
| Opening Bootstrap 不兼容 | 独立路径（用 OpeningBootstrapQuestAgent），完全不受影响 |
| 旧存档加载失败 | 4 个 agent 历史 key 不存在时 restore 已有 .get() 容错 |
| 回退困难 | 子系统代码只改 agent=None，恢复只需改回 deps + bootstrap |

---

## 七、预期效果

| 指标 | 当前 | 改后 |
|------|------|------|
| LLM 调用次数/settlement | 10-20+（replay×events×subsystems×roundtrips） | **1-2**（UnifiedPlanner 1-2 轮 roundtrip） |
| thinking_level | high | **high**（保留） |
| 单次结算延迟 | 60-90s | **10-20s** |
| escalation_level | 68（失控） | ≤10（cap） |
| NpcDirector 格式问题 | parse_failed | **消除**（统一 prompt 统一格式） |
| 代码复杂度 | 5 LLM + 5 registry + 5 prompt | **1 LLM + 1 registry + 1 prompt** |
| Token 消耗/settlement | 5 × context（重复上下文传递） | **1 × context**（上下文只传一次） |

---

## 八、实施步骤

```
Phase 1：prompt 合并（~200 行）
  新建 UNIFIED_PLANNER_PROMPT
  基于 _SYSTEM_PROMPT + 子系统专业规则
  删除"directives 必须为空"
  "强制 read_design_skill" → "建议查阅"

Phase 2：deps + bootstrap 改造（~120 行）
  deps.py：1 个 LLM + 1 个 registry
  bootstrap.py：子系统 agent=None
  planner_system.py：字段标可选

Phase 3：hook execute() 简化（~200 行）
  删 _run_replay
  改为：预处理 → 收集 events → 构建上下文 → plan() → apply
  确定性逻辑搬到预处理

Phase 4：PacingController 合并 + save/load 精简（~60 行）
  NarrativeWeaver 吸收 handler
  runtime.py 只存 1 个历史

Phase 5：测试更新
  更新 planner 相关测试断言
```
