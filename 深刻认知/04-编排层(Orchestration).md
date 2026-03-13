# L4 编排层 (Orchestration Layer)

> 路径：`backend/app/game_core/orchestration/`
> 职责：Tick 生命周期管理 + 27个 Settlement Hook 优先级链 + 事件引擎。

---

## 一、整体结构

```
orchestration/
├── tick_coordinator.py     — 主循环（Tick 生命周期入口）
├── pipeline.py             — 三阶段管线（上下文装配→规则引擎→Agent）
├── context_assembler.py    — L0-L7 上下文层装配
├── settlement.py           — SettlementContext（Hook 执行上下文）
├── scene_bus.py            — SceneBus（叙事缓冲 + 语义标签）
├── event_engine.py         — 事件条件评估引擎
├── models.py               — PipelineResult / SSEEvent / HookResult
├── defaults.py             — Hook 注册默认列表
├── npc_interaction.py      — NPC 对话协调器（6步管线）
├── private_chat.py         — 私聊协调器（4步管线）
├── companion_manager.py    — 队伍同伴管理
├── presence.py             — NPC 在场追踪
├── combat_sse.py           — 战斗 SSE 事件提取
└── hooks/
    ├── base.py                     — SettlementHook / NoOpSettlementHook 基类
    ├── status_effect.py            P20
    ├── passive_perception.py       P45
    ├── event_condition.py          P50
    ├── milestone_completion.py     P54
    ├── milestone_unlock.py         P55（推断）
    ├── quest_objective_tracking.py P56
    ├── npc_schedule.py             P60
    ├── shared_experience.py        P62
    ├── campfire.py                 P63
    ├── relationship.py             P65
    ├── time_advance.py             P70
    ├── private_chat_trigger.py     P75
    ├── gm_narration.py             P80
    ├── narrative_planner.py        P35（规划层驱动）
    ├── encounter.py                P40
    ├── ai_osiris.py                P30
    ├── directive_trigger.py        P80
    ├── quest_expiry.py
    ├── task_monitor.py
    └── xp_advancement.py
```

---

## 二、Tick 生命周期（TickCoordinator）

```
TickCoordinator.process(input_payload, event_sink, after_engine)
    │
    ▼
1. 创建 SharedContext（world, state, rules_engine, scene_bus, companion_manager）
    │
    ▼
2. PipelineOrchestrator.process()      ← 三阶段管线
    │
    ▼
3. 记录 action_log（action_type, actor, time_cost）
    │
    ▼
4. 发射语义标签到 SceneBus（ENGINE source，见下表）
    │
    ▼
5. 分发 Companion 运行时事件
    │
    ▼
6. 同步队伍位置 CompanionManager.sync_to_player()
    │
    ▼
7. 提取即时战斗 SSE 事件
    │
    ▼
8. state.time.accumulated += time_cost
    │
    ▼
9. 结算循环：while accumulated >= 1.0:
       _tick_settlement(event_sink)    ← 执行全部 Hook，优先级升序
       consume_tick(1.0)
       emit SSE events
```

### 语义标签表（ENGINE 写入 SceneBus）

| 命令类型 | 标签 |
|----------|------|
| `rest_long` | `["REST", "LONG_REST"]` |
| `rest_short` | `["REST", "SHORT_REST"]` |
| `dialogue_turn` | `["DIALOGUE", "NPC_INTERACTION"]` |
| `private_chat_turn` | `["DIALOGUE", "PRIVATE_CHAT"]` |
| `start_combat` | `["COMBAT"]` |
| `end_combat` | `["COMBAT_END"]` |
| `navigate` | `["NAVIGATION"]` |
| `advance_quest` | `["QUEST_PROGRESS"]` |

---

## 三、三阶段管线（PipelineOrchestrator）

### Stage A — 上下文装配（ContextAssembler）

| 层 | 内容 |
|----|------|
| L0 | 世界常量（lore, factions）|
| L1 | 章节状态（quests, milestones, escalation）|
| L2 | 区域环境（地图模板, 动态子区域）|
| L3 | 位置详情（子地点模板, 探索状态）|
| L4 | 动态状态（player, time, relations, flags, party）|
| L5 | 场景总线（entries + state_changes，按角色过滤）|
| L6 | 记忆召回（当前为 stub）|
| L7 | 引擎结果（executed, rolls, time_cost — 阶段 B 后覆写）|

