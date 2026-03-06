# GAP-2 深化调查：NPC 被动反应质量补齐

创建时间：2026-03-06
状态：设计完成，待确认后实施
前置依赖：无（可独立实施）

---

## 调查结论

**审计文档中 GAP-2 的描述"B2 完全不存在"已不准确。**

`agent_orchestration.py:357` 的 `run_post_action_round()` 已实现 B1→B2→B3 完整序列：
- B1: `_generate_gm_reaction_from_shared()` (line 380)
- B2: `_generate_npc_reactions_from_shared()` (line 396)
- B3: `_generate_teammate_reactions_from_shared()` (line 405)

通过 `runtime.py:218` 的 `set_agent_round_runner()` 接入 TickCoordinator，在 `pipeline.py:166` 的引擎执行后被调用。

**但存在 5 个质量缺口**，影响被动反应的自然度和上下游信息流通。以下逐一分析并给出修复方案。

---

## 缺口 A：NPC/Teammate 反应未写入 SceneBus

### 现状

`run_post_action_round()` 中，GM 反应写入 SceneBus（line 386-393），但 NPC 和 Teammate 反应只 `_emit()` 为 SSE 事件，不写入 SceneBus。

```python
# GM — 有 SceneBus 写入 ✓
for event in gm_events:
    shared.scene_bus.add_entry({...})
    await _emit(event)

# NPC — 无 SceneBus 写入 ✗
for event in npc_events:
    await _emit(event)

# Teammate — 无 SceneBus 写入 ✗
for event in teammate_events:
    await _emit(event)
```

### 影响

1. **Teammate 看不到 NPC 说了什么** — 队友反应在 NPC 之后执行，但 SceneBus 里没有 NPC 的话，队友的反应无法参考 NPC 刚说的内容
2. **结算 Hook 感知不到** — SharedExperienceHook(P62)、CampfireHook(P63) 等依赖 SceneBus 的 Hook 遗漏 NPC 被动反应
3. **违反设计规范** — `编排层设计规范.md:697` 明确要求"每个 Agent 能看到之前 Agent 写入 SceneBus 的内容"

### 修复方案

**文件**：`app/agent_orchestration.py` — `run_post_action_round()`

在 NPC 事件循环和 Teammate 事件循环中添加 SceneBus 写入，模式与 GM 一致：

```python
# --- B2: NPC reactions ---
npc_events = await self._generate_npc_reactions_from_shared(...)
for event in npc_events:
    if event.event_type in ("npc_response", "npc_emote"):
        shared.scene_bus.add_entry({
            "source": f"NPC:{event.payload.get('npc_id', 'unknown')}",
            "content": str(event.payload.get("content", ""))
                       or str(event.payload.get("action", "")),
            "visibility": "public",
            "tags": [event.event_type, "passive_reaction"],
        })
    await _emit(event)

# --- B3: Teammate reactions ---
teammate_events = await self._generate_teammate_reactions_from_shared(...)
for event in teammate_events:
    if event.event_type == "teammate_response":
        shared.scene_bus.add_entry({
            "source": f"TEAMMATE:{event.payload.get('character_id', 'unknown')}",
            "content": str(event.payload.get("content", "")),
            "visibility": "public",
            "tags": [event.event_type, "passive_reaction"],
        })
    await _emit(event)
```

**工作量**：~15 行改动，0 新文件。

---

## 缺口 B：NPC 缺少"被动观察"模式提示词

### 现状

NPC 被动反应和 NPC 交互对话共用同一个 `_build_npc_prompt_text()` 系统提示词（`context_builder.py:1180`）。该提示词末尾写着：

> "You MUST respond when spoken to — do not use `pass_turn`."

这对交互对话合理，但对被动反应不合适。NPC 并没有被"对话"，它只是观察到了一个玩家动作。

### 影响

- NPC 被迫说话，即使动作跟自己毫无关系（例如玩家翻箱倒柜，旁边的铁匠也要评论）
- 反应语气容易像"被搭话"而非"旁观"

### 修复方案

**文件**：`app/game_core/narrative/context_builder.py`

1. `_build_npc_prompt_text()` 新增 `is_passive: bool = False` 参数
2. 当 `is_passive=True` 时，替换 Tool usage rules 的最后两条：

```python
if is_passive:
    tool_rules = """\
## Tool usage rules
- You just witnessed a player action. You are NOT being spoken to directly.
- If the action is relevant to you, react briefly with `speak` (1-2 sentences max) or `emote`.
- If the action has nothing to do with you, use `emote` with a brief idle action or do nothing.
- Use `update_feeling` only if the action genuinely changes your feelings.
- Do NOT initiate conversation topics or offer quests/trade unprompted.
- Do not output plain text outside tool calls.
- Use at most one visible response: one `speak` OR one `emote`.

## Language
Respond in the same language as the game context.\
"""
else:
    tool_rules = ... # 现有逻辑不变
```

