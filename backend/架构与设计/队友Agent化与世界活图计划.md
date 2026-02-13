# 队友 Agent 化与世界活图 — 架构升级计划

> 日期: 2026-02-13
> 分支: buyu的异世界冒险
> 前置调查: 4 个并行 Agent 深度调查（事件系统 / 图谱系统 / Agentic 工具 / 好感度+NPC实例）

---

## 〇、为什么先做这个，而不是战斗系统？

`战斗系统重构计划.md` 中 Phase 2 原设计为规则引擎 `AllyAI` — 这与项目的 Agentic 哲学矛盾。

如果先完成队友 Agent 化：
- **Phase 2（队友战斗AI）自动解决** — 队友作为 Agent 在战斗回合中自主调用 `choose_combat_action`
- **Phase 5.2（队友HP同步）** — Agent 工具执行结果自然包含状态同步
- **Phase 5.3（好感度更新）** — 交给队友 Agent 自己判断和执行
- **Phase 6（模式切换）** — 不再需要区分战斗/非战斗模式，Agent 根据上下文自适应

**战斗计划中不受影响的部分**（仍需单独实施）：
- Phase 1: 队友战斗属性体系（`PartyMember` 扩展、`class_templates.py`、FlashCPU allies 填充）
- Phase 3: 物品系统桥接
- Phase 4: 法术系统桥接
- Phase 5.1: 物品掉落系统
- Phase 7: 前端战斗UI

---

## 一、现状诊断

### 1.1 队友系统 — 被动对话生成器

```
当前 C 阶段队友流：
  TeammateResponseService.process_round_stream()
    ├─ Phase A: 注入上下文（玩家输入 + GM叙述 → ContextWindow）
    ├─ Phase B: 并发 LLM 决策（should_respond → JSON {yes/no}）
    ├─ Phase C: 顺序 LLM 生成（generate_simple → JSON {response, reaction, mood}）
    └─ Phase D: 流式推送 teammate_chunk 事件

工具调用能力: 0 个
状态修改能力: 仅更新自身 ContextWindow + mood
```

| 能力 | GM (FlashCPU) | 队友 (当前) |
|------|--------------|------------|
| 工具总数 | 21 个 | 0 个 |
| 导航 | navigate / enter / leave | 自动跟随（无操作） |
| NPC 对话 | npc_dialogue | 无法主动对话 |
| 战斗行动 | start_combat / choose_action | 完全无参与 |
| 技能检定 | ability_check | 不可执行 |
| 好感度 | update_disposition | 被动接受 |
| 记忆 | recall_memory / create_memory | 只有 ContextWindow 被动记录 |
| 物品/金币 | add_item / remove_item | 无 |
| 事件 | activate_event / complete_event | 无 |
| 思考等级 | high | 固定 low |

### 1.2 事件系统 — 已有 pub/sub 但利用率低

**已有基础设施**：

| 组件 | 文件 | 能力 |
|------|------|------|
| EventBus | `app/services/event_bus.py` | 内存 pub/sub，按 EventType 分发 |
| AdminEventService | `app/services/admin/event_service.py` | 结构化+自然语言双模式入图 |
| EventLLMService | `app/services/admin/event_llm_service.py` | 3步管线：parse → encode → transform_perspective |
| CompanionInstance | `app/runtime/companion_instance.py` | 轻量事件接收（CompactEvent 列表） |
| AreaRuntime | `app/runtime/area_runtime.py` | 事件状态机 + 同伴分发 |

**当前瓶颈**：
- EventBus 是内存级别的简单 pub/sub，只有 `AdminEventService` 使用
- 事件分发到角色图谱需要 LLM perspective_transform（昂贵、慢）
- 队友只收到 CompactEvent 摘要，不触发任何行为
- 没有"世界状态变化 → Agent 自主反应"的链路

### 1.3 图谱系统 — 已有分层存储，可扩展为活图

**已有**：