### Stage B — 规则引擎执行

```
input → Command → RulesEngine.execute() → ExecuteResult
     → state.apply(delta)
     → run_inline_event_check()     ← 轻量内联事件检测
     → 填充 L7（engine metadata）
```

### Stage C — Agent 执行（可选，需安装 AgentRoundRunner）

```
AgentRoundRunner.run()
  → 收集即时 SSE events + 延迟 dialogue_options tail events
  → run_inline_event_check()（阶段 C 后再次）
```

### PipelineResult

```python
@dataclass
class PipelineResult:
    executed: bool
    delta: StateDelta | None
    action_type: str
    time_cost: float
    sse_events: list[SSEEvent]
    metadata: dict
    commands: list[Command]      # 所有被执行的命令（含 Hook 内的）
    narrative_hints: list[str]
    rolls: list[DiceRoll]
```

---

## 四、SettlementContext

```python
@dataclass(frozen=True, slots=True)
class SettlementContext:
    change_log: list[StateChange]       # 上次结算以来的所有状态变更
    state: StateContainer               # 当前游戏状态
    world: WorldInstance                # 内容注册表
    scene_bus: SceneBus                 # 叙事缓冲
    _rules_engine: RulesEngine          # 内部用，repr=False
    _apply_delta: Callable              # 内部用，repr=False
    action_log: list[dict]              # 本 tick 行动窗口（time_cost 合计 ≤ 1.0）
    rest_phase: RestPhaseInfo | None    # 休息阶段信息（含 is_final_rest_slot）
    companion_manager: ... | None
    knowledge_graph: Any | None         # WorldKnowledgeGraph（可选注入）

    def execute_command(cmd: Command) → ExecuteResult
        # Hook 内唯一合法的状态变更路径
        # = rules_engine.execute() + apply_delta()
```

---

## 五、全部 Settlement Hook（优先级排序）

| 优先级 | Hook | 文件 | 触发条件 | 主要行为 | SSE 事件 |
|--------|------|------|----------|---------|---------|
| **P20** | StatusEffectHook | status_effect.py | 每次结算 | tick_effects + tick_combat_effects | `status_effects_ticked`, `combat_effects_ticked` |
| **P30** | AIOsirisHook | ai_osiris.py | 状态变更 | LLM 因果判断（AIOsirisEvaluator） | `ai_osiris_result` |
| **P35** | NarrativePlannerHook | narrative_planner.py | 里程碑/时间 | LLM 叙事规划（AgenticNarrativePlanner） | `narrative_plan_updated`, `milestone_failed` |
| **P40** | EncounterHook | encounter.py | 区域移动 | 随机遭遇检定 | `encounter_triggered` |
| **P45** | PassivePerceptionHook | passive_perception.py | 位置变化 | 被动感知 DC 检测发现/陷阱/可交互物 | `discovery_reveal`, `hidden_object_revealed`, `trap_detected` |
| **P50** | EventConditionHook | event_condition.py | 每次结算 | 评估事件触发条件，执行 on_trigger 命令 | `event_state_changed`, `quest_completed` |
| **P54** | MilestoneCompletionHook | milestone_completion.py | 每次结算 | 检测 ACTIVE 里程碑的成功条件 | `milestone_completed` |
| **P55** | MilestoneUnlockHook | milestone_unlock.py | 里程碑完成后 | 解锁依赖的下一个里程碑 | — |
| **P56** | QuestObjectiveTrackingHook | quest_objective_tracking.py | 每次结算 | 追踪动态任务目标完成情况 | `quest_objective_updated`, `quest_auto_completed` |
| **P60** | NpcScheduleHook | npc_schedule.py | 时段变化 | 按日程/规则移动 NPC | `npc_moved` |
| **P62** | SharedExperienceHook | shared_experience.py | 每次结算 | 检测战斗/任务/探索经历并记录 | — |
| **P63** | CampfireHook | campfire.py | 长休最终时段 | 篝火对话 + 好感+5 | `campfire_dialogue` |
| **P65** | RelationshipHook | relationship.py | 关系/队伍变化 | 关系阶段跃迁 + 自动驱逐敌对队友 | `relationship_stage_changed`, `companion_dismissed` |
| **P70** | TimeAdvanceHook | time_advance.py | accumulated>=1.0 | 推进时间，刷新日常商店 | `time_advanced` |
| **P75** | PrivateChatTriggerHook | private_chat_trigger.py | 休息时段 | 评估私聊触发（romance/trust/intimate）| `npc_wants_to_chat` |
| **P80** | GmNarrationHook | gm_narration.py | 状态变化 | LLM/模板生成 GM 叙事文本 | `gm_narration`, `gm_narration_added` |
| **P80** | DirectiveTriggerHook | directive_trigger.py | 每次结算 | 执行 NarrativePlanSlice 中待触发指令 | — |
| — | XpAdvancementHook | xp_advancement.py | 战斗结算 | 计算 XP 奖励 | — |
| — | QuestExpiryHook | quest_expiry.py | 每次结算 | 过期任务标记 | `quest_expired` |
| — | TaskMonitorHook | task_monitor.py | 每次结算 | 监控异步任务完成状态 | — |

