# P18 NarrativePlanner 深化与知识层修复

创建时间：2026-03-09
状态：设计确认，待实施

---

## 1. 背景

P12 审计发现 NarrativePlanner 系统（§3）存在架构偏离和功能缺口。深入调查后发现问题域更大——不仅是 planner 本身，还涉及动态知识积累和持久化的系统性断裂。

本文档将三个关联问题域统一规划，按依赖关系排序实施。

---

## 2. 系统运行状态基线

### 2.1 正常工作（无需改动）

| 组件 | 说明 |
|---|---|
| NarrativePlannerHook 格结算链 | P35 触发、should_skip 逻辑、指令分发全部正常 |
| 9 种指令执行代码 | 每个指令都有真实状态变更，无占位符 |
| NPC 对话 6 步链路 | InteractionService → AgentOrchestration → ContextBuilder → LLM → 结果处理 |
| GM / Teammate 反应 | 场景感知 + 关系响应链路通畅 |
| direct_npc 消费链路 | NarrativePlanSlice.directives → InstanceManager inject → NPC prompt 注入 |
| ensure_seeded 静态种子 | 10 个 ContentRegistry 正确注入 WorldKnowledgeGraph |
| query_spread 图谱查询 | BFS 扩散激活算法正确，30+ 测试覆盖 |
| L0-L7 上下文构建 | 7 层全部实现，无 stub |
| FlagSlice / QuestSlice / RelationSlice / EventSlice | 读写持久化全部正常 |

### 2.2 三个系统性断点

#### 断点 A：write_episode 触发条件不可达

- **现象**：ContextWindow max_tokens=200,000，overflow 阈值 0.9（180K tokens）。一次 NPC 对话约 500-2000 tokens，需连续 100+ 轮才可能溢出。实际游戏中不可能发生。
- **影响**：动态知识无法从对话中提取 → 图谱只有静态数据 → NPC 不知道游戏中发生了什么
- **位置**：`app/game_core/narrative/context_window.py` overflow 检测；`app/world_knowledge_graph.py:write_episode()`
- **InstanceManager evict 同理**：LRU 驱逐时调用 write_episode，但 evict 本身也不频繁（200 实例池）

#### 断点 B：动态知识不持久化

- **现象**：WorldKnowledgeGraph、ContextWindow、NPC 私有记忆（actor_graphs）全部是内存数据，session 重启后从静态 registry 重建
- **影响**：
  - NPC 重启后不记得对话历史
  - 动态积累的知识图谱边全部丢失
  - NPC 私有记忆（remember()）丢失
- **位置**：`app/world_knowledge_graph.py`（无 serialize/restore）；`app/game_core/narrative/context_window.py`（无 serialize/restore）

#### 断点 C：escalate 不落世界状态

- **现象**：NarrativePlannerHook 的 escalate 指令只做 `adjust_escalation(delta)` 改内部计数器，不产生 `adjust_danger` / `set_flag` 等 Command
- **影响**：拖延主线时 escalation_level 上升但世界不变危险，L4-L5 策略完全空转
- **位置**：`app/game_core/orchestration/hooks/narrative_planner.py` L1038-1045（主处理）和 L1558（on_expire 处理）
- **所需基础设施**：`SettlementContext.execute_command()` ✅ 已存在；`adjust_danger` handler ✅ 已注册；`set_flag` handler ✅ 已注册

---

## 3. NarrativePlanner 重新设计

### 3.1 设计决策