```
GraphScope 6 层寻址:
  world()             → worlds/{wid}/graphs/world/
  chapter(cid)        → worlds/{wid}/chapters/{cid}/graph/
  area(cid, aid)      → worlds/{wid}/chapters/{cid}/areas/{aid}/graph/
  location(cid,a,lid) → worlds/{wid}/chapters/{cid}/areas/{aid}/locations/{lid}/graph/
  character(char_id)  → worlds/{wid}/characters/{char_id}/
  camp()              → worlds/{wid}/camp/graph/

MemoryGraph (NetworkX MultiDiGraph):
  - 多重索引（type/name/chapter/area/location/day/participant）
  - 扩散激活检索（CRPG 专用衰减：跨视角×0.5, 跨章节×0.4）
  - 多作用域合并 from_multi_scope()
  - BFS 子图扩展 expand_nodes()

MemoryGraphizer:
  - 对话 → 图谱自动转换
  - 双层事件层级（EventGroup → SubEvent）
  - 实体自动提取（person/location/item/knowledge/rumor/goal/emotion）
```

**好感度系统**：
```
Firestore: worlds/{wid}/characters/{cid}/dispositions/{target_id}
四维度: approval / trust / fear / romance (±100 范围)
历史: 50 条变更记录 (reason + game_day)
当前: 由 GM agentic 工具 update_disposition 驱动
```

---

## 二、目标架构

### 2.1 三层递进设计

```
Phase A: 队友 Agent 化 ←── 本计划核心，最先实施
  └─ 队友从被动对话器 → 拥有工具的独立 Agent
  └─ 好感度自主管理
  └─ 战斗时自然获得战斗工具

Phase B: 结构化广播系统
  └─ GM 工具执行 → 生成结构化世界事件
  └─ 事件按 GraphScope 路由到相关 Agent
  └─ Agent 异步接收并自主反应

Phase C: 世界活图（远期愿景）
  └─ 图节点 = 有行为的实体
  └─ 边 = 事件传播通道
  └─ 章节门控/区域状态机 = 图上约束
```

### 2.2 最终形态概念图

```
                    ┌──────────────────┐
                    │  世界通识母节点   │ ← 随剧情可变
                    │ (GraphScope.world)│
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐
        │ 章节1 节点 │ │ 章节2 节点 │ │  ...      │
        │(门控:locked)│ │(门控:open) │ │           │
        └─────┬─────┘ └─────┬─────┘ └───────────┘
              │              │
        ┌─────┴─────┐  ┌────┴────┐
        │ 区域A 节点 │  │区域B节点│
        └─────┬─────┘  └────┬────┘
              │              │
     ┌────────┼────────┐    │
     │        │        │    │
  ┌──┴──┐ ┌──┴──┐ ┌──┴──┐ ...
  │子地点│ │子地点│ │子地点│
  │ 酒馆 │ │ 市场 │ │ 公会 │
  └──┬──┘ └──┬──┘ └──┬──┘
     │        │        │
   NPC-A   NPC-B    NPC-C  ←── 每个 NPC = 独立 Agent（有图谱+工具）
     │        │        │
  [记忆图谱] [记忆图谱] [记忆图谱]
  [好感度]  [好感度]   [好感度]

  ===== 队友 Agent 池 =====
  女神官(priest) ──┐
  妖精弓手(ranger)─┤── 每个队友 = 独立 Agent
  矮人术士(mage) ──┤   拥有: 工具注册表 + ContextWindow + 记忆图谱
  蜥蜴僧侣(priest)─┘

  ===== 广播通道 =====
  玩家行动 → GM Agent → [结构化事件] → EventBus → 订阅者 Agent 列表
                                                    ├─ 同区域 NPC
                                                    ├─ 队友
                                                    └─ 同伴实例
```

---

## 三、Phase A: 队友 Agent 化（核心）

### 3.1 新增: TeammateAgenticService

**新文件**: `app/services/teammate_agentic_service.py`

**核心理念**: 每个队友每回合运行一个**轻量 agentic 循环**，拥有一组工具，自主决策要做什么。

```python
class TeammateAgenticService:
    """队友 Agent 服务 — 替代 TeammateResponseService 的核心生成逻辑。"""

    async def process_teammate_round(
        self,
        party: Party,
        player_input: str,
        gm_response: str,
        context: dict,
        session: SessionRuntime,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> List[TeammateAgenticResult]:
        """
        为每个活跃队友运行独立的 agentic 循环。

        流程:
        1. 决策阶段: 与现有逻辑相同（should_respond 判断）
        2. Agent 阶段: 替代 generate_simple()，使用 agentic_generate() + 工具
        3. 收集结果: tool_calls + 叙述/对话
        """
```

