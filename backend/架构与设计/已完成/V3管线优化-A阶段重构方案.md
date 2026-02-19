# V3 管线优化：A 阶段重构方案

## 背景与动机

当前 V3 管线的 A 阶段（预处理）存在四个核心问题：

1. **数据注入不足** — 地点详情、NPC 完整档案、怪物/物品/技能数据在 Firestore 中可用，但未注入 LLM 上下文
2. **不必要的预判断** — StoryDirector + ConditionEngine 在 A 阶段做机械评估，实际数据分析显示条件极为简单（详见数据发现），直接告诉 LLM 即可
3. **机械种子低效** — 扩散激活的种子构建仅用 location_id + active_npc，忽略玩家输入中的语义关键词
4. **图谱职责混乱** — prefilled_graph.json 将静态世界知识（角色、地点、概念 ~1,264 节点）和动态游戏记忆混在同一个图谱中，静态数据重复存储且检索效率低于直接注入

### 核心原则

- **A 阶段只做数据收集和打包，不做任何判断。** 所有智能决策交给 B 阶段。
- **静态世界数据按章节直接注入，** 不经过图谱。
- **图谱专注动态游戏记忆，** 运行时生长，不预填充。

---

## 数据发现

### chapters_v2.json 条件分布（82 章 323 事件）

| 条件类型 | 使用次数 | 占比 | 说明 |
|---------|---------|------|------|
| `EVENT_TRIGGERED` | 320 | 79.4% | 查「上一个事件是否已触发」 |
| `LOCATION` | 54 | 13.4% | 查「玩家是否在某地点」 |
| `NPC_INTERACTED` | 29 | 7.2% | 查「是否与某 NPC 交互过」 |
| 其余 6 种条件类型 | 0 | 0% | TIME_PASSED / ROUNDS_ELAPSED / FLASH_EVALUATE / PARTY_CONTAINS / OBJECTIVE_COMPLETED / GAME_STATE 均未使用 |

- 99.4% 事件为 `is_required=true`
- 0 个事件有 `side_effects`
- 所有章节转换为单出口（无分支）
- 本质是**线性叙事链**：事件1 → 事件2 → 事件3 → 章节结束 → 下一章

### 当前未注入的可用数据

| 数据 | 来源文件 | 运行时来源 | 现状 |
|------|---------|-----------|------|
| 地点描述/氛围/危险等级/可执行操作/核心特征 | maps.json | Firestore `maps/{mid}` | 有但未传给 LLM |
| 子地点完整信息（描述、交互类型、驻留NPC） | maps.json | Firestore `maps/{mid}` | 只传了 id+name |
| NPC 完整档案（外貌/背景/关系/说话风格） | characters.json | Firestore `characters/{cid}` | 只传了 id+name+简述 |
| 怪物数据（D&D 属性/技能/掉落/三防） | monsters.json | Firestore `combat_entities/monsters` | 完全缺席 |
| 技能数据库 | skills.json | Firestore `combat_entities/skills` | 完全缺席 |
| 物品数据库 | items.json | Firestore `combat_entities/items` | 完全缺席 |
| 导航门控条件（等级/物品/任务要求） | maps.json `connections[].requirements` | Firestore | 内部校验但未暴露给 LLM |
| NPC 年龄、示例对话 | characters.json | Firestore | 加载但未注入 agentic 上下文 |

---

## 数据过滤层级

章节是数据过滤的顶层入口。`chapters_v2.json` 每个章节的 `available_areas` 字段定义了该章节可用的地图区域：

```
当前章节 (chapters_v2.json)
  │ available_areas: ["frontier_town", "whispering_woods"]
  │
  ├─→ 地图过滤 (maps.json)
  │     只加载这两张地图的地点/子地点/连接
  │
  ├─→ NPC 过滤 (characters.json)
  │     map_assignments["frontier_town"] + ["whispering_woods"]
  │     → 再按当前子地点 resident_npcs 缩小范围
  │
  ├─→ 怪物过滤 (monsters.json)
  │     当前地点 danger_level → challenge_rating 映射
  │
  └─→ 技能过滤 (skills.json)
        玩家 + 队友职业 → source 字段匹配
```

