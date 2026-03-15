# Planner 系统优化计划

记录日期：2026-03-15（修正版，基于 Codex 验证反馈）
状态：待确认

---

## 一、问题诊断

### 1.1 性能问题

每次结算触发**多轮 replay**，每轮中每个 semantic event 都会分发给多个子系统，每个子系统独立调 LLM。

```
实际热点：replay_rounds × events_per_round × accepting_subsystems_per_event

例：玩家导航到新区域
  → 产出 3 个事件（area_entered, sub_location_entered, scene_changed）
  → 每个事件被 NpcDirector + WorldBuilder 接受（2 个 LLM 调用）
  → Round 1: 3 × 2 = 6 次 LLM 调用（串行）
  → 如果产出新指令 → Round 2: 可能又 2-4 次
  → 最后 Blackboard.plan() 又 1 次
  → 总计可能 10+ 次 LLM 调用，全部 thinking_level="high"
```

### 1.2 功能故障

| 问题 | 证据 | 根因 |
|------|------|------|
| NpcDirector LLM 输出被拒 | planner_directive_rejected 日志 + parse_failed/invalid_contract | LLM 输出格式不符合 directive_contracts 验证 |
| 自动升级失控（escalation=68） | narrative_plan.json | NarrativeWeaver 在每轮 replay 中都调 execute_command(prune) + 检查升级，多轮 replay 导致重复升级 |
| Blackboard 输出 0 directives | narrative_plan.json trace: blackboard=noop | Blackboard prompt 明确要求 directives 为空（设计如此，不是 bug） |

### 1.3 架构现状（基于代码实读确认）

```
当前分工：

Blackboard（PLANNER_BLACKBOARD_PROMPT）= 纯协调者
  - prompt 明确："directives 必须是空数组"
  - 产出：story_facts + strategy_notes + outline_updates + next_scheduled_tick
  - 没有 design_skill_port
  - 只有 planner_tools（读写设计模板），但 prompt 禁止产 directives

子系统 Agent = 实际决策者（各自独立 LLM + design_skill + 工具注册表）
  - QuestManager：可产 create_quest/publish_bulletin/retire_quest/update_quest
  - NpcDirector：可产 direct_npc/spawn_quest_npc
  - WorldBuilder：可产 plant_environmental/fill_area/fill_location/fill_room/plant_encounter
  - NarrativeWeaver：可产 retire_quest/adjust_pacing/escalate
  - 每个子系统都有"强制要求：先调 read_design_skill 再生成 directive"
    → 实际是多轮 agent 调用（先 tool call 查模板 → 再输出 JSON）

执行流：
  _run_replay 多轮循环（子系统各自 LLM 决策 → apply directives → 新事件 → 下一轮）
  → Blackboard.plan（只产 story_facts + strategy_notes，不产 directives）
  → 结束
```

### 1.4 延迟放大因素

每个子系统 LLM 调用实际是多轮 agent：
1. 系统提示词（~1000 token）
2. LLM 第 1 轮：调 `read_design_skill` tool（查模板）
3. Tool 返回模板内容
4. LLM 第 2 轮：基于模板生成 directives JSON
→ 每个子系统 ≈ 2 次 LLM roundtrip × thinking_level="high"
→ 单个子系统一次 evaluate ≈ 15-25 秒

---

## 二、关键约束（不可打破）

| 约束 | 原因 | 文件 |
|------|------|------|
| Opening bootstrap 依赖 quest_manager_agent | `OpeningBootstrapQuestAgent` 确定性种子任务创建 | runtime.py:754, opening_bootstrap.py:129 |
| PacingController 是 escalate/adjust_pacing 唯一 handler | NarrativeWeaver 只 handle schedule_event | pacing_controller.py:24 |
| NarrativeWeaver.evaluate() 写状态（GC/despawn） | 必须在其他子系统之前执行 | narrative_weaver.py:83-96 |
| 子系统 prompt 包含 strategy_notes | 软协调机制，不能随意切断 | narrators.py:1155 |
| Blackboard 当前不产出 directives | prompt 明确 `directives: []` | narrators.py:1541 |

---

## 三、分阶段优化方案

### Phase 1：快速止血（~10 行，立即生效）

**目标**：不改架构，降低延迟和修复失控。