### 3.2 新增: TeammateToolRegistry

**新文件**: `app/services/admin/teammate_tools.py`

镜像 `V4AgenticToolRegistry` 模式，但提供**队友视角的工具子集**：

| 工具 | 说明 | 对应 V4 工具 |
|------|------|-------------|
| `speak(message: str)` | 对队伍说话（产生对话） | 无（新增） |
| `inner_thought(thought: str)` | 内心独白（不发出声音） | 无（新增） |
| `ability_check(skill, dc)` | 执行技能检定 | `ability_check` |
| `update_my_disposition(target_id, deltas, reason)` | 更新自己对某人的好感度 | `update_disposition` |
| `recall_memory(seeds)` | 检索自己的记忆图谱 | `recall_memory` |
| `create_memory(content, importance)` | 记住某事 | `create_memory` |
| `use_item(item_id)` | 使用自己的物品 | 无（新增） |
| `choose_combat_action(action_id)` | 战斗中选择行动 | `choose_combat_action` |
| `observe_environment()` | 观察当前环境（返回区域信息） | 无（新增） |

**不包含**的工具（队友不应拥有）：
- `navigate` / `enter_sublocation` / `leave_sublocation` — 自动跟随玩家
- `start_combat` — 只有 GM 能发起战斗
- `activate_event` / `complete_event` / `advance_chapter` — 只有 GM 控制叙事
- `add_teammate` / `remove_teammate` — 只有 GM 管理队伍
- `heal_player` / `damage_player` / `add_xp` — 只有 GM 修改玩家属性
- `generate_scene_image` — 只有 GM 生成图片

**关键差异**：
```python
class TeammateToolRegistry:
    def __init__(
        self,
        *,
        member: PartyMember,          # 当前队友
        session: SessionRuntime,       # 会话运行时
        graph_store: GraphStore,       # 图谱存储
        flash_cpu: FlashCPUService,    # MCP 调用（战斗工具）
        combat_active: bool = False,   # 是否在战斗中
        event_queue: Optional[asyncio.Queue] = None,
    ):
        ...

    def get_tools(self) -> List[Any]:
        """根据上下文动态返回可用工具。"""
        tools = [
            self.speak,
            self.inner_thought,
            self.update_my_disposition,
            self.recall_memory,
            self.create_memory,
            self.observe_environment,
        ]
        # 战斗中追加战斗工具
        if self.combat_active:
            tools.append(self.choose_combat_action)
        # 技能检定总是可用
        tools.append(self.ability_check)
        return tools
```

### 3.3 队友 Prompt 重设计

**新文件**: `app/prompts/teammate_agent_system.md`

从"你是对话角色"变为"你是独立冒险者"：

```markdown
# {name} — {role}

你是 {name}，一个 {occupation}。你是冒险队伍的一员。

## 你的性格
{personality}

## 你当前的情绪
{current_mood}

## 你能做的事
你有以下工具可以使用：
- speak: 对队伍说话
- inner_thought: 内心思考（队友不会听到）
- ability_check: 尝试技能检定
- update_my_disposition: 更新你对某人的看法
- recall_memory: 回忆过去的事
- create_memory: 记住重要的事
{combat_tools_section}

## 行为准则
1. 你不是 GM。你不控制世界，你只控制自己。
2. 你可以选择不做任何事（不调用任何工具）。
3. 如果你觉得没有什么值得说或做的，就保持沉默。
4. 好感度由你自己管理 — 如果玩家做了让你高兴/不满的事，用 update_my_disposition。
5. 战斗中，根据你的职业和性格选择行动。
6. 你的 speak 输出是队友们能听到的。inner_thought 只有你自己知道。

## 当前场景
{scene_context}

## 你的近期记忆
{recent_memory}
```

### 3.4 管线集成点

**修改文件**: `app/services/admin/pipeline_orchestrator.py` — C 阶段

```python
# 现有（将被替代）:
if party and party.get_active_members():
    async for tm_event in self.teammate_response_service.process_round_stream(...):
        ...

# 新方案:
if party and party.get_active_members():
    combat_active = bool(session.game_state and session.game_state.combat_id)
    results = await self.teammate_agentic_service.process_teammate_round(
        party=party,
        player_input=player_input,
        gm_response=gm_narration,
        context=context_dict,
        session=session,
        combat_active=combat_active,
        event_queue=event_queue,
    )
    # 收集结果
    for result in results:
        teammate_responses.append(result.to_response_dict())
```