不支持按章节过滤的数据：
- **怪物** — 无章节/地点标签，用 danger_level 粗滤
- **物品** — 无章节标签，全量注入（~20 条）
- **技能** — 无章节标签，按职业过滤

---

## 新 A 阶段数据组装方案

### 数据包 ①：地点信息（章节过滤 + 全量增强）

**来源**：Firestore（初始化自 `maps.json`）
**范围**：当前章节 `available_areas` 内的当前地点 + 当前子地点

```
location: {
  // 现有字段
  location_id, name, npcs_present, available_destinations,

  // 新增字段
  description,              // 地点完整描述（含历史、地理）
  atmosphere,               // 氛围基调，如 "喧闹、充满生活气息"
  danger_level,             // low / medium / high / extreme
  available_actions,        // 如 ["购物", "休息", "接任务", "情报收集"]
  key_features,             // 如 ["冒险者公会", "碎盾工坊", "地母神神殿"]

  // 增强字段
  available_destinations: [
    {
      target_map_id, name, travel_time, connection_type,
      requirements: {              // 非空时才注入
        min_level,                 // 可选
        required_items: [],        // 可选
        required_quests: []        // 可选
      }
    }
  ],
  sub_locations: [
    {
      id, name,
      description,          // 如 "城镇的心脏，冒险者们接取委托的地方"
      interaction_type,     // quest / rest / shop / visit
      resident_npcs,        // 该子地点驻留的 NPC ID 列表
      available_actions,    // 如 ["接取委托", "提交报告"]
      passerby_spawn_rate   // 路人生成概率 0.0-1.0
    }
  ],
  current_sub_location: { ... }  // 如果玩家在子地点内，给出完整信息
}
```

**示例（frontier_town 的 guild_hall 子地点）**：
```json
{
  "id": "guild_hall",
  "name": "冒险者公会大厅",
  "description": "城镇的心脏，冒险者们接取委托、结算报酬和交流情报的地方。空气中弥漫着羊皮纸和墨水的味道。",
  "interaction_type": "quest",
  "resident_npcs": ["guild_girl", "inspector", "goblin_slayer", "spearman", "heavy_warrior"],
  "available_actions": ["接取委托", "提交报告", "寻找队友", "咨询监察官"],
  "passerby_spawn_rate": 0.8
}
```

### 数据包 ②：NPC 信息（章节 + 场景双重过滤，跳过 null 字段）

**来源**：Firestore（初始化自 `characters.json`，131 角色：7 main + 124 secondary）
**过滤逻辑**（三级漏斗）：
1. 当前章节 `available_areas` → 过滤 `map_assignments`，得到章节 NPC 池
2. 玩家在子地点 → 取该子地点的 `resident_npcs` 列表
3. 玩家在地图主区域 → 取整个地图的 `map_assignments`
- 只注入非 null 字段

```
npcs_in_scene: [
  {
    id, name, tier, importance,
    age,                  // 如 15（数字）
    occupation,           // 如 "公会职员"
    personality,          // 如 "专业干练，温柔负责"
    speech_pattern,       // 如 "职业化的礼貌与微笑"
    example_dialogue,     // 仅 main 层级注入，如 "……（沉默地点头）"
    appearance,           // 如 "栗色长发盘成辫子"（非 null 时）
    backstory,            // 如 "出身小贵族，在边境公会工作"（非 null 时）
    relationships: {},    // 如 {"goblin_slayer": "爱慕对象"}（非空时）
    tags,                 // 如 ["公会", "后勤", "暗恋者"]
    aliases               // 如 ["公会职员"]
  }
]
```

**关键决策**：很多 secondary 角色字段大量为 null（如 `boatmans_daughter` 只有 backstory）。对这类角色只注入有值的字段，避免无意义的 null 噪声。`example_dialogue` 仅对 main 层级 NPC 注入（数量少、重要性高、GM 叙事中更可能引用其声音）。

### 数据包 ③：战斗实体（按场景过滤）

#### 怪物：按 danger_level ↔ challenge_rating 映射

