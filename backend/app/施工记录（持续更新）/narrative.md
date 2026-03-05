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
| `AgenticExecutor` | [完成] | 单轮 `run()` + 多轮 `run_agentic()` + LlmPort 注入（D-N10） |
| `RoleToolRegistry` | [完成] | gm/npc/teammate 分桶 + 同名工具按序覆盖 |
| `ToolResult` | [完成] | success/message/commands/metadata |
| **GM 工具（5 个）** | [完成] | describe_environment / narrate / comment / pass_turn / suggest_options（D-N08） |
| **NPC 工具（8 个）** | [完成] | speak / emote / update_feeling / remember / offer_quest / offer_trade / refuse / reveal_secret（D-N09） |
| **Teammate 工具（6 个）** | [完成] | speak / emote / express_opinion / suggest_tactic / share_memory / request_action（D-N09） |

### 叙事规划（planning/）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlanner` | [完成] | L0-L5 升级阶梯 + 区域填充 + 任务播种 + 解冻（D-N07） |
| `DynamicSubAreaManager` | [完成] | AreaSlice-backed create/expire/list_active |
| `PlanningDirective` 系列 | [完成] | 9 种指令类型 dataclass |

### Hook 边界状态（编排层联动）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlannerHook` | [完成] | planner 输入 contract + 全 9 指令执行（D-N06） |
| `GmNarrationHook` | [完成] | narrator 注入边界 + SceneBus 受控写入 |

## 决策记录

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

## [D-N20] N-2 Phase B：PrivateChatTriggerHook（NPC 主动发起私聊）（2026-03-01）

**问题**：Phase A 完成了玩家主动发起的 4 步私聊管线，但 §7.4 的另一侧（NPC 根据 disposition 主动发起信号）缺失。

**目标**：在格结算时检测 NPC 的 romance/trust/stage 阈值，满足条件时推送 `npc_wants_to_chat` SSE 事件。冷静期（FlagSlice）防止同一 NPC 短时间内重复触发。

**触发条件**：romance ≥ 60 OR trust ≥ 50 OR stage == "intimate"（任一满足）
**冷静期**：`absolute_tick + COOLDOWN_TICKS(=6)` 写入 FlagSlice，下次检查时比较

#### 新建 `app/game_core/orchestration/hooks/private_chat_trigger.py`（~170 行）

- `PrivateChatTriggerEvaluator` Protocol + `BasicPrivateChatTriggerEvaluator` + `NullPrivateChatTriggerEvaluator`
- `PrivateChatTriggerHook(NoOpSettlementHook)`：HOOK_PRIORITY=75（NpcScheduleHook=60 之后）
  - `execute()` 遍历 `relations.npc_dispositions`，逐 NPC 检查冷静期 + 阈值
  - 触发时：set `private_chat_cooldown_{npc_id}` flag = current_tick + COOLDOWN_TICKS，emit SSEEvent
  - 冷静期到期时：remove 旧 flag，允许重新触发
  - FlagSlice/TimeSlice 不存在时各有 guard，安全降级
- `_get_npc_name()` lazy import `_profile_get`（避免模块加载时循环依赖）

#### 修改 `app/game_core/orchestration/hooks/__init__.py`（+2 行）
- 新增 import + `__all__` export `PrivateChatTriggerHook`

#### 修改 `app/game_core/orchestration/defaults.py`（+2 行）
- `DEFAULT_SETTLEMENT_HOOK_TYPES` tuple 在 `NpcScheduleHook` 之后插入 `PrivateChatTriggerHook`

**设计决策**：
- 冷静期用 `TimeSlice.absolute_tick()` = `(day-1)*24+slot` 作为单调计数器，无需额外状态
- Hook 直接 mutation FlagSlice（与 NpcScheduleHook 直接修改 AreaSlice 的模式一致）
- Phase C（阈值参数化、LLM 生成开场白、NPC 主动发起完整对话流）留后续

**测试**：新建 `tests/test_private_chat_trigger.py`（19 个测试）：
- `TestBasicPrivateChatTriggerEvaluator`（5）：romance/trust/intimate 各触发 + 无触发 + 优先级
- `TestNullEvaluator`（1）：从不触发
- `TestPrivateChatTriggerHook`（13）：no_relations / 各阈值触发 / 冷静期活跃/过期 / 冷静期写入值 / 多 NPC / name 从 registry / name 回退 id / null evaluator / metadata / 无 FlagSlice 降级

**测试基线**：714 passed（+19，零回归）

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
