# NarrativePlanner 设计规范

创建时间：2026-02-26
状态：设计中
前置文档：`AI-Osiris设计规范.md`，`编排层设计规范.md`，`内容层设计规范.md`（QuestRegistry），`状态层设计规范.md`

> NarrativePlanner = 主动叙事规划器。AI Osiris 的姊妹组件。
> AI Osiris 向后看（发生了什么 → 后果），NarrativePlanner 向前看（现在在哪 → 下一步引导什么）。
>
> **核心隐喻：价值与价格。**
> ❶ QuestRegistry 存储"价值"——主线里程碑节点（从世界书提取的故事骨架）。
> NarrativePlanner 生成"价格"——动态任务（围绕里程碑、随玩家行为波动的具体任务）。

---

## 一、定位

### 1.1 与 AI Osiris 的关系

```
                    格结算链（SettlementHooks）
                    │
    P30: AI Osiris  │  反应式因果引擎
         ↓          │  "偷窃 → 商人 2 格后发现"
    P35: NarrativePlanner  │  主动叙事规划器
         ↓          │  "进度 15% → 该引导玩家去调查牧场了"
    P40: Encounter  │
```

| 维度 | AI Osiris | NarrativePlanner |
|------|-----------|------------------|
| 方向 | 过去 → 现在（因果） | 现在 → 未来（引导） |
| 本质 | 推理后果 | 设计任务 |
| 输入 | 本格状态变化 | 故事里程碑 + 玩家行为 |
| 输出 | 后果指令（flag/好感/事件） | 动态任务 + 投递指令 |
| 频率 | 每格（非平凡格） | 条件触发 + 兜底 |
| 状态 | 无状态 | **有状态**（NarrativePlanSlice） |

### 1.2 不做什么

| 职责 | 归属 | NarrativePlanner 只做 |
|------|------|----------------------|
| 因果推理 | ⊕ AI Osiris | 不推理后果 |
| 任务状态机 | ❸ QuestSlice | 不管理任务状态流转 |
| 叙述文本 | ❺ GM Agent | 不生成叙述（只生成任务结构 + 投递指令） |
| 规则计算 | ❷ RulesEngine | 不计算 DC/奖励数值 |
| 任务完成判定 | ❷ + EventEngine | 不判定目标是否完成 |

---

## 二、故事里程碑——QuestRegistry 演化

### 2.1 设计理念

原 QuestRegistry 存储详细任务模板（目标、奖励、前置条件）。现在：

```
旧模型：QuestRegistry 存详细任务 → 管线直接消费
新模型：QuestRegistry 存故事里程碑 → NarrativePlanner 消费 → 动态生成任务
```

**里程碑 = 故事骨架上的节点**。每个里程碑是一个"玩家必须经历的故事节拍"，但不规定玩家如何到达——那是 NarrativePlanner 的工作。

### 2.2 StoryMilestone Schema（替代旧 QuestTemplate）

```python
StoryMilestone = {
    id: str,                          # "investigate_western_farm"
    name: str,                        # "调查西部牧场"
    tags: list[str],                  # [MAIN_QUEST, GOBLIN, INVESTIGATION]

    # === 故事定位 ===
    chapter_id: str,                  # 所属章节 "ch1_goblin_crisis"
    completion_value: int,            # 对章节完成度的贡献（5-30）
    sequence: int,                    # 章节内排序（10, 20, 30...）
    prerequisites: list[str],         # 前置里程碑 ID

    # === 叙事上下文（给 NarrativePlanner 的创作素材）===
    narrative_context: str,           # 故事背景描述
    # "西部牧场是边境最大的农业聚居地，最近频繁遭受哥布林袭击。
    #  公会收到了多份来自牧场的求援报告，但尚无冒险者前去调查。"

    key_elements: list[str],         # 关键叙事元素（NarrativePlanner 生成任务时必须包含）
    # ["goblin_attacks", "western_farm", "refugees", "guild_request"]

    involved_npcs: list[str],         # 相关 NPC ID（可用于投递任务）
    involved_locations: list[str],    # 相关地点 ID

    # === 达成判定 ===
    success_conditions: list[MilestoneCondition],
    # [
    #   {type: "location_visited", params: {location: "western_farm"}},
    #   {type: "flag_set", params: {key: "farm_investigation_started"}},
    # ]

    failure_conditions: list[MilestoneCondition] | None,
    # [
    #   {type: "time_elapsed", params: {days: 7}},  ← 7 天不去牧场就沦陷
    # ]

    # === 失败后果 ===
    failure_fallback: str | None,     # 失败时的叙事走向描述
    # "牧场被哥布林彻底摧毁，难民涌入边境镇，主线走向改变。"
}

MilestoneCondition = {
    type: str,        # location_visited / flag_set / npc_talked / item_obtained / kill_count / time_elapsed
    params: dict,     # 条件参数
    optional: bool,   # 是否为可选条件（可选条件不影响达成判定）
}
```

