# P25 Planner 运行时体验审计

创建时间：2026-03-12
更新时间：2026-03-12（P23/P24 交叉审计 + 条件追踪验证 + 因果链分析）
状态：审计完成，待实施
前置：P23 二次审计基本完成，前端 VN 模式开发中
范围：P25 原始 16 项 + P23 残留验证 + P24 双路径验证 → 共 18 项（含 P25-09/10 已吸收入 P25-13）

## 1. 背景

P23 完成后进行实际游戏测试，发现 NarrativePlanner 在真实运行时大量 directive 被拒绝（单轮 6 次 `dispatcher_rejected`），同时 Gemini API 偶尔返回空响应导致 GM 观察崩溃。本文档记录排查结果和修复方案。

---

## 2. 问题清单

### P25-01：Planner Context 缺失关键 ID 列表（根因）

**现象：** `planner directive rejected: dispatcher_rejected` × 6，settlement tick 期间 planner 生成的 directive 全部被 RulesEngine handler 拒绝。

**根因：** `_format_planner_context()` 向 LLM 提供的上下文中，NPC 和 Board 有明确的 `Allowed xxx ids` 列表，但 area 相关的 directive 缺失关键信息：

| 信息 | 是否提供 | 影响的 directive |
|------|---------|-----------------|
| `Allowed npc ids` | ✅ 有 | `direct_npc`, `curate_shop` |
| `Allowed board ids` | ✅ 有 | `publish_bulletin` |
| `Dynamic quests` 列表 | ✅ 有 | `create/retire/update_quest`, `design_reward` |
| **Allowed area_ids** | ❌ **缺失** | `plant_environmental`, `fill_area`, `plant_encounter` |
| **当前 area 的 sub_area_ids** | ❌ **缺失** | `fill_area`, `plant_encounter`, `plant_environmental` |

LLM 只能从 `Location: area=frontier_town` 推断当前区域 ID，无法知道其他合法 area_id 或当前区域的 sub_area_id 列表。导致 LLM "幻觉"出不存在的 ID。

**修复方案：**
- 在 `_build_planner_context()` 中从 `state.areas.areas` 提取合法 area_id 列表
- 从 `MapRegistry` / `AreaSlice` 提取当前 area 的 sub_area_id 列表（含已有 + 模板中的）
- 在 `_format_planner_context()` 中输出 `Allowed area_ids: ...` 和 `Sub-areas for <area_id>: ...`

**文件：**
- `app/game_core/orchestration/hooks/narrative_planner.py` — `_build_planner_context()`
- `app/narrators.py` — `_format_planner_context()`

**预估：** ~30 行改动

**P23 交叉状态：** P23 W2-2（context 丰富化）声称已完成，但代码验证发现 `_build_planner_context()` 虽然收集了 area 数据（area_npcs、area_boards），`_format_planner_context()` 却从未将 "Allowed area_ids" 和 "Sub-area IDs for \<area>" 渲染到 LLM 可见的 prompt 文本中。**数据收集层有了，但 LLM 看不到。P23 的修复只完成了一半。**

---

### P25-02：Directive 拒绝原因在传播链中丢失

**现象：** `previous_directive_results` 反馈给 LLM 的信息只有 `reason_code: "dispatcher_rejected"`，不包含具体验证失败原因。LLM 无法从失败中学习，下轮可能重复同样的错误。

**原因链：**
```
RulesEngine handler.validate() → ValidationResult(ok=False, reason="unknown area_id: xxx")
  → ExecuteResult(executed=False, ...) — reason 在这里
  → subsystem.apply_directive() 只检查 result.executed，返回 False — reason 丢失
  → NarrativePlannerHook 记录 reason_code="dispatcher_rejected" — 具体原因不可见
  → previous_directive_results 只含 "dispatcher_rejected" — LLM 无法学习
```

**修复方案（两步）：**

1. **subsystem 层：** 让各 subsystem 的 `_apply_xxx()` 方法在 `result.executed == False` 时，将 `result` 中的失败原因（如 `result.reason` 或 handler 写入的 metadata）返回给调用方。可以改 `apply_directive` 返回值为 `bool | str`（str 为拒绝原因），或通过 collector 传递。

2. **NarrativePlannerHook 层：** 将具体拒绝原因写入 `directive_audit` 的 `reason_code` 字段（而非笼统的 `"dispatcher_rejected"`），并传入 `previous_directive_results` 反馈给 LLM。

3. **日志层：** warning 消息中直接包含 `kind` 和 `reason`，不仅放在 `extra` 中。

**文件：**
- `app/game_core/planning/subsystem.py` — `PlannerDispatcher.apply_directive()` 返回值语义
- `app/game_core/planning/quest_manager.py` / `npc_director.py` / `world_builder.py` / `pacing_controller.py` / `item_designer.py` — 各 `_apply_xxx()` 方法
- `app/game_core/orchestration/hooks/narrative_planner.py` — `_apply_directive_batch()` 记录逻辑

**预估：** ~80 行改动

**P23 交叉状态：** P23 W2-1（反馈环）声称已完成，但代码验证发现 `_build_planner_context()` 确实从 `last_planner_replay_trace` 中提取了 `previous_directive_results`（含 kind/status/reason_code，最近 10 条），但 `_format_planner_context()` 从未将这些数据格式化到 prompt 文本中。**反馈数据收集了但 LLM 看不到——反馈链的最后一公里断了。** P25-02 的修复需要同时修 `_format_planner_context()` 中的渲染逻辑。

---

### P25-03：Gemini API 空响应未防护

**现象：** `NpcInteractionCoordinator: GM observation failed` — `TypeError: 'NoneType' object is not iterable`，traceback 指向 `llm_gemini.py:142` 的 `for candidate in response.candidates:`。

**原因：** Gemini API 偶尔返回 `response.candidates = None`（安全过滤、过载、超时等），`_parse_response` 未做 None 检查。

**修复方案：**
1. 在 `_parse_response` 开头加 `if not response.candidates:` 守卫，返回 `LlmResponse(text="", finish_reason="safety_filter", ...)`
2. 上层调用方检测到 `finish_reason="safety_filter"` 时，通过 SSE 推送提示事件到前端（如 `{"type": "content_filtered", "message": "内容被安全过滤，请换个方式表达"}`），让玩家直接在界面上看到原因

**文件：** `app/llm_gemini.py` — `_parse_response()`

**预估：** ~10 行

---

### P25-04：Planner 日志诊断能力不足

**现象：** `planner directive rejected: dispatcher_rejected` 日志重复 6 次，但看不出是哪个 directive kind 被拒、具体原因是什么。

**原因：**
- `narrative_planner.py:1238` 的 warning 把 `kind` 放在 `extra={}` 中，标准 logging format 不会打印 `extra`
- dispatcher 的 debug 日志（"no sub-system handles kind=%r"）在 INFO 级别下不可见

**修复方案：** 把 `kind` 和 `reason_code` 直接写在 warning 消息文本中：
```python
logger.warning(
    "planner directive rejected: %s (kind=%s, subsystem=%s)",
    reason_code, validation.kind, subsystem_name,
)
```

**文件：** `app/game_core/orchestration/hooks/narrative_planner.py` — 两处 `logger.warning`

**预估：** ~4 行

---

*（P25-01~04 的初始优先级和依赖关系已合并到下方 §3/§4 综合表格中）*

### P25-05：graphize_counter 重启后归零

**现象：** `ContextWindow.import_messages()` 恢复历史时硬编码 `self.graphize_counter = 0`。如果用户退出时 NPC 已累积 28K token（接近 32K 阈值），重新加载后进度归零，需要再聊 32K 才能触发 `write_episode`。

**影响：** WorldKnowledgeGraph 的动态知识积累严重滞后——每次加载游戏都重置了 graphize 进度条。

**修复方案：**
- `export_messages()` 序列化时包含 `graphize_counter` 字段
- `import_messages()` 恢复时从数据中读取 `graphize_counter`（缺失时 fallback 为 0）

**文件：** `app/game_core/narrative/context_window.py` — `export_messages()` + `import_messages()`

**预估：** ~10 行

---

### P25-06：检定结果对 NPC 无约束力（A+B 混合方案）

**现象：** 玩家通过 `[说服 DC12]` 选项触发检定后，NPC LLM 仅在 SceneBus 中看到一行文本记录 `"Player attempted persuasion check (DC 12): Passed (rolled 15)"`。NPC 可以完全无视检定结果——检定成功了但 NPC 依然拒绝，检定失败了但 NPC 照样同意。检定系统在链路上是通的，但在行为约束上是"软"的。

**根因分析：**

当前检定结果的传播路径：
```
gameplay.py 执行 skill_check Command → 骰点
  → check_result dict 传入 NpcInteractionCoordinator
    → _write_skill_check_observation() 写入 SceneBus 一行文本
      → NPC LLM 在 scene_entries 中看到这行文本
        → NPC 自由发挥（无约束）
```

问题在于：
1. SceneBus 的文本记录对 NPC 来说跟"外面下雨了"没区别——没有行为约束力
2. NPC 的 system prompt 中没有任何关于检定结果的行为规则
3. GM 生成选项时不知道成功/失败后应该发生什么

**修复方案（A+B 混合）：**

#### A 层 — Prompt 约束注入（所有检定的基线保障）

当 `check_result` 存在时，在 NPC 的 agent context 中注入**结构化行为约束**（不再只是 SceneBus 一行文字）：

检定**成功**时注入：
```
## 当前检定结果（必须遵守）
玩家对你发起了【说服】检定（DC 12），掷出 15，**成功**。
你必须在本轮回复中体现被说服的效果：让步、透露信息、或改变态度。
你可以表现得不情愿，但最终结果必须是顺从。
不得无视检定结果。
```

检定**失败**时注入：
```
## 当前检定结果（必须遵守）
玩家对你发起了【说服】检定（DC 12），掷出 8，**失败**。
你不应被说服。你可以表现得更加警觉、不耐烦或怀疑。
不得因为玩家的话术好就改变立场——骰子已经决定了结果。
```

**注入位置：** `NpcInteractionCoordinator` 在构建 NPC agent context 时，将 check_result 转换为上述约束文本，附加到 NPC 的 system prompt 或 context metadata 中。

**实现要点：**
- 在 `npc_interaction.py` 中新增 `_format_check_constraint(check_result)` 方法
- 约束文本根据 `check_result["skill"]` 和 `check_result["passed"]` 生成
- 通过 `AgentContext.metadata` 或直接拼接到 NPC system prompt 传入
- 不同技能的约束措辞可以不同（persuasion → 让步/透露, intimidation → 畏惧/退缩, deception → 被欺骗/信以为真）

#### B 层 — GM 预定义 pass/fail 剧本（重要选项的精确控制）

扩展 `SuggestOptionsTool` 的选项 schema，让 GM 在生成选项时**可选地**写好成功和失败的剧情走向：

```json
{
  "text": "用甜言蜜语打动她",
  "check": {"skill": "persuasion", "dc": 12},
  "on_pass": {
    "npc_instruction": "你被打动了，答应透露密道的位置",
    "narration": "她的表情软化，叹了口气。"
  },
  "on_fail": {
    "npc_instruction": "你觉得对方油嘴滑舌，更加警惕",
    "narration": "她冷冷地别过头去。"
  }
}
```

检定结果出来后：
1. GM 的 `narration` 直接作为旁白叙事发出（硬叙事，不经 NPC LLM）
2. `npc_instruction` **替代** A 层的通用约束，注入 NPC 的下轮 prompt（精确行为约束）
3. 如果 GM 没写 `on_pass`/`on_fail`（可选字段），退回 A 层的通用约束

**实现要点：**
- `SuggestOptionsTool.parameters` 的 option schema 新增 `on_pass` 和 `on_fail` 可选字段
- `_validate_option()` 校验 `on_pass`/`on_fail` 结构（`npc_instruction`: string, `narration`: string，均可选）
- `_finalize_dialogue_options()` 保留 `on_pass`/`on_fail` 到 normalized option 中
- `_dialogue_option_dispatch()` 将 `on_pass`/`on_fail` 写入 dispatch payload
- `gameplay.py` 检定结果出来后，根据 `passed` 选择 `on_pass` 或 `on_fail`：
  - 如有 `narration`，发 `narrate` SSE 事件
  - 如有 `npc_instruction`，传入 NPC interaction 的 check_result 中
- NPC interaction 收到 `npc_instruction` 时，用它替代 A 层的通用约束文本

#### C 层 — 选项展示/推送分离（丰富 NPC 收到的上下文）

当前 `SuggestOptionsTool` 的选项有 `text`（展示给玩家）和 `message`（推送给 NPC）两个字段，但 `message` 未被充分利用——玩家点击后实际推送的内容与展示文本几乎相同。

**设计：** 玩家看到简洁的选项文本，但点击后推送给 NPC LLM 的是**更丰富的上下文信息**，包含：

1. **选项原始意图**（GM 写的 `message` 字段，比 `text` 更详细地描述玩家想做什么）
2. **检定结果**（如有）：技能名、DC、掷骰结果、成功/失败
3. **行为约束**（A 层或 B 层的约束文本）

示例：
```
玩家展示文本（text）：
  "试着说服她透露情报"

实际推送给 NPC LLM 的内容：
  "玩家试图用甜言蜜语打动你，希望你透露关于密道的情报。
   【检定结果】说服 DC 12，掷出 15，成功。
   【行为约束】你必须在本轮回复中体现被说服的效果。"
```

**实现要点：**
- GM 生成选项时，`text` 写玩家可读的简短描述，`message` 写更详细的意图说明
- GM prompt 中明确指导：`text` 是给玩家看的（简洁），`message` 是给 NPC 看的（详细意图+情境）
- `gameplay.py` 点击选项后，组装推送内容：`message`（或 fallback `text`）+ 检定结果文本 + 行为约束
- 组装后的完整内容作为用户消息传入 NPC interaction，替代当前的裸 `text`

**文件：**
- `app/game_core/orchestration/npc_interaction.py` — `_format_check_constraint()` 新增，`execute_interaction()` 注入逻辑
- `app/game_core/narrative/gm_tools.py` — `SuggestOptionsTool` schema 扩展 + `_validate_option()` 校验，GM prompt 指导 text/message 分工
- `app/game_core/orchestration/npc_interaction.py` — `_finalize_dialogue_options()` 保留 on_pass/on_fail + message
- `app/agent_orchestration.py` — `_dialogue_option_dispatch()` 写入 dispatch payload
- `app/routers/gameplay.py` — 检定结果后组装丰富推送内容 + 选择 on_pass/on_fail 并分发
- GM prompt（`context_builder.py` 中的 Step 5 prompt）— 告知 GM text/message 分工 + 可使用 on_pass/on_fail 字段

