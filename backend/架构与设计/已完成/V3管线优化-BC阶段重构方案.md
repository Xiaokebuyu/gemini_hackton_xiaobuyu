# V3 管线优化：B+C 阶段重构方案

## 背景与动机

当前 V3 管线的 B 阶段（Agentic 会话）和 C 阶段（后处理）存在以下问题：

1. **StoryDirector 两阶段评估冗余** — pre_evaluate（A 阶段）+ post_evaluate（C 阶段）分裂评估同一套条件，80% 是 `EVENT_TRIGGERED` 线性链，完全可以机械检查
2. **AdminCoordinator 职责过重** — 5327 行，同时承担世界模型和处理管线，状态管理散落在 StateManager / PartyService / NarrativeService / WorldRuntime 等多个服务中
3. **无区域概念** — 导航只是扁平地在地图间跳转，没有「进入/离开区域」的生命周期管理，每轮重新拼装上下文
4. **事件系统僵化** — 当前事件全部是章节级线性链，无区域级事件，无支线任务，触发条件类型虽多但实际只用了 3 种
5. **工具校验过度** — enforcement + repair 机制是对上下文不足的补偿，在丰富上下文下不再必要

### 核心原则

- **世界模型和处理管线分离。** Game Runtime 维护状态，Pipeline 读取/修改状态。
- **区域是上下文作用域的基本单元。** 有明确的 load/unload 生命周期。
- **事件条件机械检查，叙事交给 LLM。** Runtime 做条件判断，LLM 做故事表达。
- **章节转换由玩家主动选择，** 不自动触发。

---

## 第一部分：Game Runtime（底层架构）

> Runtime 是 A/B/C 所有阶段的基础。A 阶段的 ContextAssembler 从 Runtime 读取分层数据，B 阶段的工具通过 Runtime API 操作，C 阶段将结果持久化回 Runtime。

### 架构总览

```
┌──────────────────────────────────────────────────────┐
│                    API Layer (Routes)                  │
│            POST /input → Orchestrator.process()       │
└────────────────────────┬─────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────┐
│              PipelineOrchestrator (薄层)               │
│                                                       │
│  A: context = assembler.assemble(session)             │
│  B: result  = agentic_session.run(context, input)     │
│  C: post_processor.run(session, result)               │
└───────┬────────────────┬────────────────┬────────────┘
        │reads           │calls tools     │updates
        ▼                ▼                ▼
┌───────────────────────────────────────────────────────┐
│                  Game Runtime (底层)                    │
│                                                       │
│  WorldInstance ──┬── AreaRuntime (per loaded area)     │
│  (常量+注册表)    │   (生命周期: load → active → unload) │
│                  │                                     │
│                  └── SessionRuntime (per session)      │
│                      (玩家/队伍/时间/叙事/当前区域ref)   │
│                                                       │
│  Persistence: Firestore read/write                    │
└───────────────────────────────────────────────────────┘
```

**与现有代码的映射：**

| 新组件 | 吸收的现有代码 | 变化 |
|--------|---------------|------|
| `WorldInstance` | `AreaNavigator`（地图）+ `NarrativeService`（章节数据）+ 各 Store 初始化 | 统一为世界容器 |
| `AreaRuntime` | **全新** | 现有系统无此概念 |
| `SessionRuntime` | `StateManager` + `PartyService` + `TimeManager` + `GameSessionStore` | 统一为会话运行时 |
| `ContextAssembler` | `AdminCoordinator._build_context()` + `FlashCPU._build_agentic_*_context()` | 机械化分层读取 |
| `PipelineOrchestrator` | `AdminCoordinator.process_player_input_v3()` 的流程骨架 | 瘦身为纯管线编排 |

### 模块结构

```
app/
  runtime/                           ← 新: 游戏运行时底层
    __init__.py
    game_runtime.py                  ← 顶层入口 (持有 world + sessions)
    world_instance.py                ← 世界实例 (常量 + 注册表 + 区域管理)
    area_runtime.py                  ← 区域运行时 (生命周期 + 上下文)
    session_runtime.py               ← 会话运行时 (玩家状态 + 区域切换)
    context_assembler.py             ← 分层上下文组装器
    models/
      area_state.py                  ← AreaState, VisitSummary, AreaDefinition
      world_constants.py             ← WorldConstants
      layered_context.py             ← LayeredContext 输出模型

  services/admin/                    ← 现有: 简化
    pipeline_orchestrator.py         ← 原 admin_coordinator 瘦身
    flash_cpu_service.py             ← B 阶段 (接收 LayeredContext)
    agentic_tools.py                 ← 工具通过 Runtime API 操作
```

---

### WorldInstance — 世界的容器

```python
class WorldInstance:
    """一个世界的运行时实例。持有世界级不变数据和注册表。"""

    world_id: str

    # Layer 0: 世界常量 (初始化后不变)
    world_constants: WorldConstants    # 背景、地理、势力
    character_registry: Dict[str, CharacterProfile]  # 全部 131 角色档案
    area_registry: Dict[str, AreaDefinition]   # 全部 10 区域定义
    monster_registry: Dict[str, MonsterData]   # 怪物库 (~20 种)
    item_registry: Dict[str, ItemData]         # 物品库 (~20 种)
    skill_registry: Dict[str, SkillData]       # 技能库 (~120 种)

    # 区域生命周期管理
    loaded_areas: Dict[str, AreaRuntime]

    async def initialize(world_id):
        """从 Firestore 一次性加载全部世界数据"""

    async def load_area(area_id) -> AreaRuntime:
        """加载区域到活跃状态"""

    async def unload_area(area_id, session) -> VisitSummary:
        """卸载区域: 生成摘要 → 持久化 → 释放"""

    def get_characters_in_area(area_id) -> List[CharacterProfile]:
        """根据 default_map 过滤区域内角色"""

    def get_monsters_for_danger(danger_level) -> List[MonsterData]:
        """按危险等级过滤怪物"""

    def get_skills_for_classes(classes) -> List[SkillData]:
        """按职业过滤技能"""
```