3. `build_npc_full_context()` 新增 `is_passive: bool = False` 参数，透传给 `_build_npc_prompt_text()`

**文件**：`app/agent_orchestration.py` — `_generate_npc_reactions_from_shared()`

调用 `build_npc_full_context()` 时传入 `is_passive=True`：

```python
npc_full = await builder.build_npc_full_context(
    npc_id,
    memory_retriever=self._memory_retriever,
    active_directive=active_directive,
    is_passive=True,  # 新增
)
```

**工作量**：~30 行改动，0 新文件。

---

## 缺口 C：NPC 角色无法静默（无 pass_turn 工具）

### 现状

`executor.py:147-153` 对 NPC 角色的处理：如果 LLM 不调用任何工具，直接返回 `protocol_error`。NPC 没有 `pass_turn` 工具（只有 GM 和 Teammate 有）。

这意味着一旦 NPC Agent 被调用，它**必须**调用至少一个工具（speak/emote/refuse 等）。如果 `_should_character_respond` 概率门控通过但实际动作无关，NPC 被迫说些无意义的话。

### 修复方案

**方案选择**：不给 NPC 添加 `pass_turn` 工具（会增加工具注册复杂度），而是在 executor 中允许被动模式的 NPC 静默。

**文件**：`app/game_core/narrative/executor.py`

在 `run_agentic()` 的无工具响应分支中，检查 metadata 中的 passive 标记：

```python
if not response.tool_calls:
    final_text = (response.text or "").strip()
    if role == "npc":
        # 被动模式允许 NPC 不调用工具（等价于 pass_turn）
        is_passive = (context.metadata or {}).get("is_passive", False)
        if is_passive:
            return AgentResult(
                text="",
                tool_results=all_results,
                turns_used=turn + 1,
                metadata={"status": "completed", "finish_reason": "pass_turn"},
            )
        return self._protocol_error_result(...)
```

**文件**：`app/agent_orchestration.py` — `_generate_npc_reactions_from_shared()`

构建 context 时注入 passive 标记：

```python
context = builder.build_agent_context(
    "npc",
    npc_id,
    execute_command=execute_command,
    metadata={
        **({"memory_writer": memory_writer} if memory_writer is not None else {}),
        "is_passive": True,
    },
)
```

同时，在 `_generate_npc_reactions_from_shared` 的结果处理中，跳过 pass_turn 的 NPC（不产生 SSE 事件，不写 context_window）：

```python
if agent_result.metadata.get("finish_reason") == "pass_turn":
    continue  # NPC 选择沉默，跳过
```

**工作量**：~15 行改动，0 新文件。

---

## 缺口 D：缺少动作类型过滤

### 现状

`run_post_action_round()` 只检查 `result.success`，不过滤 action_type。所有成功动作都会触发 NPC 被动反应流程（包括 `look_inventory`、`check_stats`、`equip` 等元操作）。

这些"内务"动作不应该触发 NPC 反应 — 现实中 NPC 不会因为你打开背包就评论。

### 修复方案

**文件**：`app/agent_orchestration.py`

在 `_generate_npc_reactions_from_shared()` 入口处添加黑名单过滤：

```python
# 不触发 NPC 被动反应的元操作动作
_NPC_PASSIVE_SKIP_ACTIONS: set[str] = {
    "noop",
    "look_inventory", "equip", "unequip", "use_item", "drop_item",
    "check_stats", "check_quest_log", "check_map",
    "save_game", "load_game",
    "trade_buy", "trade_sell",  # 交易已有专用交互
}

async def _generate_npc_reactions_from_shared(self, shared, result, ...):
    if result.action_type in _NPC_PASSIVE_SKIP_ACTIONS:
        return []
    ...
```

**工作量**：~10 行改动，0 新文件。

---

## 缺口 E：user_message 缺少被动观察语义

### 现状

NPC 被动反应的 `user_message` 是：

```json
{
    "action_type": "skill_check",
    "success": true,
    "narrative_hints": ["..."],
    "time_cost": 0.17
}
```

NPC 从这条消息无法区分"我被搭话了"还是"我看到旁边发生了什么"。

### 修复方案

**文件**：`app/agent_orchestration.py` — `_generate_npc_reactions_from_shared()`

丰富 user_message，明确标记为被动观察：