#### 1a. thinking_level 降级

`app/deps.py` 5 处 `thinking_level="high"` → `"low"`

理由：子系统决策是创意性叙事决策，不需要 extended thinking。

#### 1b. replay 轮数限制

`app/game_core/orchestration/hooks/narrative_planner.py`:
```python
_MAX_REPLAY_ROUNDS = 5  →  _MAX_REPLAY_ROUNDS = 2
```

理由：实际级联很少超过 2 轮。第 3-5 轮基本空转但每轮都可能触发 LLM。

#### 1c. escalation 上限

`app/game_core/planning/narrative_weaver.py` 的 `_check_auto_escalation()`:
```python
# 新增：
if level >= 10:
    return None  # 到达上限，不再升级
```

理由：escalation 到 68 说明安全网完全失控。上限 10 已经足够表达"叙事严重停滞"。

**预期效果**：结算从 ~90s 降到 ~20-30s，升级不再无限增长。

---

### Phase 2：修复子系统功能故障（~100 行）

**目标**：让子系统 LLM 产出的指令能实际生效。

#### 2a. 诊断 NpcDirector LLM 输出格式问题

需要进一步调查：
- 查看 NPC_DIRECTOR_AGENT_PROMPT（narrators.py）的输出格式要求
- 对比 directive_contracts 的验证规则
- 找出 LLM 输出哪个字段不符合验证
- 可能是嵌套结构问题（payload.npc_id vs payload.directive.npc_id）

修复方向：
- 调整 prompt 使输出更贴合验证格式
- 或放宽 directive_contracts 的验证（如果过于严格）
- 或在 normalize_planner_directive 中增加格式修正逻辑

#### 2b. 修复 auto-escalation 在 replay 中重复触发

问题：NarrativeWeaver.evaluate() 在每轮 replay 中都调 execute_command(prune) + 检查升级。多轮 replay 导致同一个 settlement 中多次升级。

修复：
```python
# narrative_weaver.py evaluate() 中：
# 加一个 "本 settlement 已经升级过" 的 guard
if event.payload.get("_escalation_already_checked"):
    return SubSystemResult(directives=directives)
```

或者更简单：将自动升级检查从 evaluate() 搬到 hook.execute() 中只执行一次。

---

### Phase 3：性能优化（保持架构，~100 行）

**目标**：在不改架构的前提下最大化并行和减少重复计算。

#### 3a. 子系统 evaluate 并行化

`app/game_core/planning/subsystem.py` 的 `dispatch()` 方法：

```python
# 当前（串行）：
for subsystem in self._subsystems:
    if subsystem.accepts_event(event):
        result = await self._run_and_drain(subsystem, event, context)

# 改后（安全并行）：
# NarrativeWeaver 必须先执行（写状态），其余可并行
weaver_results = []
parallel_tasks = []
for subsystem in self._subsystems:
    if not subsystem.accepts_event(event):
        continue
    if subsystem.name == "narrative_weaver":
        weaver_results = await self._run_and_drain(subsystem, event, context)
    else:
        parallel_tasks.append(self._run_and_drain(subsystem, event, context))

parallel_results = await asyncio.gather(*parallel_tasks) if parallel_tasks else []
results = weaver_results + [r for batch in parallel_results for r in batch]
```

理由：QuestManager/NpcDirector/WorldBuilder/PacingController 的 evaluate() 不写状态，安全并行。NarrativeWeaver 写状态（GC/despawn），必须先跑。

#### 3b. 上下文缓存（同轮复用）

`app/game_core/orchestration/hooks/narrative_planner.py` 的 _run_replay():

```python
# 当前：每个事件重建上下文
for semantic_event in pending_events:
    event_context = self._build_planner_context(context, ...)  # 每次重建

# 改后：同一轮内复用
round_context = self._build_planner_context(context, ...)  # 每轮只建一次
for semantic_event in pending_events:
    event_context = dict(round_context)  # 浅拷贝
    event_context["planner_events"] = ...
```

理由：同一轮内状态不变（指令在批量 apply 后才生效），重复构建完全浪费。

#### 3c. PacingController 合并进 NarrativeWeaver