### 2.3 章节里程碑图示例

```
Chapter 1: 哥布林危机
    │
    ├─ M10: 听闻哥布林袭击 (5%)
    │     conditions: flag "goblin_rumor_heard"
    │     key_elements: [tavern_gossip, merchant_complaint, passerby_news]
    │
    ├─ M20: 接受调查任务 (10%)
    │     prerequisites: [M10]
    │     conditions: flag "farm_quest_accepted"
    │     key_elements: [guild_request, farm_refugees]
    │
    ├─ M30: 调查西部牧场 (25%)    ← 里程碑，25% 阶段解锁
    │     prerequisites: [M20]
    │     conditions: location_visited "western_farm" + flag "farm_investigated"
    │     key_elements: [destroyed_farm, survivor_testimony, goblin_tracks]
    │
    ├─ M40: 追踪哥布林巢穴 (40%)
    │     prerequisites: [M30]
    │     conditions: location_visited "goblin_forest_entrance"
    │     key_elements: [tracking, forest_dangers, goblin_scouts]
    │
    ├─ M50: 发现巢穴 (50%)        ← 里程碑，50% 阶段解锁
    │     prerequisites: [M40]
    │     conditions: location_visited "goblin_nest"
    │     key_elements: [nest_layout, goblin_hierarchy, prisoner_rescue]
    │
    ├─ M60: 准备突袭 (65%)
    │     prerequisites: [M50]
    │     conditions: flag "assault_prepared"
    │     key_elements: [strategy_planning, ally_recruitment, equipment_upgrade]
    │
    ├─ M70: 突袭哥布林巢穴 (80%)
    │     prerequisites: [M60]
    │     conditions: flag "nest_assault_completed"
    │     key_elements: [dungeon_crawl, boss_fight, prisoners_freed]
    │
    └─ M80: 击败哥布林首领 (100%)  ← 章节完结
          prerequisites: [M70]
          conditions: flag "goblin_champion_defeated"
          key_elements: [final_boss, chapter_resolution, reward_ceremony]
```

**注意**：里程碑之间没有预定义的"任务"——那些全由 NarrativePlanner 在运行时根据玩家行为动态生成。

### 2.4 QuestRegistry 演化后的查询 API

```python
class QuestRegistry(ContentRegistry):
    """故事里程碑注册表（原 QuestRegistry 演化）"""
    name = "quests"

    milestones: dict[str, StoryMilestone]

    def get_milestone(self, milestone_id: str) -> StoryMilestone: ...
    def get_chapter_milestones(self, chapter_id: str) -> list[StoryMilestone]:
        """获取某章节的所有里程碑，按 sequence 排序"""
    def get_next_milestones(self, completed: list[str]) -> list[StoryMilestone]:
        """获取下一批可达成的里程碑（前置已满足）"""
    def get_milestone_graph(self, chapter_id: str) -> dict:
        """返回里程碑依赖图（用于可视化 / NarrativePlanner 规划）"""
```

---

## 三、NarrativePlanner 架构

### 3.1 输入设计

NarrativePlanner 每次运行接收四部分输入：

#### ⓐ 故事蓝图

当前章节的里程碑图 + 各里程碑状态：

```json
{
  "chapter": {
    "id": "ch1_goblin_crisis",
    "name": "哥布林危机",
    "completion": 15
  },
  "milestones": [
    {
      "id": "M10",
      "name": "听闻哥布林袭击",
      "status": "completed",
      "completion_value": 5
    },
    {
      "id": "M20",
      "name": "接受调查任务",
      "status": "completed",
      "completion_value": 10
    },
    {
      "id": "M30",
      "name": "调查西部牧场",
      "status": "available",
      "completion_value": 25,
      "narrative_context": "西部牧场是边境最大的农业聚居地...",
      "key_elements": ["destroyed_farm", "survivor_testimony", "goblin_tracks"],
      "involved_locations": ["western_farm"]
    }
  ],
  "next_target": "M30"
}
```

#### ⓑ 玩家行为画像

从 ❸ 多个 Slice 汇总的玩家当前状态和行为趋势：