**关键**：WorldInstance 是进程级单例。所有静态数据在 `initialize()` 时一次性加载，后续只读。

---

### AreaRuntime — 区域的生命周期

```python
class AreaRuntime:
    """一个已加载区域的运行时。有明确的 load/unload 生命周期。"""

    area_id: str
    definition: AreaDefinition        # 静态定义 (worldbook)

    # 动态状态
    state: AreaState                  # 可变状态 (事件进度、NPC 位置等)
    area_graph: Optional[MemoryGraph] # 区域级动态记忆图谱
    npc_contexts: Dict[str, Any]      # NPC 上下文窗口快照
    events: List[AreaEvent]           # 区域内事件列表 (含状态)

    # 访问历史
    visit_summaries: List[VisitSummary]   # 历次访问摘要
    current_visit_log: List[ActionRecord] # 本次访问的行动记录

    # ── 生命周期 ──

    async def load():
        """
        1. 从 WorldInstance.area_registry 加载静态 AreaDefinition
        2. 从 Firestore 加载 AreaState (上次离开时保存的动态状态)
        3. 从 Firestore 加载 area_graph (区域级动态记忆)
        4. 从 Firestore 加载 visit_summaries (历史摘要)
        5. 从 Firestore 加载 npc_contexts (NPC 上下文快照)
        6. 从 Firestore 加载 events + 状态
        """

    async def unload(session: SessionRuntime) -> VisitSummary:
        """
        1. 根据 current_visit_log 生成本次访问摘要 (LLM 调用)
        2. 追加到 visit_summaries
        3. 持久化 AreaState + area_graph + events 到 Firestore
        4. 持久化 NPC 上下文到 Firestore
        5. 清理内存
        """

    def record_action(action: str):
        """记录玩家行动 (用于离开时生成摘要)"""

    # ── 事件机械检查 ──

    def check_events(session: SessionRuntime) -> List[EventUpdate]:
        """每轮检查事件条件变化 (纯机械, O(n), n<10)"""

    def check_chapter_transition(session: SessionRuntime) -> Optional[TransitionReady]:
        """检查章节转换条件是否满足"""

    # ── 上下文输出 ──

    def get_area_context(world: WorldInstance, session: SessionRuntime) -> dict:
        """输出 Layer 2 上下文"""

    def get_location_context(sub_location_id: str) -> dict:
        """输出 Layer 3 上下文"""
```

**区域 NPC 实例生命周期**：区域 NPC 实例随区域加载/卸载。不立即创建（lazy），玩家交互时才初始化。上下文持久化在区域状态中：

```
Firestore: worlds/{world_id}/areas/{area_id}/npc_contexts/{npc_id}
→ 上下文窗口快照（对话历史 + token 计数）
```

> **同伴不在此列。** 同伴实例住在 SessionRuntime 中，跨区域持续存在。详见第七部分。

---

### SessionRuntime — 玩家会话状态

```python
class SessionRuntime:
    """一个玩家会话的运行时。整合原来散落的状态管理。"""

    session_id: str
    world: WorldInstance              # 反引用世界实例

    # 整合自 StateManager + CharacterStore + PartyService + TimeManager
    player: PlayerState               # HP/XP/等级/属性/装备/背包（全量，含 equipment/inventory/spells/conditions）
    party: PartyState                 # 队伍列表
    time: TimeState                   # day/hour/minute
    narrative: NarrativeProgress      # 章节/事件/进度（全量注入）
    history: ConversationHistory      # 对话历史

    # 同伴实例 (会话级持久, 跨区域)
    companions: Dict[str, CompanionInstance]

    # 核心: 当前区域引用
    current_area: Optional[AreaRuntime]

    # 状态审计
    delta_log: List[StateDelta]

    # ── 区域切换 (核心方法) ──

    async def enter_area(area_id: str) -> AreaTransitionResult:
        """
        1. 验证 chapter.available_areas 包含 area_id
        2. 验证 current_area.connections 包含目标
        3. 计算旅行时间
        4. unload 当前区域 (生成摘要, 持久化)
        5. load 目标区域
        6. 推进游戏时间 (旅行耗时)
        7. 更新 player location
        8. 返回 AreaTransitionResult (用于旅行叙事)
        """

    async def enter_sublocation(sub_id: str):
        """在当前区域内进入子地点"""

    async def leave_sublocation():
        """离开当前子地点回到区域主地图"""

    # ── 持久化 ──

    async def persist():
        """每轮 C 阶段结束时: 将全部状态写入 Firestore"""

    async def restore(session_id: str):
        """从 Firestore 恢复会话 (会话恢复)"""
```

---

### 分层上下文模型

```
┌────────────────────────────────────────────────────┐
│  Layer 0: 世界常量 (World Constants)                │
│  世界背景、地理概况、主要势力                         │
│  缓存: 进程级，永不变                                │
├────────────────────────────────────────────────────┤
│  Layer 1: 章节作用域 (Chapter Scope)                │
│  章节目标、事件链、转换条件、卷级概述                  │
│  缓存: 章节级，章节切换时更新                         │
├────────────────────────────────────────────────────┤
│  Layer 2: 区域作用域 (Area Scope)                   │
│  区域描述/氛围/danger_level                          │
│  区域内NPC完整档案、子地点列表、区域连接               │
│  怪物/技能 (按danger_level+职业过滤)                 │
│  区域事件状态、历次访问摘要                           │
│  缓存: 区域级，进入区域时加载，离开时卸载+持久化       │
├────────────────────────────────────────────────────┤
│  Layer 3: 地点作用域 (Location Scope)               │
│  子地点详情、驻留NPC、交互类型                        │
│  缓存: 无需独立缓存，从 Layer 2 切片                 │
├────────────────────────────────────────────────────┤
│  Layer 4: 动态状态 (Dynamic State)                  │
│  玩家状态、队伍、时间、对话历史、好感度                │
│  缓存: 无，每轮实时                                  │
├────────────────────────────────────────────────────┤
│  Memory: 动态图谱召回 (可选)                         │
│  区域级 + 角色级扩散激活结果                          │
│  缓存: 无，按需召回                                  │
└────────────────────────────────────────────────────┘
```