```python
user_message = json.dumps(
    {
        "context": "passive_observation",  # 新增：明确是被动观察
        "what_happened": (
            result.narrative_hints[0]
            if result.narrative_hints
            else f"The player performed: {result.action_type}"
        ),
        "action_type": result.action_type,
        "success": result.success,
        "time_cost": result.time_cost,
    },
    ensure_ascii=False,
    default=str,
)
```

**工作量**：~5 行改动。

---

## 实施计划

### 单 Phase，预计改动量

| 缺口 | 文件 | 改动行数 | 风险 |
|------|------|---------|------|
| A: SceneBus 写入 | `agent_orchestration.py` | ~15 | 低 |
| B: 被动提示词 | `context_builder.py` + `agent_orchestration.py` | ~30 | 低 |
| C: NPC 静默允许 | `executor.py` + `agent_orchestration.py` | ~15 | 低（仅影响 is_passive=True 路径） |
| D: 动作类型过滤 | `agent_orchestration.py` | ~10 | 低 |
| E: user_message 语义 | `agent_orchestration.py` | ~5 | 低 |
| **合计** | **3 个文件** | **~75 行** | **低** |

### 改动文件清单

1. `app/agent_orchestration.py` — 缺口 A/B/C/D/E 全部在此
2. `app/game_core/narrative/context_builder.py` — 缺口 B（`_build_npc_prompt_text` + `build_npc_full_context` 参数透传）
3. `app/game_core/narrative/executor.py` — 缺口 C（NPC passive pass_turn 放行）

### 不改动

- `tick_coordinator.py` — 无需改（agent_runner 注入链路已正确）
- `pipeline.py` — 无需改
- `runtime.py` — 无需改
- `character_tools.py` — 不给 NPC 添加 pass_turn 工具

### 执行顺序

1. 缺口 D（动作类型过滤）— 最简单，立即减少不必要的 LLM 调用
2. 缺口 E（user_message 语义）— 无依赖，独立改
3. 缺口 B（被动提示词）— 需要 E 先就位（提示词与 user_message 语义配合）
4. 缺口 C（NPC 静默允许）— 需要 B 先就位（提示词告诉 NPC 可以不说话后，executor 才需要处理静默）
5. 缺口 A（SceneBus 写入）— 最后做，因为需要确认 NPC 反应内容质量后再写入 SceneBus

### 测试计划

新增 ~10 个测试：

1. **test_npc_passive_reaction_writes_to_scene_bus** — 缺口 A
2. **test_teammate_sees_npc_passive_in_scene_entries** — 缺口 A 下游验证
3. **test_npc_passive_prompt_differs_from_interactive** — 缺口 B
4. **test_npc_passive_can_pass_turn** — 缺口 C
5. **test_npc_interactive_still_requires_tool** — 缺口 C 回归
6. **test_npc_passive_skips_meta_actions** — 缺口 D
7. **test_npc_passive_triggers_on_combat_actions** — 缺口 D 正向
8. **test_npc_passive_user_message_has_observation_context** — 缺口 E
9. **test_npc_passive_silent_no_sse_events** — 缺口 C 端到端
10. **test_npc_passive_reaction_in_context_window** — 已有逻辑回归

---

## 需要确认的问题

### Q1：`_NPC_PASSIVE_SKIP_ACTIONS` 黑名单范围

上面列的黑名单是保守版本。以下动作需要你确认是否也跳过：

- `navigate` — 玩家移动到新区域时，NPC 是否应该反应？（新区域的 NPC 说"欢迎"？还是跳过？）
- `rest_short` / `rest_long` — 短休/长休时 NPC 是否反应？（SharedExperienceHook 和 CampfireHook 已覆盖队友反应，NPC 是否需要？）
- `attack` / `defend` / `disengage` — 战斗中的单次动作是否触发 NPC 反应？（战斗中每轮都触发可能太频繁）

**我的建议**：`navigate` 保留触发（NPC 对玩家到来有反应很自然），`rest_*` 跳过（已有专用 Hook），战斗动作跳过（等 `COMBAT_END` tag 在结算时统一处理）。

### Q2：被动反应的 LLM 成本

当前 `_collect_nearby_npcs` 上限是 3 个 NPC。每个被动反应是一次 LLM 调用（最多 2 turn）。加上概率门控（base tendency 0.2），平均每次动作约 0-1 个 NPC 实际调用 LLM。

这个成本你接受吗？如果觉得太高，可以：
- 降低 base tendency 到 0.1
- 限制 `_collect_nearby_npcs` 上限为 1
- 增加全局冷却（如每 3 tick 最多触发 1 次 NPC 被动反应）

**我的建议**：当前参数合理，暂不调整。概率门控 + 动作类型过滤（缺口 D）已经足够控制频率。