```json
{
  "player": {
    "level": 3,
    "location": "border_town.tavern",
    "guild_rank": "porcelain",
    "gold": 42,
    "play_style_tags": ["CAUTIOUS", "DIALOGUE_HEAVY", "EXPLORER"]
  },
  "party": [
    {"id": "paladin_companion", "name": "审判骑士"}
  ],
  "behavior": {
    "ticks_since_last_milestone": 8,
    "recent_actions": ["dialogue", "trade", "explore_sub_location"],
    "visited_locations": ["border_town", "border_town.general_store", "border_town.tavern"],
    "ignored_hints": ["guild_board_notice"]
  }
}
```

#### ⓒ 当前叙事计划状态

上一次 NarrativePlanner 的输出（有状态，从 ❸ NarrativePlanSlice 读取）：

```json
{
  "active_quests": [
    {
      "id": "dq_001",
      "title": "打听哥布林消息",
      "target_milestone": "M20",
      "status": "completed"
    }
  ],
  "escalation_level": 1,
  "last_intervention_tick": 12,
  "strategy_notes": "玩家倾向对话探索，优先用 NPC 对话方式投递任务"
}
```

#### ⓓ 世界上下文

当前区域相关的 NPC、势力、世界规则（精简版，供 LLM 生成合理任务）：

```json
{
  "area_npcs": ["guild_receptionist", "merchant_tom", "tavern_keeper"],
  "area_description": "边境小镇，冒险者公会所在地...",
  "relevant_factions": ["adventurer_guild", "merchant_guild"],
  "world_rules": ["冒险者公会按等级分配任务", "边境地区治安靠冒险者维持"]
}
```

### 3.2 输出设计：指令类型

NarrativePlanner 输出一个 `plan` 对象，包含 `interventions` 数组：

```json
{
  "reasoning": "玩家已完成 M20（接受调查任务），下一目标是 M30（调查西部牧场）。玩家在酒馆已待了 8 格未出发，应该通过路人 NPC 制造紧迫感。同时考虑到玩家的对话偏好，用 NPC 对话方式投递。",

  "strategy_update": "升级紧迫感——让受伤难民出现在酒馆，通过情感触发推动玩家出发",

  "interventions": [
    {
      "type": "create_quest",
      "params": { ... }
    },
    {
      "type": "spawn_quest_npc",
      "params": { ... }
    }
  ]
}
```

#### 指令类型全集

| 指令类型 | 参数 | 效果 | 典型场景 |
|---------|------|------|---------|
| `create_quest` | quest_def | 创建动态任务，写入玩家任务日志 | 生成引导任务 |
| `spawn_quest_npc` | npc_profile, dialogue_hook, quest_id | 生成携带任务的路人 NPC | 受伤农民冲进酒馆 |
| `direct_npc` | npc_id, directive, quest_id | 指挥现有 NPC 下次交互时主动提及任务 | 公会接待员叫住玩家 |
| `publish_bulletin` | quest_id, board_location | 在公告板发布任务 | 公会任务板新增委托 |
| `plant_environmental` | location_id, description, discovery | 在地点放置环境线索 | 酒馆门口出现难民帐篷 |
| `escalate` | escalation_type, params | 升级叙事紧迫感 | 难民数量增加、danger 上升 |
| `adjust_pacing` | direction, reason | 调整节奏（加速/减速） | 玩家刚打完仗，减缓推进 |
| `retire_quest` | quest_id, reason | 过期/失效的动态任务下架 | 玩家已通过其他途径达成目标 |

### 3.3 `create_quest` 详细格式

```json
{
  "type": "create_quest",
  "params": {
    "quest_def": {
      "title": "受伤农民的求助",
      "description": "一个浑身是血的农民冲进酒馆，声称西部牧场遭到了哥布林大规模袭击。他的家人还被困在那里。",
      "target_milestone": "M30",
      "urgency": "high",

      "objectives": [
        {
          "description": "和受伤农民交谈，了解详情",
          "type": "talk_to",
          "target": {"npc": "_spawned_wounded_farmer"},
          "optional": false
        },
        {
          "description": "前往西部牧场",
          "type": "reach_location",
          "target": {"location": "western_farm"},
          "optional": false
        }
      ],

      "rewards": {
        "xp": 50,
        "gold": 0,
        "reputation": {"adventurer_guild": 5}
      },

      "expiry_ticks": 18,
      "on_expire": "escalate"
    }
  }
}
```

### 3.4 `spawn_quest_npc` 详细格式

