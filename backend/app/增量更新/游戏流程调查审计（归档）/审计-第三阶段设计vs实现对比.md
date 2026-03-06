# 审计：第三阶段（正式游戏循环）设计 vs 实现对比

创建时间：2026-03-06
状态：审计完成，待逐项深化
源设计文档：`app/博德之门3架构设计规范/编排层设计规范.md`

---

## 总览

第三阶段覆盖"每个 Tick"的完整生命周期：玩家动作 → 引擎执行 → Agent 叙述 → 时间累积 → 格结算。

**匹配度概要**：核心骨架（TickCoordinator、时间经济、SceneBus、NPC/私聊管线）基本匹配。主要缺口集中在 PipelineOrchestrator 的 B/C 阶段和 EventEngine 调用频次。

---

## 缺口清单

### GAP-1：EventEngine 主管线调用缺失（A6 + C1）

**设计要求**：EventEngine.check_conditions() 在每个 tick 中被调用 4 次：
- A6：主管线引擎执行后（pre-check）
- C1：主管线 Agent 操作后（post-check）
- P10：格结算延时事件（check_scheduled）
- P50：格结算条件检查（check_conditions）

**实际实现**：仅 P10 + P50（2 次/tick），A6 和 C1 完全缺失。

**影响**：
- 引擎执行后立即满足的事件条件，要等到下次格结算 P50 才被检测
- Agent 工具调用（如 NPC advance_quest → set_flag）产生的状态变化同样延迟
- 最坏延迟：5 个 1/6 动作（约 5 次玩家操作后才触发结算）

**严重度**：中 — 事件不会丢失（P50 最终捕获），但响应延迟影响叙事连贯性。

**修复方案**：在 `pipeline.py` 的 `process()` 中 A5 之后、after_agents 之前各加一次 EventEngine 调用。需要将 EventEngine 实例注入到 PipelineOrchestrator 或通过 SharedContext 传递。

**深化状态**：[ ] 待实施

---

### GAP-2：B2 NPC 被动反应缺失

**设计要求**：PipelineOrchestrator B 阶段包含 B2 — 场景中有 NPC 时，NPC 会对玩家动作做出被动反应（对话 + 可选 Command）。执行顺序固定：GM(B1) → NPC(B2) → Teammate(B3)。

**实际实现**：
- B1(GM) 和 B3(Teammate) 在 `TickCoordinator.agent_round_hooks` 中实现
- B2(NPC 被动反应) **完全不存在** — NPC 只在玩家主动点击"交谈"（`/interact/stream`）时才有反应

**影响**：
- 玩家在 NPC 面前施法/战斗/搜索/移动时，NPC 不会做出任何被动反应
- 降低游戏沉浸感（NPC 像木头人）
- 设计中描述的"NPC Agent → 对话 + 可选 Command（仅自身相关）"能力未被利用

**严重度**：中 — 不影响核心功能，但显著影响游戏体验质量。

**修复方案**：在 `agent_orchestration.py` 的 agent round runner 中，GM 反应之后、Teammate 反应之前，增加一步"场景 NPC 被动反应"。需要判断场景中是否有 NPC、NPC 是否有理由反应（避免每次都触发）。

**深化状态**：[ ] 待实施

---

### GAP-3：C 阶段几乎全空（C1/C3/C4）

**设计要求**：
- C1：EventEngine.check_conditions()（Agent 操作后二次检查）
- C2：PipelineHook(after_agents) 扩展点
- C3：同伴事件分发 — CompanionInstance 接收本次动作的所有事件，更新内部状态（记忆摘要、事件日志）
- C4：汇总 PipelineResult → SSE 推送

**实际实现**：
- C1：缺失（同 GAP-1）
- C2：骨架存在（`_hooks_for("after_agents")` 循环），但**没有任何注册的 hook**
- C3：**完全缺失** — 没有 CompanionInstance 概念，没有事件日志更新
- C4：PipelineResult 汇总存在，但 SSE stream 由 TickCoordinator 和应用层处理

**影响**：
- C1 影响同 GAP-1
- C3 影响：同伴不会记住"这一轮发生了什么"，长期影响同伴对话质量和个性化
- C2/C4 影响小（C2 是扩展点，暂无消费者；C4 功能已在其他位置实现）

**严重度**：
- C1：中（同 GAP-1）
- C3：中低 — 当前同伴记忆通过 SharedExperienceHook(P62) 在结算时部分补偿
- C2/C4：低

**修复方案**：
- C1：同 GAP-1 一起修
- C3：需要设计 CompanionInstance 事件日志机制，或扩展现有 SharedExperienceHook 的粒度

**深化状态**：[ ] 待实施

---

### GAP-4：PipelineOrchestrator B 阶段位置偏移

**设计要求**：B 阶段（GM/NPC/Teammate Agent 反应）在 PipelineOrchestrator 内部执行，作为 `_stage_b()` 方法。

**实际实现**：B 阶段被移到 TickCoordinator 的 `agent_round_hooks` 链中，在 pipeline.process() 返回之后、时间累积之前执行。

**影响**：
- 功能等价 — Agent 反应仍在引擎执行后、结算前发生
- 但 Agent 反应产生的 delta 通过 `_apply_delta` 回调写入 TickCoordinator 的 change_log，而非 PipelineResult
- 设计偏差已在代码注释中标注

**严重度**：低 — 架构决策合理（避免 PipelineOrchestrator 持有 Agent 依赖），功能无损。

