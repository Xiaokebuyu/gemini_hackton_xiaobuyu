# L1 上层 Agent 地图 — 深审定稿

> 创建时间：2026-02-23
> 历次审计：Codex 补勘 → **Claude Opus 四路代理逐文件深审**（本版）
> 验证方法：每个文件检查 imports 段 + LLM 调用链 + except 计数 + L3 直连点
> 状态：**深审定稿，全部分类经代码级验证，L1 支撑组件已调和归 L3**

---

## 一、L1 定义（本文件口径）

**L1 = Agent 决策层 + LLM 能力层 + 工具编排层**，核心职责是：
- 决定"该做什么"（策略、对话、工具调用）
- 调用 L2/L3 执行，而不是直接改底层状态
- 输出叙述、对话、结构化推理结果

**判定标准**：
- 文件内有 LLM/Gemini SDK 调用（`genai.Client`），或委托 LLMService 调用 LLM
- 定义 Agent 可调用工具（LLM function-calling 合约）
- 做 Agent 决策编排逻辑（工具分发、角色路由、记忆图谱化）

**不属于 L1**：
- 纯规则/状态机 → L3
- tiktoken 计数 → L3
- 纯编排路由（不含 LLM）→ L2
- `TYPE_CHECKING` 下的导入不计入运行时依赖

---

## 二、全量 L1 模块地图（深审后共 10 个 L1 模块）

### 2.1 Agent 执行引擎（4 个，均在 `app/world/` ❌ 层级错位）

| # | 模块 | 文件 | 行数 | 评分 | 验证 | LLM 调用 | except Exception | 备注 |
|---|------|------|------|------|------|----------|------------------|------|
| 1 | AgenticExecutor | `world/agentic_executor.py` | 198 | B | ✓ | 1 async (`agentic_generate` L83) | 2 (L136,L189) | GM/NPC/队友统一执行器；❌ 放在 L3 目录 |
| 2 | ImmersiveTools | `world/immersive_tools.py` | 587 | C- | ✓ | 0 | 4 (L289,L305,L322,L337) | 30 个沉浸式工具定义 + AgenticContext；❌ 放在 L3 目录 |
| 3 | GmExtraTools | `world/gm_extra_tools.py` | 411 | D+ | ✓ | 0 | 9 (L83,L112,L159,L187,L219,L244,L276,L359,L385) | 8 个 MCP 依赖 GM 工具；❌ 放在 L3 目录 |
| 4 | RoleRegistry | `world/role_registry.py` | 65 | A- | ✓ | 0 | 0 | 角色→工具集映射；❌ 放在 L3 目录 |

### 2.2 LLM 能力层（3 个）

| # | 模块 | 文件 | 行数 | 评分 | 验证 | LLM 调用 | except Exception | 备注 |
|---|------|------|------|------|------|----------|------------------|------|
| 5 | LLMService | `services/llm_service.py` | 918 | C- | ✓ | **12**（7 sync + 5 async） | **15** | 唯一 Gemini 文本网关；7 处同步阻塞事件循环 |
| 6 | ImageGenerationService | `services/image_generation_service.py` | 153 | B+ | ✓ | 1 async (L55) | 2 (L65,L124) | 独立 genai.Client；异常处理质量最优 |
| 7 | TieredAIService | `services/tiered_ai_service.py` | 219 | C | ✓ | 0 直接（2 处委托 LLMService） | 2 (L103,L130) | NPC 分层响应路由；被 PasserbyService 使用 |

### 2.3 Agent 编排层（3 个）

| # | 模块 | 文件 | 行数 | 评分 | 验证 | LLM 调用 | except Exception | 备注 |
|---|------|------|------|------|------|----------|------------------|------|
| 8 | TeammateResponseService | `services/teammate_response_service.py` | 1161 | B- | ✓ | 2 async (LLMService L548 + AgenticExecutor L1007) | 8 | 队友决策+响应；choose_battle_action 重名冲突 |
| 9 | MemoryGraphizer | `services/memory_graphizer.py` | 699 | C | ✓ | 1 async (LLMService L302) | 1 (L150) | 对话→图谱转换；**L1/L3-M2 混合**（直写 WorldGraph） |
| 10 | EventLLMService | `services/admin/event_llm_service.py` | 270 | B+ | ✓ | 2 async (LLMService L96,L189) | 0 | 事件 NLP 3 步管线；❌ 放在 admin/(L2 目录) |