```json
{
  "type": "spawn_quest_npc",
  "params": {
    "npc_profile": {
      "name": "受伤的农民",
      "appearance": "一个中年男人，左臂缠着粗糙的绷带，衣服被撕裂，眼中满是恐惧",
      "personality": "绝望但坚强，急切地想找人帮忙",
      "tags": ["CIVILIAN", "FARMER", "WOUNDED", "QUEST_GIVER"]
    },
    "spawn_location": "border_town.tavern",
    "dialogue_hook": "他跌跌撞撞地推开酒馆的门，几乎摔倒在地。'求求你们！有人能帮帮我吗？哥布林...牧场...我的家人还在那里！'",
    "linked_quest_id": "dq_002",
    "despawn_after_ticks": 12,
    "interaction_style": "urgent_plea"
  }
}
```

> **复用现有系统**：`spawn_quest_npc` 底层使用已有的路人生成系统（`POST .../passersby/spawn`），但注入了 NarrativePlanner 提供的性格、对话钩子和关联任务。

### 3.5 `direct_npc` 详细格式

```json
{
  "type": "direct_npc",
  "params": {
    "npc_id": "guild_receptionist",
    "directive": "下次玩家与你交谈时，主动提到西部牧场的紧急委托。表现出担忧，暗示这是一个适合瓷级冒险者证明自己的机会。",
    "priority": "high",
    "linked_quest_id": "dq_002",
    "expires_after_ticks": 6
  }
}
```

> **实现方式**：NPC Directive 写入 NPC 的工作记忆（ContextWindow），作为优先指令。NPC Agent 下次执行时会自然融入对话。

---

## 四、触发条件

### 4.1 条件触发（主路径）

| 触发条件 | 检查方式 | 示例 |
|---------|---------|------|
| **里程碑状态变化** | QuestSlice 监听 | M20 完成 → 规划 M30 的引导 |
| **进入新区域** | AreaSlice 变化 | 进入 goblin_forest → 检查是否需要调整引导 |
| **玩家行为标记** | FlagSlice 变化 | `farm_quest_accepted` 被设 → 里程碑达成检查 |
| **AI Osiris 输出** | P30 结果 | Osiris 设了 `goblin_danger_rising` → 可能需要升级引导 |
| **动态任务到期** | NarrativePlanSlice 检查 | `dq_001` 过期了 → 需要替换策略 |
| **升级阈值** | 停滞检测 | `ticks_since_progress >= N` → 升级引导 |

### 4.2 周期兜底（防遗漏）

```python
class NarrativePlannerHook(SettlementHook):
    priority = 35
    name = "narrative_planner"

    FALLBACK_INTERVAL = 6  # 兜底间隔：每 6 格（约 1 小时游戏时间）

    def should_skip(self, change_log: list[StateChange]) -> bool:
        # 条件触发：任何触发条件满足 → 不跳过
        if self._has_trigger_condition(change_log):
            return False

        # 兜底：距上次运行超过 N 格 → 不跳过
        if self._ticks_since_last_run() >= self.FALLBACK_INTERVAL:
            return False

        # 既无触发条件也未到兜底周期 → 跳过
        return True
```

### 4.3 触发后的完整执行流程

```
NarrativePlannerHook.execute()
    │
    ├─ 1. 加载输入
    │     故事蓝图 ← ❶ QuestRegistry.get_chapter_milestones()
    │     里程碑状态 ← ❸ QuestSlice
    │     玩家画像 ← ❸ PlayerSlice + AreaSlice + 行为分析
    │     当前计划 ← ❸ NarrativePlanSlice
    │     世界上下文 ← ❶ CharacterRegistry + MapRegistry + LoreRegistry
    │
    ├─ 2. 预处理
    │     检查活跃动态任务状态（到期？完成？失效？）
    │     计算停滞指标（ticks_since_progress）
    │     判定当前升级等级
    │
    ├─ 3. LLM 推理
    │     构建 prompt → NarrativePlanner LLM 调用
    │     → 输出 reasoning + strategy_update + interventions
    │     → JSON 解析 + 格式验证
    │
    ├─ 4. 指令执行
    │     逐条验证指令合法性
    │     create_quest     → 写入 NarrativePlanSlice + QuestSlice（DISCOVERED）
    │     spawn_quest_npc  → 调用 PasserbyService.spawn() + 注入 NPC 指令
    │     direct_npc       → 写入目标 NPC 的 ContextWindow
    │     publish_bulletin → 更新公告板状态（AreaSlice）
    │     plant_environmental → 更新地点状态（AreaSlice + SceneBus）
    │     escalate         → 产生 Command → ❷ 执行
    │     retire_quest     → 更新 NarrativePlanSlice（任务下架）
    │
    ├─ 5. 更新 NarrativePlanSlice
    │     strategy_notes 更新
    │     last_run_tick 更新
    │     active_quests 列表更新
    │
    └─ 6. 返回 HookResult
          commands: 产生的 Command 列表
          sse_events: 任务通知 SSE 事件（如有直接发布的任务）
```