### 3.5 好感度自主管理

**变更**: `update_disposition` 不再只由 GM 调用，队友 Agent 也能调用。

当前流程:
```
玩家做了勇敢的事 → GM 叙述 → GM 调用 update_disposition(priestess, {approval: +10})
```

新流程:
```
玩家做了勇敢的事 → GM 叙述 → 女神官 Agent 读到 GM 叙述
  → Agent 内心: "他真勇敢..." → 调用 update_my_disposition(player, {approval: +10, trust: +5}, "保护了我")
  → Agent 说: "谢...谢谢你..."
```

**实现**: `TeammateToolRegistry.update_my_disposition()` 内部调用 `graph_store.update_disposition()`，但 scope 限制为只能修改**自己对别人**的好感度（不能修改别人对自己的）。

### 3.6 战斗集成

当 `combat_active=True` 时：

1. 队友 Agent 获得 `choose_combat_action` 工具
2. 轮到队友回合时，CombatEngine 暂停等待 → 管线驱动队友 Agent 执行一轮
3. Agent 选择战斗行动（攻击/防御/施法/治疗等）
4. 同时可以用 `speak` 喊战斗台词
5. 结果自动同步到战斗引擎

**CombatEngine 改动**:
```python
# combat_engine.py: _run_enemy_turns_until_player()
# 当遇到 ally 回合时，不再自动处理
# 而是设置 WAITING_ALLY_INPUT 状态，由管线层驱动

def _run_npc_turns_until_player_or_ally(self, session):
    while session.state != CombatState.ENDED:
        current = session.get_current_actor()
        if current.is_enemy():
            # 敌人 AI（保持不变）
            ...
        elif current.is_ally() or current.is_player():
            # 玩家/队友：暂停，等待外部输入
            self._set_waiting_input(session)
            return
```

管线 C 阶段中，如果战斗激活且轮到队友：
```python
# 获取当前等待行动的 ally
pending_ally_id = combat_state.get("pending_actor_id")
if pending_ally_id and pending_ally_id != "player":
    member = party.get_member(pending_ally_id)
    if member:
        # 运行该队友的 agentic 循环（含 choose_combat_action 工具）
        result = await teammate_agentic_service.process_single_teammate(
            member=member, combat_active=True, ...
        )
```

### 3.7 与现有 TeammateResponseService 的关系

**渐进式替换**，不一次性删除：

1. 保留 `TeammateResponseService` 作为 fallback
2. 新增 `TeammateAgenticService`
3. 管线中优先使用新服务，异常时 fallback 到旧服务
4. 决策阶段（should_respond）可复用，只替换生成阶段
5. 稳定后移除旧服务

### 3.8 SSE 事件扩展

现有事件类型:
```
teammate_start / teammate_chunk / teammate_end / teammate_skip
```

新增事件类型:
```
teammate_tool_call: {type, character_id, tool_name, tool_args, success, duration_ms}
teammate_combat_action: {type, character_id, action_id, result}
teammate_disposition_change: {type, character_id, target_id, deltas, current}
teammate_dice_result: {type, character_id, skill, roll, dc, success}
```

---

## 四、Phase B: 结构化广播系统（中期）

### 4.1 核心概念

GM Agent 的每次工具调用 → 自动生成**结构化世界事件** → 广播给相关 Agent。

```
玩家: "我拔剑攻击哥布林"
  → GM Agent 调用 start_combat(enemies=[goblin])
    → WorldEvent 生成:
        {
          type: "combat_started",
          location: "abandoned_mine_entrance",
          participants: ["player", "priestess", "high_elf_archer"],
          data: {enemies: ["goblin"], threat_level: "low"},
          scope: GraphScope.area("chapter_1", "water_town_outskirts")
        }
    → EventBus.publish(event)
      → 订阅者:
          priestess_agent.receive_event(event)  → "危险！让我准备治愈术..."
          high_elf_archer_agent.receive_event(event)  → 拉弓准备
          nearby_npc.receive_event(event)  → 路人逃跑
```