### 2.4 L1 提示模板（8 个 .md）

| # | 文件 | 用途 | 消费方 |
|---|------|------|--------|
| 11 | `prompts/flash_agentic_system.md` | V4 Agentic GM 系统提示 | PipelineOrchestrator → AgenticExecutor |
| 12 | `prompts/flash_cpu_system.md` | Flash CPU 系统提示 | FlashCPUService |
| 13 | `prompts/teammate_agentic_system.md` | 队友 Agentic 系统提示 | TeammateResponseService → AgenticExecutor |
| 14 | `prompts/teammate_decision.md` | 队友决策提示 | TeammateResponseService |
| 15 | `prompts/teammate_response.md` | 队友响应提示 | TeammateResponseService |
| 16 | `prompts/travel_narration.md` | 旅行叙述提示 | PipelineOrchestrator |
| 17 | `prompts/opening_narration.md` | 开场叙述提示 | AdminCoordinator |
| 18 | `prompts/session_resume.md` | 会话恢复提示 | AdminCoordinator |

---

## 三、旧版错误纠正清单

### 3.1 从 L1 移出（深审调和 → L3）

| 模块 | 文件 | 旧版归属 | 深审结论 | 原因 |
|------|------|----------|----------|------|
| InstanceManager | `services/instance_manager.py` | L1 支撑 | **L3** ✓ | 运行时零 LLM import；`MemoryGraphizer` 延迟导入委托所有 LLM 工作 |
| ContextWindow | `services/context_window.py` | L1 支撑 | **L3** ✓ | tiktoken ≠ LLM；纯滑动窗口数据结构 + 机械 token 计数 |
| TeammateVisibilityManager | `services/teammate_visibility_manager.py` | L1 支撑 | **L3** ✓ | 纯规则引擎；零 I/O、零 LLM、零导入 |

### 3.2 旧版遗漏与纠错

| 类型 | 旧版问题 | 补正结论 |
|------|----------|----------|
| 幽灵模块 | `flash_llm_service.py` 被列为 L1 | 当前仓库不存在，已移除 |
| 幽灵模块 | `scene_bus_graphizer.py` 被列为 L1 | 当前仓库不存在，已移除 |
| 模块遗漏 | 未纳入 `image_generation_service.py` | 独立 L1 模块，拥有自己的 genai.Client |
| 数字错误 | `gm_extra_tools.py` 裸异常写成 10 处 | 实测 **9 处** |
| 数字错误 | 沉浸工具直通 L3 写成 18 个 | 实测 **18 处** ctx.session.*（含 1 处 raw dict write），正确 |
| 层级错误 | TieredAIService 未纳入 L1 地图 | 实际由 PasserbyService 使用，委托 LLMService，归 L1 |
| 评分调整 | EventLLMService 评分 B | 调升 **B+**（0 个裸异常，纯函数式 LLM 转换，本轮最干净文件） |

---

## 四、L1 核心屎点（深审版）

### P0-1：LLMService 7 处同步 Gemini 调用阻塞事件循环

`self.client.models.generate_content(...)` 在 `async def` 内同步执行 HTTP 往返，阻塞整个 FastAPI 事件循环。

| # | 行号 | 方法 | 模型 |
|---|------|------|------|
| 1 | `llm_service.py:197` | `generate_response` | `self.main_model` |
| 2 | `llm_service.py:431` | `generate_response_stream` | `self.main_model` |
| 3 | `llm_service.py:491` | `analyze_messages_for_archive` | `self.flash_model` |
| 4 | `llm_service.py:548` | `should_merge_topics` | `self.flash_model` |
| 5 | `llm_service.py:599` | `merge_artifacts` | `self.main_model` |
| 6 | `llm_service.py:732` | `classify_for_archive` | `self.flash_model` |
| 7 | `llm_service.py:898` | `generate_with_tools` | `self.main_model` |