**预估：** ~140 行改动

---

### P25-07：Planner 任务标题/描述泄露英文与系统内部字符串

**现象：** Planner 发布的动态任务在前端显示为英文标题，甚至包含系统内部 field name（如 `quest_id`、`board_id` 等）。

**根因：**
1. **Planner prompt 无语言约束**：`AgenticNarrativePlanner._SYSTEM_PROMPT`（`narrators.py:225`）通篇中文但**没有**"使用与玩家相同语言"的指令。GM narrator 有 `"Match the language of the user message"`，planner 没有。
2. **Quest 内容零过滤直通**：`PlannerQuestHandler._compute_create_quest()`（`planner.py:163`）将 LLM 输出的 `title`/`summary` **直接写入** `dynamic_quests` dict，不做内容过滤或语言检查。
3. **前端原样显示**：`interaction_views.py` 对 quest 的 `title`/`summary` 直接 `str()` 转换，无过滤层。

**修复方案：**
1. Planner system prompt 末尾追加语言约束：`"所有面向玩家的文本（title, summary, objective 等）必须使用中文。内部标识符（quest_id, npc_id 等）保持英文 snake_case。"`
2. （可选）`_compute_create_quest()` 中对 `title`/`summary` 做基本清洗（strip 系统前缀、检测并 log 异常长度或纯 ASCII 标题）

**文件：**
- `app/narrators.py` — `_SYSTEM_PROMPT` 追加语言约束
- `app/game_core/rules/handlers/planner.py` — `_compute_create_quest()` 可选清洗

**预估：** ~20 行

---

### P25-08：AI Osiris 后果扩散无边界

**现象：** 玩家在某个子地点的行为（如偷窃、战斗）产生的后果影响到全图所有 NPC，包括不在场的角色。

**根因（三重缺失）：**

| 层级 | 问题 |
|------|------|
| **Prompt** | `OSIRIS_SYSTEM_PROMPT`（`evaluators.py:24-80`）没有任何**局部性约束**指令，不告诉 LLM"只影响在场的人" |
| **Context** | snapshot 向 LLM 提供**全区域所有 NPC**（`nearby_npcs` 实际是 same-area NPCs，不分子地点）、**全局阵营关系**、**所有活跃任务** |
| **Validation** | `_passes_minimal_semantic_validation()` 只检查"NPC 存在"和"party 成员才能改 approval"，**不检查 NPC 是否在场** |

**后果链：** 玩家在酒馆偷东西 → LLM 决定"全城名誉受损" → 对不在场的 NPC 执行 `modify_disposition(trust: -20)` → 验证通过（NPC 存在）→ 全图生效。

**与 Larian Osiris 的差距分析：**

| 维度 | Larian Osiris | 当前 AI Osiris | 差距 |
|------|-------------|-----------------|------|
| **局部性** | 事件绑定 region/trigger zone，只影响该区域实体 | 全区域 NPC 都受影响，无边界 | 严重 |
| **规则性** | if-then 规则表，确定性后果 | LLM 自由发挥，无底线约束 | 严重 |
| **复现性** | 同输入同输出，玩家可建立因果直觉 | 同行为每次后果不同，不可预期 | 严重 |
| **实时性** | 事件即时触发规则链，支持链式传播 | 等 settlement tick 批量处理，单次评估无传播 | 中等 |

核心设计差异：Larian 的 Osiris 是**规则驱动**，我们的是**LLM 驱动**。LLM 替代了规则编写的工作量，但丢掉了确定性、局部性和复现性。改造方向不是抛弃 LLM，而是**给 LLM 套上骨架**。

**改造方案：三级后果体系 + 分阶段实施**

#### 三级后果体系

```
Level 1 — 硬规则（确定性，不经 LLM）
  后果食谱驱动。偷窃被目击 → crime_flag + 目击者 trust-20 + 守卫敌对。
  写在内容层 consequences.json 中，可调整不改代码。

Level 2 — 软规则（LLM 在约束框内判断）
  社交冲突等模糊场景，LLM 判断严重程度但有上下限约束。
  例："侮辱酒馆老板" → LLM 决定 trust -5 到 -15 之间，不能超范围。

Level 3 — 叙事层（纯 LLM，不改状态）
  NPC 情绪反应、对话内容变化、氛围描写。
  只影响 NPC 下次对话的 context，不执行 modify_disposition 等命令。
```

#### 三层传播模型（局部性）

```
即时层（同一 sub_location）
  → "目击者"立刻反应，Level 1 硬规则即时执行
  → 例：酒馆偷东西 → 酒馆里的人看到了

延迟层（同一 area，下一个 tick）
  → "听说了"，通过 rumor 机制传播
  → 例：酒馆老板告诉了街上的商人

远程层（其他 area，多 tick 后）
  → "传闻"，通过阵营/rumor 网络传播
  → 需要事件严重程度够高 + 阵营网络连通
```

#### 关键架构决策

| 决策 | 方案 | 理由 |
|------|------|------|
| **后果食谱放哪** | 内容层 `consequences.json` + `ConsequenceRegistry` | 内容与逻辑分离，可不改代码调后果，不同世界观可有不同规则 |
| **事实数据库** | 新建 `FactSlice`（第 11 个 slice） | FlagSlice 是 schemaless 简单标志，事实记录需要结构化（actor/location/tick/witnesses/consequence_applied），职责独立 |
| **ConsequenceEngine 放哪层** | 编排层独立组件（非 Hook） | Hook 只在 settlement tick 跑，但 Level 1 硬规则需即时执行。ConsequenceEngine 被 pipeline（即时）和 AIOsirisHook（延迟）各自调用 |
| **AIOsirisHook 定位** | 退化为"调用 ConsequenceEngine 延迟传播 + LLM 软规则+叙事" | 不再是万能评估器，LLM 只处理 Level 2/3 |

#### 分阶段实施

| 阶段 | 内容 | 依赖 | 预估 |
|------|------|------|------|
| **Phase 1** | 局部性修复：prompt 约束 + context 子地点过滤 + validation gate | 无 | ~60 行 |
| **Phase 2** | `consequences.json` + `ConsequenceRegistry` 数据结构定义 | Phase 1 | ~120 行 |
| **Phase 3** | `ConsequenceEngine` 硬规则执行（即时后果）+ pipeline 接入 | Phase 2 | ~200 行 |
| **Phase 4** | `FactSlice` + 事实记录 + 防重复 | Phase 3 | ~150 行 |
| **Phase 5** | 延迟传播 + AIOsirisHook 改造（LLM 退化为软规则+叙事） | Phase 4 | ~200 行 |

Phase 1 即现有 P25-08 方案，可立刻实施。每个 phase 都是闭合的可验证单元。

**Phase 1 修复方案（即时可做）：**

1. **Prompt 局部性约束**：在 `OSIRIS_SYSTEM_PROMPT` 中追加：
   ```
   ## 局部性规则
   - 只对 scene_presence.present_character_ids 中列出的角色产生即时后果
   - 不在场的 NPC 不应直接受 modify_disposition 影响
   - 若后果需要扩散到远处，使用 create_rumor 并设 spread_to 延迟传播
   ```

2. **nearby_npcs 子地点过滤**：`_build_nearby_npcs()`（`ai_osiris.py:1442`）应在有子地点信息时，仅返回同一 sub_location 的 NPC，而非整个 area 的 NPC。

3. **Validation gate**：`_passes_minimal_semantic_validation()` 对 `modify_disposition` 命令新增检查：`target_npc` 必须在 `scene_presence.present_character_ids` 中，否则拒绝。

**文件：**
- `app/evaluators.py` — `OSIRIS_SYSTEM_PROMPT` 追加局部性规则
- `app/game_core/orchestration/hooks/ai_osiris.py` — `_build_nearby_npcs()` 子地点过滤 + `_passes_minimal_semantic_validation()` 局部性验证
- （Phase 2+）`data/goblin_slayer/v2/consequences.json` — 后果食谱数据
- （Phase 2+）`app/game_core/content/registries/consequence_types.py` — ConsequenceRegistry
- （Phase 3+）`app/game_core/orchestration/consequence_engine.py` — ConsequenceEngine
- （Phase 4+）`app/game_core/state/slices/facts.py` — FactSlice

**预估：** Phase 1 ~60 行；完整改造（Phase 1-5）~730 行

---

### P25-09：fill_tags / fill_density 未消费（低优，记录）

**现象：** `SubAreaClusterConfig` 的 `fill_tags` 和 `fill_density` 字段已定义在 `map_types.py` 中并可从数据加载，但 WorldBuilder 创建子地点时**完全不读取**这两个配置。cluster 机制目前只有容量管控（`has_cluster_capacity`），没有偏好引导。

**状态：** P3 低优，暂不修，等子地点系统深化时再消费。

---

### P25-10：子地点簇与 Planner 的位置感知（待调查）

**用户关注点：** Planner 应当能在**正确的区域**创建**只在该位置可访问的**子地点。当前子地点簇机制是否能保证这一点？Planner 生成 `fill_area` / `plant_environmental` 时，是否知道它在为哪个区域创建子地点？创建出的子地点是否正确绑定到目标区域？

**待调查内容：**
- Planner directive 的 `area_id` 字段如何传递到 WorldBuilder → DynamicSubAreaManager
- 创建的动态子地点是否正确写入目标 area 的 `temporary_sub_areas`（而非当前玩家所在区域）
- 玩家在 frontier_town 时，Planner 能否为 ancient_ruins 创建子地点？应该可以还是不应该？
- 当前 cluster 容量检查是否在正确的 area_id 上执行

---

### P25-11：Planner 未主动使用 Design Skill 模板

**现象：** 项目中有完整的设计模板体系（54 个 Markdown 模板文件，覆盖 7 个分类），Planner 的 6 个子系统都注册了 `ReadDesignSkillTool` + `ListDesignSkillsTool`，但 **Planner 几乎不会主动调用这些工具**，导致生成的任务/NPC 指令/环境描述全靠 LLM 凭空编造，质量不稳定。

**现有资源：**

| 分类 | 模板数 | 注册到的子系统 | 示例模板 |
|------|--------|---------------|---------|
| `quests/` | 12 | quest_manager | bounty, escort, investigate, rescue... |
| `npcs/` | 8 | npc_director | merchant, villain, informant, quest_giver... |
| `areas/` | 5 | world_builder | dungeon, settlement, wilderness, shop, poi |
| `encounters/` | 7 | world_builder | ambush, boss, swarm, trap, patrol... |
| `environments/` | 6 | world_builder | clue, hidden_area, interactable, landmark... |
| `items/` | 5 | item_designer | weapon, armor, consumable, quest_item, special |
| `narrative/` | 8 | narrative_weaver | companion_arc, milestone, moral_dilemma, rumor... |
| `social/` | 3 | quest_manager, npc_director | bulletin, dialogue_hook, reputation_event |

**根因：工具链完整但 LLM 不知道要用。**

工具注册只意味着 LLM 在 function declarations 中**能看到**工具签名，但没有任何 prompt 指导告诉 LLM：
1. 这些模板存在、有什么用
2. 什么时候应该查阅（创建任务前？设计遭遇前？）
3. 模板内容的预期用法（参考格式？填充模板？约束输出结构？）

这就像给人一个工具箱但不告诉他里面有什么——理论上可以打开看，实际上没人会主动去翻。

**修复方案（渐进式加载模式）：**

在 Planner system prompt 中追加 **Design Skill 使用指南**，采用三级渐进加载：

**Level 1 — Prompt 中列出分类目录（始终可见）：**
```
## 设计模板库
你有一套设计模板可供参考。在生成 directive 前，建议先查阅对应模板以确保输出质量。

可用分类：
- quests: 任务设计模板（bounty, escort, investigate, rescue 等 12 种）
- npcs: NPC 设计模板（merchant, villain, informant 等 8 种）
- areas: 区域设计模板（dungeon, settlement, wilderness 等 5 种）
- encounters: 遭遇设计模板（ambush, boss, swarm, trap 等 7 种）
- environments: 环境要素模板（clue, hidden_area, interactable 等 6 种）
- items: 物品设计模板（weapon, armor, consumable 等 5 种）
- narrative: 叙事设计模板（companion_arc, milestone, moral_dilemma 等 8 种）
- social: 社交设计模板（bulletin, dialogue_hook, reputation_event 3 种）

使用方式：
1. 先调用 list_design_skills(category="quests") 查看该分类下的具体模板
2. 再调用 read_design_skill(category="quests", name="bounty") 阅读模板内容
3. 参考模板的结构和要点生成你的 directive
```

**Level 2 — LLM 按需 list → read（工具调用）：**
- `list_design_skills(category)` → 返回该分类下所有模板名
- `read_design_skill(category, name)` → 返回模板全文

**Level 3 — 强制查阅规则（可选，防止 LLM 跳过）：**
在特定 directive 类型的生成前，prompt 中可追加硬约束：
```
创建 create_quest directive 前，你**必须**先调用 read_design_skill 查阅对应任务类型的模板。
如果没有查阅模板就直接生成 directive，该 directive 将被拒绝。
```

**实现要点：**
- 在 `narrators.py` 的 `AgenticNarrativePlanner._SYSTEM_PROMPT` 末尾追加 Level 1 分类目录
- Level 3 的强制规则可选择性启用（prompt 约束，不需要代码变更）
- 不需要改工具链本身——`ReadDesignSkillTool` / `ListDesignSkillsTool` 已完整可用

**文件：**
- `app/narrators.py` — `_SYSTEM_PROMPT` 追加设计模板使用指南

**预估：** ~30 行 prompt 文本

**P23 交叉状态：** P23 W2-3（Skills 激活）已完成基础工作——`_SYSTEM_PROMPT` 中新增了"设计模板工具"段落，提及 `list_design_skills` / `read_design_skill`，各子系统 prompt 末尾也添加了"（可选）可通过 list_design_skills / read_design_skill 查阅设计模板"。但由于标注为"可选"，LLM 实际几乎不会主动调用。P25-11 的修复应在 P23 基础上**加强为 Level 2/3 渐进式引导**，将关键 directive（create_quest、plant_encounter）的模板查阅从"可选"改为"必须"。

