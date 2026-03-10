# P19 — 记忆系统改造与 Planner 交互修复

创建时间：2026-03-09
状态：待实施
前置：P18（知识图谱持久化）已完成

---

## 背景

专项调查发现两大类问题：

1. **Planner ↔ NPC 交互路径断裂**：Planner 通过 `direct_npc` 下发 directive，但 NPC 只在玩家主动对话时才消费。无主动对话触发路径，导致 directive 堆积无效。
2. **记忆系统效果不佳**：L6 预注入不看对话内容、ContextWindow 200K 永不溢出导致 write_episode 无效触发、上下文不持久化。

---

## Phase A：Planner ↔ NPC 交互修复

**目标**：让 Planner 的 directive 能被有效消费，NPC 能主动找玩家说话。

### A1: Directive → NPC 主动对话触发

当 NPC 收到高优先级 directive 且玩家在同一区域时，系统主动触发 NPC 对话（类似 `npc_wants_to_chat` SSE 事件，但由 directive 驱动而非好感度阈值）。

- 新增 `DirectiveTriggerHook`（SettlementHook，检测未消费的高优先级 directive）
- 或扩展 `PrivateChatTriggerHook` 增加 directive 触发源
- 前端已有 `npc_wants_to_chat` SSE 处理，可复用

**体量**：~60 行产品代码 + ~40 行测试

### A2: Planner 感知 pending directives

`_format_planner_context()` 增加"未消费指令摘要"段落，让 Planner 看到自己之前发了什么还没被消费，避免重复下发。

- 修改 `narrators.py` `_format_planner_context()`
- NarrativePlannerHook 传入 pending directives 到 context

**体量**：~20 行

### A3: Directive kind 约束 + prompt 约束

- Planner prompt 增加 `directive.kind` 合法值列表（`talk / move / wait / react`）
- prompt 明确禁止写完整台词，只给行为意图

**体量**：~10 行 prompt 修改

### A4: 同 NPC 指令去重/覆盖

`add_directive()` 对同一 NPC 的同类 kind 做覆盖（新指令替换旧指令），避免堆积。

- 修改 `NarrativePlanSlice.add_directive()` 或 `InstanceManager.add_directive()`

**体量**：~20 行 + ~20 行测试

### Phase A 总计：~110 行产品代码 + ~60 行测试

---

## Phase B：RecallTool + L6 改造

**目标**：NPC/Teammate 从"被动注入知识"改为"主动回忆"，查询质量由 LLM 自身决定。

### B1: RecallTool 实现

新增 `RecallTool`（`character_tools.py`），NPC/Teammate 在对话中需要查知识时主动调用。

- 接受 `query: str` 参数
- 通过 `context.metadata["memory_retriever"]` 调用图谱查询
- 返回格式化的 hits 给 LLM

**体量**：~40 行

### B2: memory_retriever 注入

`agent_orchestration.py` 中将 `memory_retriever` 注入到 `context.metadata`，使 RecallTool 可用。

**体量**：~10 行

### B3: 简化 L6 预注入

- NPC/Teammate 的 `build_*_context()` 中 `_build_l6()` 调用移除或降级
- `_build_npc_prompt_text()` 中 `knowledge_block` 构建移除
- 清理 `_extract_scene_keywords()` 及相关签名

**体量**：-25 行（净删除）

### B4: NPC/Teammate prompt 增加 recall 使用说明

prompt tool_rules 段增加 recall 使用说明。

**体量**：~5 行

### Phase B 总计：~55 行净变更 + ~40 行测试

---

## Phase C：滑动窗口 + 持久化

**目标**：ContextWindow 缩至 32K 滑动窗口，溢出触发批量图谱化，窗口跨 session 持久化。

### C1: ContextWindow 参数调整 + 图谱化计数器

- `max_tokens` 从 200K 改为 32K（NPC/Teammate 默认值）
- 新增 `graphize_token_counter`：独立累计新增 token，到 32K 触发图谱化，计数器归零
- 窗口本身继续滑动，不清空

**体量**：~30 行

### C2: 窗口消息序列化

- `NarrativePlanSlice` 新增 `context_windows: dict[str, list]` 字段
- `ContextWindow` 新增 `export_messages()` / `import_messages()` 方法
- save 时序列化各 actor 的窗口消息

**体量**：~50 行 + ~30 行测试

### C3: Session 恢复重建窗口

- `GameRuntime._restore_knowledge_graph()` 扩展，恢复 ContextWindow 到 InstanceManager

**体量**：~20 行

### C4: 删除 proactive extraction 补丁

- `agent_orchestration.py` 中删除 `_collect_npc_interaction_exchange` / `_collect_private_chat_exchange` 的 proactive 路径
- 保留溢出路径作为唯一触发

**体量**：-30 行（净删除）

### Phase C 总计：~70 行净变更 + ~60 行测试

---

## Phase D：Planner/GM 上下文扩展

**目标**：Planner 获得滑动上下文窗口，保留跨 tick 推理记忆。

### D1: Planner 100K 滑动上下文窗口

- `AgenticNarrativePlanner` 持有独立 ContextWindow（max_tokens=100K）
- 每次 plan() 调用后将输入上下文 + 输出存入窗口
- 下次 plan() 时作为 conversation_history 传入（或拼入 user message）
- 持久化复用 Phase C 机制

**体量**：~40 行 + ~20 行测试

### D2: GM 知识图谱接入（评估）

评估 GM 是否需要 RecallTool 或其他知识感知机制。GM 不对话，可能更适合在 context 中注入关键世界事实而非工具调用。视 Phase A-C 实施后效果决定。

**体量**：待定

### Phase D 总计：~40 行 + 测试（D2 待定）

---

## Phase E：收尾

### E1: story_facts relation enum 约束

Planner prompt 增加合法 relation 列表：`knows_about / interacted_with / made_promise / related_to / has_opinion_of`。

**体量**：~5 行

### E2: BFS deque 优化

`_spread_activation_in_graph()` 中 `frontier.pop(0)` 改为 `collections.deque.popleft()`。

**体量**：~3 行

### Phase E 总计：~8 行

---

## 总体量估算

| Phase | 产品代码 | 测试 | 核心改动 |
|-------|---------|------|----------|
| A | ~110 行 | ~60 行 | Planner ↔ NPC 交互 |
| B | ~55 行 | ~40 行 | RecallTool + L6 简化 |
| C | ~70 行 | ~60 行 | 滑动窗口 + 持久化 |
| D | ~40 行 | ~20 行 | Planner 上下文窗口 |
| E | ~8 行 | — | 收尾优化 |
| **合计** | **~283 行** | **~180 行** | |

---

## 执行顺序

```
Phase A（交互修复）→ Phase B（RecallTool）→ Phase C（滑动窗口）→ Phase D（Planner 扩展）→ Phase E（收尾）
```

A 是体验影响最大的，优先做。B 和 C 相对独立可并行。D 依赖 C 的基础设施。E 随时可做。

---

## 涉及文件预览

| 文件 | Phase |
|------|-------|
| `app/game_core/orchestration/hooks/narrative_planner.py` | A |
| `app/game_core/state/slices/narrative_plan.py` | A, C |
| `app/game_core/narrative/instance_manager.py` | A, C |
| `app/narrators.py` | A, D, E |
| `app/game_core/narrative/character_tools.py` | B |
| `app/agent_orchestration.py` | B, C |
| `app/game_core/narrative/context_builder.py` | B |
| `app/game_core/narrative/context_window.py` | C |
| `app/game_core/runtime.py` | C |
| `app/world_knowledge_graph.py` | E |