**修复**：全部替换为 `await self.client.aio.models.generate_content(...)`。
**额外**：`generate_response_stream`（L431）是同步迭代伪装成 async generator，需改为 `async for chunk in await self.client.aio.models.generate_content_stream(...)`。

### P0-2：沉浸工具 18 处直通 L3（ctx.session.*）

`immersive_tools.py` 中 18 处 `ctx.session.*` 调用直接操作 SessionRuntime，未经 L2 API。

| 行号 | 调用 | 类型 |
|------|------|------|
| 170 | `update_disposition(npc_id, deltas, reason)` | facade |
| 206 | `await recall(role, actor_id, seeds, ...)` | async facade |
| 235 | `record_memory(owner_id, memory_type, ...)` | facade |
| 304 | `complete_event(event_id, outcome_key)` | facade |
| 320 | `advance_chapter(target_chapter_id, transition_type)` | facade |
| 335 | `fail_event(event_id, reason)` | facade |
| **354** | **`flash_results[prompt] = bool(result)`** | **raw dict write ❌** |
| 364 | `heal(amount)` | facade |
| 369 | `damage(amount)` | facade |
| 376 | `add_xp(amount)` | facade |
| 387 | `add_item(item_id, name, qty)` | facade |
| 397 | `remove_item(item_id, qty)` | facade |
| 406 | `activate_event(event_id)` | facade |
| 412 | `complete_objective(objective_id)` | facade |
| 422 | `advance_stage(event_id, stage_id)` | facade |
| 432 | `complete_event_objective(event_id, objective_id)` | facade |
| 446 | `update_disposition(npc_id, deltas, reason)` | facade |
| 472-484 | `record_memory(owner_id, ...)` | facade |

**特别标记**：`report_flash_evaluation`（L354）直接写 `ctx.session.flash_results[prompt]`，绕过所有 facade/脏标记。

### P0-3：队友 `choose_battle_action` 工具重名冲突

- 沉浸工具 STUB（`immersive_tools.py:571`）：`roles={"teammate"}`，签名 `(action_id, target_id="")`
- 战斗时 extra tool（`teammate_response_service.py:51`）：签名 `(action_id)`（无 target_id）
- `AgenticExecutor.run()` 直接 `tools.extend(extra_tools)`（L76），无去重
- LLM 见到**两个同名工具**，签名不同，行为不可预测

### P0-4：GmExtraTools 直调 SessionRuntime 私有方法

`gm_extra_tools.py` 的 `choose_combat_action` 工具直接调用 SessionRuntime 私有方法：

| 行号 | 调用 | 问题 |
|------|------|------|
| 241 | `session.game_state.combat_id = None` | 直接属性写入，绕过 setter |
| 274 | `session._sync_tick_to_narrative(tick_result)` | **调用私有方法** |
| 275 | `session._apply_tick_side_effects(tick_result)` | **调用私有方法** |
| 99 | `session.narrative.npc_interactions[npc_id] = count+1` | 直接 dict 写入 |
| 249-251 | `getattr(session, "_behavior_engine", None)` | 访问私有属性 |

SessionRuntime 重构时这些隐式调用方会静默断裂。

### P1-1：LLMService 13 处 print() 代替 logging

`llm_service.py` 中 13/15 个 `except Exception` 处理器使用 `print()` 而非 `logger.error()`。生产环境日志聚合器无法捕获这些错误。

**位置**：L207, L450, L510, L567, L616, L705, L754, L793, L827, L852, L909 等。

### P1-2：MemoryGraphizer 非原子图谱写入

`_merge_to_world_graph()` 顺序写入多个节点/边，无事务边界、无锁、无回滚。中途异常导致 WorldGraph 永久处于部分写入状态。

- `graphize()` 外层 `except Exception`（L150）捕获但不回滚已写入的节点
- LLM 返回的 `node.id` 未做碰撞检查 — 可能覆盖现有 `"player"`、location 等关键节点

### P1-3：MemoryGraphizer 文档声称 "持久化到 Firestore" 实为虚假

`memory_graphizer.py:42` docstring 列出 "5. 持久化到 Firestore"，但实际**零 Firestore 写入**。所有持久化由下游 `SessionRuntime.persist()` 完成。