---

## 六、各 Hook 关键逻辑

### RelationshipHook（P65）— 关系跃迁

```
正向跃迁：
  stranger     → acquaintance  (approval > 10)
  acquaintance → friend        (approval > 30, trust > 20)
  friend       → close_friend  (trust > 60, shared_exp >= 3, critical_moments >= 1)
  close_friend → intimate      (trust > 80, romance > 60)

负向入口（_NEGATIVE_ENTRY_THRESHOLD）：
  acquaintance → cold (approval < -20)
  friend       → cold (approval < -30)
  close_friend → cold (approval < -40)
  intimate     → cold (approval < -50)

负向进展（_NEGATIVE_PROGRESSION）：
  cold    → hostile (approval < -50 AND trust < -30)
  hostile → enemy   (trust < -60)

自动驱逐：跃迁到 hostile/enemy 且在队伍中 → force_leave_companion
```

### CampfireHook（P63）— 篝火叙事

```
触发条件：rest_phase.is_final_rest_slot == True
  AND (有重大经历 OR 30% 概率)

对每个可对话队伍成员（stage ∉ [stranger, cold, hostile], approval >= 0）：
  1. 选最佳共同记忆（评分：今日重大+100, 今日任意+50, critical+30, 重大类型+10）
  2. 按记忆类型填充中文模板对话
  3. modify_disposition(approval, +5)
  4. emit campfire_dialogue SSE
```

### PrivateChatTriggerHook（P75）— 私聊触发

```
对当前区域每个有倾向值的 NPC：
  BasicPrivateChatTriggerEvaluator.should_initiate():
    romance >= 60 → "romance" reason (40% 概率)
    trust   >= 50 → "trust"   reason (30% 概率)
    stage == "intimate" → "intimate" reason (60% 概率)

  冷静期检查：flag "private_chat_cooldown_{npc_id}"
    current_tick >= flag_value 才触发
    触发后设置 cooldown_until = current_tick + 6

emit: npc_wants_to_chat {npc_id, npc_name, reason, colocated}
```

### PassivePerceptionHook（P45）— 被动感知

```
passive_check = 10 + player.get_modifier("wis")

触发条件：change_log 含 player.current_area 或 player.current_location 变化

检测项：
  - 区域 discoveries（visibility_dc）
  - 子地点隐藏 interactables（visibility_dc）
  - 容器陷阱（trap.detect_dc）
  - 动态子区域（discovery_mode="check"）

通过检定 → state.areas.mark_discovery()
SSE: discovery_reveal / hidden_object_revealed / trap_detected
```

---

## 七、事件引擎（EventEngine）

