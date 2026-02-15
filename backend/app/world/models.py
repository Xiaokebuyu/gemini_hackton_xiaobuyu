"""
World Graph Data Models -- Step C1

世界图数据模型：定义 WorldGraph 的节点、边、行为、事件生命周期。
基于设计文档 `架构与设计/世界底层重构与战斗系统设计专项/世界图数据模型设计.md`，对标 BG3 级 CRPG。

=== 模块关系 ===

本模块 (app.world) 是世界活图的数据层，后续 C2-C7 步骤将基于此构建。
与现有模块的关系：

  本模块新增                      替代/吸收的旧模块
  ─────────────────────────────────────────────────────────
  WorldNode(type=event_def)    ← AreaEvent (app/runtime/models/area_state.py)
                                  6 态状态机取代旧 4 态 (新增 failed + cooldown)
  WorldNode(type=player)       ← PlayerCharacter (app/models/player_character.py)
                                  统一为图节点，D&D 字段放 state Dict
  WorldNode(type=area/location)← AreaDefinition / SubLocationDef (app/runtime/models/area_state.py)
                                  静态属性 → properties, 运行时 → state
  WorldNode(type=chapter)      ← Chapter (app/models/narrative.py)
                                  events 改为独立 event_def 节点 + HAS_EVENT 边
  WorldEdgeType                ← CRPGRelationType (app/models/graph_nodes.py) 的世界结构子集
                                  旧类型用于知识图谱，新类型用于世界图
  EventStage / EventOutcome    ← StoryEvent.on_complete (app/models/narrative.py)
                                  结构化替代旧 Dict side_effects
  Behavior                     ← AreaRuntime.check_events() 的 8 个条件评估器
                                  声明式行为取代命令式 if-else 链
  WorldEvent                   ≠ Event (app/models/event.py)
                                  旧 Event 用于 GM 事件分发，WorldEvent 用于图内传播

  保留不变:
    ConditionGroup/Condition  — 从 app.models.narrative 直接复用
    MemoryNode/MemoryEdge     — WorldNode 继承 MemoryNode，GraphStore 兼容
    GraphScope                — 寻址系统不变
    WorldInstance 静态注册表   — monster/item/skill 仍在此，不进入 WorldGraph

依赖:
  - app.models.graph.MemoryNode    (基础图节点)
  - app.models.narrative.ConditionGroup (条件系统，直接复用)
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.graph import MemoryNode
from app.models.narrative import ConditionGroup


# =============================================================================
# Enums
# =============================================================================


class WorldNodeType(str, Enum):
    """世界图节点类型 (10 种)

    Z 轴层级: world → chapter → region → area → location → entities
    """
    WORLD = "world"           # Level 0: 全局唯一根节点
    CHAPTER = "chapter"       # Level 1: 叙事阶段
    REGION = "region"         # Level 2: 地理区域（新增层）
    AREA = "area"             # Level 3: 地图
    LOCATION = "location"     # Level 4: 子地点
    NPC = "npc"               # Level 5: NPC 实体
    PLAYER = "player"         # Level 5: 玩家实体
    EVENT_DEF = "event_def"   # Level 5: 故事事件定义
    CAMP = "camp"             # Level 1: 营地（全图唯一，与 chapter 同级）


class WorldEdgeType(str, Enum):
    """世界图边类型 (8 种)"""
    CONTAINS = "contains"       # 层级包含 (world→chapter→region→area→location)
    CONNECTS = "connects"       # 空间连接 (area↔area, location↔location)
    HOSTS = "hosts"             # 实体驻扎 (location→npc)
    HAS_EVENT = "has_event"     # 事件归属 (area→event_def)
    HAS_ITEM = "has_item"       # 物品放置 (location→item_instance)
    MEMBER_OF = "member_of"     # 成员关系 (npc/player→camp)
    GATE = "gate"               # 章节门控 (chapter→chapter)
    RELATES_TO = "relates_to"   # 角色关系 (npc↔npc)


class TriggerType(str, Enum):
    """行为触发器类型 (7 种)"""
    ON_TICK = "on_tick"                     # 每回合评估
    ON_ENTER = "on_enter"                   # 实体进入此节点作用域
    ON_EXIT = "on_exit"                     # 实体离开此节点作用域
    ON_EVENT = "on_event"                   # 收到传播来的 WorldEvent
    ON_STATE_CHANGE = "on_state_change"     # 自身或子节点 state 变化
    ON_DISPOSITION = "on_disposition"        # 好感度越过阈值
    ON_TIME = "on_time"                     # 游戏时间满足条件


class ActionType(str, Enum):
    """行为动作类型 (6 种)"""
    EMIT_EVENT = "emit_event"               # 发射 WorldEvent（触发传播）
    CHANGE_STATE = "change_state"           # 修改目标节点的 state
    NARRATIVE_HINT = "narrative_hint"        # 注入叙事指令给 LLM
    SPAWN = "spawn"                         # 创建新节点
    REMOVE = "remove"                       # 移除节点
    CHANGE_EDGE = "change_edge"             # 修改边属性


class EventStatus(str, Enum):
    """事件生命周期 6 态状态机

    状态转换:
      LOCKED → AVAILABLE → ACTIVE → COMPLETED / FAILED → COOLDOWN (可重复时)
                                                           ↓
                                                        AVAILABLE (重置后)

    替代旧 AreaEvent 的 4 态 (locked/available/active/completed)，
    新增 FAILED 和 COOLDOWN 支持失败路径与可重复事件。
    """
    LOCKED = "locked"           # 条件未满足，不可见
    AVAILABLE = "available"     # 条件满足，等待激活
    ACTIVE = "active"           # 进行中
    COMPLETED = "completed"     # 成功完成
    FAILED = "failed"           # 失败
    COOLDOWN = "cooldown"       # 可重复事件冷却中


class ChapterStatus(str, Enum):
    """章节状态 3 态"""
    LOCKED = "locked"           # 未解锁（GATE 条件未满足）
    ACTIVE = "active"           # 当前活跃章节
    COMPLETED = "completed"     # 已完成


# =============================================================================
# Behavior System
# =============================================================================


class Action(BaseModel):
    """行为动作

    params 按 ActionType:
      EMIT_EVENT:     {event_type: str, data: dict, visibility: str}
      CHANGE_STATE:   {updates: dict, merge: bool}  (merge=True 时浅合并)
      NARRATIVE_HINT: {text: str, priority: "high"/"normal"/"low"}
      SPAWN:          {node: dict, parent: str}
      REMOVE:         {}  (target 即要删除的节点)
      CHANGE_EDGE:    {source: str, target: str, updates: dict}
    """
    type: ActionType
    target: str = "self"    # 目标节点 ID / "self" / "parent" / "player"
    params: Dict[str, Any] = Field(default_factory=dict)


class Behavior(BaseModel):
    """节点行为: trigger + conditions → actions

    运行时状态存储在 node.state 中（非 Behavior 自身）:
      node.state["behavior_fired"]: list[str]     -- once 类型已触发的 behavior ID
      node.state["behavior_cooldowns"]: dict      -- {behavior_id: remaining_ticks}
    """
    id: str
    trigger: TriggerType

    # --- 触发器专用过滤器 ---
    event_filter: Optional[str] = None
    """ON_EVENT: 匹配的事件类型 (如 "combat_started")"""

    disposition_filter: Optional[Dict[str, Any]] = None
    """ON_DISPOSITION: {"dimension": "trust", "gte": 50} 或 {"dimension": "fear", "lte": -20}"""

    watch_key: Optional[str] = None
    """ON_STATE_CHANGE: 监听的 state key"""

    time_condition: Optional[Dict[str, Any]] = None
    """ON_TIME: {"hour_gte": 18, "hour_lte": 6} 或 {"day_gte": 3}"""

    # --- 条件 ---
    conditions: Optional[ConditionGroup] = None
    """完全复用 ConditionGroup (8 结构化 + 1 语义化条件类型)"""

    # --- 动作 ---
    actions: List[Action] = Field(default_factory=list)

    # --- 控制 ---
    priority: int = 0
    """同 tick 内执行顺序（数值大的先执行）"""

    once: bool = False
    """触发一次后永久禁用"""

    cooldown_ticks: int = 0
    """冷却回合数"""

    enabled: bool = True
    """可动态开关"""


# =============================================================================
# Event System Sub-models
# =============================================================================


class EventObjective(BaseModel):
    """可追踪的事件目标"""
    id: str                         # "obj_ask_guild"
    text: str                       # "询问公会情报"
    required: bool = True           # true=必须完成, false=可选
    completion_hint: str = ""       # 给 LLM: "当玩家与公会柜台交谈后标记完成"


class EventStage(BaseModel):
    """事件阶段（多阶段任务）"""
    id: str                                             # "stage_1"
    name: str                                           # "找到哥布林巢穴入口"
    description: str = ""
    narrative_directive: str = ""                        # 给 LLM 的指令
    objectives: List[EventObjective] = Field(default_factory=list)
    completion_conditions: Optional[ConditionGroup] = None


class EventOutcome(BaseModel):
    """事件分支结局

    每个 outcome 带 conditions（Option B: LLM 调用时必须满足，否则拒绝）。
    """
    description: str                                    # "成功清除了哥布林巢穴"
    conditions: Optional[ConditionGroup] = None         # 验证条件
    rewards: Dict[str, Any] = Field(default_factory=dict)
    """奖励: {xp: int, gold: int, items: list[str]}"""

    reputation_changes: Dict[str, int] = Field(default_factory=dict)
    """阵营声望变化: {"adventurer_guild": +10}"""

    unlock_events: List[str] = Field(default_factory=list)
    """解锁的 event_def ID"""

    world_flags: Dict[str, Any] = Field(default_factory=dict)
    """设置世界标记: {"goblin_nest_cleared": true}"""

    narrative_hint: str = ""
    """结局叙事指令"""


# =============================================================================
# Core Graph Models
# =============================================================================


class WorldNode(MemoryNode):
    """世界图节点 -- 扩展 MemoryNode

    继承字段 (from MemoryNode):
      id, type, name, importance, properties: Dict, created_at, updated_at

    新增字段:
      state: Dict       -- 可变运行时状态
      behaviors: list    -- 行为定义列表

    各节点类型的 properties / state 字段约定:
      见设计文档第三章 (不是强制 schema，是数据规范)。
      辅助函数: constants.default_character_state() / default_npc_state() / default_player_state()
    """
    state: Dict[str, Any] = Field(default_factory=dict)
    behaviors: List[Behavior] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

    def get_state(self, key: str, default: Any = None) -> Any:
        """便捷读取 state 字段"""
        return self.state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """便捷写入 state 字段（同时更新 updated_at）"""
        self.state[key] = value
        self.updated_at = datetime.now()

    def merge_state(self, updates: Dict[str, Any]) -> None:
        """浅合并 state（同时更新 updated_at）"""
        self.state.update(updates)
        self.updated_at = datetime.now()

    def get_active_behaviors(self, trigger: TriggerType) -> List[Behavior]:
        """获取指定触发器类型的活跃行为（已启用且未消耗）"""
        fired = set(self.state.get("behavior_fired", []))
        cooldowns = self.state.get("behavior_cooldowns", {})
        return [
            b for b in self.behaviors
            if b.trigger == trigger
            and b.enabled
            and not (b.once and b.id in fired)
            and cooldowns.get(b.id, 0) <= 0
        ]

    def mark_behavior_fired(self, behavior_id: str) -> None:
        """标记 once-type behavior 为已触发"""
        fired = self.state.setdefault("behavior_fired", [])
        if behavior_id not in fired:
            fired.append(behavior_id)

    def set_behavior_cooldown(self, behavior_id: str, ticks: int) -> None:
        """设置 behavior 冷却"""
        cooldowns = self.state.setdefault("behavior_cooldowns", {})
        cooldowns[behavior_id] = ticks

    def tick_cooldowns(self) -> None:
        """每回合递减冷却计数"""
        cooldowns = self.state.get("behavior_cooldowns", {})
        expired = []
        for bid, remaining in cooldowns.items():
            if remaining > 0:
                cooldowns[bid] = remaining - 1
            if cooldowns[bid] <= 0:
                expired.append(bid)
        for bid in expired:
            del cooldowns[bid]


# =============================================================================
# World Event (Propagation)
# =============================================================================


class WorldEvent(BaseModel):
    """传播事件 -- 在图中传播的事件信号

    区别于 app.models.event.Event (GM 事件分发)。
    WorldEvent 由 Behavior(EMIT_EVENT) 产生，通过 EventPropagator 沿边传播。
    """
    event_id: str = Field(default_factory=lambda: f"we_{uuid4().hex[:8]}")
    event_type: str                     # combat_started / quest_completed / npc_killed / ...
    origin_node: str                    # 事件发生的节点 ID
    timestamp: datetime = Field(default_factory=datetime.now)
    game_day: int = 0
    game_hour: int = 0
    actor: str = ""                     # 发起者: "player" / npc_id
    data: Dict[str, Any] = Field(default_factory=dict)

    # 传播控制
    visibility: Literal["local", "scope", "global"] = "scope"
    """local=仅本节点, scope=沿 CONTAINS 边向上/向下, global=全图"""

    strength: float = 1.0
    """初始强度（沿边传播时被衰减因子削弱）"""

    min_strength: float = 0.1
    """低于此阈值停止传播"""


# =============================================================================
# Tick Context
# =============================================================================


class TickContext(BaseModel):
    """Tick 评估上下文 -- 传递给 BehaviorEngine.tick()

    由 PipelineOrchestrator 在 A/C 阶段构造。
    包含 ConditionEvaluator 评估所需的全部上下文（解耦 SessionRuntime）。
    """
    session: Any = None                 # SessionRuntime (避免循环导入)
    phase: Literal["pre", "post"] = "pre"
    """pre = A4 阶段前检查, post = C1 阶段后检查"""

    player_location: str = ""
    game_day: int = 1
    game_hour: int = 8
    active_chapter: str = ""
    party_members: List[str] = Field(default_factory=list)
    events_triggered: List[str] = Field(default_factory=list)
    objectives_completed: List[str] = Field(default_factory=list)
    round_count: int = 0

    # ConditionEvaluator 需要的额外字段 (C4)
    player_sub_location: str = ""
    """当前子地点 ID (LOCATION condition)"""

    npc_interactions: Dict[str, int] = Field(default_factory=dict)
    """NPC 交互次数: {npc_id: count} (NPC_INTERACTED condition)"""

    game_state: str = ""
    """当前游戏状态字符串 (GAME_STATE condition)"""

    world_flags: Dict[str, Any] = Field(default_factory=dict)
    """世界标记 (备用扩展)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