### P1-4：TeammateResponseService 静默全轮失败

`process_round()`（L421）和 `process_round_stream()`（L876）的 per-member 循环中：
- 任何异常被裸 `except Exception` 吞咽
- 产出 `TeammateResponseResult(response=None, model_used="error")`
- 调用方无法区分"有意跳过"与"硬错误"
- 无指标计数器、无断路器、无重试信号

### P1-5：TeammateResponseService 决策/响应使用不同模型常量

| 阶段 | 行号 | 模型 |
|------|------|------|
| 决策 | L547 | `settings.gemini_flash_model` |
| 响应 | L1099 | `settings.admin_flash_model` |

两个设置可能指向不同模型，无文档说明是否有意为之。

### P1-6：队友 model_config_override 完全失效

- `PartyMember.model_config_override: Optional[TeammateModelConfig]`（`party.py:60`）
- 响应生成时硬编码 `settings.admin_flash_model` + `"low"`（`teammate_response_service.py:1099-1100`）
- `default_model_config`（L99）创建后从未读取
- 配置了 `TeammateModelConfig` 的队友被静默忽略

### P1-7：TieredAIService 静默层级降级

`tiered_ai_service.py:103`：SUBCONSCIOUS 或 DEEP tier 失败时，**无任何日志**静默降级到 FAST tier。Main NPC 不可见地退化为路人品质。

`_respond_fast`（L130）失败同样静默返回硬编码字符串，零诊断输出。

### P2-1：AgenticExecutor 静默丢弃 SSE 事件

`agentic_executor.py:189`：`except Exception: pass` 吞掉 `event_queue.put_nowait()` 的所有异常（包括 `asyncio.QueueFull`）。高吞吐场景下所有 SSE 事件可能被静默丢失，无警告、无计数器、无背压信号。

### P2-2：AgenticExecutor 硬编码 30s 超时

`agentic_executor.py:131`：`asyncio.wait_for(tool_fn(**kwargs), timeout=30)` 不走 `settings`。而 `gm_extra_tools.py` 使用 `settings.admin_agentic_tool_timeout_seconds`。两个超时值可以分叉。

### P2-3：9 个沉浸工具是静默 STUB

返回 `{"success": True, "stub": True}` 但不做任何事。LLM 收到成功确认，实际无操作。

**NPC trait 工具（6 个）**：
- `evaluate_offer`（L499）、`propose_deal`、`adjust_my_prices`（merchant）
- `grant_passage`（guard）、`offer_quest`（quest_giver）、`offer_healing`（healer）

**队友工具（3 个）**：
- `express_need`（L563）、`choose_battle_action`（L570）、`assess_situation`（L581）

### P2-4：GM extra tool ENGINE_TOOL_EXCLUSIONS 覆盖不足

`gm_extra_tools.py:27`：`ENGINE_TOOL_EXCLUSIONS` 仅覆盖 2 条映射（`talk`→`npc_dialogue`、`use_item`→`ability_check`）。其他引擎已执行的操作（如 REST、MOVE）对应的 GM extra tool 可能重复执行。

### P2-5：队友工具事件类型被覆盖

`teammate_response_service.py:1022`：`evt["type"] = "teammate_tool_call"` 抹掉原始工具事件类型。

### P2-6：双重 genai.Client 实例

`LLMService.__init__`（`llm_service.py:46`）和 `ImageGenerationService.__init__`（`image_generation_service.py:22`）各自创建独立 `genai.Client`。速率限制和连接池不统一。

### P2-7：LLMService.generate_simple 异常重包装

`llm_service.py:705`：`raise Exception(f"文本生成失败: {str(e)}")` 吞掉原始异常类型（如 `ResourceExhausted`、`ContentPolicyViolation`），调用方无法区分限流/超时/内容审查。

### P2-8：TieredAIService _respond_chat 忽略 npc_profile

`tiered_ai_service.py:157-163`：FAST tier 使用 `_build_fast_prompt` 含性格/职业/外貌字段。SUBCONSCIOUS/DEEP tier 的 `_respond_chat` 只用 `npc_id/world_id/location_id/history/query` — 更贵的 tier 获得更差的上下文。

