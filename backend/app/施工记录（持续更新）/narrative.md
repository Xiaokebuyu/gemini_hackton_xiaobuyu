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
| `AgenticExecutor` | [完成] | 稳定工具执行循环 + 错误 metadata contract（无 LLM 集成） |
| `RoleToolRegistry` | [完成] | gm/npc/teammate 分桶 + 同名工具按序覆盖 |
| `ToolResult` | [完成] | success/message/commands/metadata |

### 叙事规划（planning/）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlanner` | [完成] | 确定性默认 planner（任务播种 + 停滞响应 + 解冻） |
| `DynamicSubAreaManager` | [完成] | AreaSlice-backed create/expire/list_active |
| `PlanningDirective` 系列 | [完成] | 9 种指令类型 dataclass |

### Hook 边界状态（编排层联动）

| 组件 | 状态 | 说明 |
|------|------|------|
| `NarrativePlannerHook` | [完成] | planner 输入 contract + 最小 directive 子集执行 |
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

## 填充 TODO

- [ ] `AgenticExecutor`：接入 LLM agentic 循环（Gemini function calling）
- [ ] `RoleToolRegistry`：traits 过滤逻辑
- [ ] 具体 AgentTool 实现（GM 工具 16 个 / NPC 工具 / 队友工具）
- [ ] `NarrativePlanner`：更丰富的规划策略（超出当前最小任务播种 / 停滞响应子集）
