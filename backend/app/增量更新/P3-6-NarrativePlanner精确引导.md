# P3-6: NarrativePlanner 精确引导

> 状态：设计完成，可施工
> 创建：2026-03-05
> 关联：P3 §七.P6（NarrativePlanner 精确引导）
> 前置调查：修正了 P3 的错误结论

---

## 一、P3 审计修正

P3 将 6 个 MilestoneTemplate 字段标记为"未消费"。深入调查发现 2 个已消费：

| 字段 | P3 说 | 实际 |
|------|-------|------|
| `success_conditions` | 未消费 | **已消费** — NarrativePlannerHook._create_milestone_condition_events() |
| `failure_conditions` | 未消费 | **已消费** — 同上，创建 EventSlice 条件事件 |
| `involved_npcs` | 未消费 | **仅验证** — world.py 启动时校验存在性，运行时不消费 |
| `involved_locations` | 未消费 | **仅验证** — 同上 |
| `key_elements` | 未消费 | 确实未消费 |
| `failure_fallback` | 未消费 | 确实未消费 |
| `sequence` | 未消费 | 确实未消费（prerequisites 图提供等效排序） |

**实际未消费：3 个核心字段 + 1 个低优先级字段。**

---

## 二、当前 NarrativePlanner 架构

### 2.1 已实现的能力

NarrativePlanner 已是一个**完整的 5 级升级系统**：

```
ticks_since_milestone_progress:
  4-6   → L1 hint（公告板 + 升级紧迫感）
  7-9   → L2 recommend（指派 NPC 引导）
  10-12 → L3 urgent（创建动态任务 + 指派 NPC）
  13-15 → L4 crisis（调整节奏 + 环境线索）
  16+   → L5 final_warning（强制升级）
```

9 种指令类型：create_quest / direct_npc / publish_bulletin / escalate / adjust_pacing / retire_quest / spawn_quest_npc / plant_environmental / fill_area。

### 2.2 "精确引导"的缺失

当前 planner 的决策逻辑**不知道里程碑的叙事细节**：

| 决策维度 | 设计预期 | 当前实现 |
|---------|---------|---------|
| 投递方式选择 | 根据 `involved_npcs` 优先使用相关 NPC | 随机选择或固定模式 |
| 任务内容生成 | 包含 `key_elements` 叙事要素 | 无叙事约束 |
| 失败处理 | 触发 `failure_fallback` 备选路线 | 无失败分支 |
| LLM prompt | 传递 key_elements/involved_npcs 给 LLM 参考 | prompt 中无这些信息 |

---

## 三、断裂点

### 3.1 key_elements 未注入 planner context

`NarrativePlannerHook._build_planner_context()` 构建的上下文不包含当前目标里程碑的 key_elements。planner 无法生成包含特定叙事要素的任务。

### 3.2 involved_npcs 未驱动投递选择

确定性 planner 的 `_l2_recommend()` 和 `_l3_urgent()` 生成 `direct_npc` 指令时，npc_id 的选择逻辑不参考里程碑的 involved_npcs。

### 3.3 AgenticNarrativePlanner prompt 缺少素材

`narrators.py::_format_planner_context()` 的 prompt 不包含 key_elements、involved_npcs、involved_locations。LLM 缺少做精确决策的素材。

### 3.4 failure_fallback 无消费链路

里程碑失败（failure_conditions 触发后）没有读取 failure_fallback 来调整叙事方向的逻辑。

---

## 四、施工方案

### Phase 1: 上下文注入

**目标**：让 planner 能"看到"当前里程碑的叙事细节。

在 `NarrativePlannerHook._build_planner_context()` 中，查询当前目标里程碑的 MilestoneTemplate，注入：

```python
target_milestone_id = narrative_plan.current_target_milestone
if target_milestone_id and world.has_registry("quests"):
    template = world.quests.get_milestone(target_milestone_id)
    if template:
        context["target_milestone_detail"] = {
            "key_elements": list(template.key_elements),
            "involved_npcs": list(template.involved_npcs),
            "involved_locations": list(template.involved_locations),
            "narrative_context": template.narrative_context,
            "failure_fallback": template.failure_fallback,
        }
```