**Token 预算估算**：

| 层级 | 内容 | 估算 token |
|------|------|-----------|
| Layer 0 | 世界背景 | ~2K |
| Layer 1 | 章节信息 | ~1-2K |
| Layer 2 | 区域全量 (NPC + 怪物 + 子地点 + 事件) | ~4-8K |
| Layer 3 | 子地点详情 | ~0.5-1K |
| Layer 4 | 动态状态 | ~2-4K |
| Memory | 图谱召回 | ~0-2K |
| **总计** | | **~10-16K** |

### ContextAssembler — A 阶段的实现

A 阶段方案中定义的 6 个数据包在新架构中的来源：

| 数据包 | 分层来源 |
|--------|---------|
| ① 地点信息 | Layer 2: `area_runtime.get_area_context()` + Layer 3 |
| ② NPC 信息 | Layer 2: `world.get_characters_in_area(area_id)` |
| ③ 战斗实体 | Layer 2: `world.get_monsters_for_danger()` + 物品/技能注册表 |
| ④ 章节/事件 | Layer 1: `session.narrative.get_chapter_context()` + 区域事件 |
| ⑤ 基础状态 | Layer 4: `session.get_dynamic_state()` |
| ⑥ 好感度 | Layer 4: 动态状态的一部分 |

```python
class ContextAssembler:
    """纯机械的分层上下文组装。无副作用，无判断逻辑。"""

    @staticmethod
    def assemble(world: WorldInstance, session: SessionRuntime) -> LayeredContext:
        return LayeredContext(
            world=world.world_constants.to_context(),
            chapter=session.narrative.get_chapter_context(),
            area=session.current_area.get_area_context(world, session),
            location=session.current_area.get_location_context(
                session.player.sub_location
            ) if session.player.sub_location else None,
            dynamic=session.get_dynamic_state(),
            memory=session.current_area.recall_if_graph_exists(session),
        )
```

---

## 第二部分：事件系统重设计

### 现有事件系统的问题

1. **事件只有章节级**，无区域维度 — 所有事件挂在 chapter 下
2. **80% 条件是 EVENT_TRIGGERED**（线性链），条件类型虽多但实际几乎不用
3. **0% 使用 FLASH_EVALUATE** — 语义条件设计了但没用上
4. **0 个有 side_effects** — 完成效果字段为空
5. **StoryDirector 两阶段评估** — pre + post 拆开评估同一套条件，设计复杂但收益低

### 解决方案：事件生成框架

**核心思路**：在世界初始化管线中增加一步，由 LLM 根据世界书为每个区域生成结构化事件。

```
世界初始化管线 (init_world_cli):
  1. 提取世界书 → maps, characters, chapters          (现有)
  2. 提取 D&D 实体 → monsters, items, skills           (现有)
  3. 生成区域事件 → area_events.json                   (新增)
  4. 增强章节转换 → chapter_transitions.json            (增强)
```

#### 生成输入（per area + chapter）

```
LLM 接收:
  - 区域定义 (描述、氛围、danger_level、子地点)
  - 区域内 NPC 档案 (性格、背景、关系)
  - 当前章节叙事目标和背景
  - 世界观背景
```

#### 生成输出（结构化 JSON）

```json
{
  "id": "frontier_town_ev_01",
  "area_id": "frontier_town",
  "chapter_id": "ch_1_1",
  "name": "公会柜台的初次登记",
  "description": "在冒险者公会领取白瓷等级牌，正式成为冒险者。",
  "importance": "main",

  "narrative_directive": "描述公会内喧闹的氛围，柜台女孩职业化的笑容...",

  "trigger_conditions": {
    "operator": "and",
    "conditions": [
      { "type": "LOCATION", "params": { "sub_location": "guild_hall" } },
      { "type": "NPC_INTERACTED", "params": { "npc_id": "guild_girl", "min": 1 } }
    ]
  },

  "completion_conditions": {
    "operator": "and",
    "conditions": [
      { "type": "NPC_INTERACTED", "params": { "npc_id": "guild_girl", "min": 2 } }
    ]
  },

  "on_complete": {
    "unlock_events": ["frontier_town_ev_02"],
    "add_items": [{ "id": "white_porcelain_tag", "name": "白瓷等级牌" }],
    "add_xp": 50,
    "narrative_hint": "公会柜台女孩递来了冰冷的白瓷牌..."
  }
}
```

#### 生成规则（写入 LLM 生成提示词）

```
每个区域 + 章节组合生成:
  - 2-5 个 main 事件 (主线推进链)
  - 1-3 个 side 事件 (支线任务，独立触发)
  - 0-2 个 ambient 事件 (氛围事件，时间/位置触发)

main 事件形成依赖链但不只用 EVENT_TRIGGERED，混合使用:
  - LOCATION (在特定子地点)
  - NPC_INTERACTED (和特定 NPC 交流 N 次)
  - TIME_PASSED (时间推进到特定点)
  - PARTY_CONTAINS (队伍有特定角色)
  - EVENT_TRIGGERED (前置事件完成)

side 事件独立于主线，有各自的触发和完成条件。
ambient 事件主要用时间/位置触发，提供氛围。

on_complete 必须包含具体效果:
  - unlock_events: 解锁后续事件
  - add_items / add_xp: 奖励
  - narrative_hint: 完成时的叙事线索
```

### 事件运行时模型

#### 事件生命周期

```
LOCKED ──(trigger_conditions 满足)──→ AVAILABLE ──(LLM 叙述触发)──→ ACTIVE
                                                                      │
                                          (completion_conditions 满足) │
                                                                      ▼
                                                                  COMPLETED
                                                                      │
                                                          (on_complete 执行)
                                                          ├─ unlock_events
                                                          ├─ add_items
                                                          ├─ add_xp
                                                          └─ narrative_hint
```

**状态说明**：

