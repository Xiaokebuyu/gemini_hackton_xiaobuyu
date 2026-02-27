# AI Osiris 设计规范

创建时间：2026-02-26
状态：设计中
前置文档：`博德之门3架构深度分析.md` §4.3-4.7，`编排层设计规范.md`（P30 Hook 集成），`内容层设计规范.md`（LoreRegistry/FactionRegistry 提供规则上下文），`状态层设计规范.md`（指令写入目标切片）

> AI Osiris = 专职因果判定 LLM，替代 BG3 的 Osiris 手工规则引擎。
> 每时间格结算一次，批量推理本格所有状态变化的跨系统后果，输出结构化指令。

---

## 一、已确定的设计决策

来自架构讨论，已写入 `博德之门3架构深度分析.md`：

| 决策 | 内容 |
|------|------|
| **触发时机** | 格级结算，每时间格结束时触发一次 |
| **触发方式** | TickCoordinator 在 `accumulated >= 1.0` 时调用 |
| **输入** | 本格状态变化摘要 + 世界状态快照 + 世界规则上下文 |
| **输出** | 结构化 JSON 指令（不是叙述文本） |
| **执行** | AI Osiris 只判不执行，引擎验证后写入 ❸ |
| **面向** | 后台运行，不面向玩家 |
| **链式** | 不存在——每格一次推理，看到全貌，无需递归 |
| **叙述** | AI Osiris 的后果由 GM Agent 叙述，不是自己叙述 |

---

## 二、输入设计

AI Osiris 每次调用接收三部分输入：

### 2.1 本格状态变化摘要

一个时间格内发生的所有有意义的状态变化，结构化记录：

```json
{
  "time_slot": {"day": 3, "slot": 7, "period": "afternoon"},
  "location": "border_town.general_store",
  "duration_minutes": 60,

  "actions": [
    {
      "type": "trade",
      "actor": "player",
      "detail": "purchased dagger from merchant_tom",
      "tags": ["WEAPON", "DAGGER", "TRANSACTION"]
    },
    {
      "type": "steal",
      "actor": "player",
      "detail": "stole healing_potion from merchant_tom, stealth check passed (DC12)",
      "tags": ["CONSUMABLE", "POTION", "THEFT", "UNDETECTED"],
      "witnessed_by": ["paladin_companion"]
    },
    {
      "type": "dialogue",
      "actor": "player",
      "target": "passerby_adventurer",
      "detail": "learned about western farm attack",
      "tags": ["INFORMATION", "QUEST_HOOK", "WESTERN_FARM"]
    }
  ],

  "state_changes": [
    {"field": "player.inventory", "change": "+dagger, +healing_potion"},
    {"field": "player.gold", "change": "-8"},
    {"field": "merchant_tom.inventory", "change": "-dagger, -healing_potion (untracked)"}
  ]
}
```

> **Q1 已决定**：中等详细度——记录动作 + Tag + 关键细节（谁做了什么、对谁、结果如何），省略纯 UI 操作（查看背包、切换面板）。

### 2.2 世界状态快照

当前世界的关键状态，供 AI Osiris 推理时参考：

```json
{
  "player": {
    "level": 3, "hp": "28/35", "gold": 42,
    "location": "border_town.general_store",
    "active_quests": ["western_farm_investigation"],
    "guild_rank": "porcelain",
    "tags": ["ADVENTURER", "NEWCOMER"]
  },
  "party": [
    {"id": "paladin_companion", "approval": 65, "trust": 40,
     "relationship_stage": "acquaintance",
     "tags": ["COMPANION", "PALADIN", "LAWFUL"]}
  ],
  "nearby_npcs": [
    {"id": "merchant_tom", "disposition": 55,
     "tags": ["MERCHANT", "CIVILIAN", "GENERAL_STORE"]}
  ],
  "faction_standings": {
    "merchant_guild": 50,
    "temple_order": 60,
    "adventurer_guild": 45
  },
  "active_flags": ["western_farm_under_attack", "goblin_activity_rising"],
  "current_chapter": {"id": "ch1_goblin_crisis", "completion": 15}
}
```