---

## 五、工具体系实测（深审版）

### 5.1 工具数量与分配

| 来源 | 工具数 | 说明 |
|------|--------|------|
| ImmersiveTools base | 5 | react/share_thought/recall/form_impression/notice |
| ImmersiveTools GM | 16 | complete_event/advance_chapter/fail_event/heal/damage/add_xp/... |
| ImmersiveTools NPC trait | 6 | merchant(3)/guard(1)/quest_giver(1)/healer(1) **全 STUB** |
| ImmersiveTools teammate | 3 | express_need/choose_battle_action/assess_situation **全 STUB** |
| GmExtraTools | 8 | npc_dialogue/start_combat/get_combat_options/choose_combat_action/add_teammate/remove_teammate/disband_party/ability_check |
| **总计** | **38** | 9 个 STUB（占 24%） |

**角色可见工具数**：
- GM：29（base 5 + GM 16 + extra 8）
- NPC：5-11（base 5 + trait 0-6，trait 工具全 STUB）
- 队友：8（base 5 + teammate 3，teammate 工具全 STUB）

### 5.2 关键统计（代码实测）

| 指标 | 数量 | 来源 |
|------|------|------|
| LLM 直接 API 调用（sync） | **7 处** | llm_service.py |
| LLM 直接 API 调用（async） | **6 处** | llm_service.py(5) + image_generation_service.py(1) |
| LLM 委托调用 | **7 处** | teammate_response(2) + memory_graphizer(1) + event_llm(2) + tiered_ai(2) |
| **LLM 调用总计** | **20 处** | 7 sync + 13 async/委托 |
| ctx.session.* 直通 L3 | **18 处** | immersive_tools.py |
| session.* 直通 L3（含私有） | **12 处** | gm_extra_tools.py |
| 私有方法调用（session._*） | **3 处** | gm_extra_tools.py L249/274/275 |
| STUB 工具 | **9 个** | immersive_tools.py |
| `except Exception` 总计 | **43 处** | llm(15)+gm_extra(9)+teammate(8)+immersive(4)+agentic(2)+tiered_ai(2)+image(2)+memory(1)+event_llm(0) |
| print() 代替 logging | **13 处** | llm_service.py |
| genai.Client 实例数 | **2 个** | llm_service + image_generation |

---

## 六、混合文件全景（L1 成分在其他层的文件中）

以下文件主体归属 L2，但含有 L1 成分：

| 文件 | 主体层级 | L1 成分 | L1 行为 |
|------|----------|---------|---------|
| `admin/flash_cpu_service.py` | L2 | **重度** | 直接 import+实例化 LLMService(L25,56)；构建 agentic 系统提示；NPC 对话生成 |
| `admin/pipeline_orchestrator.py` | L2 | 中度 | B 阶段调用 AgenticExecutor；开场/旅行叙述调用 LLMService.generate_simple_stream |
| `admin/admin_coordinator.py` | L2 | 轻度 | lazy-load LLMService(L587)；开场/恢复/过章叙述 LLM 调用 |
| `admin/event_service.py` | L2 | 轻度 | lazy-load EventLLMService(L43-44)；`ingest_event_natural()` 委托 |
| `mcp/tools/npc_tools.py` | L2 | **重度** | 自建独立 InstanceManager+LLMService（L9-10），完全独立的 L1 旁路入口 |
| `services/passerby_service.py` | L2 | 轻度 | 委托 TieredAIService.respond()（L196） |

---

## 七、L1 健康度评分（深审版）

| 维度 | 评分 | 结论 |
|------|------|------|
| Agent 执行器设计 | 7/10 | AgenticExecutor 结构清晰，单一 LLM 入口 |
| 工具体系完整性 | 3/10 | 38 个工具中 9 个 STUB（24%）；18+12 处 L3 直通；私有方法调用 |
| LLM 接入质量 | 3/10 | 7/12 直接调用仍是同步；print 代替 logging；异常重包装 |
| NPC/队友记忆一致性 | 4/10 | MemoryGraphizer 非原子；虚假 Firestore 文档 |
| 层级纯度 | 2/10 | 4 个 L1 文件在 L3 目录；1 个 L1 在 admin/ 目录；flash_cpu 混合严重 |
| 异常处理 | 2/10 | 43 个裸异常；13 处 print；静默降级/丢弃/全轮失败 |
| **总体** | **D+ (3.5/10)** | **可跑，但 LLM 阻塞 + 工具直通 + 裸异常 = 三重债务** |