`app/game_core/planning/narrative_weaver.py`:
- `_HANDLES` 加入 `"escalate"`, `"adjust_pacing"`
- 新增 `_apply_escalate()`, `_apply_adjust_pacing()`（从 PacingController 搬过来）

`app/game_core/planning/pacing_controller.py`:
- 保留文件但标记 deprecated，或删除并更新 defaults/bootstrap

`app/game_core/orchestration/defaults.py` + `bootstrap.py`:
- 移除 PacingController 注册

理由：PacingController evaluate() 为空，合并后减少一个子系统的分发开销。注意 escalate/adjust_pacing 的 handler 方法必须完整搬过去。

---

### Phase 4：架构升级（可选，视 Phase 1-3 效果决定）

如果 Phase 1-3 后仍不满意，有两个方向：

#### 4a. 升级 Blackboard 为唯一决策者（大改）

需要的改造（不是改个 prompt 就行）：
- 给 Blackboard 注入 design_skill_port（当前只有子系统 agent 有）
- 给 Blackboard 注册所有 directive 种类对应的 tool declarations
- 修改 PLANNER_BLACKBOARD_PROMPT 允许产出 directives（当前明确禁止）
- 合并 4 个子系统 prompt 的专业知识到 Blackboard prompt
- 子系统 evaluate 逐步退化为空
- **约束**：不能影响 opening bootstrap（保留 OpeningBootstrapQuestAgent 确定性路径）
- **约束**：PacingController handler（escalate/adjust_pacing）必须有路由目标
- **预估**：~300 行改动 + prompt 大改

#### 4b. 子系统合并为 2 个（中改）

- QuestManager + NpcDirector → 一个 "QuestNpcAgent"（任务+NPC 联动决策）
- WorldBuilder 独立（内容生成是独立领域）
- NarrativeWeaver 保持确定性（不需要 LLM）
- 3 个 LLM（包含 Blackboard）代替 5 个
- 预估：~150 行改动 + 2 个 prompt 合并

#### 4c. 去掉 design_skill 强制查询（小改，高收益）

当前每个子系统 LLM 被强制要求"先 read_design_skill 再生成 directive"，导致每次 evaluate 是 2 轮 agent roundtrip。如果改为"建议但不强制"，可以减少一半的 LLM 交互次数。
- 修改：4 个子系统 prompt 中删除"强制要求"
- 风险：directive 质量可能下降（不参考模板）
- 预估：~10 行 prompt 调整

---

## 四、影响面

### Phase 1 影响

| 文件 | 改动 |
|------|------|
| deps.py | 5 处 thinking_level 值 |
| narrative_planner.py | 1 处 MAX_REPLAY_ROUNDS 值 |
| narrative_weaver.py | _check_auto_escalation 加上限 |

测试影响：可能有几个测试检查 replay round 数或 escalation 值的断言需要更新。

### Phase 2 影响

取决于具体诊断结果。NpcDirector 格式修复可能涉及 narrators.py prompt 或 directive_contracts.py 验证逻辑。

### Phase 3 影响

| 文件 | 改动 |
|------|------|
| subsystem.py | dispatch() 并行化 |
| narrative_planner.py | _run_replay 上下文缓存 |
| narrative_weaver.py | 吸收 PacingController handler |
| pacing_controller.py | 删除或 deprecated |
| defaults.py + bootstrap.py | 移除 PacingController 注册 |

---

## 五、预期效果

| 指标 | 当前 | Phase 1 后 | Phase 3 后 | Phase 4c 后 |
|------|------|-----------|-----------|------------|
| 每子系统 LLM roundtrips | 2（查模板+生成） | 2 | 2 | 1（去掉强制查模板） |
| 单次 roundtrip 延迟 | 10-15s (high) | 3-5s (low) | 3-5s (low) | 3-5s |
| replay 轮数上限 | 5 | 2 | 2 | 2 |
| 子系统并行度 | 串行 | 串行 | 4 并行 | 4 并行 |
| 上下文重建 | 每事件 | 每事件 | 每轮一次 | 每轮一次 |
| 典型结算延迟 | ~60-90s | ~20-30s | ~8-15s | ~5-10s |
| escalation_level | 68（失控） | ≤10 | ≤10 | ≤10 |
| NpcDirector 指令 | parse_failed | 修复后正常 | 正常 | 正常 |