### 4.2 WorldEvent 数据模型

```python
class WorldEvent(BaseModel):
    """结构化世界事件 — GM 工具执行后自动生成。"""
    event_id: str
    event_type: str          # combat_started, item_purchased, location_entered, npc_dialogue_ended, ...
    timestamp: datetime
    game_day: int
    location: str            # 发生地点
    scope: GraphScope        # 影响范围

    # 参与者
    actor: str               # 发起者（通常是 player）
    participants: List[str]  # 所有相关者
    witnesses: List[str]     # 目击者

    # 事件数据
    data: Dict[str, Any]     # 事件特定数据（如战斗的敌人列表、商店的购买物品等）

    # 传播控制
    visibility: str = "local"  # local（同区域）/ party（仅队伍）/ global（全世界）
    importance: float = 0.5    # 0.0-1.0，决定 Agent 是否需要反应
```

### 4.3 事件生成钩子

在 `V4AgenticToolRegistry` 的每个工具执行后，自动生成 WorldEvent：

```python
# v4_agentic_tools.py 中每个工具的 _record() 之后
async def _post_tool_event(self, tool_name: str, args: dict, result: dict):
    """工具执行后生成世界事件。"""
    event = self._create_world_event(tool_name, args, result)
    if event:
        await self.event_bus.publish(event)
```

工具→事件映射:
```
navigate          → location_changed {from, to, travel_time}
npc_dialogue      → dialogue_occurred {npc_id, topic, mood_change}
start_combat      → combat_started {enemies, location}
ability_check     → skill_attempted {skill, dc, result, actor}
update_disposition → attitude_changed {npc_id, deltas}
update_time       → time_advanced {minutes, new_hour}
add_item          → item_acquired {item_id, source}
activate_event    → story_event_activated {event_id}
complete_event    → story_event_completed {event_id}
```

### 4.4 Agent 事件订阅

```python
class TeammateAgenticService:
    async def on_world_event(self, event: WorldEvent, member: PartyMember):
        """队友 Agent 接收世界事件。"""
        # 1. 添加到 ContextWindow（作为 system 消息）
        instance = await self.instance_manager.get_or_create(member.character_id)
        instance.context_window.add_message("system", f"[世界事件] {event.summary}")

        # 2. 判断是否需要立即反应
        if event.importance >= 0.7 or member.character_id in event.participants:
            # 触发 mini-agentic 循环
            await self.process_single_teammate_reaction(member, event)
```

---

## 五、Phase C: 世界活图（远期愿景）

### 5.1 概念

将当前的**被动数据图谱**升级为**有行为的活图**：

```
当前: GraphScope 是寻址方式，GraphStore 是 CRUD 层
目标: 每个图节点可以附加 behaviors/triggers/constraints

示例:
  node: "abandoned_mine_entrance" (location)
    behavior: 当 game_time.hour >= 20 → spawn_event("夜间哥布林巡逻")
    constraint: 需要 chapter_1.objective_3 完成后才能进入深处
    trigger: 当 player 进入 → 播放环境描述

  node: "priestess" (character)
    behavior: 当 approval < -20 → 触发离队事件
    behavior: 当 trust > 50 → 解锁个人支线
    trigger: 当收到 combat_started 事件 → 自动准备治愈法术
```

### 5.2 实现路径

这需要在 `MemoryNode.properties` 中添加结构化行为定义：

```python
# 扩展 MemoryNode properties
{
    "id": "abandoned_mine_entrance",
    "type": "location",
    "name": "废弃矿洞入口",
    "importance": 0.8,
    "properties": {
        # 现有属性...
        "behaviors": [
            {
                "trigger": "player_enter",
                "condition": {"time_hour_gte": 20},
                "action": {"type": "spawn_event", "event_id": "goblin_night_patrol"}
            },
            {
                "trigger": "player_enter",
                "condition": {"first_visit": true},
                "action": {"type": "narrative_hint", "text": "阴冷的风从洞口吹出..."}
            }
        ],
        "constraints": [
            {
                "type": "chapter_gate",
                "requires": "chapter_1.objective_investigate_mine",
                "status": "completed"
            }
        ]
    }
}
```

### 5.3 与现有系统的兼容