```
地点 danger_level    →    可注入的怪物 challenge_rating
────────────────────────────────────────────────────
low                  →    白瓷
medium               →    白瓷, 黄金
high                 →    白瓷, 黄金, 白银, 钢铁
extreme              →    全部
```

每个怪物注入字段：id, name, type, challenge_rating, description, stats (hp/ac/str/dex/con/int/wis/cha), skills, special_abilities, attacks (name + damage), loot, resistances, vulnerabilities, immunities

**注意**：monsters.json 没有地点标签字段，只能通过 danger_level 粗略过滤。怪物总共约 20 种生态，过滤后通常 5-10 种，数据量可控。

#### 技能：按玩家/队友职业过滤

skills.json 的 `source` 字段标明来源职业（如 `"神官职业"`, `"魔术师(真言咒文)"`）。按玩家 `character_class` + 队友职业匹配过滤。

每个技能注入：id, name, tier, type, school, source, description, effect, cost, range

#### 物品：全量注入

约 20 条，数据量很小，全量作为参考数据库注入。

每个物品注入：id, name, type, subtype, description, properties (ac_bonus/damage/weight/price), effects, rarity

### 数据包 ④：章节/事件进度（当前章 + 下一章预览 + 卷级概述）

**取消 `StoryDirector.pre_evaluate()`，改为直接打包原始数据给 LLM。**

#### 卷级叙事概述

```
current_mainline: {
  id: "vol_1",
  name: "📖第一卷",
  description: "..."    // 卷级叙事方向概述（200-400 token）
}
```

#### 当前活跃章节（完整数据）

```
current_chapter: {
  id, name, mainline_id,
  objectives,                    // 章节目标列表
  events: [
    {
      id, name,
      trigger_conditions,        // 原始条件 JSON（如 {operator: "and", conditions: [{type: "event_triggered", params: {event_id: "ch_1_2_event_3"}}, {type: "location", params: {area_id: "frontier_town"}}]}）
      narrative_directive,       // 叙事指令（如 "描述哥布林杀手默默接下委托的场景"）
      is_required,
      status: "triggered" | "pending"   // 结合 progress.events_triggered 计算
    }
  ],
  transitions: [
    {
      target_chapter_id,
      conditions,                // 转换条件 JSON
      transition_type,           // normal / branch / failure
      narrative_hint             // 过渡叙事提示
    }
  ],
  pacing: {
    min_rounds, ideal_rounds, max_rounds,
    stall_threshold,
    hint_escalation              // ["subtle_environmental", "npc_reminder", "direct_prompt"]
  }
}
```

#### 进度状态

```
progress: {
  events_triggered: [...],               // 已触发的事件 ID 列表
  rounds_in_chapter,                     // 当前章节已过回合数
  rounds_since_last_progress,            // 距上次进展的回合数
  npc_interactions: { npc_id: count }    // NPC 交互计数
}
```

> **注意**：不计算 `next_pending_events`。所有 pending 事件已在 `current_chapter.events` 中标记 `status: "pending"`，由 LLM 自行判断哪个接近触发。保持 A 阶段零判断。

#### 下一章预览

```
next_chapter_preview: {
  id, name,
  narrative_hint,       // 来自当前章节 transition.narrative_hint
  available_areas       // 新章节解锁的地图
}
```

### 数据包 ⑤：基础状态（现有保留 + 增强）

- `player_character`：角色属性、装备、背包、等级
- `party`：队友列表（character_id, name, role, personality, current_mood）
- `time`：day, hour, minute, period, formatted
- `state`："exploring" | "in_dialogue" | "combat"
- `chat_mode`："think"（内心独白，队友听不到）| "say"（公开发言）
- `conversation_history`：最近对话记录（受 token 预算控制）
- `world_background`：世界观概述
- `world_geography`：当前区域 + 相邻可达区域（来自 world_map.json）
  ```
  world_geography: {
    current_region: { name: "边境地区", danger_level: "medium" },
    nearby_regions: [
      { name: "西方未知境", danger_level: "high", route: "frontier_town → elf_forest" }
    ]
  }
  ```