**同步更新 `narrators.py::_format_planner_context()`**：

在 "故事蓝图" 段落中追加：
```
## 当前目标里程碑
关键叙事要素: {key_elements}
相关NPC: {involved_npcs}
相关地点: {involved_locations}
叙事背景: {narrative_context}
```

**变更**：narrative_planner.py（~10 行）+ narrators.py（~10 行）。

### Phase 2: involved_npcs 驱动投递

**目标**：direct_npc 指令优先选择里程碑相关 NPC。

在确定性 planner 的 `_l2_recommend()` 和 `_l3_urgent()` 中：

```python
# 当前：硬编码或随机选 NPC
# 改为：优先从 involved_npcs 中选择
target_detail = context.get("target_milestone_detail", {})
involved = target_detail.get("involved_npcs", [])
if involved:
    npc_id = involved[0]  # 简单策略：取第一个
else:
    npc_id = self._pick_fallback_npc(context)  # 现有逻辑
```

**变更**：planner.py 的 L2/L3 方法（~10 行改动）。

### Phase 3: key_elements 约束任务生成

**目标**：create_quest 指令的 payload 包含 key_elements，供下游消费。

在确定性 planner 的 `_l3_urgent()` 生成 create_quest 时：

```python
target_detail = context.get("target_milestone_detail", {})
quest_payload = {
    "quest_id": ...,
    "key_elements": target_detail.get("key_elements", []),
    "involved_locations": target_detail.get("involved_locations", []),
    ...
}
```

NarrativePlannerHook._apply_directive() 处理 create_quest 时，将 key_elements 写入动态任务的描述/条件中。

**变更**：planner.py（~5 行）+ narrative_planner.py _apply_directive（~5 行）。

### Phase 4: failure_fallback 消费（可选）

**目标**：里程碑失败时，planner 参考 failure_fallback 调整方向。

这需要一个"里程碑失败"事件的触发链路。当前 failure_conditions 已通过 EventSlice 事件检测，但事件触发后的"叙事路径切换"逻辑不存在。

方案：在 NarrativePlannerHook 中检测到里程碑 failure 事件后，读取 failure_fallback，将其作为 strategy_notes 注入 planner context，影响后续决策。

**变更**：narrative_planner.py（~15 行）。
**复杂度**：中，需要设计失败事件的检测逻辑。

---

## 五、文件变更清单

| Phase | 文件 | 变更 |
|-------|------|------|
| 1 | orchestration/hooks/narrative_planner.py | _build_planner_context 注入 milestone detail |
| 1 | narrators.py | _format_planner_context prompt 追加 milestone 信息 |
| 2 | planning/planner.py | L2/L3 优先选择 involved_npcs |
| 3 | planning/planner.py | L3 create_quest payload 含 key_elements |
| 3 | orchestration/hooks/narrative_planner.py | _apply_directive 传递 key_elements |
| 4 | orchestration/hooks/narrative_planner.py | 失败检测 + failure_fallback 消费 |
| all | tests/ | 针对性测试 |

---

## 六、复杂度评估

| Phase | 复杂度 | 说明 |
|-------|--------|------|
| 1 | 小 | 纯数据注入，不改逻辑 |
| 2 | 小 | 条件分支 + fallback |
| 3 | 小 | payload 字段追加 |
| 4 | 中 | 需要失败事件检测链路 |

**总体：小-中。Phase 1-3 可独立施工，Phase 4 可推迟。**

---

## 七、测试策略

- `test_planner_context_includes_milestone_detail`: 验证 _build_planner_context 包含 key_elements/involved_npcs
- `test_l2_prefers_involved_npc`: L2 指令优先选择里程碑相关 NPC
- `test_create_quest_includes_key_elements`: create_quest payload 包含 key_elements
- `test_llm_prompt_contains_milestone_info`: AgenticNarrativePlanner prompt 包含叙事素材
- `test_failure_fallback_injected_on_milestone_fail`: 失败时 failure_fallback 注入 strategy_notes