### 2.3 世界规则上下文

静态的世界设定信息，帮助 AI Osiris 理解"这个世界的规则"：

```
- 商人公会对偷窃行为零容忍，一旦发现会全城通缉
- 神殿骑士团信仰正义，目睹偷窃会严厉谴责
- 边境小镇治安松散，小额偷窃可能不被追究
- 冒险者公会中立，不关心个人道德
- 西部牧场事件是当前章节的主线钩子
```

> **Q2 已决定**：管线统一提供。所需数据类型集中定义在 model 中，由管线（TickCoordinator → ContextAssembler）统一提取组装。不在 prompt 模板中硬编码，也不在 AI Osiris 内部检索。
> 世界规则上下文的数据来源：`内容层设计规范.md` §十二 LoreRegistry + §十一 FactionRegistry.behavioral_rules。

---

## 三、输出设计：指令类型全集

AI Osiris 的输出是一个 `consequences` 数组，每个元素是一条结构化指令。

### 3.1 指令类型总表

| 指令类型 | 参数 | 效果 | 示例场景 |
|---------|------|------|---------|
| `set_flag` | key, value | 设置/修改世界标记 | 偷窃行为记录 |
| `modify_disposition` | target(NPC/faction), delta, reason | 修改好感度 | 商人公会对玩家好感 -5 |
| `modify_approval` | character, delta, reason | 修改同伴审批值 | 审判骑士目睹偷窃，approval -15 |
| `advance_quest` | quest_id, to_state | 推进任务状态 | 西部牧场调查：RUMOR_HEARD |
| `schedule_event` | event_id, trigger_condition | 安排延迟事件 | 2 格后商人发现药水失踪 |
| `create_rumor` | content, spread_to[] | 创建传播中的信息 | "有人在杂货店行窃" |
| `modify_location` | location_id, change | 修改地点状态 | 杂货店标记为"有失窃记录" |
| `add_knowledge` | character_id, knowledge | 角色获得认知 | 审判骑士知道玩家偷了东西 |
| `modify_completion` | chapter_id, delta, reason | 修改主线完成度 | 获得牧场情报 +5% |
| `adjust_danger` | area_id, delta, reason | 调整区域危险度 | 哥布林活动加剧，西部危险 +0.2 |

> **Q3 已决定**：固定枚举。新指令类型必须通过代码变更加入，引擎只接受已注册的类型。安全优先。

### 3.2 输出格式

```json
{
  "reasoning": "商人虽然没当场发现偷窃，但库存盘点迟早会发现差异。审判骑士目睹了偷窃但未当场揭穿，内心产生了不信任。玩家获得的牧场情报推进了主线认知。",

  "consequences": [
    {
      "type": "set_flag",
      "params": {"key": "stolen_from_general_store", "value": true}
    },
    {
      "type": "modify_disposition",
      "params": {"target": "merchant_guild", "delta": -5},
      "reason": "库存差异迟早会被发现"
    },
    {
      "type": "modify_approval",
      "params": {"character": "paladin_companion", "delta": -15},
      "reason": "目睹偷窃行为，与信仰冲突"
    },
    {
      "type": "add_knowledge",
      "params": {"character": "paladin_companion", "knowledge": "玩家在杂货店偷了治疗药水"}
    },
    {
      "type": "schedule_event",
      "params": {
        "event_id": "merchant_discovers_theft",
        "trigger_condition": {"type": "time_slots_elapsed", "count": 2}
      }
    },
    {
      "type": "advance_quest",
      "params": {"quest_id": "western_farm_investigation", "to_state": "RUMOR_HEARD"}
    },
    {
      "type": "modify_completion",
      "params": {"chapter_id": "ch1_goblin_crisis", "delta": 5},
      "reason": "获得了牧场被袭击的关键情报"
    }
  ]
}
```

**`reasoning` 字段**：AI Osiris 的推理过程，不面向玩家，用于调试和日志。

### 3.3 引擎验证规则

引擎收到指令后逐条验证：