---

### P25-12：任务面板显示策略（里程碑 vs 动态任务分离）

**现象：** 当前任务面板将 `milestone_states`（系统内部进度追踪）和 `dynamic_quests`（Planner 创建的玩家任务）一股脑返回前端。玩家看到的是里程碑的内部 ID 和状态机字段（如 `ms_familiar_banter: AVAILABLE`），而不是有叙事包装的任务描述。

**设计意图：**
- **里程碑**是内部剧情骨架，驱动 Planner 决策和内容解锁，**不应直接暴露给玩家**
- **动态任务**是 Planner 基于里程碑创建的、面向玩家的叙事包装（标题/描述/目标/奖励），**这才是玩家应该看到的**
- 效果：玩家只看到"消灭地下水道的哥布林巢穴"（动态任务），不知道背后是 `ms_familiar_banter` 里程碑在驱动。营造**宿命感**——玩家以为自己在自由选择，实际是被里程碑引导

**当前问题：**
1. `_quest_response()`（`panels.py:99`）同时返回 `milestone_states` 和 `dynamic_quests`
2. 前端直接显示两者，里程碑的内部字段（英文 ID、state 枚举值）对玩家毫无意义
3. 没有"哪些任务是当前可见的"过滤逻辑——所有 `dynamic_quests` 不论 status 全部返回

**修复方案：**

1. **后端：隐藏里程碑**
   - `_quest_response()` 不再返回 `milestone_states` 给前端（或放到 debug-only 字段）
   - 只返回 `dynamic_quests`，且按 status 过滤：只返回 `available`/`active`/`ready_to_report`/`completed`/`failed` 的任务
   - `retired`/`expired` 任务不展示（或放到"历史"分区）

2. **后端：补完成提示**
   - 动态任务的 `objectives` 字段已有进度追踪（`completed: true/false`）
   - 在返回 payload 中追加 `completion_hint` 字段：当所有 objectives 完成时，提示"返回汇报"或"任务即将结算"
   - 与现有的 `can_report` / badge `"待汇报"` 逻辑衔接

3. **前端：只渲染动态任务**
   - 移除里程碑面板（或改为开发者调试面板）
   - 动态任务按状态分组：进行中 / 可接取 / 已完成

**文件：**
- `app/routers/panels.py` — `_quest_response()` 过滤逻辑
- `app/quest_views.py` — `normalize_dynamic_quest_view()` 补 completion_hint
- 前端任务面板组件（待定）

**预估：** 后端 ~40 行，前端另算

---

### P25-13：三层嵌套子地点系统（area → sub_location → room）

**现象：** 当前地点系统只有两层（area → sub_location）。NPC 定位到 sub_location 级别（如"酒馆"），但酒馆内部没有更细的空间划分。玩家进入酒馆后能看到所有酒馆 NPC，无法体验"在柜台找到柜员"或"去包间密谈"的探索感。

**设计目标：** 新增第三层 **room**，实现细粒度空间体验。

```
frontier_town（area）
  → 冒险者公会（sub_location）
    → 大厅（room）— 冒险者们在此聚集
    → 柜台（room）— 柜台小姐的工作位置
    → 包间（room）— 可用于私密对话
    → 二楼走廊（room）— 通往更多房间
  → 铁匠铺（sub_location）
    → 店面（room）— 展示商品
    → 锻造间（room）— 铁匠工作区域
  → 神殿（sub_location）
    → 礼拜堂（room）
    → 后院（room）
```

**核心设计决策：**

| 决策 | 方案 |
|------|------|
| **嵌套深度** | 固定三层（area → sub_location → room），不允许更深嵌套 |
| **Room 来源** | 静态：内容层 maps.json 预定义；动态：Planner 创建，存运行时状态 |
| **Room 发现** | 不自动暴露，需要询问 NPC 或主动探索（investigate/perception）才能发现 |
| **可见性** | 严格同 room 可见。在大厅看不到柜台的人，必须导航到柜台才能交互 |
| **NPC 移动** | NPC 专用工具（`move_to_room`），对话中自主决定移动 |
| **NPC 归位** | 特殊职责 NPC（柜员、铁匠等）离开岗位一段时间后自动返回原 room |
| **移动范围** | NPC 可在同 area 内任意 room 间移动，不能跨 area |

**涉及的层级和改动：**

#### 1. 内容层 — maps.json 扩展

sub_location 新增 `rooms` 字段：
```json
{
  "id": "adventurer_guild",
  "label": "冒险者公会",
  "rooms": [
    {
      "id": "guild_hall",
      "label": "大厅",
      "description": "嘈杂的大厅，冒险者们在此交流信息",
      "discoverable": false
    },
    {
      "id": "guild_counter",
      "label": "柜台",
      "description": "公会的接待柜台",
      "discoverable": false
    },
    {
      "id": "private_room",
      "label": "包间",
      "description": "可用于私密对话的隔间",
      "discoverable": true,
      "discovery_dc": 0
    }
  ]
}
```

`discoverable: true` 表示需要发现（询问/探索）才会出现在导航选项中；`false` 表示进入 sub_location 后直接可见。`discovery_dc: 0` 表示询问即可知道，不需要检定。

#### 2. 状态层 — NPC room 定位

`AreaSlice.npc_locations` 当前是 `dict[str, str | None]`（npc_id → sub_location_id）。

扩展方案（两选一）：
- **方案 A**：值改为 `dict[str, str | None]` 但用复合 ID：`"adventurer_guild.guild_counter"`
- **方案 B**：新增 `npc_rooms: dict[str, str | None]` 字段（npc_id → room_id），与 `npc_locations` 配合

倾向方案 B——不破坏现有 `npc_locations` 的消费者，room 定位是增量信息。

新增 `discovered_rooms: dict[str, set[str]]`（sub_location_id → 已发现的 room_id 集合），追踪玩家已发现的 room。

#### 3. 规则层 — NavigationHandler 扩展

新增命令类型：
- `enter_room`：从 sub_location 进入某个 room
- `leave_room`：从 room 回到 sub_location 大厅

验证逻辑：
- 目标 room 必须属于当前 sub_location
- `discoverable: true` 的 room 必须已被发现（在 `discovered_rooms` 中）
- 玩家当前必须在该 sub_location 内

#### 4. 编排层 — NPC 归位机制

扩展 `NpcScheduleHook` 或新增 `NpcReturnToPostHook`：
- `CharacterTemplate` 新增 `home_room` 字段（如柜台小姐的 `home_room: "guild_counter"`）
- 离开 home_room 后开始计时（可用 FlagSlice 记录离开 tick）
- 超过 N tick 未返回 → 自动移动回 home_room
- 计时期间如果玩家仍在与 NPC 对话，暂停归位计时

#### 5. 叙事层 — NPC 移动工具

新增 `MoveToRoomTool`（NPC 专用）：
```json
{
  "name": "move_to_room",
  "description": "移动到当前区域内的某个房间",
  "parameters": {
    "target_sub_location": "string (可选，不填则留在当前 sub_location)",
    "target_room": "string (目标 room_id)",
    "reason": "string (移动原因，用于叙事)"
  }
}
```

执行时：
- 更新 `npc_locations` + `npc_rooms`
- 发 SSE 事件通知前端（`npc_moved`）
- 如果玩家在同一 room，显示"XX 离开了房间"
- 如果玩家在目标 room，显示"XX 走了进来"

#### 6. 前端 — 导航 + 可见性

- 进入 sub_location 后显示已发现的 room 列表
- 每个 room 显示"这里有 N 个人"（不显示具体谁，除非已进入）
- 进入 room 后只显示该 room 内的 NPC
- NPC 移动时有过渡动画/提示

**一并解决的现有子地点层问题（来自附录 A）：**

在做三层嵌套的同时，顺带修复 sub_location 这一层积压的问题：

| 问题 | 当前状态 | 在 P25-13 中如何解决 |
|------|---------|---------------------|
| 容量检查硬编码，不读 `SubAreaClusterConfig.max_dynamic` | `has_cluster_capacity()` 写死 permanent<3, timed<5, temporary<3, total<6 | Phase 1 改为从 cluster config 读取限制值 |
| `fill_tags` / `fill_density` 未消费 | WorldBuilder 创建子地点时完全不看 | Phase 5 WorldBuilder 创建动态 sub_location/room 时读取 fill_tags 做偏好匹配，fill_density 控制生成频率 |
| `discovery_mode` / `discovery_dc` 未完整联动 | 字段存在但 sub_location 级别发现机制未实现 | Phase 2 统一实现 sub_location 和 room 两级的发现机制（inquire / investigate / passive_perception） |
| Planner 只看到 sub_area counts，不知道 cluster 偏好和限制 | planner context 缺乏 cluster config 信息 | Phase 5 在 planner context 中补充 cluster config（max_dynamic, fill_tags, fill_density），供 Planner 做更合理的空间规划 |

**一并解决的角色出场机制问题（来自附录 B）：**

当前 13 个 NPC 有 12 个 `area_id="frontier_town"`，游戏开始全部在场，缺乏剧情驱动的出场编排：

| 问题 | 当前状态 | 在 P25-13 中如何解决 |
|------|---------|---------------------|
| 所有 NPC 一开始全部出现 | `get_area_npcs()` 无条件返回所有 area_id 匹配的 NPC | Phase 3 `get_area_npcs()` 增加 `appear_condition` 过滤 |
| 无剧情触发出场 | 没有 `appear_after_milestone` 机制 | Phase 1 `characters.json` 新增 `appear_condition` 字段（如 `{"milestone": "ms_familiar_banter", "state": "completed"}`） |
| Planner 无法调度出场/离场 | `direct_npc` 只发行为指令，不控制 NPC 出现/消失 | Phase 5 NpcDirector 新增 `summon_npc` / `dismiss_npc` directive 类型 |
| 无条件可见性 | NPC 没有 `visibility_condition`，无法按 flag/阵营/时间段隐藏 | Phase 3 `get_area_npcs()` 支持通用条件过滤 |
| Planner 不知道"谁还没出场" | planner context 只列在场 NPC | Phase 5 planner context 补充"尚未出场的 NPC 及其出场条件"，供 Planner 编排剧情推进 |

**待调查项（实施前需确认）：**

以下问题需要在实施前调查清楚，结论将影响具体方案：

1. **动态子地点的 room 支持**：当前 `temporary_sub_areas` 是 `list[dict]`，Planner 创建的动态子地点能否也包含 room？如果能，room 定义存在哪里？（存 `temporary_sub_areas` 的 dict 里？）
2. **`has_cluster_capacity()` 的调用链**：谁在调用？只有 WorldBuilder 还是有其他消费者？改为读 config 是否有副作用？
3. **`discovery_mode` 的现有消费者**：PassivePerceptionHook 是否已经在检查 sub_location 的 discovery_mode？还是只检查 discoveries/interactables/traps？
4. **NPC 跨 sub_location 移动时的 room 处理**：NpcScheduleHook 移动 NPC 到另一个 sub_location 时，room 应该重置为 null（进入默认大厅）还是指定目标 room？
5. **前端 NavigationHandler 现有行为**：`enter_sub_location` 命令是否已经有发现检查？还是所有 sub_location 进入时都无条件允许？
6. **`get_area_npcs()` 消费者盘点**：除了前端场景展示，还有哪些系统调用 `get_area_npcs()`？加 appear_condition 过滤后是否影响这些消费者？（如 AIOsirisHook 的 nearby_npcs、NpcScheduleHook 等）
7. **`direct_npc` 现有 validation**：NpcDirector 的 `apply_directive()` 对 npc_id 做什么验证？新增 `summon_npc`/`dismiss_npc` 时是否需要新的 PlannerHandler 命令类型？

**与其他条目的关系：**
- P25-08 局部性：room 级可见性天然成为 Osiris 目击者系统的最细粒度边界
- P25-09 fill_tags：**被 P25-13 吸收**——fill_tags/fill_density 消费将在 Phase 5 实现
- P25-10 位置感知：**被 P25-13 吸收**——Planner 创建动态 room/sub_location 时的位置正确性将在 Phase 5 验证

**分阶段实施：**

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 0 | 调查：确认上述 7 个待调查项，确定方案细节 | — |
| Phase 1 | 数据结构：maps.json room 定义 + characters.json `appear_condition` 字段 + MapRegistry/CharacterRegistry 加载 + AreaSlice 扩展（npc_rooms, discovered_rooms）+ `has_cluster_capacity()` 改读 config | ~200 行 |
| Phase 2 | 导航 + 发现：enter_room/leave_room 命令 + NavigationHandler 扩展 + sub_location/room 两级发现机制（discovery_mode/discovery_dc 联动） | ~150 行 |
| Phase 3 | NPC 定位 + 可见性：presence.py room 级过滤 + `get_area_npcs()` appear_condition 过滤 + scene_views room 信息 + 前端适配 | ~140 行 |
| Phase 4 | NPC 移动：MoveToRoomTool + SSE 事件 + 归位机制 | ~150 行 |
| Phase 5 | Planner 联动：`summon_npc`/`dismiss_npc` directive + 动态 room 创建 + fill_tags/fill_density 消费 + planner context 补 cluster config + "未出场 NPC 及条件" + Osiris room 级目击者 | ~200 行 |

**总预估：** ~840 行

---

### P25-14：游戏主循环闭合（里程碑自动完成 + 任务目标追踪 + 进程引导）

**现象：** 按照预期的游戏流程（登记 → 领取任务 → 结识队友 → 完成任务 → 推进主线 → 章节结束），当前系统有多个关键断点导致主线无法自然推进。

**预期游戏流程 vs 系统支撑度：**

```
1. 进入世界 → 到达边境小镇                    ✅ 初始化 + bootstrap 完整
2. 完成登记 → 与柜台小姐对话                   ⚠️ 无引导，玩家可跳过
3. 领取低等级任务                              ✅ 任务板机制完整
4. 结识队友                                    ❌ 无叙事触发招募
5. 逐步完成任务                                ❌ 目标无自动追踪
6. 接近里程碑主线                              ❌ 里程碑无法自动完成
7. 区域转移                                    ✅ 区域间连接存在
8. 完成边境小镇 → 下一章                       ❌ 无章节完成机制
```