| 状态 | 含义 | 谁负责推进 |
|------|------|-----------|
| `LOCKED` | 前置条件未满足 | Runtime 自动检查 |
| `AVAILABLE` | 条件已满足，等待 LLM 在叙事中引入 | Runtime → 注入上下文 → LLM 叙述 |
| `ACTIVE` | LLM 已在叙事中引入此事件 | LLM 调用 `activate_event(event_id)` |
| `COMPLETED` | 完成条件满足 | Runtime 自动检查 + 执行 on_complete |

#### 机械条件检查（Runtime 层）

```python
def check_events(self, session: SessionRuntime) -> List[EventUpdate]:
    """每轮机械检查事件状态变化。由 Runtime 调用，非 LLM 调用。"""
    updates = []
    for event in self.events:
        if event.status == "completed":
            continue

        if event.status == "locked":
            if self._evaluate_conditions(event.trigger_conditions, session):
                event.status = "available"
                updates.append(EventUpdate(event, "locked→available"))

        if event.status == "active":
            if self._evaluate_conditions(event.completion_conditions, session):
                event.status = "completed"
                self._apply_on_complete(event.on_complete, session)
                updates.append(EventUpdate(event, "active→completed"))

    return updates
```

**8 种结构化条件全部机械检查**：

| 条件类型 | 检查逻辑 | 数据来源 |
|---------|---------|---------|
| `EVENT_TRIGGERED` | `event_id in progress.events_triggered` | NarrativeProgress |
| `LOCATION` | `session.area_id == area_id` | SessionRuntime |
| `NPC_INTERACTED` | `interactions[npc_id] >= min` | NarrativeProgress |
| `TIME_PASSED` | `time.day >= min_day` | SessionRuntime.time |
| `ROUNDS_ELAPSED` | `rounds in [min, max]` | NarrativeProgress |
| `PARTY_CONTAINS` | `character_id in party` | SessionRuntime.party |
| `GAME_STATE` | `state == expected` | SessionRuntime |
| `OBJECTIVE_COMPLETED` | `obj_id in completed` | NarrativeProgress |

`FLASH_EVALUATE` 类型：当前数据中 0 个使用。如果未来生成框架产出了语义条件，可在 B 阶段通过一个轻量工具 `evaluate_condition(condition_id, result)` 由 LLM 判定。**暂不实现。**

#### 事件状态注入上下文

事件检查结果注入到 B 阶段上下文中：

```json
{
  "area_events": {
    "available": [
      {
        "id": "frontier_town_ev_02",
        "name": "轻率的组队",
        "narrative_directive": "体现队伍初次组队的热情...",
        "completion_conditions": { "..." }
      }
    ],
    "active": [
      {
        "id": "frontier_town_ev_01",
        "name": "公会柜台的初次登记",
        "completion_conditions": { "..." },
        "on_complete": { "..." }
      }
    ],
    "recently_completed": [
      {
        "id": "frontier_town_side_01",
        "name": "铁匠的烦恼",
        "on_complete": { "narrative_hint": "..." }
      }
    ]
  }
}
```

LLM 看到 `available` 事件 → 在叙述中自然引入。看到 `recently_completed` → 描述完成效果。

---

### 章节转换

章节转换借鉴事件思路，但由**玩家主动选择进入**。

```json
{
  "from_chapter": "ch_1_1",
  "to_chapter": "ch_1_2",
  "conditions": {
    "operator": "and",
    "conditions": [
      { "type": "EVENT_TRIGGERED", "params": { "event_id": "frontier_town_ev_03" } },
      { "type": "EVENT_TRIGGERED", "params": { "event_id": "frontier_town_ev_04" } }
    ]
  },
  "player_choice": true,
  "narrative_hint": "你感觉到边境小镇的事务已经告一段落，通往水之都的道路已经开启...",
  "unlocks": {
    "areas": ["water_town"],
    "chapters": ["ch_1_2"]
  }
}
```

**运行时流程**：

1. Runtime 每轮检查转换条件（机械，O(1)）
2. 条件满足 → 在上下文中标记 `chapter_transition_available: true` + narrative_hint
3. LLM 在叙述中自然引导（不强制）
4. LLM 生成选项：`[选项] - 前往水之都 / - 继续探索边境小镇`
5. 玩家选择 → LLM 调用 Runtime API 触发章节切换
6. Runtime 执行：解锁新区域/章节 → 更新 NarrativeProgress

---

## 第三部分：B 阶段（Agentic 会话）

### 整体变化

| 方面 | 当前 | 新方案 |
|------|------|--------|
| 上下文来源 | `_build_context()` ad-hoc 拼装 | ContextAssembler 分层读取 |
| 工具执行 | 通过 FlashOperation 委托 FlashCPU | 通过 Runtime API 执行 |
| 事件判断 | LLM 通过 `evaluate_story_conditions` | Runtime 机械检查 + LLM 叙述引入 |
| 记忆召回 | A 阶段预填充 + B 阶段工具 | 仅 B 阶段工具 (按需) |
| 工具校验 | enforcement + repair | 移除，改为日志监控 |
| 故事导演 | StoryDirector pre/post evaluate | 移除，Runtime 事件检查替代 |

### 工具集方向

> **工具的具体设计推迟到 Runtime 实现时。** 工具 = Runtime API 的投影。以下是方向性描述。

**保留并改造的工具**：

| 工具 | 变化 |
|------|------|
| `navigate(area_id)` | 通过 `session.enter_area()` 执行，触发完整区域切换 |
| `enter_sublocation(sub_id)` | 通过 `session.enter_sublocation()` 执行 |
| `npc_dialogue(npc_id, message)` | 通过 AreaRuntime 的 NPC 实例执行 |
| `recall_memory(seeds)` | 保留，查询动态图谱 |
| `start_combat(enemies)` | 保留 |
| `update_time(minutes)` | 保留 |
| `heal_player / damage_player / add_xp / add_item / remove_item` | 通过 SessionRuntime 执行 |
| `ability_check(skill, dc)` | 保留 |
| `generate_scene_image(description)` | 保留 |
| `trigger_narrative_event(event_id)` | 改名为 `activate_event`，将事件从 available → active |