---

## 八、优先级排序（L1 视角）

| 优先级 | 问题 | 影响 | 工作量 |
|--------|------|------|--------|
| **P0** | LLMService 7 处同步调用阻塞事件循环 | 并发全停 | 中 |
| **P0** | 沉浸工具 18 处直通 L3 + 1 处 raw dict write | 状态一致性 | 大（需 L2 API） |
| **P0** | 队友 choose_battle_action 同名冲突 | 战斗工具不确定 | 小 |
| **P0** | GmExtraTools 调 SessionRuntime 私有方法 | 重构时静默断裂 | 中 |
| **P1** | LLMService 13 处 print→logging | 生产不可观测 | 小 |
| **P1** | MemoryGraphizer 非原子写入 | 图谱损坏 | 中 |
| **P1** | TeammateResponseService 静默全轮失败 | 调试困难 | 小 |
| **P1** | TieredAIService 静默层级降级（无日志） | 品质退化不可见 | 小 |
| **P1** | 队友 model_config_override 失效 | 配置无效 | 小 |
| **P1** | 决策/响应用不同模型常量 | 行为分叉 | 小 |
| **P2** | AgenticExecutor SSE 事件静默丢失 | 前端丢数据 | 小 |
| **P2** | 硬编码 30s 超时 vs settings 分叉 | 配置不统一 | 小 |
| **P2** | 9 个 STUB 工具虚假成功 | LLM 以为执行成功 | 中 |
| **P2** | 双重 genai.Client | 速率限制不统一 | 小 |
| **P2** | generate_simple 异常重包装 | 错误类型丢失 | 小 |
| **P2** | TieredAIService _respond_chat 忽略 npc_profile | 高 tier 获差上下文 | 小 |

---

## 九、数字汇总

| 指标 | 数量 |
|------|------|
| L1 纯代码模块 | **10 个**（含 1 个 L1/L3-M2 混合） |
| L1 提示模板 | **8 个** |
| L1 纯代码行数 | **4,681 行** |
| LLM 调用点总计 | **20 处**（7 sync + 13 async/委托） |
| `except Exception` 总计 | **43 处** |
| L3 直通点 | **30 处**（immersive 18 + gm_extra 12） |
| STUB 工具 | **9 / 38 = 24%** |
| 层级错位文件 | **5 个**（4 个 L1 在 world/ + 1 个 L1 在 admin/） |
| 从旧 L1 移出 → L3 | **3 个**（instance_manager + context_window + teammate_visibility_manager） |

---

## 十、建议修复顺序（只列 L1 关键动作）

### 止血（1-2 天）

1. LLMService 7 处 `self.client.models.*` → `await self.client.aio.models.*`
2. 队友 `choose_battle_action` 去重：删除 STUB，保留战斗时动态注入的真实工具
3. GmExtraTools P6 `combat_ended` block 改用公共方法而非 `session._sync_tick_to_narrative`
4. `report_flash_evaluation` 改为 `ctx.session.set_flash_result(prompt, result)` 公共方法

### 可观测性（1-2 天）

5. LLMService 13 处 `print()` → `logger.error()`
6. TieredAIService L103/L130 补 `logger.warning()`
7. TeammateResponseService L421/L876 裸异常补日志 + 指标计数
8. AgenticExecutor L189 SSE 丢失补 `logger.warning()`

### L2 建设后的 L1 收口（专项）

9. 沉浸工具 18 处 `ctx.session.*` → 统一 WorldAPI 门面
10. GmExtraTools 12 处 `session.*` → WorldAPI
11. EventLLMService 从 admin/ 移到 services/
12. 4 个 L1 文件从 world/ 移到 agents/ 或 services/ai/