**断点 1（最致命）：里程碑无法自动完成**

里程碑的 `success_conditions` 在 `quests.json` 中已定义（`location_visited`、`npc_talked`、`kill_count` 等），但**没有任何 Hook 或 Engine 周期性检查这些条件并触发 ACTIVE → COMPLETED 转换**。

当前里程碑状态机：
```
LOCKED → AVAILABLE（MilestoneUnlockHook 级联解锁）
AVAILABLE → ACTIVE（PlannerHandler 创建任务时自动推进）
ACTIVE → COMPLETED（❌ 没有自动触发器，只能手动 advance_quest）
COMPLETED → 下游 LOCKED → AVAILABLE（MilestoneUnlockHook 级联解锁）
```

ACTIVE → COMPLETED 这一步断了，导致整个主线推进链卡死。

**修复方案：** 新增 `MilestoneCompletionHook`（编排层，settlement tick）
- 每 tick 扫描所有 state=="ACTIVE" 的里程碑
- 检查其 `success_conditions` 是否全部满足（查 FlagSlice/AreaSlice/QuestSlice 等）
- 全部满足时执行 `advance_quest(milestone_id, "COMPLETED")` 命令
- 与现有 `MilestoneUnlockHook` 配合：完成后级联解锁下游里程碑
- 优先级建议：54（在 MilestoneUnlockHook=55 之前，确保同 tick 内级联生效）

**条件评估器复用：** EventEngine 已有条件类型定义（`npc_talked`、`item_obtained`、`kill_count`、`location_visited`），新 Hook 可复用这些评估逻辑，不需要重写。

---

**断点 2：动态任务目标无自动追踪**

Planner 创建的动态任务的 `objectives` 是 LLM 生成的自由文本（如 `"消灭3只哥布林"`），没有结构化条件绑定。即使玩家完成了目标行为，objective 的 `completed` 字段不会自动更新。

```
Planner 创建任务 → objectives: [{"text": "消灭3只哥布林", "completed": false}]
  → 玩家杀了 3 只哥布林 → kill_count 在某处累积（FlagSlice/SceneBus）
  → 但没有系统把 kill_count 和这个 objective 关联
  → objective 仍然 completed: false
  → 任务面板显示"未完成"
```

**修复方案（两层）：**

**A 层 — 结构化目标（Planner 生成时绑定条件）：**

扩展 `create_quest` directive 的 objectives schema，让 Planner 在创建任务时可选地绑定结构化条件：
```json
{
  "text": "消灭3只哥布林",
  "condition": {"type": "kill_count", "params": {"monster_type": "goblin", "count": 3}},
  "completed": false
}
```

新增 `QuestObjectiveTrackingHook`（编排层，settlement tick）：
- 每 tick 扫描所有 active 动态任务的 objectives
- 有 `condition` 的 objective → 评估条件是否满足 → 自动标记 completed
- 无 `condition` 的 objective → 跳过（仍由 LLM/Planner 手动管理）
- 所有 objectives 完成 → 任务状态自动推进为 `ready_to_report`（如果 requires_report）或 `completed`

**B 层 — Planner 手动更新（兜底）：**

保留现有的 `update_quest` directive，让 Planner 可以根据叙事判断手动标记 objective 完成。A 层处理不了的非结构化目标（如"赢得酒馆老板的信任"）由 Planner 负责。

---

**断点 3：无进程引导**

玩家进入世界后可以做任何事，没有系统引导"先去柜台登记"。

**修复方案（Planner prompt 强化，不需新系统）：**

在 Planner 的 system prompt 中追加进程引导指令：
```
## 进程引导原则
1. 优先创建推进当前 ACTIVE 里程碑的任务
2. 如果玩家偏离主线太久（ticks_since_milestone_progress > 阈值），
   通过 direct_npc 让关键 NPC 主动提醒/引导玩家
3. 游戏初期按以下顺序引导：
   a. 引导玩家与柜台小姐对话（公会登记）
   b. 引导玩家领取第一个任务
   c. 在适当时机安排队友出场和招募对话
4. 不要同时给玩家超过 3 个活跃任务
```

GM 的 `SuggestOptionsTool` 也应优先推荐与当前里程碑相关的选项。

---

**断点 4：队友无叙事触发招募**

当前招募是纯机械操作（recruit_companion 命令），没有"NPC 在适当剧情节点主动提出入队"的机制。

**修复方案：**

- `characters.json` 中可招募 NPC 新增 `recruit_condition` 字段（如 `{"milestone": "ms_familiar_banter", "state": "ACTIVE", "min_approval": 10}`）
- Planner context 补充"可招募但未入队的 NPC 及其招募条件"
- Planner 可通过 `direct_npc` 让 NPC 发起招募对话（NPC 在对话中使用"提议入队"工具）
- 或新增 `recruit_offer` directive 类型，Planner 直接安排招募事件
- 与 P25-13 的 `appear_condition` 联动：NPC 先出场（满足 appear_condition），再在条件成熟时发起招募

---

**断点 5：无章节完成机制**

当前里程碑链在 `ms_call_from_water_capital`（sequence 3）断裂（`next_milestones: []`）。没有"章节所有里程碑完成 → 触发章节结算 → 解锁下一章"的机制。

**修复方案：**

- `quests.json` 章节定义新增 `completion_milestone`（最后一个里程碑 ID）
- 新增 `ChapterCompletionHook`（编排层）或扩展 `MilestoneCompletionHook`：
  - 检测 completion_milestone 状态为 COMPLETED
  - 触发章节结算（SSE `chapter_complete` 事件、叙事总结）
  - 解锁下一章的里程碑
- 这个可以后做——先有足够的章节内容再说

---

**条件追踪链路验证（2026-03-12 代码调查）：**

| 条件类型 | 写入机制 | 读取机制 | 状态 |
|----------|---------|---------|------|
| `npc_talked` | `talked_to_{npc_id}` 写入 FlagSlice（npc_interaction.py:356 + private_chat.py:178） | `talked_to_{npc_id}` 从 FlagSlice 读取（event_engine.py:451） | ✅ 持久 flag，可靠 |
| `kill_count` | `kill_count_{monster_id}` 写入 FlagSlice（combat.py:999, 1302 每次击杀）| `kill_count_{monster_type}` 从 FlagSlice 读取（event_engine.py:486） | ✅ 持久 flag，monster_id 与 monster_type 匹配已确认（monsters.json 中 id 就是 type） |
| `location_visited` | **无持久写入**；NavigationHandler 只更新 `player.current_area`（实时） | `state.player.current_area == area_id`（event_engine.py:296，实时位置检查） | ⚠️ 实时检查，非历史记录 |
| `item_obtained` | 无标志；直接查 PlayerSlice inventory（实时） | `item.item_id in inventory`（event_engine.py:466） | ✅ 实时库存检查 |

**`location_visited` 语义说明：** `_check_location_entered()`（event_engine.py:282）检查玩家**当前是否在目标地点**，不是**是否曾经去过**。`location_visited` 被别名到 `location_entered`（event_engine.py:235-236）。MilestoneCompletionHook 只在玩家**恰好在目标 area 时的 settlement tick** 才能判定条件满足。

**对三个里程碑的影响评估：**
- `ms_familiar_banter`（location_visited: ancient_ruins + npc_talked: goblin_slayer）：玩家到达 ancient_ruins 时的 tick 会通过（npc_talked 是持久 flag，提前说过话即可）→ ✅ 可行
- `ms_specialist_cleaning`（kill_count: goblin≥3 + location_visited: ancient_ruins/inner_sanctum）：玩家在 ancient_ruins 战斗杀满哥布林后进入 inner_sanctum → 该 tick 两条件同时满足 → ✅ 可行
- `ms_call_from_water_capital`（location_visited: frontier_town + npc_talked: guild_girl）：玩家回到 frontier_town 时的 tick 会通过 → ✅ 可行

**建议（非阻塞但提升健壮性）：** NavigationHandler 在 `navigate`/`move_area` 命令中追加 `visited_area_{area_id}=True` 写入 FlagSlice（~5 行），`_check_location_entered` 在 `location_visited` 类型时优先检查此 flag，fallback 到实时位置。防止边缘情况（如导航后立刻导航到别处，tick 在非目标位置触发）。可并入 Phase 1 一起做。

---

**分阶段实施：**

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | `MilestoneCompletionHook`：success_conditions 自动评估 + ACTIVE→COMPLETED 转换 + 复用 EventEngine 条件评估 + `visited_area_*` 持久标记（可选） | ~120 行 |
| Phase 2 | 任务目标追踪 A 层：objectives condition schema 扩展 + `QuestObjectiveTrackingHook` 自动标记 + 任务状态自动推进 | ~150 行 |
| Phase 3 | 进程引导：Planner prompt 强化 + GM 选项优先推荐主线 | ~40 行 prompt |
| Phase 4 | 队友叙事触发：`recruit_condition` 字段 + Planner context 补充 + 招募对话触发 | ~100 行 |
| Phase 5 | 章节完成：`ChapterCompletionHook` + SSE 事件 + 下一章解锁（等内容充足时再做） | ~80 行 |

**总预估：** ~490 行

**文件：**
- `app/game_core/orchestration/hooks/milestone_completion.py` — 新 Hook
- `app/game_core/orchestration/hooks/quest_objective_tracking.py` — 新 Hook
- `app/game_core/rules/handlers/planner.py` — objectives condition schema
- `app/narrators.py` — Planner prompt 进程引导
- `data/goblin_slayer/v2/characters.json` — recruit_condition 字段
- `data/goblin_slayer/v2/quests.json` — chapter completion_milestone

---

### P25-15：死代码与死字段清理

**现象：** 全面子系统健康检查发现多处"看着在转实际死了"的代码和字段。这些死代码增加维护负担、误导开发者以为功能已就绪。

#### 15-1. ContextBuilder `_build_l6()` 从未调用（🔴 严重）

**位置：** `app/game_core/narrative/context_builder.py:1118-1137`

N-1 Phase 4（D-N18）实现了 knowledge graph hits 注入 NPC/Teammate system prompt 的功能，但 `_build_l6()` 方法**定义了却从未被任何 context builder 方法调用**。所有 context builder 路径对 L6 返回硬编码空值 `{"hits": [], "source": "..."}`。

**影响：** NPC 对话中完全没有世界知识注入。整个 N-1 Phase 4 的工作（WorldKnowledgeGraph → BFS 扩散激活 → hits 注入 prompt）在运行时是死的。

**修复：** 在 `build_npc_context()` / `build_teammate_context()` / `build_gm_context()` 中实际调用 `_build_l6()`，将结果写入 context_layers["L6"]。

**预估：** ~15 行（调用接入 + 测试验证）

---

#### 15-2. RelationSlice `faction_standings` 零消费（🟡 中等）

**位置：** `app/game_core/state/slices/relations.py`

`faction_standings`（dict[faction_id → int]）可读可写可序列化，但**整个代码库没有任何地方读取它做决策**。既不影响 NPC 行为，也不影响商店价格、任务可用性或战斗。FactionRegistry 有 4 个阵营定义，但声望值从未被消费。

**处置：** 暂不删除（未来阵营系统深化时会用），但标记为 `# NOT YET CONSUMED` 避免误认为已工作。

**预估：** ~2 行注释

---

#### 15-3. AreaSlice `interactable_states` 写了不读（🟡 中等）

**位置：** `app/game_core/state/slices/area.py`

`mark_interactable_used()` 标记交互物为已使用状态，但**没有任何系统回读这个标记**来阻止重复交互或改变交互结果。InteractableHandler 不检查这个字段。

**修复：** InteractableHandler 的 validate() 应检查 `interactable_states`，已使用的交互物应返回不同结果或拒绝重复交互。

**预估：** ~20 行

---

#### 15-4. SSEPresentationPort / OutputPort 死适配器（🟡 中等）

**位置：**
- `app/game_core/adapters/presentation.py:36-76` — SSEPresentationPort
- `app/game_core/adapters/outbound.py` — OutputPort

`SSEPresentationPort` 定义了但**从未注入 GameRuntime**。SSE 事件全部在路由层通过 asyncio.Queue 直接处理，完全绕过适配器。`OutputPort` 更彻底——零实现、零调用。

**处置：** 两个选择：
- A. 删除死适配器（减少代码噪音）
- B. 将 SSE 流式推送迁移到 PresentationPort（回归六边形架构意图）

倾向 A——当前 queue 模式工作良好，强行走适配器增加不必要的间接层。

**预估：** A 方案 ~删除 80 行；B 方案 ~150 行改造

---

#### 15-5. GmTool `describe_environment` 被 prompt 封印（🟢 低）

**位置：** `app/game_core/narrative/gm_tools.py`

工具已注册到 RoleToolRegistry，但 GM 的 system prompt 明确写 `"Do NOT call describe_environment"`。工具链在但被 prompt 禁用。

**处置：** 要么从注册表移除（减少 LLM 的 function declaration 噪音），要么解除封印并在适当场景启用。

**预估：** ~5 行

---

#### 15-6. ContainerHandler `interact_object` v1 残留（🟢 低）

**位置：**
- `app/game_core/rules/handlers/container.py:26` — v1 还在 COMMAND_TYPES 中
- `app/game_core/orchestration/defaults.py:71` — 还映射 `"interact_object" → "interact_object"`
- `app/game_core/rules/handlers/interactable.py:22` — v2 是实际活跃路径

v1→v2 迁移未完全收尾。v1 路径仍注册但不应使用。

**修复：** 从 ContainerHandler.COMMAND_TYPES 移除 `interact_object`，从 defaults.py 移除旧映射。

**预估：** ~4 行删除

---

#### 15-7. ProficiencyHandler 三个 always-True 检查（🟢 低）

**位置：** `app/game_core/rules/handlers/proficiency.py:23-52`

`check_weapon_proficiency()`、`check_armor_proficiency()`、`check_tool_proficiency()` 三个方法在数据缺失时全部返回 True。被 InventoryHandler（装备验证）和 CombatHandler 调用，但实际不执行任何限制。

**状态：** D-P3c 记录过，暂不修（等装备系统深化）。

---

#### 15-8. DiscoveryHandler `passive_scan` 空壳（🟢 低）

**位置：** `app/game_core/rules/handlers/discovery.py:49-54`