### 数据包 ⑥：NPC 好感度系统（Dispositions）

**来源**：Firestore `worlds/{wid}/characters/{cid}/dispositions/{target_id}`
**接口**：`graph_store.get_disposition()` / `graph_store.get_all_dispositions()`

#### 数据结构

```
dispositions: {
  "npc_id": {
    approval: -100 ~ +100,    // 认可度（-100 厌恶 → +100 敬仰）
    trust: -100 ~ +100,       // 信任度（-100 猜忌 → +100 完全信赖）
    fear: 0 ~ 100,            // 恐惧度（0 无惧 → 100 极度恐惧）
    romance: 0 ~ 100,         // 浪漫值（0 无感 → 100 深爱）
    last_updated: datetime,
    recent_changes: [          // 最近的变化记录（从 history 裁剪）
      { reason: "玩家救了她的朋友", delta: {approval: +15}, game_day: 3 }
    ]
  }
}
```

#### 过滤与注入规则

- **只注入当前场景在场 NPC 对玩家的好感度**（与数据包②的 NPC 列表对齐）
- **只注入有变化的关系**：如果四个维度都是默认值（0），跳过该 NPC
- **history 裁剪**：只取最近 3-5 条变化记录，避免 token 膨胀
- **队友之间的好感度**：如果有队伍，额外注入队友对玩家的好感度

#### 好感度影响规则（待细化为独立子系统）

好感度数据注入后，需要配合 B 阶段的系统提示词，让 LLM 理解如何使用：

| 维度 | 影响范围 | 示例 |
|------|---------|------|
| `approval` | NPC 对话态度、是否愿意帮忙 | approval > 50 → 主动提供线索 |
| `trust` | 是否透露秘密、是否把后背交给玩家 | trust < -30 → 拒绝共享敏感信息 |
| `fear` | 是否服从、是否回避 | fear > 70 → 不敢拒绝玩家要求 |
| `romance` | 对话亲密度、特殊互动触发 | romance > 60 → 对话中流露感情 |

---

## 图谱系统重新定位

### 职责分离：静态注入 vs 动态图谱