# =============================================================================
# Behavior Engine Output
# =============================================================================


class BehaviorResult(BaseModel):
    """单个 Behavior 触发后的执行结果

    BehaviorEngine.evaluate() 返回 List[BehaviorResult]，
    供 PipelineOrchestrator 决定后续处理（应用 state 变更、传播事件、注入叙事）。
    """
    behavior_id: str
    """触发的 behavior ID"""

    node_id: str
    """behavior 所属节点 ID"""

    trigger: TriggerType
    """触发器类型"""

    actions_executed: List[Action] = Field(default_factory=list)
    """实际执行的 actions"""

    events_emitted: List[WorldEvent] = Field(default_factory=list)
    """EMIT_EVENT 动作产生的 WorldEvent（待 EventPropagator 传播）"""

    state_changes: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    """CHANGE_STATE 动作产生的变更: {node_id: {key: new_value}}"""

    narrative_hints: List[str] = Field(default_factory=list)
    """NARRATIVE_HINT 动作产生的叙事指令（注入 LLM 上下文）"""

    pending_flash: List[Any] = Field(default_factory=list)
    """含 FLASH_EVALUATE 条件时，待 LLM 评估的条件对象"""


class EvalResult(BaseModel):
    """条件评估结果 -- ConditionEvaluator.evaluate() 的返回值"""
    satisfied: bool = True
    """结构化条件是否全部满足"""

    pending_flash: List[Any] = Field(default_factory=list)
    """待 LLM 评估的 FLASH_EVALUATE 条件对象"""

    details: Dict[str, Any] = Field(default_factory=dict)
    """各条件的评估详情: {condition_key: bool}"""


class TickResult(BaseModel):
    """BehaviorEngine.tick() 的完整返回值"""
    results: List[BehaviorResult] = Field(default_factory=list)
    """所有触发的 behavior 结果"""

    all_events: List[WorldEvent] = Field(default_factory=list)
    """本次 tick 产生的所有 WorldEvent（含级联）"""

    narrative_hints: List[str] = Field(default_factory=list)
    """收集的叙事指令（注入 LLM 上下文）"""

    pending_flash: List[Any] = Field(default_factory=list)
    """待 LLM 评估的 FLASH_EVALUATE 条件"""

    state_changes: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    """本次 tick 的 state 变更汇总: {node_id: {key: new_value}}"""