| 决策项 | 结论 | 理由 |
|---|---|---|
| Planner 类型 | **删除确定性 planner**（planner.py，807 行），只保留 LLM planner | 确定性 planner 是降级方案，不是设计目标。固定 L0-L5 阶梯缺乏创造性 |
| 运行位置 | 仍在 **P35 Hook** 内 | 基础设施完备，级联效应天然可用，状态一致性有保障 |
| 输出粒度 | **一步一场景**（2-3 个相关 directives） | 太碎无整体感，太重约束灵活性 |
| NPC 投递 | **`direct_npc` 为主**，利用已有 NPC 满世界走动的机制 | 暂不需要 PasserbyService / spawn_quest_npc |
| 剧情记忆 | **story_facts → WorldKnowledgeGraph**（作为世界信息的一部分，NPC 可读）；**strategy_notes → NarrativePlanSlice**（planner 私有笔记） | 一举两得：planner 有记忆 + NPC 自然感知剧情 |
| 触发频率 | 维持 **6 tick 兜底** + slice 变化触发 | Flash 足够便宜，空跑代价低 |

### 3.2 指令集调整

| 指令 | 角色变化 | 说明 |
|---|---|---|
| `create_quest` | 核心，不变 | 创建引导任务 |
| `direct_npc` | **升级为主力** | 取代 spawn_quest_npc 成为主要投递手段 |
| `publish_bulletin` | 辅助，不变 | 公告板信息 |
| `spawn_quest_npc` | **降级** | 代码保留但 LLM prompt 中不提供 |
| `plant_environmental` | **核心，需深化** | 深化到 Tier 1-3（discovery + interactables + content_hints） |
| `escalate` | **核心，需修正** | 走 RulesEngine 产生真实世界状态变更 |
| `adjust_pacing` | 保留，不变 | 自我调节 |
| `retire_quest` | 保留，需补级联清理 | despawn NPC + remove bulletins + remove sub_areas |
| `fill_area` | 保留，不变 | 世界丰富度 |

### 3.3 LLM planner 输出格式

```json
{
  "reasoning": "玩家在酒馆已待 8 格，公会接待员就在旁边，适合通过她推进",
  "scene_description": "公会接待员收到牧场紧急求援报告",
  "directives": [
    {"type": "direct_npc", "params": {"npc_id": "guild_receptionist", "directive": "..."}},
    {"type": "escalate", "params": {"commands": [{"type": "adjust_danger", "params": {...}}]}}
  ],
  "story_facts": [
    {"subject": "western_farm", "relation": "under_attack_by", "object": "goblin_raiders"},
    {"subject": "guild_receptionist", "relation": "received_report", "object": "farm_emergency"}
  ],
  "strategy_notes": "下次如果玩家还不动，让商人也抱怨运输路线不安全",
  "next_trigger_hint": "player_moves_or_3_ticks"
}
```

- **directives**：具体指令，由 Hook 逐条执行
- **story_facts**：世界事实三元组，写入 WorldKnowledgeGraph，NPC 可通过 L6 查询
- **strategy_notes**：planner 私有笔记，存入 NarrativePlanSlice，只有 planner 下次运行时读
- **next_trigger_hint**：planner 对下次运行时机的建议（非强制）

### 3.4 LLM prompt 结构

```
你是叙事编剧。根据玩家当前处境，编排"下一幕"。

## 故事骨架
{milestones — 当前章节的里程碑图 + 各里程碑状态}

## 你之前的笔记
{strategy_notes}

## 世界中已确立的事实
{story_facts — 从 WorldKnowledgeGraph 中 query 出的相关剧情事实}

## 玩家最近做了什么
{behavior_window}

## 当前场景
{location, danger_level, active_quests, flags...}

## 可用 NPC
{area 内 NPC 列表，含 personality / 当前位置 / 关系状态}

## 可用指令
{指令类型说明：direct_npc, create_quest, escalate, plant_environmental, publish_bulletin, adjust_pacing, retire_quest, fill_area}

## 输出格式
{JSON schema}
```

### 3.5 story_facts 持久化方案

story_facts 写入 WorldKnowledgeGraph 后在内存中生效，但图谱不持久化。

**解决方案**：在 `NarrativePlanSlice` 中新增 `story_facts: list[dict]` 字段，持久化到 `narrative_plan.json`。加载 session 时，将 story_facts 注入 WorldKnowledgeGraph 作为动态种子。