| 验证项 | 拒绝条件 | 处理 |
|--------|---------|------|
| 类型合法 | type 不在已知枚举中 | 跳过该条，记录警告 |
| 目标存在 | target NPC/faction/quest 不存在 | 跳过该条，记录警告 |
| 数值合理 | disposition delta 超出 [-50, +50] | 裁剪到范围边界 |
| 不重复 | 同一 flag 被设置两次 | 取最后一次 |
| 权限合法 | 试图直接修改 HP/金币/背包 | **拒绝**——这些只能由 ❷ 规则引擎修改 |

> **关键约束**：AI Osiris **不能直接修改玩家的 HP、金币、背包、位置**。这些是 ❷ 规则引擎的专属领域（详见 `规则引擎层设计规范.md` §四 权限矩阵）。AI Osiris 只能修改"世界对玩家的看法"（好感、标记、事件、认知），不能修改"玩家自身的硬状态"。
>
> 指令写入目标：`状态层设计规范.md` §四（AI Osiris 指令→切片映射表）。
> `add_knowledge` 影响 NPC 记忆图谱：`NPC与队友子系统设计规范.md` §四.2。

---

## 四、Prompt 模板

```markdown
你是 AI Osiris，一个游戏世界的因果判定引擎。

你的职责：分析一个时间格内发生的所有事件，推理这些事件在这个世界中会产生什么跨系统后果。

## 世界规则
{world_rules_context}

## 当前世界状态
{world_state_snapshot}

## 本格发生的事件
{time_slot_changes_summary}

## 你的任务

分析上述事件，推理它们会产生什么后果。注意：

1. 只输出**跨系统的涟漪后果**，不要重复已经发生的事实
2. 考虑：NPC 会怎么看待这些行为？阵营关系会如何变化？有没有延迟后果？对进行中的任务有什么影响？
3. 利用实体的 Tag 来推理（例如偷了 [SACRED] 标签的物品 → 宗教阵营后果）
4. 如果本格没有产生有意义的跨系统后果，输出空 consequences 数组
5. **绝不输出**对 HP、金币、背包、位置的直接修改

## 输出格式

严格输出以下 JSON：
{output_schema}
```

> **Q4 已决定**：medium thinking。因果推理需要一定深度，low 可能不够。如果成本压力大再降级。

---

## 五、与 TickCoordinator 的集成

> **与 NarrativePlanner 的协作**：AI Osiris（P30）负责反应式因果推理，NarrativePlanner（P35）负责主动叙事引导。两者在格结算链中紧邻执行，NarrativePlanner 能看到 Osiris 本格的输出。详见 `叙事规划子系统设计规范.md` §一（定位）和 §九（P35 集成）。

### 5.1 调用时序

```
TickCoordinator.tick_settlement()
    │
    ├─ 1. self.collect_state_changes()
    │     → 构造 §2.1 格式的摘要
    │
    ├─ 2. self.build_world_snapshot()
    │     → 构造 §2.2 格式的快照
    │
    ├─ 3. self.ai_osiris.evaluate(summary, snapshot, rules_context)
    │     → LLM 调用，返回 §3.2 格式的结果
    │     → 解析 JSON，验证格式
    │
    ├─ 4. self.engine.execute_validated(consequences)
    │     → 逐条验证（§3.3）
    │     → 执行合法指令 → 写入 ❸
    │     → 返回执行报告（哪些成功、哪些被拒绝）
    │
    ├─ 5. self.gm_narrate_settlement(executed_consequences)
    │     → 如有玩家可感知的变化，GM 生成一段过渡叙述
    │     → SSE 推送
    │
    ├─ 6. self.time_manager.advance(1)
    │
    ├─ 7. self.check_period_transition()
    │
    └─ 8. self.scene_bus.reset()
```

### 5.2 空结算优化

如果本格没有有意义的状态变化（例如玩家只是查看了背包），可以跳过 AI Osiris 调用：

```python
def tick_settlement(self):
    changes = self.collect_state_changes()

    if changes.is_trivial():
        # 没有有意义的变化，跳过 AI Osiris
        self.time_manager.advance(1)
        self.check_period_transition()
        self.scene_bus.reset()
        return

    # 有变化，走完整结算流程
    ...
```