| | 静态世界知识 | 动态游戏记忆 |
|--|------------|------------|
| **内容** | 角色档案、地点、怪物、物品、技能、章节事件 | 对话压缩、玩家选择、关系变化、事件经历 |
| **来源** | structured_new/*.json → Firestore | 游戏运行时 B 阶段产生 |
| **交付方式** | A 阶段数据包①-⑥直接注入 | B 阶段 `recall_memory` 工具（扩散激活检索） |
| **变化频率** | 不变 | 每轮可能增长 |

**prefilled_graph.json 不再在运行时加载。** 其中的静态知识已被数据包覆盖。

### 动态图谱生命周期

```
游戏开始
  ↓
创建锚点节点（轻量 ID，不带完整数据）:
  character_goblin_slayer, character_priestess,
  location_frontier_town, location_guild_hall, ...
  ↓
游戏运行中 — B 阶段 LLM 通过 create_memory 工具写入:
  ├─ 记忆节点: "conv_day1_guild_girl_quest" (对话记忆)
  ├─ 事件节点: "event_ch1_goblin_attack" (剧情事件)
  ├─ 关系边: guild_girl →[discussed_quest]→ conv_node
  ├─ 地点边: conv_node →[happened_at]→ location_guild_hall
  └─ 好感度边: priestess →[approves +15]→ player
  ↓
NPC 上下文窗口溢出 → memory_graphizer 自动压缩入图（已有机制）
  ↓
图谱持续增长，扩散激活召回质量随记忆积累提升
```

### A 阶段扩散激活（可选，图谱非空时执行）

如果动态图谱已有内容，A 阶段可做一次轻量预热：

- **种子**：当前 location_id + active_npc_ids + character_id（机械种子，无需 LLM）
- **图谱**：仅动态游戏记忆（不含静态世界知识，噪声低）
- **结果**：激活的记忆子图作为 `recalled_memories` 注入上下文
- **游戏早期**：图谱为空或极小，此步骤自动跳过，零开销

B 阶段的 `recall_memory` 工具可做更精确的语义召回（LLM 自选种子 + RAG 匹配节点名）。

---

## 被移除的部分

| 移除内容 | 原因 | 替代方式 |
|---------|------|---------|
| `StoryDirector.pre_evaluate()` | 判断逻辑交给 LLM | 章节/事件原始数据直接打包（数据包④） |
| `ConditionEngine` 预评估调用 | 80% 是查 EVENT_TRIGGERED，LLM 看数据即可判断 | 事件标记 triggered/pending 状态 |
| `_build_effective_seeds()` | 机械种子忽略语义 | B 阶段 LLM 通过 recall_memory 工具自选种子 |
| `player_memory_task` 阻塞等待 | 移出 A 阶段 | B 阶段 LLM 按需调用 recall_memory |
| `teammate_prefill_tasks` 预填充 | 移出 A 阶段 | 队友响应阶段或 B 阶段处理 |
| `pending_flash_conditions` 打包 | 不再区分机械/语义条件 | 所有条件作为原始数据交给 LLM |
| `prefilled_graph.json` 运行时加载 | 静态世界知识已由数据包直接注入覆盖 | 图谱专注动态游戏记忆 |
| `next_pending_events` 计算 | 违反零判断原则（需扫描条件排序） | 所有 pending 事件已在 events 中标记状态 |

---

## 新 A 阶段流程

```
玩家输入
    ↓
Wave 1 — 基础状态（无依赖，全并行）：
  ├─ 会话状态 → chapter_id, area_id, location_id
  ├─ 当前章节 + 进度 + 下一章预览 + 卷级概述       ← 数据包④
  └─ 玩家角色 + 队伍 + 对话历史 + 世界背景 + 地理   ← 数据包⑤
    ↓
Wave 2 — 场景数据（依赖 chapter available_areas + location）：
  ├─ 地点完整信息 + 子地点详情                     ← 数据包①
  ├─ 场景内 NPC 完整档案（章节 + 场景双重过滤）     ← 数据包②
  └─ 战斗实体（danger_level + 职业过滤）            ← 数据包③
    ↓
Wave 3 — 关系数据（依赖 NPC 列表）：
  └─ 在场 NPC 好感度数据                           ← 数据包⑥
    ↓
可选：动态图谱扩散激活（若图谱非空，机械种子）
    ↓
组装 context 包 → 交给 B 阶段（Agentic 会话）
```

### 设计约定

- **时间点快照**：A 阶段输出的是快照数据。B 阶段工具调用（如导航后地点变化）获取实时数据，不依赖 A 阶段的旧值。
- **加载延迟**：三波实际为 2 次串行等待（Wave 1 → Wave 2+3），总延迟取决于 Firestore 并行读取速度（通常 200-400ms）。Wave 2 和 Wave 3 之间依赖轻微（只需 NPC ID 列表），可考虑合并为一波并行。

---

## 未完成部分状态（已在 B+C 方案中决议）

> 以下 TODO 的设计决议详见 `V3管线优化-BC阶段重构方案.md` 的「A 阶段遗留 TODO 决议」章节和对应的第六~八部分。

| TODO | 决议 | 详见 |
|------|------|------|
| TODO-1 玩家角色详细状态 | **全量注入** + `SessionRuntime.player` 提供状态变更 API | BC 方案 A 阶段遗留决议 |
| TODO-2 玩家选择与未兑现后果 | **延后**，核心循环不依赖，后期叙事增强 | BC 方案 A 阶段遗留决议 |
| TODO-3 叙事进度完整字段 | **全量注入**，实际运行后按 token 消耗裁剪 | BC 方案 A 阶段遗留决议 |
| TODO-4 营地共享图谱 | **废弃**，折入同伴 `shared_events` | BC 方案第六部分 |
| TODO-5 路人池数据 | **低优先级**，B 阶段工具按需获取 | BC 方案 A 阶段遗留决议 |
| TODO-6 好感度变更子系统 | **已设计**，`update_disposition` 工具 + 护栏 + 参考量表 | BC 方案第八部分 |