```
保存：planner 产出 story_facts → 写入 WorldKnowledgeGraph + 追加到 NarrativePlanSlice.story_facts
加载：NarrativePlanSlice.story_facts → 注入 WorldKnowledgeGraph（与静态种子并列）
```

---

## 4. plant_environmental 深化

### 4.1 层级规划

| 层级 | 功能 | 下游依赖 | 本次是否实施 |
|---|---|---|---|
| 当前 | 创建空壳 sub_area（name + desc + tags） | — | — |
| **Tier 1** | + discovery_mode（auto / check） | DiscoveryHandler ✅ 已实现（D-P3e） | ✅ |
| **Tier 2** | + interactables（可交互物件） | InteractableHandler ✅ 已实现（D-P3e） | ✅ |
| **Tier 3** | + content_hints 注入 Agent 上下文 | context_builder L2/L3 扩展 | ✅ |
| Tier 4 | + hostile_config（敌对遭遇） | 战斗系统端到端可用（P12 §5.2 待修） | ❌ 后续 |
| Tier 5 | + loot_table / items | 物品系统数据补齐（P12 §7 待修） | ❌ 后续 |

### 4.2 Tier 1-3 具体改动

**Tier 1**：plant_environmental 创建子地点时传入 `discovery_mode` 和 `discovery_dc` 参数。当 discovery_mode="check" 时，PassivePerceptionHook（P45）自动检测。

**Tier 2**：plant_environmental 创建子地点时传入 `interactables` 列表。每个 interactable 遵循现有 InteractableTemplate schema（visibility_dc、checks 多路径检定）。

**Tier 3**：context_builder 在构建 L2/L3 时，对动态子区域的 `content_hints` 字段注入 Agent 上下文。GM/NPC/Teammate 能据此描述氛围、提供线索。

---

## 5. 场景变化通知

### 5.1 现状

plant_environmental 和 fill_area 静默创建子地点，玩家不知道世界变了。

### 5.2 方案

双通道通知：

1. **SSE 事件**：创建子地点后发 `"environment_changed"` SSE，前端可弹提示
2. **SceneBus 标签**：写入 ENGINE 标签，GM/Teammate Agent 下次叙述时自然提及

---

## 6. 知识层修复

### 6.1 write_episode 触发条件修复

**问题**：overflow 阈值不可达（200K × 0.9 = 180K tokens）。

**方案**：改为**主动触发**而非被动溢出。每次 NPC 对话结束后，如果本次对话产生了有意义的内容（非闲聊），主动调用 write_episode 提取三元组。

具体：
- 对话结束时，取本次对话的消息（不是整个 ContextWindow）
- 直接传给 write_episode 提取三元组
- 不再依赖 ContextWindow overflow

ContextWindow 的 overflow 机制保留作为兜底，但主要的知识积累改为主动路径。

**三元组提取是独立的 LLM 调用**，不是对话中的 NPC Agent。流程：

```
玩家和 NPC 对话（NPC Agent 用 LLM 生成回复）  ← 第 1 次 LLM 调用
  → 对话结束
  → 判断是否有意义（使用了工具 / 消息轮数 > N / 非纯闲聊）
  → 把本次对话消息发给 write_episode            ← 第 2 次 LLM 调用（独立的，用 RECORD_TRIPLE_TOOL prompt）
  → LLM 提取 (subject, relation, object) 三元组
  → 三元组写入 WorldKnowledgeGraph
```

每次提取开销：Flash ~500 tokens 输入 + ~200 tokens 输出，成本可忽略。

### 6.2 知识持久化

**问题**：WorldKnowledgeGraph 动态边 + ContextWindow 对话历史 + NPC 私有记忆全部不持久化。

**分阶段方案**：

| 阶段 | 内容 | 说明 |
|---|---|---|
| **Phase A** | story_facts 持久化 | 存 NarrativePlanSlice，加载时注入图谱（§3.5 已设计） |
| **Phase B** | 动态三元组持久化 | 给 WorldKnowledgeGraph 加 serialize/restore，只存动态添加的边（非静态种子） |