`passive_scan` 命令注册了，validate() 直接返回 True，compute() 返回 `{"status": "deferred_to_hook"}`——不产生任何 StateChange，所有工作延迟到 PassivePerceptionHook。

**状态：** 设计意图如此（Hook 负责实际检测），但 handler 层的空壳可能误导。可考虑从 handler 移除，仅在 Hook 中处理。

---

**总清理预估：** ~50 行修改/删除 + ~80 行可选删除（死适配器）

---

### P25-16：基础设施空转（有系统无数据）

**现象：** 多个子系统的代码和 Hook 链路完整、每 tick 正常运行，但因为**缺乏初始数据或触发条件**而实际空转，从未产生有意义的输出。

#### 16-1. EventSlice 全链路空转（🟡 中等）

**涉及系统：**
- `ScheduledEventHook`（priority 0）— 每 tick 调用 `events.check_triggers()`，但 `pending_events` 始终为空
- `EventConditionHook`（priority 70）— 每 tick 扫描 `active_events`，但始终为空
- `EventSlice` — 状态机完整（dormant→available→triggered→active→resolved），但初始无任何事件数据

**根因：** 游戏初始化时不创建任何 pending_events。quests.json 定义了 success_conditions 但这些**不会自动转换为 EventSlice 的事件记录**（这也是 P25-14 断点 1 的一部分）。Planner 可以通过 `schedule_event` directive 创建事件，但实际很少这样做。

**修复方案：**
1. 在 bootstrap 阶段从 quests.json 的 success_conditions 生成初始 pending_events
2. 或在 MilestoneCompletionHook（P25-14 Phase 1）中直接评估 success_conditions，绕过 EventSlice
3. 在 Planner prompt 中引导 LLM 更积极地使用 `schedule_event` directive

**与 P25-14 的关系：** P25-14 Phase 1 的 MilestoneCompletionHook 如果直接评估 success_conditions（不经 EventSlice），则 EventSlice 空转问题降级为"可选优化"。但长期来看，EventSlice 应该被充分利用。

---

#### 16-2. QuestExpiryHook 闲置（🟢 低）

**位置：** `app/game_core/orchestration/hooks/quest_expiry.py`

Hook 每 tick 扫描所有动态任务的 `expiry_ticks` 字段，但 Planner 创建任务时**几乎不设置 expiry_ticks**。导致 Hook 每 tick 扫描→找不到过期任务→空返回。

**修复方案：**
1. Planner prompt 中指导 LLM 为非主线任务设置合理的 expiry_ticks（如 20-50 ticks）
2. `create_quest` directive schema 中将 expiry_ticks 从可选改为推荐字段，提供默认值建议
3. 或在 PlannerQuestHandler 中为 non-milestone 任务自动填充默认 expiry_ticks

**预估：** ~10 行（prompt 引导）或 ~20 行（handler 默认值）

---

#### 16-3. PacingController 纯被动（🟡 中等）

**位置：** `app/game_core/planning/pacing_controller.py`

PacingController 的 `evaluate()` 方法在无 agent 时返回空结果，有 agent 时也只被动等待 LLM 返回 `escalate` 或 `adjust_pacing` directive。**没有自主调节逻辑**——不会根据玩家行为模式、时间流逝或里程碑停滞主动调节节奏。

NarrativeWeaver 有 auto-escalation 安全网（`ticks_since_milestone_progress` 超阈值时触发），但这是兜底机制，不是精细的节奏控制。

**修复方案：**
1. 在 PacingController.evaluate() 中添加**自主评估逻辑**（不依赖 LLM agent）：
   - 如果 `ticks_since_milestone_progress > N` 且 escalation_level < max → 建议 escalate
   - 如果玩家连续 M 次战斗无休息 → 建议 adjust_pacing（降低遭遇率）
   - 如果玩家长时间在同一区域 → 建议 Planner 引导转场
2. 自主评估结果作为 directive 返回给 Planner 参考，不直接执行

**预估：** ~80 行

---

#### 16-4. ItemDesigner 无确定性 fallback（🟡 中等）

**位置：** `app/game_core/planning/item_designer.py`

无 LLM agent 时，`evaluate()` 返回空结果。意味着：
- 动态任务没有奖励设计（rewards 为空或由 Planner 随意填写）
- `curate_shop` directive 不执行（商店不会动态更新商品）
- `design_reward` directive 不执行（任务完成无奖励）

**修复方案：**
1. 添加确定性 fallback：从 ItemRegistry 中按任务难度/类型随机选取合适奖励
2. 商店库存定期刷新可走确定性逻辑（按 NPC 的 merchant tags 从 ItemRegistry 筛选）
3. fallback 逻辑在有 LLM 时不激活，仅作为无 LLM 降级保障

**预估：** ~60 行

---

**P25-16 总预估：** ~170 行（不含 P25-14 重叠部分）

---

### P25-17：P24 双路径残留收口

**来源：** P24 台账交叉审计（2026-03-12 代码验证）

当前后端没有第二套完整主链并行跑，但仍有**三处局部双路径残留**影响代码可维护性和运行时一致性。

#### 17-1. ContextWindow graphize 双路径（🔴 高优）

**位置：**
- `app/game_core/narrative/context_window.py` — 同时存在新方法 `collect_for_graphize()`（line 111）和旧方法 `pop_oldest_for_graphize()`（line 126，标 deprecated）
- `app/game_core/orchestration/npc_interaction.py:435` — 仍调用 `pop_oldest_for_graphize()`
- `app/game_core/orchestration/private_chat.py:263` — 仍调用 `pop_oldest_for_graphize()`
- `app/game_core/narrative/instance_manager.py:237` — 仍调用 `pop_oldest_for_graphize(fraction=1.0)`

**语义差异：**
- **新方法**（`collect_for_graphize`）：返回未 graphized 消息，标记为已 graphized，重置计数器，**不从窗口移除**
- **旧方法**（`pop_oldest_for_graphize`）：移除指定比例的最旧消息，**从窗口中删除**

三处调用方仍在使用旧语义（删除消息），而 `AgenticGmNarrator`（narrators.py:158）已使用新语义（保留消息）。graphize 触发机制不统一。

**修复：** 将三处调用方迁移到 `collect_for_graphize()`，确认语义一致后删除 deprecated 方法。

**预估：** ~20 行改动

**与其他条目的关系：**
- P25-05（graphize_counter 持久化）依赖此项先统一语义
- P25-15-1（L6 知识注入）依赖 graphize 正确工作才能积累知识

---

#### 17-2. Opening 生成混拼（🟡 中等）

**位置：** `app/routers/gameplay.py:715-792`（`/opening/stream` 端点）

**现状：** 一次 opening 可能是混合产物——先尝试 agent opening，narration/comment/options 任一缺失时分别回退到 deterministic `opening_views`。结果可能是 agent narration + deterministic comment + deterministic options。

**影响：** 表现一致性差，调试时难以判断哪条路径生效。但不阻塞功能。

**修复方案：**
- 方案 A：全 agent 路径（有 LLM 时），deterministic 仅作为 no-LLM fallback
- 方案 B：保留混拼但在 SSE 事件中标注来源（`source: "agent"` / `source: "deterministic"`），方便调试

**预估：** 方案 A ~30 行；方案 B ~10 行

---

#### 17-3. `/interact/stream` 双入口（🟡 中等）

**位置：** `app/routers/gameplay.py:795-985`

**现状：** 路由有两条执行分支：
1. **Branch 1**（新路径）：speech-like 请求走 `UtteranceOrchestrator`（lines 804-835），执行完直接 return
2. **Branch 2**（旧路径）：其他请求走 `input_port → interaction_service → agent_svc.run_*`（lines 837-977）

正常请求不会双执行，但路由层是新旧混合入口，维护成本高，容易让后续修复落到错误分支。

**修复方案：** 将 Branch 2 的核心逻辑迁移到 UtteranceOrchestrator 内部统一处理，路由层只保留一个入口。

**预估：** ~80 行重构

---

**P25-17 总预估：** ~130 行

---

### P25-18：P23 残留未闭项

**来源：** P23 §14 验收后仍保留的 open 项 + 本次代码验证发现的 P23 "完成"项实际缺口

#### 18-1. P23 W2-1/W2-2 渲染层缺失（🔴 与 P25-01/02 同源）

**现象：** P23 声称 W2-1（反馈环）和 W2-2（context 丰富化）已完成，但代码验证发现**数据收集完成、渲染缺失**：

| P23 工作项 | 收集层 | 渲染层 | 状态 |
|-----------|--------|--------|------|
| W2-1 反馈环 | `_build_planner_context()` 从 `last_planner_replay_trace` 提取 `previous_directive_results`（kind/status/reason_code，最近 10 条） | `_format_planner_context()` **从未将这些数据输出到 prompt 文本** | ❌ LLM 看不到 |
| W2-2 area 丰富化 | `_build_planner_context()` 收集了 area_npcs、area_boards 等丰富数据 | `_format_planner_context()` 输出了 "Allowed npc ids" 和 "Allowed board ids" 但**从未输出 "Allowed area_ids" 和 "Sub-area IDs"** | ❌ LLM 看不到 |

**结论：** P25-01 和 P25-02 不是新问题，而是 **P23 修复未完成的最后一公里**。修复方案不变（见 P25-01 和 P25-02），但实施时需要在 `_format_planner_context()` 中补充渲染逻辑，而非重新收集数据。

**已被 P25-01/P25-02 覆盖，不需要独立工作项。**

---

#### 18-2. S5-09 temple_keeper 过时快照（🟢 低优）

**位置：** `app/game_core/narrative/context_builder.py` temple_keeper 提取器

**现象：** temple_keeper 的 prompt 在构建时读取 `hp/gold`，但从 prompt 构建到 LLM 响应之间，如果玩家状态变化（如被攻击扣血），temple_keeper 仍基于旧状态给建议。

**修复方案：** 在 NPC 工具执行前做二次校验（如 `heal` 工具执行时重新读取当前 HP）。

**预估：** ~15 行

**优先级：** P3 低优，Demo 期间几乎不会触发此问题（temple_keeper 对话期间不会有其他状态变更）。

---

#### 18-3. S5-04 LLM 在环遵循性未验证（⚠️ 不可代码修复）

**现象：** tool-level 约束护栏（OfferQuestTool 职责过滤、RoleToolRegistry 运行时屏蔽）已有专项测试覆盖。但"真实 LLM 是否稳定遵守 prompt 约束"（如 merchant 不编造价格、receptionist 不推荐不存在的任务）未做集成验证。

**处置：** 这不是代码缺陷，是 LLM 行为一致性问题。需要：
1. 设计 E2E 场景测试（给定 context → LLM 响应 → 检查是否遵守约束）
2. 在 Demo 试玩中观察并记录违规案例
3. 根据违规模式针对性加强 prompt 约束

**不作为 P25 实施项，在 Demo 试玩阶段记录观察结果。**

---

## 6. P23/P24 交叉审计总结

### 6.1 P23 完成度核实（2026-03-12 代码验证）

| P23 工作项 | 声称状态 | 实际状态 | 影响的 P25 条目 |
|-----------|---------|---------|----------------|
| W1-1 奖励发放 | ✅ 已完成 | ✅ **确认完成** — board.py 和 receptionist.py 均调用 `_build_reward_changes()` | — |
| W1-2 商店 bootstrap | ✅ 已完成 | ✅ **确认完成** — opening_bootstrap.py 预初始化 merchant shop_state | — |
| W1-3 任务原子化 | ✅ 已完成 | ✅ **确认完成** — planner.py create_quest 自动 publish_bulletin（delivery_method="board" 时） | — |
| W1-4 错误可见化 | ✅ 已完成 | ✅ **确认完成** — narrative_planner.py 错误计数 + SSE 推送 | — |
| W2-1 反馈环 | ✅ 已完成 | ❌ **渲染层缺失** — 数据收集完成但 prompt 文本中不可见 | → P25-02 |
| W2-2 context 丰富化 | ✅ 已完成 | ❌ **area_ids 未渲染** — Allowed area_ids / sub_area_ids 不在 prompt 中 | → P25-01 |
| W2-3 Skills 激活 | ✅ 已完成 | ⚠️ **弱激活** — prompt 标注"可选"，LLM 几乎不调用 | → P25-11 |
| W2-4 NPC 数据深化 | ✅ 已完成 | ✅ **确认完成** | — |
| W3-1~W4-8 战斗系列 | ✅ 已完成 | ✅ **确认完成** — router/端点/AI/地形/地图均已落地 | — |
| W5-1~W6-9 收尾系列 | ✅ 已完成 | ✅ **确认完成** — 面板导航/碰撞检测/过期 Hook 等 | — |
| S5-09 temple_keeper | 仍未完成 | ⚠️ **仍未完成** — 过时快照问题 | → P25-18-2 |
| S5-04 LLM 遵循性 | 部分完成 | ⚠️ **不可代码修复** — 需 E2E 场景验证 | → P25-18-3 |

### 6.2 P24 双路径收口状态（2026-03-12 代码验证）

| P24 残留 | 当前状态 | 风险 | 影响的 P25 条目 |
|---------|---------|------|----------------|
| §5.1 ContextWindow graphize | **活跃** — 3 处调用方仍用 deprecated `pop_oldest_for_graphize()` | 🔴 HIGH — 语义分裂（删除 vs 标记） | → P25-17-1 |
| §5.2 Opening 混拼 | **活跃** — agent + deterministic per-component 混合 | 🟡 MEDIUM — 表现不一致 | → P25-17-2 |
| §5.3 /interact/stream 双入口 | **活跃** — UtteranceOrchestrator + 旧 input_port 分支 | 🟡 MEDIUM — 维护成本高 | → P25-17-3 |
| §7.1 generate_npc_response | **孤儿** — 仅被测试调用 | 🟢 LOW — 不在 runtime 主链 | 不需要 P25 条目 |
| §7.2 Planner single-shot | **活跃 fallback** — executor=None 时触发（runtime 中永远有 executor） | 🟢 LOW — 安全防御性 fallback | 不需要 P25 条目 |

### 6.3 跨文档依赖汇总