---

## 五、递进引导策略（升级机制）

### 5.1 停滞检测

NarrativePlanner 跟踪玩家在主线方向上的"停滞程度"：

```
停滞指标 = ticks_since_last_milestone_progress
```

| 停滞格数 | 升级等级 | 引导策略 | 示例 |
|---------|---------|---------|------|
| 0-3 | L0 无干预 | 玩家在正常探索，不打扰 | — |
| 4-6 | L1 暗示 | 环境中自然出现主线相关线索 | 酒馆客人谈论西部的消息 |
| 7-9 | L2 推荐 | NPC 主动提及/推荐任务 | 公会接待员叫住玩家 |
| 10-12 | L3 紧迫 | 生成紧迫事件，直接投递任务 | 受伤农民冲进酒馆 |
| 13-15 | L4 危机 | 世界状态恶化，错过窗口的后果开始显现 | 第二波难民涌入、danger 上升 |
| 16+ | L5 失败倒计时 | 明确告知后果，给最后机会 | "牧场最后的防线即将崩溃" |

### 5.2 升级策略不是线性的  

NarrativePlanner 会根据**玩家行为类型**调整升级方式：

```
对话型玩家（DIALOGUE_HEAVY）
  → 偏好用 NPC 对话投递
  → L1: 酒馆 NPC 闲聊提及  →  L2: 公会 NPC 正式推荐  →  L3: 受伤农民求助

探索型玩家（EXPLORER）
  → 偏好用环境线索投递
  → L1: 路边发现马车残骸  →  L2: 发现逃难的村民  →  L3: 路上遭遇哥布林侦察兵

战斗型玩家（COMBAT_FOCUSED）
  → 偏好用遭遇/冲突投递
  → L1: 小镇外哥布林小队出没  →  L2: 哥布林袭击商队  →  L3: 哥布林直接攻击小镇
```

这些**不是硬编码规则**——NarrativePlanner 作为 LLM，在 prompt 中被告知玩家行为倾向后自主决策投递方式。升级等级表只是参考框架。

### 5.3 减速机制

如果玩家刚经历了重大事件（大战、重要对话、升级），NarrativePlanner 应该**暂缓**而非继续推进：

```python
# 减速条件（示例）
should_slow_down = (
    recent_combat_ended  # 刚打完仗，让玩家喘口气
    or milestone_just_completed  # 刚完成里程碑，享受成就感
    or player_in_private_chat  # 正在私聊，不打断
    or party_hp_low  # 队伍血量低，应该先休息
)
```

减速时 NarrativePlanner 不生成新干预，只更新 strategy_notes："玩家刚完成 M30，暂缓推进，等待 2 格后再评估。"

---

## 六、动态任务生命周期

### 6.1 任务状态

```
generated ──→ delivered ──→ discovered ──→ accepted ──→ completed
    │              │             │             │            │
    │              │             │             │            └→ 贡献里程碑进度
    │              │             │             └→ 目标跟踪（EventEngine）
    │              │             └→ 出现在任务日志
    │              └→ 投递指令已执行（NPC 出现/公告板更新）
    └→ NarrativePlanner 生成
                                   ↘ expired（到期未接受）
                                   ↘ retired（被 NarrativePlanner 主动下架）
```

### 6.2 DynamicQuest 完整数据结构