**可能移除的工具**：

| 工具 | 原因 |
|------|------|
| `evaluate_story_conditions` | Runtime 机械检查替代，FLASH_EVALUATE 暂不实现 |
| `get_progress` | 进度信息已在上下文 Layer 1 中 |
| `get_status` | 状态信息已在上下文 Layer 4 中 |
| `get_combat_options` | 可合并到战斗流程中 |

**可能新增的工具**：

| 工具 | 说明 |
|------|------|
| `activate_event(event_id)` | 将 available 事件标记为 active (LLM 在叙述中引入事件时调用) |
| `complete_event(event_id)` | 显式完成事件 (大部分由 Runtime 自动完成，少数需 LLM 主动标记) |
| `advance_chapter(target_chapter_id)` | 玩家选择进入下一章时调用 |
| `create_memory(...)` | 动态图谱写入 — **待设计** |
| `update_disposition(npc_id, changes)` | 好感度变更 — **待设计** |

### 区域切换的工具交互

当 `navigate` 触发区域切换时，工具返回新区域的上下文，LLM 基于此生成到达叙述：

```json
// navigate("water_town") 返回:
{
  "success": true,
  "transition": {
    "from_area": "frontier_town",
    "to_area": "water_town",
    "travel_time_minutes": 480,
    "visit_summary": "在边境小镇接取了讨伐任务，与女神官组队..."
  },
  "new_area_context": {
    "name": "水之都",
    "description": "壮丽的运河城市...",
    "atmosphere": "庄严而繁华",
    "npcs_present": ["sword_maiden", "..."],
    "sub_locations": ["temple_supreme_god", "bath_house"],
    "visit_history": ["上次来过，拜访了剑之圣女..."]
  }
}
```

### 工具校验 → 移除

**移除 `agentic_enforcement.py` 的 enforcement + repair 机制。**

原因：
- 新架构上下文丰富，LLM 有足够信息做出正确工具选择
- Runtime 层做合法性校验（navigate 只能去 connections 中的目标），工具不合法时返回 error
- enforcement 是对上下文不足的补偿，不再需要

**替代方案**：轻量日志监控，记录工具调用和推断意图的匹配度，用于调试和质量分析，不干预 LLM 行为。

### 系统提示词方向

当前 `flash_agentic_system.md`（245 行）需要重写，适配：

1. **分层上下文字段说明** — Layer 0-4 + Memory，替代当前的扁平字段列表
2. **事件系统指导** — 看到 `available` 事件如何在叙述中引入，何时调用 `activate_event`
3. **章节转换指导** — 看到 `chapter_transition_available` 时如何呈现选择
4. **区域感知** — 理解区域边界，NPC 只知道本区域的事
5. **移除的部分** — 去掉 `pending_flash_conditions`、`story_directives`、`story_pacing` 相关指导

> 具体提示词内容在实现时编写。

---

## 第四部分：C 阶段（后处理）

### 整体变化

| 方面 | 当前 | 新方案 |
|------|------|--------|
| StoryDirector post-evaluate | C2 阶段独立执行 | **移除**，Runtime 事件检查替代 |
| 事件计数与节奏控制 | C3 阶段复杂合并逻辑 | Runtime 事件检查自动处理 |
| 章节转换 | C4 阶段自动执行 | 玩家主动选择 (B 阶段工具) |
| 队友响应 | C5 阶段独立 | 保留，但上下文从 AreaRuntime 获取 |
| 事件分发到队友图谱 | C6 阶段 | **待重设计**（与动态图谱方案关联） |
| 历史记录 | C7 阶段 | 保留 |
| 状态持久化 | 散落在各处 | 统一为 `session.persist()` |

### C 阶段新流程

```
B 阶段 AgenticResult 返回
    ↓
C1. 事件状态更新
    area_runtime.check_events(session)
    → 更新事件 LOCKED→AVAILABLE / ACTIVE→COMPLETED
    → 执行 on_complete (解锁事件、发放奖励)
    → 检查章节转换条件
    ↓
C2. 记录行动到区域访问日志
    area_runtime.record_action(本轮行动摘要)
    ↓
C3. 队友响应 (保留现有流程)
    teammate_response_service.process_round()
    → 每个队友: 决策是否发言 → 生成响应
    ↓
C4. 历史记录
    session.history.record(player_input, gm_narration, teammate_responses)
    ↓
C5. 统一持久化
    session.persist()
    → SessionRuntime 全部状态 → Firestore
    → AreaRuntime 状态 (事件、NPC 上下文) → Firestore
    ↓
CoordinatorResponse → 前端
```

### 被移除的 C 阶段步骤

| 移除内容 | 原因 | 替代 |
|---------|------|------|
| `StoryDirector.post_evaluate()` | 条件在 Runtime 机械检查 | `area_runtime.check_events()` |
| 事件计数与冷却更新 | Runtime 事件模型内置 | `AreaEvent.status` + cooldown |
| `_reevaluate_transition_after_progress()` | 转换条件由 Runtime 统一检查 | `area_runtime.check_chapter_transition()` |
| 事件分发到队友图谱 (event_service) | 待动态图谱方案重设计 | **TODO** |
| memory_graphizer 自动压缩 | 待动态图谱方案重设计 | **TODO** |

### NPC 交互计数

B 阶段 `npc_dialogue` 工具执行时，Runtime 自动递增交互计数：

```python
async def npc_dialogue(npc_id, message):
    # ... 执行对话 ...
    session.narrative.npc_interactions[npc_id] += 1
    # 这会影响 NPC_INTERACTED 条件的检查结果
```

### 会话恢复

**原则**：每轮 C 阶段持久化完整快照，恢复时从快照重建。