Phase A 随 NarrativePlanner 重构一起做。Phase B 可后续独立推进。

> **ContextWindow 持久化不再需要**：§6.1 改为主动 write_episode 后，每次有意义对话的知识都会提取为三元组存入图谱。NPC 需要的是"知道发生了什么事实"而非"记得对话原文"，三元组足够覆盖。

### 6.3 中文关键词质量

**问题**：`_extract_scene_keywords()` 纯空格分词，中文无效。

**方案**：暂不单独修复。中文环境下 keyword matching 使用的是 substring 匹配（`keyword in node_label`），对单个汉字或短语仍有一定效果。后续如需改善，可引入 jieba 或 LLM 关键词提取。

---

## 7. 其他关联修复

### 7.1 publish_bulletin 的 notify_resident_npcs

- 当前未实现
- 修复：发布公告后自动为驻留 NPC 生成隐式 `direct_npc`
- ~20 行

### 7.2 retire_quest 级联清理

- 当前只改任务状态，不清理关联资源
- 修复：despawn 关联临时 NPC + 移除公告 + 移除子区域
- ~60 行

ne't---

## 8. 实施计划

按依赖关系排序，分为 4 个 Phase。

### Phase 1：断点修复（最小闭合）

| # | 任务 | 范围 | 预估 |
|---|---|---|---|
| 1.1 | escalate 走 RulesEngine | narrative_planner.py 两处 + 测试 | ~30 行 |
| 1.2 | write_episode 改为主动触发 | agent_orchestration.py 对话结束时主动调用 | ~40 行 |
| 1.3 | story_facts 持久化 | NarrativePlanSlice 新增字段 + 加载时注入图谱 | ~50 行 |
| 1.4 | 场景变化 SSE + SceneBus 通知 | narrative_planner.py 指令执行后发通知 | ~30 行 |

**Phase 1 完成后效果**：
- escalate 真正改变世界状态（danger 上升、flag 设置）
- NPC 对话后知识自动积累到图谱
- story_facts 跨 session 持久化
- 环境变化有通知

### Phase 2：NarrativePlanner LLM 化

| # | 任务 | 范围 | 预估 |
|---|---|---|---|
| 2.1 | 删除确定性 planner（planner.py） | 删除文件 + 调整 import + 更新 deps.py | ~删 807 行 |
| 2.2 | 重写 AgenticNarrativePlanner | 新 prompt 结构（§3.4）+ story_facts 输出解析 + 注入图谱 | ~200 行 |
| 2.3 | NarrativePlannerHook 适配 | story_facts 写入逻辑 + 无 LLM 时降级策略（noop 而非 fallback 到确定性） | ~60 行 |
| 2.4 | 更新测试 | 删除确定性 planner 测试 + 新增 LLM planner mock 测试 | ~150 行 |

**Phase 2 完成后效果**：
- NarrativePlanner 输出"场景"而非机械指令
- 剧情事实自动写入世界知识，NPC 通过 L6 感知
- planner 有跨 session 的策略记忆

### Phase 3：指令深化

| # | 任务 | 范围 | 预估 |
|---|---|---|---|
| 3.1 | plant_environmental Tier 1-3 | 传入 discovery_mode / interactables / content_hints | ~80 行 |
| 3.2 | retire_quest 级联清理 | despawn NPC + remove bulletins + remove sub_areas | ~60 行 |
| 3.3 | publish_bulletin notify_resident_npcs | 自动生成隐式 direct_npc | ~20 行 |

**Phase 3 完成后效果**：
- 叙事可驱动发现/探索/检定
- 任务下架时世界状态干净回收
- 公告板 NPC 知道新告示

### Phase 4：知识层深化（可独立推进）

| # | 任务 | 范围 | 预估 |
|---|---|---|---|
| 4.1 | WorldKnowledgeGraph 动态边持久化 | serialize/restore 动态三元组 | ~100 行 |
| 4.2 | 中文关键词改善（可选） | jieba 分词或 LLM 关键词提取 | ~60 行 |

