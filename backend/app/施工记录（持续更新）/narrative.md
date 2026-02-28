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

## 填充 TODO

- [x] `AgenticExecutor`：接入 LLM agentic 循环 — D-N10 完成
- [ ] `RoleToolRegistry`：traits 过滤逻辑
- [x] GM 工具 5 个 — D-N08 完成
- [x] NPC 工具 8 个 — D-N09 完成
- [x] Teammate 工具 6 个 — D-N09 完成
- [x] `NarrativePlanner`：更丰富的规划策略 — D-N07 完成