```
持久化 (每轮 C5):
  1. SessionRuntime → Firestore session metadata
     (玩家状态、队伍、时间、叙事进度、当前 area_id)
  2. AreaRuntime 状态 → Firestore area state
     (事件进度、NPC 上下文、本次访问记录)
  3. 对话历史 → Firestore messages

恢复 (resume 时):
  1. Firestore session metadata → 重建 SessionRuntime
  2. session.area_id → world.load_area(area_id) → 重建 AreaRuntime
  3. AreaRuntime.load() 自动恢复: 区域状态 + 事件进度 + NPC 上下文
  4. 完成。下一次玩家输入正常走 A→B→C
```

**中途断线不丢数据**：visit_log 每轮持久化，摘要在最终离开区域时补生成。

---

## 第五部分：Firestore 数据结构变化

```
worlds/{world_id}/

  # 区域运行时状态 (新增)
  areas/{area_id}/
    state                            ← AreaState (动态状态)
    events/{event_id}                ← 事件状态 (status, progress)
    npc_contexts/{npc_id}            ← 区域 NPC 上下文窗口快照
    graph/nodes/, edges/             ← 区域级动态记忆图谱
    visits/{visit_id}                ← 访问摘要

  # 会话状态
  sessions/{session_id}/
    metadata:
      admin_state                    ← SessionRuntime 快照
      narrative                      ← NarrativeProgress
      player_character               ← PlayerState (全量)

    # 同伴数据 (新增, 会话级持久)
    companions/{character_id}/
      context_window/                ← 同伴 ContextWindow 消息快照
      state                          ← emotional_state + mood + concerns
      shared_events                  ← 共享事件列表
      area_summaries                 ← 区域摘要列表

  # 角色图谱 (保留)
  characters/{char_id}/
    nodes/, edges/                   ← 角色级动态图谱

  # 已废弃
  # camp/graph/                      ← 营地图谱 → 折入同伴 shared_events

  # 现有保留 (世界数据)
  maps/{map_id}/                     ← 区域静态定义
  chapters/{chapter_id}/             ← 章节定义
  mainlines/{mainline_id}/           ← 卷定义

  # 新增 (世界初始化生成)
  area_events/{area_id}_{chapter_id}/
    events[]                         ← 区域事件定义
  chapter_transitions/
    transitions[]                    ← 章节转换定义
```

---

## 被移除的完整清单

| 移除内容 | 所属阶段 | 原因 | 替代 |
|---------|---------|------|------|
| `StoryDirector` 完整类 | A+C | 两阶段评估被 Runtime 事件检查替代 | `AreaRuntime.check_events()` |
| `ConditionEngine` 作为独立服务 | A+C | 条件检查逻辑内置到 AreaRuntime | `_evaluate_conditions()` |
| `agentic_enforcement.py` | B | 上下文丰富后不再需要 | 轻量日志监控 |
| `evaluate_story_conditions` 工具 | B | 无 FLASH_EVALUATE 条件 | Runtime 机械检查 |
| `get_progress` / `get_status` 工具 | B | 信息已在上下文中 | Layer 1 + Layer 4 |
| `_build_effective_seeds()` | A | 机械种子忽略语义 | B 阶段 `recall_memory` 工具 |
| `player_memory_task` 预填充 | A | 移出 A 阶段 | B 阶段按需召回 |
| `teammate_prefill_tasks` | A | 移出 A 阶段 | 同伴 ContextWindow + shared_events |
| `pending_flash_conditions` | A→B | 不再需要 | Runtime 事件条件检查 |
| `_run_curation_pipeline()` | C | 第二次 LLM 调用不再必要 | AgenticResult 直接使用 |
| 自动章节转换 | C | 改为玩家主动选择 | B 阶段 `advance_chapter` 工具 |
| `NPCInstance` 统一实例池 | B+C | 同伴和区域 NPC 需求不同 | `CompanionInstance`（SessionRuntime）+ 区域 NPC（AreaRuntime） |
| `GraphScope.world/chapter/location/camp` | 全程 | 静态数据已直接注入，camp 折入同伴 | 仅保留 area + character 两个活跃 scope |
| 每轮队友 `perspective_transform` | C | 太重，每轮为每个队友做 LLM 调用 | 事件完成时分发，日常轮次不分发 |

---

## 第六部分：动态图谱与记忆系统

### 图谱作用域简化：6 → 2 活跃

新架构下静态世界数据已通过 A 阶段数据包直接注入，图谱专注**动态游戏记忆**：

| 当前 scope | 新方案 | 原因 |
|-----------|--------|------|
| `world` | **废弃** | 世界知识在 Layer 0 直接注入 |
| `chapter` | **废弃** | 章节信息在 Layer 1 直接注入 |
| `area` | **保留 → 区域图谱** | `AreaRuntime.area_graph`，共享区域记忆 |
| `location` | **折入 area** | 子地点不需独立图谱 |
| `character` | **保留 → 角色图谱** | NPC/玩家/同伴的个人记忆 |
| `camp` | **废弃** | 折入同伴 `shared_events` |

**两个活跃图谱**：

- **Area graph**（`AreaRuntime.area_graph`）— 区域内发生了什么（客观共享事实）
- **Character graph**（`GraphScope.character`）— 某角色记住了什么（主观个人记忆）

### 图谱生长机制（4 层）

按可靠性从高到低：

```
① 事件驱动（结构化，最可靠）
   事件 AVAILABLE→ACTIVE → area graph 创建事件节点
   事件 ACTIVE→COMPLETED → 更新节点 + 执行 on_complete
   → 与游戏进度绑定，不会遗漏

② 区域生命周期（周期性检查点）
   玩家离开区域 → AreaRuntime.unload()
   → 生成 visit_summary 节点写入 area graph
   → 相当于定期"存档"

③ 上下文溢出压缩（安全网）
   ContextWindow ≥ 90% → MemoryGraphizer → character graph
   → 保留现有机制
   → 作用范围：玩家 + 活跃交互的区域 NPC + 同伴

④ LLM 主动创建（可选，按需）
   B 阶段 create_memory 工具
   → LLM 认为重要的时刻主动记录
   → 指定写入 area graph 或 character graph
```

**核心变化**：当前系统主要靠 ③（被动溢出压缩）来生长图谱。新方案以 ① 事件驱动为主力——事件本身就是结构化的记忆单元，自然入图。