> ~~ContextWindow 持久化~~：已取消。主动 write_episode（§6.1）覆盖了对话知识持久化需求，NPC 不需要记得对话原文，只需知道事实。

**Phase 4 完成后效果**：
- 所有动态知识（对话三元组 + story_facts）跨 session 持久化
- 中文检索质量提升

---

## 9. 文件影响矩阵

| 文件 | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| `app/game_core/orchestration/hooks/narrative_planner.py` | 1.1, 1.4 | 2.3 | 3.1, 3.2, 3.3 | — |
| `app/game_core/state/slices/narrative_plan.py` | 1.3 | — | — | — |
| `app/game_core/planning/planner.py` | — | 2.1 删除 | — | — |
| `app/game_core/planning/models.py` | — | 审查 | — | — |
| `app/narrators.py` | — | 2.2 | — | — |
| `app/agent_orchestration.py` | 1.2 | — | — | — |
| `app/world_knowledge_graph.py` | 1.3 注入 | 2.2 写入 | — | 4.1 |
| `app/game_core/narrative/context_window.py` | — | — | — | — |
| `app/game_core/narrative/context_builder.py` | — | — | 3.1 L2/L3 | — |
| `app/deps.py` | — | 2.1 调整 | — | — |
| `app/game_core/runtime.py` | — | 2.1 调整 | — | — |
| `app/game_core/bootstrap.py` | — | 2.1 调整 | — | — |
| `tests/test_narrative_planner_hook.py` | 1.1 | 2.4 | 3.x | — |
| `tests/test_narrative_executor.py` | — | 2.4 | — | — |

---

## 10. 验收标准

### Phase 1 验收

- [ ] escalate 指令产生 `adjust_danger` + `set_flag` Command，被 RulesEngine 执行
- [ ] NPC 对话结束后自动调用 write_episode，新三元组出现在图谱中
- [ ] story_facts 存入 narrative_plan.json，session 重启后注入图谱
- [ ] plant_environmental / fill_area 执行后发 SSE + SceneBus 标签

### Phase 2 验收

- [ ] 确定性 planner 完全删除，无残留引用
- [ ] LLM planner 输出包含 story_facts，正确写入图谱
- [ ] NPC 通过 L6 查询到 planner 写入的 story_facts
- [ ] 无 LLM 时 planner noop（不 crash、不 fallback 到确定性逻辑）
- [ ] 测试基线通过（新测试替代旧测试）

### Phase 3 验收

- [ ] plant_environmental 支持 discovery_mode=check，PassivePerceptionHook 可检测
- [ ] plant_environmental 支持 interactables，InteractableHandler 可处理
- [ ] content_hints 出现在 Agent 上下文（L2/L3）
- [ ] retire_quest 清理关联 NPC / 公告 / 子区域
- [ ] publish_bulletin 自动通知驻留 NPC

### Phase 4 验收

- [ ] WorldKnowledgeGraph 动态边跨 session 持久化
- [ ] NPC 重启后通过图谱三元组知道之前对话中的关键事实

---

## 11. 风险与注意事项

1. **Phase 2 删除 planner.py（807 行）需谨慎**：先确保 LLM planner 稳定后再删，过渡期可并存
2. **LLM 不可用时的降级**：Phase 2 后无 LLM 时 planner 完全 noop（不产生任何指令），这是可接受的——没有 LLM 就没有叙事规划
3. **story_facts 累积膨胀**：需要 GC 策略（按章节归档旧 facts？按数量限制？）
4. **测试基线变化**：Phase 2 会删除大量确定性 planner 测试，需新增 LLM mock 测试保持覆盖率
5. **Phase 1 和 Phase 2 可以并行吗**：不建议。Phase 1 的修复在现有架构上做，Phase 2 是架构变更。先 Phase 1 稳定后再做 Phase 2