> **Q5 已决定**：只有纯 0 成本动作（查看背包、装备切换、查看地图）才算 trivial。任何涉及 NPC 交互或状态变更的都不跳过。

### 5.3 多格动作的结算

区域间移动（1-3 格）和长休息（N 格）会触发多次结算。每次结算的上下文不同：

| 格类型 | AI Osiris 的输入特征 | 预期输出 |
|--------|-------------------|---------|
| **普通格**（对话/交易） | 丰富的玩家动作列表 | 好感变化、标记、任务推进 |
| **旅途格**（移动中） | 几乎无玩家动作，只有"正在旅行" | 遭遇检定结果、途中发现、环境变化 |
| **休息格**（长休中） | 无玩家动作，只有"正在休息" | 同伴营地对话触发、夜间事件检定、世界时间推进后果 |

> **Q6 已决定**：统一 prompt。摘要中自然会体现"本格是旅途/休息"，AI Osiris 根据上下文自行判断输出内容。

---

## 六、`schedule_event` 的延迟事件机制

AI Osiris 可以安排"将来发生"的事件。这需要一个事件队列：

```python
# TickCoordinator 持有
pending_events = [
    {
        "event_id": "merchant_discovers_theft",
        "trigger_condition": {"type": "time_slots_elapsed", "count": 2},
        "created_at_slot": 7,
        "source": "ai_osiris"  # 溯源
    }
]
```

每次 `tick_settlement()` 开始前，先检查待触发事件：

```python
def tick_settlement(self):
    # 0. 检查延迟事件是否满足触发条件
    triggered = self.check_pending_events()
    # 把触发的事件加入本格的状态变化摘要
    changes = self.collect_state_changes()
    changes.add_triggered_events(triggered)
    ...
```

触发条件类型：

| 条件类型 | 参数 | 含义 |
|---------|------|------|
| `time_slots_elapsed` | count | N 格后触发 |
| `period_reached` | period | 到达特定时段（黄昏/夜晚）触发 |
| `location_entered` | location_id | 玩家进入特定地点时触发 |
| `flag_set` | flag_key | 某个标记被设置时触发 |

---

## 七、成本与性能

| 指标 | 估算 |
|------|------|
| 调用频率 | 每时间格一次，约每 5-10 分钟现实时间 |
| 输入 token | ~800-1500（摘要 + 快照 + 规则） |
| 输出 token | ~200-500（reasoning + consequences） |
| 模型选择 | Flash + medium thinking |
| 空结算跳过 | 预计 30-50% 的格可以跳过（无有意义变化） |

---

## 八、设计决策记录

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 状态变化摘要的详细程度？ | **中等**：动作 + Tag + 关键细节，省略纯 UI 操作 |
| Q2 | 世界规则上下文从哪来？ | **管线统一提供**：数据类型定义在 model，由管线提取组装 |
| Q3 | 指令类型固定枚举 vs 可扩展？ | **固定枚举**：新类型需代码变更，安全优先 |
| Q4 | thinking level？ | **medium**：因果推理需要深度 |
| Q5 | `is_trivial()` 怎么定义？ | **仅 0 成本动作算 trivial**：查看/装备/地图，涉及 NPC 交互的都不跳过 |
| Q6 | 旅途/休息格需要不同 prompt 吗？ | **统一 prompt**：摘要自然体现格类型，AI 自行判断 |

补充说明：
- **对话时间成本**：发起对话消耗 1/6 格，对话内的所有来回交流免费。一格内可以深聊很久，成本只算一次。

---

## 变更日志

| 日期 | 变更 |
|------|------|
| 2026-02-26 | 创建。输入三部分设计（摘要+快照+规则）+ 输出指令类型全集（10 类）+ 引擎验证规则 + prompt 模板 + TickCoordinator 集成 + 延迟事件机制 |
| 2026-02-26 | 关闭全部 6 个开放问题：Q1 中等详细度、Q2 管线统一提供、Q3 固定枚举、Q4 medium thinking、Q5 仅 0 成本算 trivial、Q6 统一 prompt。补充对话时间成本说明 |