- `AreaRuntime.check_events()` 的事件状态机 → 可以迁移为图上的 behavior triggers
- 章节门控 → 图上的 constraint 节点
- NPC 好感度阈值触发 → character 节点的 behavior

**注意**: Phase C 是远期愿景，不阻塞 Phase A/B 的实施。当前的 `AreaRuntime` 事件状态机可以继续工作，Phase C 是对它的**渐进式替代**。

---

## 六、实施路线图

### Phase A: 队友 Agent 化

```
Step A1: 基础设施 (新建)
  ├─ app/services/admin/teammate_tools.py        ← TeammateToolRegistry
  ├─ app/services/teammate_agentic_service.py    ← TeammateAgenticService
  └─ app/prompts/teammate_agent_system.md        ← 队友 Agent Prompt

Step A2: 工具实现
  ├─ speak() — 对队伍发言
  ├─ inner_thought() — 内心独白
  ├─ ability_check() — 委托 AbilityCheckService
  ├─ update_my_disposition() — 委托 GraphStore
  ├─ recall_memory() — 委托 spreading activation
  ├─ create_memory() — 委托 GraphStore
  ├─ observe_environment() — 返回 area_context
  └─ choose_combat_action() — 委托 combat MCP (条件注入)

Step A3: 管线集成
  ├─ pipeline_orchestrator.py C阶段: 替换队友生成逻辑
  ├─ SSE 事件扩展: teammate_tool_call 等新事件类型
  └─ 向后兼容: 旧 TeammateResponseService 作为 fallback

Step A4: 战斗集成（依赖战斗计划 Phase 1）
  ├─ PartyMember 战斗属性（来自战斗计划 Phase 1）
  ├─ combat_engine.py: ally 回合暂停等待 Agent 输入
  ├─ v4_agentic_tools.py: start_combat 自动填充 allies
  └─ 端到端: 队友在战斗中通过 choose_combat_action 自主行动
```

### Phase B: 结构化广播系统

```
Step B1: WorldEvent 模型
  └─ app/models/world_event.py

Step B2: 事件生成钩子
  └─ v4_agentic_tools.py: 每个工具执行后生成 WorldEvent

Step B3: 事件路由
  └─ EventBus 扩展: 按 GraphScope 路由到相关 Agent

Step B4: Agent 事件订阅
  └─ TeammateAgenticService.on_world_event()
```

### Phase C: 世界活图

```
Step C1: 行为定义 schema
Step C2: 节点行为引擎
Step C3: 迁移 AreaRuntime 事件状态机到图行为
Step C4: 章节门控迁移
```

---

## 七、与战斗系统重构计划的对照表

| 战斗计划 Phase | 受影响程度 | 说明 |
|---------------|-----------|------|
| **Phase 1**: 队友战斗属性 | 不变 | 仍需实施。PartyMember 扩展 + class_templates.py + FlashCPU allies 填充 |
| **Phase 2**: 队友战斗 AI | **被替代** | 不再需要 `ai_ally.py` 规则引擎。队友 Agent + `choose_combat_action` 工具替代 |
| **Phase 3**: 物品系统桥接 | 不变 | 仍需实施。战斗引擎物品查询逻辑不受影响 |
| **Phase 4**: 法术系统桥接 | 不变 | 仍需实施。法术模板加载不受影响 |
| **Phase 5.1**: 物品掉落 | 不变 | 仍需实施 |
| **Phase 5.2**: 队友 HP 同步 | **简化** | Agent 工具调用结果自然包含状态，但仍需在战斗结束时同步回 PartyMember |
| **Phase 5.3**: 好感度更新 | **被替代** | 交给队友 Agent 自主通过 `update_my_disposition` 完成 |
| **Phase 6**: 战斗模式切换 | **被替代** | Agent 根据 combat_active 上下文自适应，不需要硬编码模式切换 |
| **Phase 7**: 前端战斗 UI | 不变 | 仍需实施，但 SSE 事件类型会扩展 |

**建议实施顺序**:

```
1. 本计划 Phase A Step A1-A3  →  队友 Agent 化（非战斗场景先跑通）
2. 战斗计划 Phase 1           →  队友战斗属性体系
3. 本计划 Phase A Step A4      →  战斗中队友 Agent 集成
4. 战斗计划 Phase 3+4          →  物品/法术桥接
5. 战斗计划 Phase 5.1          →  物品掉落
6. 本计划 Phase B              →  结构化广播
7. 战斗计划 Phase 7            →  前端优化
8. 本计划 Phase C              →  世界活图（远期）
```

---

## 八、关键设计决策

### 8.1 并发 vs 顺序

**决策**: 队友 Agent 循环**并发执行**（与现有决策阶段并发一致）。

原因:
- 队友之间是独立 Agent，不需要等待彼此
- 并发减少总延迟（3个队友串行 = 3x LLM 调用，并发 ≈ 1x）
- 冲突处理: 如果两个队友同时修改好感度 → Firestore 原子操作保证一致性

### 8.2 token 预算

**决策**: 队友 Agent 使用更低的 `max_remote_calls` 和更小的上下文。

```
GM Agent:    max_remote_calls=10, thinking=high,   model=flash
队友 Agent:  max_remote_calls=3,  thinking=low,    model=flash
```

原因:
- 队友的决策空间比 GM 小得多
- 3 轮工具调用足够：一轮观察 + 一轮行动 + 一轮对话
- 节省 token 成本（4个队友并发 = 4x 基础成本，需要控制）

### 8.3 决策阶段保留

**决策**: 保留现有的 should_respond 决策阶段，作为 Agent 循环的前置门控。

```
Phase B 决策 (should_respond) → 通过 → Agent 循环 (agentic_generate)
                              → 不通过 → 跳过（节省 LLM 调用）
```

原因:
- 不是每回合每个队友都需要反应
- 决策阶段成本低（简单 LLM 调用 or 规则 fallback）
- 防止 4 个队友每轮都触发完整 agentic 循环（token 爆炸）

### 8.4 向后兼容

**决策**: Firestore 数据模型不变。所有新增字段使用默认值。

- PartyMember 新增战斗属性字段（Optional + 默认值）
- CompanionInstance 不变
- GraphScope / GraphStore 不变
- 现有保存的 session 数据可以正常加载

---

## 九、文件影响清单

### 新建文件

| 文件 | 行数估计 | 说明 |
|------|---------|------|
| `app/services/admin/teammate_tools.py` | ~400 | TeammateToolRegistry |
| `app/services/teammate_agentic_service.py` | ~300 | Agent 编排服务 |
| `app/prompts/teammate_agent_system.md` | ~100 | Agent 系统 Prompt |
| `app/combat/class_templates.py` | ~100 | 职业模板（来自战斗计划） |

### 修改文件

| 文件 | 改动范围 | 说明 |
|------|---------|------|
| `app/models/party.py` | 新增战斗字段 + to_combat_ally_state() | ~50行新增 |
| `app/services/admin/pipeline_orchestrator.py` | C阶段队友处理替换 | ~30行改动 |
| `app/services/admin/v4_agentic_tools.py` | start_combat 自动填充 allies | ~10行新增 |
| `app/combat/combat_engine.py` | ally 回合暂停逻辑 | ~20行改动 |
| `app/services/party_service.py` | add_member 初始化战斗属性 | ~15行新增 |

### 不需要改动

| 文件 | 原因 |
|------|------|
| `app/services/teammate_response_service.py` | 保留作为 fallback，不删除 |
| `app/services/graph_store.py` | API 不变，队友复用现有接口 |
| `app/services/event_bus.py` | Phase B 才扩展 |
| `app/combat/ai_opponent.py` | 敌人 AI 不变 |
| `app/services/memory_graph.py` | API 不变 |
| `app/services/spreading_activation.py` | API 不变 |

---

## 十、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 4 个队友并发 agentic 调用 → token 成本 4x | 高 | max_remote_calls=3 + thinking=low + 决策门控 |
| 队友 Agent 做出不合理行为 | 中 | Prompt 约束 + 工具权限限制（无导航/事件/章节工具） |
| 战斗回合等待 Agent 返回 → 延迟 | 中 | 超时机制（5s），超时自动防御 |
| 队友好感度自主更新 → 数值不稳定 | 低 | 保持现有 ±20/次 上限 + 每回合 ±30 软限 |
| Firestore 并发写入冲突 | 低 | 原子操作 + 乐观锁（现有机制已足够） |