### 事件分发简化

当前：每轮为每个队友做 LLM `perspective_transform`，太重。

新方案：**事件完成时分发**，频率大幅降低（一个区域 3-5 个事件），信息质量更高。

```
事件完成 (ACTIVE → COMPLETED)
  ├─ area graph: 创建/更新事件节点（客观记录）
  ├─ 在场同伴: 自动追加到 shared_events（结构化，无 LLM）
  └─ 重要事件: 为参与者在 character graph 创建个人视角节点（可选 LLM 视角转换）
```

日常轮次不做分发。同伴通过 `shared_events` + area context 获取信息。

### 自动图谱化

保留现有 MemoryGraphizer 机制，触发场景：

| 场景 | 触发条件 | 图谱化目标 |
|------|---------|-----------|
| 同伴 ContextWindow 溢出 | usage ≥ 90% | 同伴的 character graph |
| 玩家 SessionHistory 溢出 | usage ≥ 90% | 玩家的 character graph |
| 区域 NPC 上下文溢出 | usage ≥ 90% | NPC 的 character graph |
| 区域卸载 | 玩家离开区域 | area graph（visit_summary 节点） |

### `create_memory` 工具（方向）

```python
async def create_memory(
    content: str,                      # 记忆内容描述
    importance: float,                 # 0.0-1.0
    scope: str,                        # "area" | "character"
    related_entities: List[str],       # 关联的 NPC/地点 ID
) -> dict:
```

LLM 在认为当前时刻值得记录时主动调用。Runtime 负责创建节点和关联边。具体 schema 在实现时细化。

### 召回机制（方向）

新架构下 `recall_memory` 工具加载的图谱范围：

- Area graph（当前区域的共享记忆）
- Character graph（调用者的个人记忆）
- 合并 → 扩散激活 → 返回相关子图

取消加载 world / chapter / camp scope（静态数据已直接注入，camp 已折入同伴）。

---

## 第七部分：同伴系统

### 核心区分：同伴 ≠ 区域 NPC

| | 区域 NPC | 同伴 |
|--|---------|------|
| 生命周期 | 随区域加载/卸载 | 随会话，跨区域持续 |
| 交互模式 | 玩家主动触发、有始有终 | 随时对话，始终在场 |
| 记忆范围 | 本区域交流 | 整段冒险旅程 |
| 情感需求 | 功能性（提供信息/任务） | 陪伴性（情绪共鸣/关系深化） |
| 数量 | 区域可能十几个 | 2-4 个 |
| 归属 | `AreaRuntime` | `SessionRuntime` |

新架构中两者**彻底分开**。区域 NPC 实例在 `AreaRuntime.npc_contexts` 中管理，同伴实例在 `SessionRuntime.companions` 中管理。

### CompanionInstance 架构

```python
class CompanionInstance:
    """同伴 — 与玩家共同冒险的角色。会话级持久，跨区域存在。"""

    character_id: str
    profile: CharacterProfile          # 完整档案（性格/说话风格/背景/示例对话）

    # ── 对话记忆（维持长期对话连续性）──
    context_window: ContextWindow      # ~100-150K tokens，会话级持久化

    # ── 结构化共享经历 ──
    shared_events: List[CompactEvent]  # 共同经历的事件
    area_summaries: List[VisitSummary] # 一起走过的区域摘要

    # ── 情感状态 ──
    emotional_state: CompanionEmotionalState

    # ── 长期记忆 → character graph ──
    # GraphScope.character(character_id) 通过 recall_memory 按需召回
```

### 三层记忆模型

```
【短期】ContextWindow（~100-150K tokens）
  ├─ 原始对话消息（玩家/GM/同伴自己/其他同伴）
  ├─ 覆盖最近 30-80 轮交互
  ├─ 直接拼入 prompt
  └─ ≥90% 时图谱化到 character graph，保留最近 50K

【中期】结构化共享经历（compact，无 token 预算压力）
  ├─ shared_events：事件完成时自动追加
  │   每条 ~50-100 token：{event_name, what_happened, companion_feeling, day}
  ├─ area_summaries：区域切换时自动追加
  │   每条 ~100-200 token：{area_name, key_actions, duration, highlights}
  └─ 注入 prompt 的"冒险日志"部分

【长期】Character Graph（扩散激活召回）
  ├─ 来源1：ContextWindow 溢出压缩（MemoryGraphizer）
  ├─ 来源2：重要事件完成时写入个人视角节点
  ├─ 来源3：区域 visit_summary 入图
  └─ 当对话涉及久远话题时 recall_memory 召回
```

**对话连续性示例**：

```
第 1 天（短期记忆覆盖）：
  玩家："你为什么想当冒险者？"
  女神官："因为我想帮助更多的人..."
  → ContextWindow 中，下次对话自然延续

第 5 天（中期记忆覆盖）：
  玩家："还记得我们第一次打哥布林吗？"
  → shared_events 有 {name: "初次哥布林讨伐", companion_feeling: "害怕但被你的勇气鼓舞"}
  → 同伴能自然引用

第 30 天（长期记忆覆盖）：
  玩家："那个酒馆里的老板..."
  → ContextWindow 已溢出图谱化
  → recall_memory("酒馆", "老板") 从 character graph 召回
  → 核心情感和事实保留
```

### 情感状态追踪

```python
class CompanionEmotionalState:
    mood: str                          # "开心" / "担忧" / "兴奋"
    mood_cause: str                    # "因为刚刚战斗胜利"
    concerns: List[str]                # ["担心玩家的伤势", "对接下来的任务感到不安"]
    recent_feelings: List[EmotionLog]  # 最近几轮的情感变化轨迹
    relationship_moments: List[str]    # 关系里的关键时刻
    # "你在哥布林洞窟里保护了我" / "你选择了救我而不是追敌人"
```

情感状态在**同伴生成响应时一并输出更新**，不需额外 LLM 调用：