```
P23 W2-1 渲染层缺失 ──→ P25-02（修 _format_planner_context 渲染逻辑）
P23 W2-2 area_ids 未渲染 ──→ P25-01（修 _format_planner_context 渲染逻辑）
P23 W2-3 弱激活 ──→ P25-11（从"可选"加强为 Level 2/3）
P24 §5.1 graphize 双路径 ──→ P25-17-1（统一调用方）──→ P25-05（graphize_counter 持久化）
P24 §5.2 Opening 混拼 ──→ P25-17-2（统一路径或标注来源）
P24 §5.3 双入口 ──→ P25-17-3（UtteranceOrchestrator 统一入口）
```

---

## 附录 E：全面子系统健康检查结果

### E.1 状态层（10 个切片）

| 切片 | 状态 | 问题 |
|------|------|------|
| TimeSlice | ✅ 健康 | 全部字段活跃读写 |
| PlayerSlice | ✅ 健康 | 11+ 字段全部有完整通路 |
| AreaSlice | ⚠️ 混合 | `interactable_states` 写了不读（P25-15-3）；`temporary_sub_areas` 绕过 StateChange 管道 |
| QuestSlice | ✅ 健康 | milestones、dynamic_quests、chapter_completion 全部活跃 |
| SceneSlice | ✅ 健康 | 81+ 写入点，每 tick 消费 |
| RelationSlice | ⚠️ 混合 | `faction_standings` 零消费（P25-15-2） |
| FlagSlice | ✅ 健康 | schemaless 设计，combat/dialogue/event 活跃使用 |
| EventSlice | ⚠️ 空转 | 链路完整但无初始数据（P25-16-1） |
| NarrativePlanSlice | ⚠️ 部分 | 核心字段（directives, escalation）活跃；辅助字段（context_windows_data, actor_knowledge）始终为空 |
| PartySlice | ✅ 健康 | companion 系统正常工作 |

### E.2 规则层（21 个 Handler）

| Handler | 状态 | 问题 |
|---------|------|------|
| CombatHandler | ✅ 工作 | 不注册到 dispatcher（intentional） |
| SkillCheckHandler | ✅ 工作 | — |
| NavigationHandler | ✅ 工作 | — |
| InventoryHandler | ✅ 工作 | — |
| EconomyHandler | ✅ 工作 | — |
| GrowthHandler | ✅ 工作 | — |
| RestHandler | ✅ 工作 | — |
| EncounterHandler | ✅ 工作 | — |
| ContainerHandler | ⚠️ 残留 | `interact_object` v1 未清理（P25-15-6） |
| StatusEffectHandler | ⚠️ 薄验证 | `tick_effects` validate 直接返回 True |
| SpellHandler | ✅ 工作 | — |
| DiscoveryHandler | ⚠️ 空壳 | `passive_scan` 延迟到 Hook（P25-15-8） |
| InteractableHandler | ✅ 工作 | — |
| CompanionHandler | ✅ 工作 | — |
| HostileAreaHandler | ✅ 工作 | — |
| PlannerHandler | ✅ 工作 | 15+ 命令全部活跃 |
| WorldStateHandler | ✅ 工作 | 10 命令全部活跃 |
| BoardHandler | ✅ 工作 | 完整任务板生命周期 |
| ReceptionistHandler | ✅ 工作 | 完整 NPC 任务生命周期 |
| CrimeHandler | ⚠️ 无后果 | steal/lockpick 不触发守卫/声望（P25-08 改造覆盖） |
| ProficiencyHandler | ⚠️ 假实现 | 3 个检查 always-True（P25-15-7） |

### E.3 编排层（19 个 Hook）

| Hook | Priority | 状态 | 问题 |
|------|----------|------|------|
| ScheduledEventHook | 0 | ⚠️ 空转 | 无初始事件数据（P25-16-1） |
| StatusEffectHook | 10 | ✅ 活跃 | — |
| AIOsirisHook | 20 | ✅ 活跃 | 无边界（P25-08） |
| NarrativePlannerHook | 30 | ✅ 活跃 | — |
| NpcScheduleHook | 40 | ✅ 活跃 | characters.json 有 schedule 数据 |
| PassivePerceptionHook | 45 | ✅ 活跃 | — |
| EncounterHook | 50 | ✅ 活跃 | — |
| MilestoneUnlockHook | 55 | ✅ 活跃 | 只做 LOCKED→AVAILABLE，缺 ACTIVE→COMPLETED（P25-14） |
| DynamicSubAreaExpiryHook | 55 | ✅ 活跃 | — |
| RelationshipHook | 60 | ✅ 活跃 | — |
| SharedExperienceHook | 62 | ✅ 活跃 | — |
| CampfireHook | 63 | ✅ 活跃 | — |
| EventConditionHook | 70 | ⚠️ 空转 | 无初始事件数据（P25-16-1） |
| TimeAdvanceHook | 100 | ✅ 活跃 | — |
| QuestExpiryHook | 110 | ⚠️ 闲置 | 任务不设 expiry_ticks（P25-16-2） |
| PrivateChatTriggerHook | 75 | ✅ 活跃 | — |
| DirectiveTriggerHook | 76 | ✅ 活跃 | — |
| GmNarrationHook | 150 | ✅ 活跃 | 有模板 fallback |
| SceneBusResetHook | 999 | ✅ 活跃 | — |

### E.4 叙事层（10 个组件）

| 组件 | 状态 | 问题 |
|------|------|------|
| AgenticExecutor | ✅ 活跃 | 单轮+多轮循环全部工作 |
| RoleToolRegistry | ✅ 活跃 | 23 个工具（5 GM + 10 NPC + 8 Teammate） |
| GmTools | ⚠️ 4/5 活跃 | `describe_environment` 被 prompt 封印（P25-15-5） |
| CharacterTools | ✅ 活跃 | 18 个工具全部有调用方 |
| PlannerTools | ❌ 未使用 | 注册了但 LLM 不调用（P25-11） |
| ContextBuilder | ⚠️ L6 死代码 | `_build_l6()` 从未调用（P25-15-1） |
| ContextWindow | ✅ 活跃 | FIFO + graphize 工作（overflow 极少触发） |
| InstanceManager | ✅ 活跃 | NPC 实例池 + LRU 淘汰 |
| CompanionRuntime | ✅ 活跃 | 经历追踪 + context 注入 |
| RoleStateProxy | ✅ 活跃 | 角色权限控制生效 |

### E.5 规划层（6 个子系统）

| 子系统 | 状态 | 问题 |
|--------|------|------|
| QuestManager | ✅ 工作 | 全链路通 |
| NpcDirector | ✅ 工作 | directive 注入需 InstanceManager |
| WorldBuilder | ✅ 工作 | 不读 SubAreaClusterConfig（P25-13 修复） |
| PacingController | ⚠️ 被动 | 无自主调节（P25-16-3） |
| NarrativeWeaver | ✅ 工作 | GC + despawn + 安全网每 tick 运行 |
| ItemDesigner | ⚠️ 无 fallback | 无 LLM 时禁用（P25-16-4） |

### E.6 适配器层

| 适配器 | 状态 | 问题 |
|--------|------|------|
| LlmPort → GeminiLlmAdapter | ✅ 注入 | — |
| MemoryGraphPort → WorldKnowledgeGraph | ✅ 注入 | — |
| DesignSkillPort → LocalDesignSkillProvider | ✅ 注入 | — |
| PersistencePort → LocalFile/Firestore | ✅ 注入 | — |
| InputPort → FastAPIInputPort | ✅ 注入 | — |
| PresentationPort → SSEPresentationPort | ❌ 死 | 从未注入（P25-15-4） |
| OutputPort | ❌ 死 | 零实现零调用（P25-15-4） |

### E.7 应用层

| 服务 | 状态 | 问题 |
|------|------|------|
| AdminCoordinator | ✅ 活跃 | LRU + 锁，全部方法有调用方 |
| GameRuntime | ✅ 活跃 | save/load 完整，仅 P25-05 graphize_counter bug |
| deps.py | ✅ 完整 | 所有适配器正确注入，LLM 检测工作 |
| InteractionService | ✅ 活跃 | 纯编排，16 方法已提取为模块级函数 |
| AgentOrchestrationService | ✅ 活跃 | 5 个 run_* 方法全部有调用方 |
| narrators.py | ✅ 活跃 | GM + Planner 两个 narrator 全部集成 |
| 6 个 Router（30+ 端点）| ✅ 活跃 | 无死端点 |

### E.8 跨系统通路验证

| 通路 | 状态 |
|------|------|
| 玩家行动 → tick → settlement hooks → SSE | ✅ 全链路通 |
| Planner directive → subsystem → command → handler → state → 持久化 | ✅ 全链路通（但 area directive 因 P25-01 常被拒绝） |
| NPC 对话 → agent → 工具调用 → 状态变更 → 响应 | ✅ 全链路通 |
| 任务生命周期：创建 → 接受 → 目标 → 完成 → 汇报 → 奖励 | ✅ 全链路通（奖励 P23 W1-1 已修） |
| 里程碑生命周期：LOCKED → AVAILABLE → ACTIVE → COMPLETED → 级联 | ❌ ACTIVE→COMPLETED 无自动触发（P25-14 Phase 1） |
| Planner context → LLM → directive → 反馈 → 下轮 context | ⚠️ 反馈数据收集了但 prompt 中不可见（P25-02，P23 W2-1 渲染缺失） |
| 知识积累：对话 → graphize → WorldKnowledgeGraph → L6 → NPC prompt | ❌ 三重断裂：graphize 双路径（P25-17-1）+ counter 不持久化（P25-05）+ L6 从未调用（P25-15-1） |
| Save → Exit → Load → Resume | ⚠️ 基本通，graphize_counter 丢失（P25-05） |
| Opening → 场景 → 初始选项 → 探索 → 主线引导 | ⚠️ 无进程引导（P25-14 Phase 3），opening 混拼（P25-17-2） |

---

## 3. 优先级排序

| 编号 | 优先级 | 理由 |
|------|--------|------|
| P25-01 | **P0 — 必须修** | 根因。不修的话 area 相关 directive 永远失败 |
| P25-08 | **P0 — 必须修** | 因果系统无边界，严重影响游戏公平性和沉浸感 |
| P25-14 | **P0 — 必须修** | 主线推进链断裂，里程碑无法自动完成，游戏核心循环不闭合 |
| P25-02 | **P1 — 应该修** | 反馈链断裂导致 LLM 无法自我纠正，放大 P25-01 的影响 |
| P25-04 | **P1 — 应该修** | 没有诊断能力就无法确认修复效果 |
| P25-06 | **P1 — 应该修** | 检定系统的核心体验缺陷，链路通但约束力为零 |
| P25-07 | **P1 — 应该修** | 任务面板显示乱码级内容，直接影响玩家体验 |
| P25-11 | **P1 — 应该修** | Planner 不查模板就凭空编，生成质量不稳定 |
| P25-12 | **P1 — 应该修** | 里程碑直接暴露破坏沉浸感，任务面板体验差 |
| P25-15 | **P1 — 应该修** | L6 死代码导致世界知识注入完全失效；死字段/死适配器增加维护负担 |
| P25-03 | **P2 — 需要修** | 偶发崩溃，不影响主流程但影响体验 |
| P25-05 | **P2 — 需要修** | 长期记忆积累滞后，短期不致命但影响长局体验 |
| P25-16 | **P2 — 需要修** | 基础设施空转，不致命但浪费计算 + 隐性功能缺失 |
| P25-13 | **P2 — 功能增强** | 三层嵌套子地点，提升探索深度，但非当前阻塞项 |
| P25-17 | **P1 — 应该修** | 17-1 graphize 双路径影响知识积累可靠性（P25-05/15-1 前置）；17-2/17-3 影响可维护性 |
| P25-18 | **P3 — 可选** | 18-2 低优小修；18-3 不可代码修复（E2E 验证）；18-1 已被 P25-01/02 覆盖 |
| P25-09 | **已吸收 → P25-13** | fill_tags/density 未消费，并入 P25-13 Phase 5 |
| P25-10 | **已吸收 → P25-13** | 子地点簇位置感知，并入 P25-13 Phase 0 调查 + Phase 5 |

## 4. 实施依赖

- P25-01 和 P25-04 互相独立，可并行
- P25-02 依赖 P25-01 先修（否则诊断链改了但 area_id 还是缺，测不出效果）
- P25-03、P25-05 独立，随时可修
- P25-06 独立，但建议在 P25-03 之后（确保 Gemini 空响应不会干扰检定流程）
- P25-06 内部：A 层先做（基线保障），B 层后做（精确控制），C 层与 B 层同步
- P25-07 独立，随时可修（纯 prompt 改动）
- P25-08 独立，但优先级高应尽早修；Phase 2-5 改造是后续长期工作
- P25-11 独立，纯 prompt 改动，但建议在 P25-07 之后（先修语言约束再加模板引导）
- P25-12 独立，后端改动小，但前端需要配合（可先做后端，前端后续跟进）
- P25-13 建议在 P25-08 Phase 1 之后实施（局部性修复为 room 级可见性打基础）
- P25-13 内部：Phase 1-2 先做（数据结构+导航），Phase 3-5 后续跟进
- P25-14 Phase 1（里程碑自动完成）是最高优先级——没有它主线完全卡死
- P25-14 Phase 2（目标追踪）依赖 Phase 1 的条件评估基础设施
- P25-14 Phase 3（进程引导）独立，纯 prompt 改动
- P25-14 Phase 4（队友触发）依赖 P25-13 的 appear_condition（先出场再招募）
- P25-14 Phase 5（章节完成）等内容充足时再做
- P25-15-1（L6 死代码）独立，随时可修，但建议在 P25-05 之后（先修 graphize_counter 持久化）
- P25-15 其余项独立，可随时清理
- P25-16-1（EventSlice 空转）与 P25-14 Phase 1 相关——如果 MilestoneCompletionHook 直接评估 success_conditions 则 EventSlice 问题降级
- P25-16-3（PacingController 被动）独立，可在 P25-14 Phase 3 一起做（都是节奏控制相关）
- P25-16-4（ItemDesigner 无 fallback）独立，随时可修
- P25-17-1（graphize 双路径）应在 P25-05（graphize_counter 持久化）之前完成——先统一语义再修持久化
- P25-17-1 也是 P25-15-1（L6 知识注入）的间接前置——graphize 正确工作才能积累知识到 WorldKnowledgeGraph
- P25-17-2（Opening 混拼）和 P25-17-3（/interact/stream 双入口）独立，随时可修
- P25-18-2（temple_keeper 过时快照）独立，低优可延后
- P25-18-3（LLM 遵循性）不可代码修复，在 Demo 试玩中观察