```python
@dataclass
class DynamicQuest:
    """NarrativePlanner 生成的动态任务"""

    # === 标识 ===
    id: str                           # 自动生成：f"dq_{timestamp}_{seq}"
    title: str                        # "受伤农民的求助"
    description: str                  # 任务描述（叙事风格）

    # === 定位 ===
    target_milestone: str             # 指向哪个里程碑
    urgency: str                      # low / medium / high / critical

    # === 目标 ===
    objectives: list[QuestObjective]  # 任务目标列表
    # QuestObjective = {
    #     description: str,
    #     type: str,         # talk_to / reach_location / collect / kill / escort / investigate
    #     target: dict,      # 目标参数
    #     optional: bool,
    #     completed: bool,
    # }

    # === 奖励 ===
    rewards: QuestRewards             # {xp, gold, items, reputation}

    # === 投递 ===
    delivery_method: str              # spawn_npc / direct_npc / bulletin / environmental / direct
    delivery_params: dict             # 投递参数（NPC 信息 / 地点 / 公告板位置）
    delivery_status: str              # pending / delivered / acknowledged

    # === 生命周期 ===
    status: str                       # generated / delivered / discovered / accepted / completed / expired / retired
    created_at_tick: int              # 创建时的时间格
    expiry_ticks: int | None          # 过期时间（None = 不过期）
    on_expire: str                    # ignore / escalate / retire
    # ignore = 静默过期
    # escalate = 过期时升级（NarrativePlanner 下次运行生成更紧迫的替代）
    # retire = 标记为 retired，从活跃列表移除

    # === 溯源 ===
    generated_by_escalation: int      # 被哪个升级等级触发生成的
    planner_reasoning: str            # NarrativePlanner 的生成理由
```

### 6.3 任务完成判定

动态任务的目标完成判定**不由 NarrativePlanner 负责**，而是复用已有系统：

```
DynamicQuest.objectives[i] (type="reach_location", target={location: "western_farm"})
    ↓
NarrativePlanner 生成时同步创建 EventSlice 条件
    ↓
EventEngine.check_conditions()（A6/C1/P50）
    → 条件满足 → 更新 DynamicQuest.objectives[i].completed = true
    → 所有必选目标完成 → DynamicQuest.status = "completed"
    → 检查是否满足关联里程碑的 success_conditions
```

---

## 七、NarrativePlanSlice——❸ 新增状态切片

### 7.1 Schema

```python
@dataclass
class NarrativePlanSlice(StateSlice):
    """NarrativePlanner 的有状态计划。❸ 状态层新增切片。"""

    name = "narrative_plan"

    # === 进度跟踪 ===
    current_chapter: str                    # 当前章节 ID
    current_target_milestone: str | None    # 当前目标里程碑 ID
    completed_milestones: list[str]         # 已完成的里程碑 ID 列表
    chapter_completion: float               # 章节完成百分比

    # === 动态任务池 ===
    active_quests: list[DynamicQuest]       # 当前活跃的动态任务
    quest_history: list[QuestSummary]       # 历史任务摘要（精简，防膨胀）
    # QuestSummary = {id, title, target_milestone, status, created_tick, resolved_tick}

    # === NPC 指令队列 ===
    npc_directives: list[NpcDirective]      # 待执行的 NPC 指令
    # NpcDirective = {npc_id, directive, priority, quest_id, expires_tick}

    # === 环境变更队列 ===
    environmental_changes: list[dict]       # 待应用的环境变更

    # === 规划状态 ===
    escalation_level: int                   # 当前升级等级（0-5）
    ticks_since_milestone_progress: int     # 距上次里程碑进展的格数
    strategy_notes: str                     # NarrativePlanner 的当前策略描述
    last_run_tick: int                      # 上次运行的时间格
    next_scheduled_tick: int | None         # 下次计划运行的时间格（兜底）

    # === 玩家行为画像 ===
    play_style_tags: list[str]              # 累积的玩家风格标签
    behavior_window: list[BehaviorEntry]    # 最近 N 格的行为记录（滑动窗口）
    # BehaviorEntry = {tick, action_type, location, details}
```

### 7.2 持久化

NarrativePlanSlice 与其他 Slice 统一持久化：

```
saves/{session_id}/state/narrative_plan.json
```

云端路径：`worlds/{world_id}/sessions/{session_id}/state/narrative_plan`

### 7.3 行为画像更新

玩家行为画像不是由 NarrativePlanner 更新的——它在**每次 Pipeline 处理时**由编排层自动记录：

```python
# TickCoordinator.process() 中，记录行为
self.state.narrative_plan.record_behavior(
    tick=current_tick,
    action_type=result.action_type,   # dialogue / combat / trade / explore / rest
    location=self.state.area.current_location,
    details=result.brief_summary,
)
```

行为窗口保持最近 24 格的记录（约 4 小时游戏时间），滑动丢弃旧记录。NarrativePlanner 运行时直接读取该窗口。

---

## 八、Prompt 模板