```json
{
  "response": "太好了，你没事就好......我刚才真的很担心。",
  "reaction": "松了口气，悄悄擦了擦眼角",
  "inner_thought": "他总是这样不顾自己......",
  "updated_mood": "释然但仍有余悸",
  "mood_cause": "玩家在战斗中受了重伤但幸存",
  "concerns_update": ["希望他以后能更小心"]
}
```

`inner_thought` 不展示给玩家，保存在 ContextWindow 和情感状态中，影响后续行为。

### 同伴响应上下文构建

```
同伴接收的完整上下文：
  ├─ 完整人设（profile + speech_pattern + example_dialogue）
  ├─ 对话历史（ContextWindow 最近消息）
  ├─ 共享经历（shared_events + area_summaries）
  ├─ 情感状态（mood + concerns + relationship_moments）
  ├─ 好感度（disposition toward player）
  ├─ 召回的相关记忆（character graph recall）
  └─ 当前场景（area context）
```

### 主动性

同伴不只被动回应，还能**主动发起对话**：

```python
class CompanionDecision:
    should_respond: bool          # 回应玩家/GM
    should_initiate: bool         # 主动发起
    initiation_type: str          # "关心" / "闲聊" / "警告" / "分享感受"
    trigger: str                  # 触发原因
```

主动对话触发条件（规则 + 性格）：

- 重大事件后玩家沉默超过 2 轮 → 关心
- 进入新区域 → 兴奋/评论
- 战斗后 → 复盘/担心
- 夜晚营地 → 闲聊/谈心
- 好感度达到阈值 → 分享个人故事
- `concerns` 列表非空 + 安全场景 → 主动提起顾虑

### 私聊增强

与同伴的私聊走和主流程同等丰富的路径。`npc_dialogue` 工具自动分流：检测到目标是同伴时，路由到 `CompanionInstance` 而非 `AreaRuntime` 的 NPC 上下文。

### 同伴间互动

- 顺序生成时后面的同伴看到前面的发言（保留现有机制）
- 同伴之间也有 `disposition`
- 同伴之间可以有分歧（基于性格差异）

### 区域切换时同伴处理

```
1. AreaRuntime.unload() → 生成 visit_summary
2. visit_summary → 追加到每个 companion 的 area_summaries
3. visit_summary → 写入 area graph
4. 同伴的 context_window 不受影响（继续累积）
5. AreaRuntime.load(new_area)
6. 同伴看到新区域 → 可能主动评论
```

### 会话恢复

```
SessionRuntime.restore()
  → 每个 CompanionInstance:
    → context_window 从 Firestore 恢复
    → shared_events, area_summaries 从 Firestore 恢复
    → emotional_state 从 Firestore 恢复
    → 完全恢复对话连续性
```

---

## 第八部分：好感度变更子系统

### `update_disposition` 工具

暴露为 B 阶段 agentic 工具：

```python
async def update_disposition(
    npc_id: str,
    deltas: Dict[str, int],   # {"approval": 5, "trust": -3}
    reason: str,               # "玩家主动帮助了她"
) -> dict:
```

### 护栏规则

- 单次每维度变化上限：±20
- 每轮最多调用 3 次
- 每轮每维度总变化上限：±30
- 自动同步 `approves` 边到 character graph

### 参考量表（注入系统提示词）

| 变化幅度 | 触发场景 |
|---------|---------|
| +3~5 | 一般好印象（帮小忙、友善对话） |
| +8~12 | 明显好感（救了一命、完成委托） |
| +15~20 | 重大转变（牺牲性行为、揭露重要秘密） |
| 负值同理取反 | |

---

## A 阶段遗留 TODO 决议

| A 阶段 TODO | 决议 |
|------------|------|
| TODO-1 玩家角色详细状态 | **全量注入** + `SessionRuntime.player` 提供状态变更 API（equip/unequip, add/remove item, modify HP/XP/gold, manage conditions 等），agentic 工具通过此 API 操作 |
| TODO-2 玩家选择与未兑现后果 | **延后**，核心循环不依赖此数据，后期作为叙事增强加入 |
| TODO-3 叙事进度完整字段 | **全量注入**，根据实际运行 token 消耗再裁剪 |
| TODO-4 营地图谱 | **已解决**，折入同伴 `shared_events` |
| TODO-5 路人池 | **已解决**，低优先级，B 阶段工具按需获取 |
| TODO-6 好感度变更 | **已解决**，见第八部分 |

---

## 待实现时细化的项目

以下项目方向已确定，具体细节在实现时确定：

| 项目 | 方向 | 实现时机 |
|------|------|---------|
| `create_memory` 工具 schema | `{content, importance, scope, related_entities}` | 实现 Runtime 时 |
| 召回机制 `recall_memory` | area graph + character graph 扩散激活 | 实现 Runtime 时 |
| NPC 对话路由 | `npc_dialogue` 自动分流：区域 NPC → AreaRuntime，同伴 → CompanionInstance | 实现工具层时 |
| 系统提示词 | 分层上下文 + 事件指导 + 区域感知 + 同伴指导 | 实现 B 阶段时 |
| 战斗系统适配 | 现有 combat MCP server 接入新 Runtime | 核心架构完成后 |

---

## 与 A 阶段方案的关系

B+C 方案在 A 阶段方案的基础上建立了底层：

```
A 阶段方案定义了: 注入什么数据、怎么过滤、6 个数据包的字段
     ↓ 建立在
Game Runtime 定义了: 数据从哪里来、怎么组织、区域生命周期
     ↓ 支撑
B+C 阶段方案定义了: 数据怎么用、工具怎么操作、事件怎么推进
```

A 阶段方案中的以下设计约定在 Runtime 架构下得到更清晰的实现：

| A 阶段约定 | Runtime 实现 |
|-----------|-------------|
| 「时间点快照」 | ContextAssembler 读取 Runtime 当前状态 |
| 「三波加载」 | 替代为分层读取 (Layer 0→4)，更清晰 |
| 「零判断」 | ContextAssembler 无逻辑，事件检查在 Runtime |
| 「数据过滤层级」 | WorldInstance 注册表 + AreaRuntime 上下文输出 |