## 5. Demo 最小闭环路径与因果链分析

### 5.1 第一章里程碑链

```
ms_familiar_banter (序列1)
  success_conditions: location_visited(ancient_ruins) + npc_talked(goblin_slayer)
  → 完成后解锁 ↓

ms_specialist_cleaning (序列2)
  success_conditions: kill_count(goblin, 3) + location_visited(ancient_ruins/inner_sanctum)
  → 完成后解锁 ↓

ms_call_from_water_capital (序列3)
  success_conditions: location_visited(frontier_town) + npc_talked(guild_girl)
  → next_milestones: [] (章节终点)
```

### 5.2 跨条目因果链

**因果链 A：Planner → 遭遇 → kill_count → 里程碑**
```
P25-01 缺 area_id → Planner 无法发 plant_encounter directive
  → ancient_ruins 没有动态遭遇 → 没有哥布林战斗
  → kill_count_goblin 永远为 0
  → ms_specialist_cleaning 的 success_conditions 永远不满足
  → 第二个里程碑卡死 → 章节无法推进
```
P25-01 不仅影响"6 次拒绝"，还间接卡死第二个里程碑。

**因果链 B：P25-08 × P25-06 组合爆炸**
```
玩家对 NPC 发起检定（说服/威吓）
  → 检定成功但 NPC 无视（P25-06 无约束）
  → AIOsirisHook settlement tick 评估后果
  → Osiris 无边界（P25-08）对全图 NPC 修改 disposition
  → 原本友好的 NPC 可能突然 hostile
  → RelationshipHook 自动 dismiss hostile 党员
  → 队伍崩溃，主线卡死
```
P25-08 和 P25-06 不修会产生组合爆炸式体验问题。

**因果链 C：P25-17-1 → P25-05 → P25-15-1 知识链路**
```
graphize 双路径（P25-17-1）→ 语义不一致
  → graphize_counter 持久化（P25-05）在不一致的基础上做了也白做
  → L6 知识注入（P25-15-1）即使修复，WorldKnowledgeGraph 也无可靠数据源
  → NPC 对话无世界知识
```
必须先统一 graphize 语义，再修持久化，再修 L6 注入。

**因果链 D：P25-16-1 EventSlice × P25-14 Phase 1 重叠**
```
方案 A（推荐）：MilestoneCompletionHook 直接用 EventEngine._condition_met() 评估
  → 不需要 EventSlice 有数据 → EventSlice 空转问题降级
方案 B：Bootstrap 时从 quests.json success_conditions 生成 pending_events
  → 架构更复杂且两套评估可能不一致
```

### 5.3 分层修复优先级（以"Demo 优异运行"为目标）

```
优先级 │ 条目                          │ 改动量   │ 阻塞关系
───────┼──────────────────────────────┼─────────┼───────────────────
       │ ══ 第 0 层：稳定性基础 ══      │         │
  1    │ P25-03 Gemini空响应防护       │ ~10行   │ 所有LLM路径的安全网
  2    │ P25-04 诊断日志               │ ~4行    │ 验证后续修复效果
       │                              │         │
       │ ══ 第 1 层：Planner 能工作 ══  │         │
  3    │ P25-01 area_ids 补全          │ ~30行   │ Planner 解锁 area 能力（→遭遇→kill_count）
  4    │ P25-02 反馈链渲染             │ ~80行   │ Planner 自我纠错
       │                              │         │
       │ ══ 第 2 层：主循环能闭合 ══    │         │
  5    │ P25-14 Phase 1 里程碑自动完成 │ ~120行  │ 主线推进的核心（含 visited_area flag）
  6    │ P25-14 Phase 3 进程引导       │ ~40行   │ 玩家有方向感
       │                              │         │
       │ ══ 第 3 层：体验不崩坏 ══      │         │
  7    │ P25-08 Phase 1 Osiris 局部性  │ ~60行   │ 后果不全图扩散
  8    │ P25-06 A 检定约束注入         │ ~50行   │ RPG 核心体验
  9    │ P25-07 语言约束               │ ~20行   │ 中文显示
 10    │ P25-15-1 L6 死代码修复        │ ~15行   │ NPC 有世界知识
 11    │ P25-17-1 graphize 统一        │ ~20行   │ 知识积累可靠性
       │                              │         │
       │ ══ 第 4 层：体验丰富 ══        │         │
 12    │ P25-12 任务面板优化           │ ~40行   │ 里程碑不泄露
 13    │ P25-11 Skills 强制引导        │ ~30行   │ Planner 内容质量
 14    │ P25-02 + P25-14 Phase 2       │ ~150行  │ 目标自动追踪
 15    │ P25-05 graphize_counter       │ ~10行   │ 长期知识积累
       │ ══ 后续优化 ══                 │         │
 16+   │ P25-06 B/C, P25-13, P25-14   │ ...     │ 精确检定/三层地点/队友招募/章节完成
       │ Phase 4/5, P25-16, P25-17    │         │ 基础设施补种/路由收口
       │ -2/3, P25-18                 │         │
───────┼──────────────────────────────┼─────────┼───────────────────
  合计 │ 第 0-3 层（Demo 最小闭环）    │ ~430行  │
  合计 │ 第 0-4 层（Demo 优异运行）    │ ~660行  │
```

---

## 5b. 架构合规性

所有修复均符合 P23 §1.1 架构边界：
- P25-01：补 context（允许的修法）
- P25-02：补 validator 反馈链（允许的修法）
- P25-03：防御性编程（适配器层）
- P25-04：日志增强（无状态变更）
- P25-05：序列化补全（纯数据层）
- P25-06-A：prompt 拼接（orchestration 层，不改 game_core 状态语义）
- P25-06-B：tool schema 扩展 + dispatch 传递（narrative 层 + 应用层，不改规则引擎）
- P25-07：prompt 语言约束 + 可选内容清洗（叙事层）
- P25-08：prompt 局部性约束 + context 过滤 + validation gate + 后续 ConsequenceEngine（orchestration 层）
- P25-11：prompt 模板引导（叙事层）
- P25-12：API 响应过滤 + view 层补字段（应用层）
- P25-13：状态层扩展 + 内容层扩展 + 规则层命令 + 编排层 Hook + 叙事层工具（跨层但每层各司其职）
- P25-14：编排层新 Hook（MilestoneCompletionHook、QuestObjectiveTrackingHook）+ 内容层 schema 扩展 + prompt 强化
- P25-15：死代码清理（各层）+ L6 调用接入（叙事层）
- P25-16：数据层补种 + 规划层自主逻辑 + 确定性 fallback（各层独立改动）
- P25-17：17-1 叙事层 ContextWindow 方法统一 + 编排层调用方迁移；17-2/17-3 应用层路由统一（不跨层）
- P25-18：18-1 已被 P25-01/02 覆盖；18-2 叙事层 context_builder 执行前校验；18-3 不涉及代码

---

## 附录 A：子地点簇（Sub-Area Cluster）调查报告

### A.1 当前实现

| 组件 | 位置 | 说明 |
|------|------|------|
| `SubAreaClusterConfig` | `map_types.py:118-124` | 模板层配置：`max_dynamic=5`, `max_permanent_dynamic=2`, `fill_tags`, `fill_density` |
| `AreaState.temporary_sub_areas` | `area.py:29` | 运行时存储：`list[dict]`，每项含 id/label/type/tier/expiry/tags/discovery_mode 等 |
| `count_dynamic_sub_areas()` | `area.py:602` | 按 tier 分类计数（permanent/timed/temporary） |
| `has_cluster_capacity()` | `area.py:615` | **硬编码**容量检查：permanent<3, timed<5, temporary<3, total<6 |
| `tick_expiry()` | `area.py:625` | 每 tick 递减 expiry，移除过期项，永久项(expiry=-1)不触碰 |
| `DynamicSubAreaManager` | `planning/dynamic_sub_area.py` | 高层 API：create/expire/list_active/get_cluster_status |
| `DynamicSubAreaExpiryHook` | `hooks/dynamic_sub_area_expiry.py` | priority=55，每 tick 清理过期子地点 + 弹出玩家 |
| `NavigationHandler` | `rules/handlers/navigation.py:96` | 先查静态 sub_locations，再 fallback 查 temporary_sub_areas |
| `scene_views.py` | 应用层 | 合并静态 + 动态子地点为统一 `sub_locations` 数组发送前端 |

### A.2 当前能力 vs 设计意图

| 能力 | 状态 | 说明 |
|------|------|------|
| **容量管控** | ✅ 运行中 | 但用硬编码值，`SubAreaClusterConfig.max_dynamic` 未被读取 |
| **位置绑定** | ✅ 运行中 | area_id 明确传递到 StateChange，子地点正确绑定到目标区域 |
| **生命周期** | ✅ 运行中 | ExpiryHook 递减 + 过期移除 + 玩家弹出 |
| **前端导航** | ✅ 运行中 | 动态子地点与静态子地点统一显示为导航选项 |
| **偏好引导** | ❌ 未消费 | `fill_tags` 已加载但 WorldBuilder 创建时不看 |
| **密度控制** | ❌ 未消费 | `fill_density`（sparse/normal/dense）无效果 |
| **发现门槛** | ⚠️ 字段有 | `discovery_mode`/`discovery_dc` 字段存在但未完整联动 |
| **Planner 感知** | ⚠️ 只看到 counts | 不知道 cluster config 的偏好和限制 |

### A.3 子地点簇应承担的完整职责

1. **空间规划**：区域能容纳多少动态地点、什么类型（容量 + 偏好）
2. **位置感知**：Planner 创建的子地点绑定到正确区域，只在该区域可访问
3. **内容适配**：不同区域生成不同风格的子地点（fill_tags 引导）
4. **密度控制**：活跃区多生成、荒凉区少生成（fill_density 控制）
5. **发现机制**：部分子地点需要感知/调查检定才能发现

---

## 附录 B：角色出场机制调查报告

### B.1 当前机制

```
characters.json 每个 NPC 定义 area_id + location_id（静态）
  → 游戏启动
    → get_area_npcs() 查询（presence.py:15）
      → 1. 先看 AreaSlice.npc_locations（运行时位置）
      → 2. Fallback: CharacterRegistry.area_id（静态模板）
    → 所有 area_id="frontier_town" 的 NPC 一开始全部出现
```

- 13 个 NPC 有 12 个 `area_id="frontier_town"`，游戏一开始全部在场
- NpcScheduleHook（priority=40）可按时间段移动 NPC 到不同子地点（O-4 已实现）
- `spawn_quest_npc` directive 可创建临时任务 NPC（已实现）

### B.2 缺失的能力

| 能力 | 说明 | 当前状态 |
|------|------|----------|
| **剧情触发出场** | NPC 在特定里程碑达成后才出现 | ❌ 没有 `appear_after_milestone` 机制 |
| **Planner 调度出场/离场** | Planner 通过 directive 控制 NPC 出现或离开 | ⚠️ `direct_npc` 只发行为指令，不控制出场 |
| **条件可见性** | NPC 有 `visibility_condition`，条件不满足时不出现在 `get_area_npcs()` | ❌ 完全没有 |
| **分批出场** | 根据剧情进展逐步引入新角色（而非一股脑全出） | ❌ 没有机制 |

### B.3 与内容层的呼应

内容层 `characters.json` 可以扩展：
- 新增 `appear_condition` 字段（如 `{"milestone": "ms_familiar_banter", "state": "completed"}`）
- `get_area_npcs()` 查询时检查条件是否满足
- Planner context 中标注"尚未出场的角色"供 LLM 做剧情编排参考

---

## 附录 C：游戏初始化流程调查报告

### C.1 完整流程（带中文注释）

```
【第一步：创建会话】
POST /api/game/{world_id}/sessions
  → GameRuntime.create_session(world_id)
    → get_world(world_id)                    // 加载世界数据（10 个内容注册表从 JSON 加载）
    → build_runtime_for_world(world)         // 构建运行时
      → StateContainer.create_new(world)     // 初始化 10 个状态切片
      → RulesEngine + 13 个 CommandHandler   // 注册规则引擎
      → ActionDispatcher（62 个动作→命令映射）// 注册动作分发器
      → PipelineOrchestrator                 // 注册管线编排器
      → SceneBus                             // 注册场景总线
      → TickCoordinator                      // 注册 tick 协调器
    → 注册 18+ 个 SettlementHook（按优先级排序）
    → save_runtime()                         // 持久化初始状态
    → 返回 ManagedSession(phase="character_creation")

【第二步：创建角色】
POST /api/game/{world_id}/sessions/{session_id}/character
  → complete_character_creation(session, spec)
    → execute create_character Command       // 写入 PlayerSlice（名字、种族、职业、属性）
    → 拾取起始物品（pick_up × N）            // 写入背包
    → 装备默认装备（equip × N）              // 写入装备栏
    → 设置出生地点（modify_location）        // 写入 player.current_area/location
    → 标记出生区域为"已发现"
    → session.phase = "opening_ready"
    → bootstrap_opening_planner()            // 【见第三步】
    → save_session()                         // 持久化

【第三步：Bootstrap 开场剧情】（确定性，不需要 LLM）
bootstrap_opening_planner(session)
  → NarrativePlannerHook.bootstrap(context)
    → OpeningBootstrapQuestAgent.evaluate()  // 确定性 Agent
      → 找到第一个可用里程碑（ms_familiar_banter）
      → 生成 directive: create_quest(dq_ms_familiar_banter)
      → 生成 directive: publish_bulletin（发布到任务板）
    → 应用 directives → 写入 NarrativePlanSlice + QuestSlice
  → 初始化商店库存（refresh_shop × 每个 merchant NPC）

【第四步：播放开场】
POST /api/game/{world_id}/sessions/{session_id}/opening/stream（SSE 流）
  → 发送 scene_change 事件                   // 场景描述
  → 发送 narration 事件                      // GM 开场白（LLM 或模板）
  → 发送 comment 事件                        // GM 吐槽（LLM 或模板）
  → 发送 character_enter × 3 事件            // 最多 3 个 NPC 立绘入场
  → 发送 status_update 事件                  // HUD 数据（HP/金币/时间）
  → 发送 dialogue_options 事件               // 初始选项（LLM 或模板）
  → 发送 location_overview 事件              // 完整场景状态
  → session.phase = "active"                 // 进入活跃状态
  → save_session()                           // 持久化
  → 发送 stream_end                          // 流结束
```