```markdown
你是 NarrativePlanner，一个游戏世界的叙事规划器。

你的职责：根据主线故事进度和玩家行为，规划下一步的叙事引导。
你不是在"讲故事"（那是 GM 的事），你是在"安排故事走向"——决定什么任务应该出现、通过什么方式投递给玩家。

## 故事蓝图
{story_blueprint}

## 玩家行为画像
{player_profile}

## 当前叙事计划
{current_plan}

## 世界上下文
{world_context}

## 你的任务

分析当前状况，决定是否需要叙事干预，以及如何干预。

### 核心原则

1. **不偏离主线**：所有生成的任务必须指向某个故事里程碑。不创造与主线无关的支线。
2. **自然融入**：任务的投递方式要自然——通过 NPC 对话、环境事件、路人求助，而不是凭空出现。
3. **尊重玩家节奏**：如果玩家在自主探索且方向正确，不要打断。只在停滞或偏离时干预。
4. **递进不跳跃**：引导应该循序渐进（暗示→推荐→紧迫→危机），不要直接跳到高紧迫度。
5. **适应玩家风格**：根据 play_style_tags 选择投递方式。对话型用 NPC，探索型用环境线索，战斗型用遭遇。
6. **避免重复**：不要重复使用相同的投递方式。如果"公会接待员推荐"已经用过且被忽略，换一种方式。
7. **保持紧凑**：同一时间不超过 2 个活跃动态任务。多了会分散注意力。

### 关于升级等级

当前升级等级：{escalation_level}（基于 ticks_since_progress={ticks_since_progress}）

- L0 (0-3格): 不干预
- L1 (4-6格): 轻微暗示，环境线索
- L2 (7-9格): NPC 主动推荐
- L3 (10-12格): 紧迫事件，路人 NPC 求助
- L4 (13-15格): 世界状态恶化
- L5 (16+格): 最后警告

根据升级等级选择对应强度的干预措施。

### 输出格式

严格输出以下 JSON：
{output_schema}

如果当前不需要干预（玩家方向正确、刚完成重要事件、正在战斗等），输出空 interventions：
{
  "reasoning": "玩家正在朝牧场方向移动，无需干预",
  "strategy_update": "维持当前策略，下次兜底检查时再评估",
  "interventions": []
}
```

---

## 九、与 TickCoordinator 集成

### 9.1 SettlementHook 实现

```python
class NarrativePlannerHook(SettlementHook):
    """叙事规划器结算钩子。P35，AI Osiris 之后。"""

    priority = 35
    name = "narrative_planner"

    FALLBACK_INTERVAL = 6  # 兜底间隔（格数）

    def __init__(self, planner: NarrativePlanner):
        self._planner = planner

    def should_skip(self, change_log: list[StateChange]) -> bool:
        plan_slice = self._get_plan_slice()

        # 条件触发
        if self._has_milestone_change(change_log):
            return False
        if self._has_area_change(change_log):
            return False
        if self._has_relevant_flag_change(change_log):
            return False
        if self._has_quest_expiry(plan_slice):
            return False

        # 升级阈值触发
        if plan_slice.ticks_since_milestone_progress >= self._escalation_threshold(plan_slice):
            return False

        # 兜底
        current_tick = self._get_current_tick()
        if current_tick - plan_slice.last_run_tick >= self.FALLBACK_INTERVAL:
            return False

        return True

    def _escalation_threshold(self, plan: NarrativePlanSlice) -> int:
        """下一个升级等级的阈值"""
        thresholds = [4, 7, 10, 13, 16]  # L1, L2, L3, L4, L5
        level = plan.escalation_level
        if level < len(thresholds):
            return thresholds[level]
        return self.FALLBACK_INTERVAL  # L5 之后每 6 格检查一次

    async def execute(self, change_log, state, world, rules_engine, scene_bus) -> HookResult:
        result = await self._planner.evaluate(
            state=state,
            world=world,
            change_log=change_log,
        )
        return HookResult(
            commands=result.commands,
            sse_events=result.sse_events,
        )
```

### 9.2 更新后的结算链

```
tick_settlement()
    │
    ├─ P10: ScheduledEventHook       ← 延时事件检查
    ├─ P20: StatusEffectHook         ← 状态效果 tick
    ├─ P30: AIOsirisHook ★           ← 反应式因果推理
    ├─ P35: NarrativePlannerHook ★   ← 【新增】主动叙事规划
    ├─ P40: EncounterHook            ← 遭遇检定
    ├─ P50: EventConditionHook       ← 事件条件检查
    ├─ P60: NpcScheduleHook          ← NPC 日程更新
    ├─ P70: TimeAdvanceHook          ← 时间推进
    ├─ P80: GmNarrationHook          ← GM 叙述
    └─ P90: SceneBusResetHook        ← SceneBus 重置
```