```python
class BasicEventConditionEvaluator:
    def evaluate(state, world) → EventConditionDecision

# 支持的条件类型（12种）：
flag_set         # FlagSlice 中某 key 等于某值
location_entered # player 进入某 area/location
period_reached   # 当前时段 == 目标时段
time_reached     # absolute_tick >= 目标 tick
quest_state      # 任务/里程碑处于某状态
disposition      # NPC 某维度 >= 阈值
time_elapsed     # 相对时段数 >= 数量
custom           # 自定义评估（可注入）
npc_talked       # 已与某 NPC 对话（flags）
item_obtained    # 背包中有某物品
kill_count       # 已击杀某种怪物 >= 数量
level_reached    # 玩家等级 >= 目标等级

# on_trigger 允许的命令：
set_flag, modify_disposition, modify_approval, advance_quest,
schedule_event, create_rumor, modify_location, add_knowledge,
modify_completion, adjust_danger, complete_objective
```

### run_inline_event_check()

在 Pipeline Stage B（规则引擎后）和 Stage C（Agent 后）都运行的轻量版评估：
- 不等到结算，立刻评估
- 应用状态跃迁
- 执行 on_trigger 命令
- emit `event_state_changed` SSE

---

## 八、协调器

### NpcInteractionCoordinator — 6步对话管线

```
1. 校验 NPC 在场 + 写 player 消息到 SceneBus + 构建 NpcFullContext
2. NPC Agent（AgenticExecutor）：Speak/Emote/UpdateFeeling/OfferQuest/OfferTrade...
3. GM 观察（默认 pass_turn）
4. 队友反应（各成员概率性响应，需能看到场景条目）
5. 对话选项（LLM suggest_options 优先 + 静态 fallback）
6. 聚合 SSE events，time_cost = 1/6
```

### PrivateChatCoordinator — 4步管线

```
1. 写 private 消息（visibility="private", audience=["player","npc:{id}"]）+ 构建 NpcFullContext
2. NPC Agent
3. 对话选项（静态 fallback）
4. 聚合结果，time_cost = 1/6
（无第3/4步：GM 观察 + 队友反应，私聊不可见）
```

### CompanionManager

```python
sync_to_player()
  # 将所有队伍成员移动到玩家当前 area + sub_location
  # 通过 AreaSlice.move_npc() 执行
```

---

## 九、SSE 事件汇总

| 事件类型 | 来源 Hook | 含义 |
|----------|----------|------|
| `time_advanced` | TimeAdvanceHook | 时间推进（day/slot/period）|
| `gm_narration` | GmNarrationHook | GM 叙事文本 |
| `npc_moved` | NpcScheduleHook | NPC 移动到新位置 |
| `npc_wants_to_chat` | PrivateChatTriggerHook | NPC 主动请求私聊 |
| `campfire_dialogue` | CampfireHook | 篝火对话内容 |
| `relationship_stage_changed` | RelationshipHook | 关系阶段变化 |
| `companion_dismissed` | RelationshipHook | 队友因敌对离队 |
| `milestone_completed` | MilestoneCompletionHook | 里程碑完成 |
| `milestone_failed` | NarrativePlannerHook | 里程碑失败 |
| `quest_objective_updated` | QuestObjectiveTrackingHook | 任务目标更新 |
| `quest_auto_completed` | QuestObjectiveTrackingHook | 任务自动完成 |
| `discovery_reveal` | PassivePerceptionHook | 发现隐藏事物 |
| `trap_detected` | PassivePerceptionHook | 发现陷阱 |
| `event_state_changed` | EventConditionHook | 事件状态跃迁 |
| `status_effects_ticked` | StatusEffectHook | 状态效果倒计时 |

---

## 十、设计模式

| 模式 | 说明 |
|------|------|
| **优先级链** | Hook 按 HOOK_PRIORITY 升序执行，新功能 = 选合适优先级插入，不改核心循环 |
| **SettlementContext.execute_command()** | Hook 内唯一合法变更入口，保证所有变更走 RulesEngine |
| **语义标签** | ENGINE 写入 SceneBus → Hook 读取，解耦触发检测与 Hook 逻辑 |
| **内联事件检查** | 管线内两次 run_inline_event_check()，不等结算即响应关键触发 |
| **冷静期 Flag** | 私聊冷静期等时间状态存 FlagSlice，不用专门字段，零架构成本 |
| **确定性降级** | 所有 LLM Provider（GmNarrator, NpcScheduleProvider）有 Template/Null 默认实现 |