### C.2 各状态切片初始值

| 切片 | 初始化来源 | 初始内容 |
|------|-----------|---------|
| **TimeSlice** | 硬编码 | day=1, slot=8（早晨）, action_count=0 |
| **PlayerSlice** | 第二步创建 | 角色创建前为空 |
| **RelationSlice** | 世界内容 | 所有 NPC 的 base_disposition + 阵营初始声望 |
| **QuestSlice** | 世界内容 | 里程碑状态（有前置=LOCKED，无前置=AVAILABLE），章节进度=0 |
| **FlagSlice** | 空 | `{}` |
| **AreaSlice** | 世界内容 | 区域结构 + NPC 位置（来自 CharacterRegistry.area_id）+ danger_level |
| **EventSlice** | 世界内容 | 初始事件（state="locked"） |
| **PartySlice** | 空 | 无队员 |
| **NarrativePlanSlice** | 第三步 bootstrap | bootstrap 后有初始 directives + story_facts |
| **SceneSlice** | 空 | 每 tick 重置的缓冲区 |

### C.3 初始化期间各系统的实际参与情况

| 系统 | 是否参与 | 做了什么 |
|------|---------|---------|
| **WorldInstance & 10 个 Registry** | ✅ 实际加载 | 从 JSON 加载验证所有内容数据 |
| **StateContainer & 10 个 Slice** | ✅ 实际初始化 | 用世界数据填充初始状态 |
| **RulesEngine** | ✅ 执行命令 | create_character + pick_up×N + equip×N + modify_location |
| **NarrativePlannerHook.bootstrap()** | ✅ 确定性执行 | 创建初始任务 + 发布公告 |
| **OpeningBootstrapQuestAgent** | ✅ 确定性执行 | 生成初始 quest directives |
| **商店初始化** | ✅ refresh_shop | 初始化 merchant NPC 的商品库存 |
| **AgentOrchestrationService** | ⚠️ 仅在有 LLM 时 | 生成 opening sequence（叙述/吐槽/选项） |
| **18+ SettlementHook** | ❌ 只注册 | 初始化时**不执行**，等第一次 settlement tick |
| **AIOsirisHook** | ❌ 只注册 | 等正常 tick |
| **GmNarrationHook** | ❌ 只注册 | 等正常 tick（开场白走 opening 专用路径） |
| **WorldKnowledgeGraph** | ❌ 延迟初始化 | 第一次 agent 调用时才 seed |
| **ContextWindow/InstanceManager** | ❌ 延迟创建 | 第一次 NPC 交互时才创建 |
| **SaveStore** | ✅ 多次持久化 | 创建会话后/创建角色后/开场后各保存一次 |

---

## 附录 D：后端子系统清单

### D.1 总览

| 层级 | 子系统数量 | 说明 |
|------|-----------|------|
| **状态层** State | 10 个 StateSlice + 基础设施 | 全部工作中 |
| **内容层** Content | 11 个 Registry + 基础设施 | 全部工作中 |
| **规则层** Rules | 23 个 CommandHandler + RulesEngine | 全部工作中 |
| **编排层** Orchestration | 20+ SettlementHook + 协调器 + 事件引擎 | 全部工作中 |
| **规划层** Planning | 6 个 PlannerSubSystem + Dispatcher + Manager | 全部工作中（LLM 依赖） |
| **叙事层** Narrative | Executor + Registry + 3 类 Tools + Context 系统 | 全部工作中 |
| **适配器层** Adapters | 9 个 Port Protocol + 实现 | 全部工作中 |
| **应用层** Application | 27+ 服务/视图/路由 | 全部工作中 |

### D.2 状态层（10 个切片）

| 切片 | 文件 | 职责 |
|------|------|------|
| TimeSlice | `state/slices/time.py` | 游戏时钟：日/时段/行动计数 |
| PlayerSlice | `state/slices/player.py` | 玩家角色：属性/背包/位置 |
| AreaSlice | `state/slices/area.py` | 地图区域：NPC 位置/子地点/公告板/敌对追踪 |
| QuestSlice | `state/slices/quests.py` | 任务生命周期：里程碑/动态任务/目标 |
| SceneSlice | `state/slices/scene.py` | 场景总线：叙事记录/状态变更（每 tick 重置） |
| RelationSlice | `state/slices/relations.py` | 关系系统：NPC 好感/信任/阶段/商店状态 |
| FlagSlice | `state/slices/flags.py` | 通用标志：schemaless key-value |
| EventSlice | `state/slices/events.py` | 事件系统：条件触发/状态转换 |
| NarrativePlanSlice | `state/slices/narrative_plan.py` | 叙事规划：指令/策略/行为窗口/故事事实 |
| PartySlice | `state/slices/party.py` | 队伍组成：同伴状态/情感追踪 |

### D.3 内容层（11 个注册表）

| 注册表 | 数据文件 | 当前数据量 |
|--------|---------|-----------|
| CharacterRegistry | characters.json | 13 个 NPC |
| ItemRegistry | items.json | 30+ 物品 |
| SkillRegistry | skills.json | 20+ 技能 |
| ClassRegistry | classes.json | 职业/种族/背景 |
| MonsterRegistry | monsters.json | 4 种怪物（全哥布林系） |
| MapRegistry | maps.json | 4 个区域 |
| QuestRegistry | quests.json | 1 章节 + 3 里程碑 |
| TagRegistry | tags.json | 7 个维度 |
| FactionRegistry | factions.json | 4 个阵营 |
| LoreRegistry | lore.json | 5 条世界观 + 3 条规则 |
| BattleMapRegistry | battle_maps.json | 10 张战斗地图 |

### D.4 规则层（23 个 Handler）

| Handler | 命令类型 | 状态 |
|---------|---------|------|
| CombatHandler | start_combat/attack/defend/disengage/flee | ✅ |
| SkillCheckHandler | skill_check/saving_throw/contest/investigate | ✅ |
| NavigationHandler | navigate/enter_sub_location/leave_sub_location | ✅ |
| InventoryHandler | pick_up/drop_item/use_item | ✅ |
| EconomyHandler | trade_buy/trade_sell | ✅ |
| GrowthHandler | level_up/gain_ability | ✅ |
| RestHandler | rest_short/rest_long | ✅ |
| CrimeHandler | steal/kill_npc | ✅ |
| EncounterHandler | trigger_encounter | ✅ |
| ContainerHandler | open_container/close_container | ✅ |
| WorldStateHandler | set_flag/modify_location/modify_disposition/... | ✅ |
| StatusEffectHandler | apply_status_effect/remove_status_effect | ✅ |
| SpellHandler | cast_spell/prepare_spells/break_concentration | ✅ |
| BoardHandler | accept_quest_board/report_quest_board | ✅ |
| ReceptionistHandler | accept_quest_npc/report_quest_npc | ✅ |
| ProficiencyHandler | 技能熟练度检查 | ✅ |
| DiscoveryHandler | discover/investigate | ✅ |
| InteractableHandler | interact_object_v2 | ✅ |
| CompanionHandler | recruit/dismiss/force_leave_companion | ✅ |
| HostileAreaHandler | enter_hostile_area | ✅ |
| PlannerHandler | 15 种 planner_ 内部命令 | ✅ |
| EventConditionHandler | 事件状态转换 | ✅ |

### D.5 编排层 SettlementHook（按优先级排序）

| 优先级 | Hook | LLM 依赖 | 说明 |
|--------|------|---------|------|
| 0 | ScheduledEventHook | 否 | 按 tick 触发预定事件 |
| 10 | StatusEffectHook | 否 | 状态效果倒计时 |
| 20 | AIOsirisHook | **是** | 因果后果评估 |
| 30 | NarrativePlannerHook | **是** | 叙事规划 + 指令分发 |
| 40 | NpcScheduleHook | 否 | NPC 日程移动 |
| 45 | PassivePerceptionHook | 否 | 被动感知检测 |
| 50 | EncounterHook | 否 | 随机遭遇触发 |
| 55 | DynamicSubAreaExpiryHook | 否 | 动态子地点过期清理 |
| 60 | RelationshipHook | 否 | 关系阶段跃迁 |
| 62 | SharedExperienceHook | 否 | 共同经历记录 |
| 63 | CampfireHook | **是** | 篝火对话 |
| 65 | MilestoneUnlockHook | 否 | 里程碑解锁 |
| 70 | EventConditionHook | 否 | 事件条件检查 |
| 75 | PrivateChatTriggerHook | 否 | 私聊触发检测 |
| 76 | DirectiveTriggerHook | 否 | 指令触发 NPC 行为 |
| 100 | TimeAdvanceHook | 否 | 时钟推进 |
| 110 | QuestExpiryHook | 否 | 任务过期 |
| 150 | GmNarrationHook | **是** | GM 结算叙事 |
| 999 | SceneBusResetHook | 否 | 场景总线清空 |

### D.6 规划层（6 个子系统）

| 子系统 | 处理的 directive 类型 | LLM 依赖 |
|--------|---------------------|---------|
| QuestManager | create_quest/publish_bulletin/retire_quest/update_quest | 是 |
| NpcDirector | direct_npc/spawn_quest_npc | 是 |
| WorldBuilder | plant_environmental/fill_area/plant_encounter | 是 |
| PacingController | escalate/adjust_pacing | 是 |
| NarrativeWeaver | （无 directive，纯维护：GC/despawn/auto-escalation） | 是 |
| ItemDesigner | design_reward/curate_shop | 是 |

### D.7 叙事层

| 组件 | 文件 | 职责 |
|------|------|------|
| AgenticExecutor | `narrative/executor.py` | 统一 Agent 执行器（单轮+多轮） |
| RoleToolRegistry | `narrative/registry.py` | 按角色/trait 过滤工具注册 |
| GmTools | `narrative/gm_tools.py` | narrate/comment/pass_turn/suggest_options |
| CharacterTools | `narrative/character_tools.py` | speak/emote/take_action/offer_trade |
| PlannerTools | `narrative/planner_tools.py` | read_design_skill/list_design_skills |
| ContextBuilder | `narrative/context_builder.py` | 7 层 Agent 上下文组装（L0-L7） |
| ContextWindow | `narrative/context_window.py` | FIFO 滑动窗口（32K token） |
| InstanceManager | `narrative/instance_manager.py` | NPC 实例池（LRU 淘汰） |
| CompanionRuntime | `narrative/companion_runtime.py` | 同伴运行时跟踪 |
| RoleStateProxy | `narrative/role_proxy.py` | 按角色限制状态访问 |

### D.8 应用层关键服务

| 服务 | 文件 | 职责 | LLM 依赖 |
|------|------|------|---------|
| AgenticGmNarrator | `narrators.py` | GM 结算叙事 | 是 |
| AgenticNarrativePlanner | `narrators.py` | LLM 叙事规划 | 是 |
| AgentOrchestrationService | `agent_orchestration.py` | NPC/GM/Teammate 交互编排 | 是 |
| AgenticAIOsirisEvaluator | `evaluators.py` | 因果后果 LLM 评估 | 是 |
| WorldKnowledgeGraph | `world_knowledge_graph.py` | 知识图谱（NetworkX） | 否 |
| KnowledgeGraphMemoryRetriever | `memory_retriever_impl.py` | L6 记忆检索 | 否 |
| GeminiLlmAdapter | `llm_gemini.py` | Gemini API 适配器 | 是 |
| InteractionService | `interaction_service.py` | 交互执行 + 视图编排 | 否 |
| AdminCoordinator | `admin_coordinator.py` | 会话缓存 + 锁 | 否 |
| GameRuntime | `game_core/runtime.py` | 会话生命周期管理 | 部分 |

### D.9 API 路由（6 个 Router）

| Router | 端点数 | 说明 |
|--------|--------|------|
| SessionsRouter | 5 | 会话创建/查询/恢复 |
| CharacterRouter | 2 | 角色创建选项/创建 |
| PanelsRouter | 3 | 背包/地图/任务面板 |
| GameplayRouter | 10+ | tick/interact/opening/stream |
| CombatRouter | 5+ | attack/defend/cast/flee |
| ImagesRouter | 2 | AI 图片生成/缓存 |

---

## 追加课题（2026-03-12）

### P25-18：柜员接取任务工具（AcceptQuestTool）

**需求**：guild_girl 应有帮助玩家接取任务的工具，让玩家在对话中自然地接取任务，而非必须手动操作任务板。

**设计方向**：
- 新增 `AcceptQuestTool`，NPC 对话中调用后触发 `board_accept_quest` 命令
- 使用 `applicable_traits=["quest_giver"]` 过滤，只有标记为 quest_giver 的 NPC 才拥有此工具
- 工具参数：`quest_id`（从当前公告板可用任务中选择）
- 工具内部调用现有的 `board_accept_quest` 命令链路，不重复实现

**文件**：
- `app/game_core/narrative/gm_tools.py` 或新文件 — AcceptQuestTool 定义
- `app/game_core/narrative/role_tool_registry.py` — 注册工具
- `data/goblin_slayer/v2/characters.json` — guild_girl tags 添加 `"quest_giver"`

**预估**：~30 行
**优先级**：P27 完成后实施

---

### P25-19：Planner 动态配发工具能力（P28 设计课题）

**需求**：让 Planner 能动态给 NPC 赋予/移除工具（如"让铁匠临时提供鉴定服务"、"让酒馆老板提供情报交换"），增加游戏动态性。

**设计方向**：
- 当前 NPC 工具是静态注册的（`RoleToolRegistry` + `applicable_traits` 过滤）
- 新增 `assign_tool` / `revoke_tool` directive 类型
- 运行时 tool override 机制：在 `AgenticExecutor` 构建 tool 列表时，合并静态注册 + 动态分配的工具
- 动态工具存储：`FlagSlice` 或新增 `NpcToolSlice`

**复杂度评估**：
- 需要定义"可分配工具"的边界（哪些工具可以动态分配？全部还是白名单？）
- Planner prompt 需要知道有哪些可分配工具
- 工具生命周期管理（永久？有效期？任务绑定？）

**预估**：~200-300 行（含 directive 定义、subsystem 处理、运行时合并、prompt 引导）
**优先级**：记录为 P28 设计课题，不急于实施——先让现有工具体系稳定运行