**P35 排在 P30 之后的理由**：NarrativePlanner 需要看到 AI Osiris 本格的输出——Osiris 可能设了新 flag 或推进了任务，这些信息影响 NarrativePlanner 的判断。

### 9.3 与后续 Hook 的联动

NarrativePlanner 的 `spawn_quest_npc` 和 `plant_environmental` 指令可能影响后续 Hook：

| NarrativePlanner 指令 | 影响的后续 Hook | 具体影响 |
|----------------------|----------------|---------|
| `escalate` (danger +) | P40 EncounterHook | 遭遇概率增加 |
| `escalate` (flag set) | P50 EventConditionHook | 可能触发新事件 |
| `direct_npc` | P60 NpcScheduleHook | NPC 行为调整 |
| 所有干预 | P80 GmNarrationHook | GM 可能需要叙述环境变化 |

---

## 十、性能与成本

| 指标 | 估算 |
|------|------|
| 调用频率 | 平均每 6 格一次（兜底），活跃引导期可能每 3-4 格 |
| 输入 token | ~1500-2500（蓝图 + 画像 + 计划 + 上下文） |
| 输出 token | ~300-800（reasoning + interventions） |
| 模型选择 | Flash + medium thinking（需要创造性但不需要深度推理） |
| 空跑率 | 预计 40-60% 的运行输出空 interventions（玩家方向正确时） |
| 同时活跃任务上限 | 2 个（prompt 约束） |

---

## 十一、与现有系统的关系

| 现有组件 | 关系 |
|---------|------|
| QuestRegistry（内容层） | **演化**：从详细任务模板 → 故事里程碑。NarrativePlanner 是其唯一消费者 |
| QuestSlice（状态层） | **扩展**：跟踪里程碑状态 + 动态任务状态 |
| EventEngine / EventSlice | **复用**：动态任务的目标完成判定通过 EventEngine 条件检查 |
| PasserbyService | **复用**：`spawn_quest_npc` 底层调用路人生成系统 |
| InstanceManager | **复用**：`direct_npc` 写入 NPC 工作记忆 |
| AI Osiris | **姊妹组件**：同为格结算 Hook，Osiris 反应、Planner 规划 |
| GM Agent | **下游消费者**：NarrativePlanner 的环境变更由 GM 叙述 |

---

## 十二、设计决策记录

| # | 问题 | 决策 |
|---|------|------|
| Q1 | QuestRegistry 定位？ | **故事里程碑图**：只存主线节点（"价值"），中间任务全由 NarrativePlanner 动态生成（"价格"） |
| Q2 | 触发频率？ | **条件触发 + 兜底**：里程碑变化/区域变化/停滞检测时触发，每 6 格兜底一次 |
| Q3 | 任务投递方式？ | **多种方式**：直接发布、路人 NPC（复用 passerby 系统）、现有 NPC 指令、公告板、环境线索 |
| Q4 | 有无状态？ | **有状态**：NarrativePlanSlice 新增切片，跟踪规划策略、活跃任务、升级等级、玩家画像 |
| Q5 | thinking level？ | **medium**：需要创造性（生成任务内容）但不需要深度推理 |

---

## 补遗：增量实现中确立的 Planner 消费链路（2026-03-05 追记）

### A. LOCKED→AVAILABLE 转换执行者

设计中描述了 prerequisites 图但未指定转换执行者。实现中由 `MilestoneUnlockHook`（P25，在 NarrativePlannerHook P35 之前）负责检查 + 状态转换 + 播种新事件条件。

### B. Planner 上下文注入里程碑细节

`_build_planner_context()` 新增 `target_milestone_detail`，将当前目标里程碑的 key_elements / involved_npcs / involved_locations / failure_fallback 注入 planner 决策上下文和 LLM prompt。

### C. MilestoneTemplate 字段消费

- `involved_npcs` — 确定性 planner L2/L3 优先选择相关 NPC 投递指令
- `key_elements` — L3 create_quest payload 携带叙事要素
- `failure_fallback` — 里程碑 FAILED 时通过 SSE `milestone_failed` 事件暴露

---

## 变更日志

| 日期 | 变更 |
|------|------|
| 2026-03-05 | 补遗 A-C：MilestoneUnlockHook + 里程碑细节注入 + MilestoneTemplate 字段消费 |
| 2026-02-26 | 创建。"价值与价格"核心理念 + QuestRegistry 演化为故事里程碑 + NarrativePlanner 动态任务生成 + 7 种指令类型 + 递进升级策略（L0-L5）+ 触发条件（条件+兜底）+ NarrativePlanSlice + Prompt 模板 + TickCoordinator P35 集成 |