**深化状态**：[x] 已由 P8 回正解决 — B 阶段已移回 PipelineOrchestrator 内部（`_stage_b_runner` injectable callable，line 171-187），TickCoordinator 的 `agent_round_hooks` 已完全移除。当前实现比设计规范更优（injectable runner vs 硬编码 `_stage_b()`），保持了六边形隔离红线。

---

### GAP-5：AdminCoordinator 不存在

**设计要求**：
```python
class AdminCoordinator:
    _tick_coordinators: dict[str, TickCoordinator]  # 会话级缓存
    async def process_player_input(session_id, input) → SSEStream
    async def process_action(session_id, action) → SSEStream
    async def process_interact(session_id, npc_id, input) → SSEStream
    async def process_private_chat(session_id, npc_id, input) → SSEStream
```

**实际实现**：路由逻辑直接在 `app/routers/gameplay.py` 的 FastAPI endpoints 中。TickCoordinator 挂在 `ManagedSession.runtime` 上，通过 `_load_session_or_404()` + `session_lock` 管理。

**影响**：
- 功能完全等价
- 缺少一层抽象意味着路由层稍"厚"，但避免了多余的间接层
- TickCoordinator 缓存通过 session 生命周期自然管理

**严重度**：低 — 扁平化路由是合理的工程选择。

**深化状态**：[x] 已接受为设计偏差，无需修复

---

### GAP-6：部分端点路由差异

**设计要求**：
- `/combat/action` — 战斗动作独立端点
- `/rest` — 休息独立端点
- `/trade` — 交易独立端点

**实际实现**：
- `/combat/action` — 不存在独立端点，战斗动作（attack/defend/disengage 等）通过 `/action/stream` 处理
- `/rest` — 不存在独立端点，rest_short/rest_long 通过 `/action/stream` 处理
- `/trade` — 不存在独立端点，trade_buy/trade_sell 通过 `/action/stream` 处理
- 额外端点：`/companion/recruit`、`/companion/dismiss`、`/opening/stream`、`/save`（设计未提及）

**影响**：
- 设计文档自己也说"navigate/rest/trade 保留为独立路由"，但实现选择统一到 action/stream
- 统一入口简化了前端调用，但丧失了特化处理的清晰性
- navigate 保留为独立端点（因为有 scene_change + hostile_entry 特殊逻辑）

**严重度**：低 — 工程选择，不影响功能。

**深化状态**：[x] 已接受为设计偏差，无需修复

---

### GAP-7：Settlement Hook 数量扩展（12 → 17）

**设计要求**：12 个内置 SettlementHook。

**实际实现**：17 个 SettlementHook，新增 5 个：
- P45 PassivePerceptionHook（自动被动感知）
- P55 MilestoneUnlockHook（任务里程碑完成）
- P62 SharedExperienceHook（同伴共享经历记录）
- P63 CampfireHook（长休营火对话）
- P65 RelationshipHook（NPC 关系阶段跃迁）

**影响**：无负面影响 — 新 Hook 插在合理位置，不破坏原有依赖链。

**严重度**：无 — 自然演进。

**深化状态**：[x] 无需修复，更新设计文档即可

---

### GAP-8：Action 类型扩展（11 → 56）

**设计要求**：11 类初始 StructuredAction。

**实际实现**：56 类 action_type，覆盖战斗/导航/经济/成长/AI Osiris 指令/容器等全部子系统。

**影响**：无负面影响 — 设计的 11 类全部存在（make_camp → set_camp 重命名），其余是子系统深化的自然产物。

**严重度**：无 — 自然演进。

**深化状态**：[x] 无需修复，更新设计文档即可

---

## 匹配项（确认一致）

以下维度设计与实现基本一致，无需深化：

| 维度 | 状态 | 备注 |
|------|------|------|
| TickCoordinator.process() 主流程 | 匹配 | 实现额外增加了 action_log、emit_action_tags、event_sink |
| 时间经济（accumulate + settlement 触发） | 匹配 | while accumulated >= 1.0 循环 |
| 多格动作处理 | 匹配 | 长休/区域移动的多次结算 |
| SceneBus 生命周期 | 匹配 | 格级创建/累积/重置 |
| SceneBus 可见性模型 | 匹配 | public/system/private + audience |
| NPC 交互管线（6 步） | 匹配 | Setup → NPC Agent → GM → Teammate → Options → Return |
| 私聊管线（4 步） | 匹配 | Setup → NPC Agent → Options → Return |
| ContextAssembler 7 层上下文 | 匹配 | L0-L7 按角色可见性过滤 |
| SettlementHook 优先级排序机制 | 匹配 | HOOK_PRIORITY 类变量 + sort |
| 平凡格优化（AIOsiris should_skip） | 匹配 | 0 成本动作跳过 AI 推理 |
| 持久化后置（P5 原则） | 匹配 | pipeline 内部不做持久化（D-O22） |

---

## 深化优先级建议

按影响从大到小排序：

1. **GAP-1**（EventEngine A6+C1）— 修复简单、收益明确（事件响应延迟 → 即时）
2. **GAP-2**（NPC 被动反应）— 需要较多设计（何时触发、频率控制），但对体验提升大
3. **GAP-3 之 C3**（同伴事件分发）— 中等工作量，影响同伴长期记忆质量
4. **GAP-3 之 C1**（同 GAP-1 一起修）
5. 其余（GAP-4/5/6/7/8）— 已接受为设计偏差或自然演进，无需修复
