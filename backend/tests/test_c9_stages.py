"""C9 Stages/Outcomes/Objectives Integration Tests

U4: EventStage 推进 (auto + manual)
U5: EventOutcome 自动判定 + 手动选择
U6: EventObjective 追踪
U11: advance_stage 工具
U21: event_def 属性透传
U22: event_def 状态初始化
P10: 上下文输出增强
6a: 状态守卫
6b: 同回合阶段连跳防护
6c: complete_event_objective 工具
6f: 老数据兼容
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub out 'mcp' package if not installed
if "mcp" not in sys.modules:
    _mcp_stub = ModuleType("mcp")
    _mcp_client = ModuleType("mcp.client")
    _mcp_session = ModuleType("mcp.client.session")
    _mcp_session.ClientSession = MagicMock  # type: ignore
    _mcp_stdio = ModuleType("mcp.client.stdio")
    _mcp_stdio.stdio_client = MagicMock  # type: ignore
    _mcp_stdio.StdioServerParameters = MagicMock  # type: ignore
    _mcp_sse = ModuleType("mcp.client.sse")
    _mcp_sse.sse_client = MagicMock  # type: ignore
    _mcp_http = ModuleType("mcp.client.streamable_http")
    _mcp_http.streamable_http_client = MagicMock  # type: ignore
    _mcp_types = ModuleType("mcp.types")
    _mcp_types.Tool = MagicMock  # type: ignore

    sys.modules["mcp"] = _mcp_stub
    sys.modules["mcp.client"] = _mcp_client
    sys.modules["mcp.client.session"] = _mcp_session
    sys.modules["mcp.client.stdio"] = _mcp_stdio
    sys.modules["mcp.client.sse"] = _mcp_sse
    sys.modules["mcp.client.streamable_http"] = _mcp_http
    sys.modules["mcp.types"] = _mcp_types

from app.models.narrative import (
    Condition,
    ConditionGroup,
    ConditionType,
    EventObjective,
    EventOutcome,
    EventStage,
    NarrativeProgress,
    StoryEvent,
)
from app.world.behavior_engine import BehaviorEngine, ConditionEvaluator
from app.world.models import (
    Action,
    ActionType,
    Behavior,
    EventStatus,
    TickContext,
    TriggerType,
    WorldEdgeType,
    WorldNode,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures
# =============================================================================


def _make_staged_event() -> StoryEvent:
    """创建一个带 3 阶段 + 2 outcomes 的测试事件。"""
    return StoryEvent(
        id="evt_goblin_quest",
        name="哥布林巢穴",
        description="清除哥布林巢穴",
        trigger_conditions=ConditionGroup(conditions=[]),
        completion_conditions=None,  # 由 stages 控制
        on_complete={"add_xp": 100, "unlock_events": ["evt_reward"]},
        is_required=True,
        narrative_directive="展现战斗紧张感",
        activation_type="npc_given",
        importance="main",
        quest_giver="guild_master",
        stages=[
            EventStage(
                id="stage_find",
                name="找到巢穴入口",
                objectives=[
                    EventObjective(id="obj_talk_scout", text="与侦察兵交谈", required=True),
                    EventObjective(id="obj_find_map", text="找到地图", required=False),
                ],
                completion_conditions=ConditionGroup(conditions=[
                    Condition(type=ConditionType.OBJECTIVE_COMPLETED, params={"objective_id": "obj_talk_scout"}),
                ]),
            ),
            EventStage(
                id="stage_fight",
                name="战斗",
                narrative_directive="激烈的战斗场景",
                objectives=[],
                completion_conditions=None,  # 手动推进
            ),
            EventStage(
                id="stage_loot",
                name="搜刮战利品",
                objectives=[
                    EventObjective(id="obj_open_chest", text="打开宝箱", required=True),
                ],
                completion_conditions=ConditionGroup(conditions=[
                    Condition(type=ConditionType.OBJECTIVE_COMPLETED, params={"objective_id": "obj_open_chest"}),
                ]),
            ),
        ],
        outcomes={
            "victory": EventOutcome(
                description="成功清除巢穴",
                conditions=ConditionGroup(conditions=[
                    Condition(type=ConditionType.EVENT_TRIGGERED, params={"event_id": "evt_goblin_quest"}),
                ]),
                rewards={"xp": 50, "gold": 200},
                reputation_changes={"adventurer_guild": 10},
                world_flags={"goblin_nest_cleared": True},
                unlock_events=["evt_celebration"],
            ),
            "mercy": EventOutcome(
                description="放过了哥布林首领",
                conditions=None,  # 无条件, fallback
                rewards={"xp": 25},
            ),
        },
    )


def _make_simple_event() -> StoryEvent:
    """创建一个无 stages 的简单事件。"""
    return StoryEvent(
        id="evt_simple",
        name="简单事件",
        description="测试用简单事件",
        trigger_conditions=ConditionGroup(conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "town_square"}),
        ]),
        completion_conditions=ConditionGroup(conditions=[
            Condition(type=ConditionType.NPC_INTERACTED, params={"npc_id": "merchant", "min_interactions": 1}),
        ]),
        on_complete={"add_xp": 50},
    )


def _build_test_graph(events: list[StoryEvent], area_id: str = "town_square") -> WorldGraph:
    """构建包含指定事件的测试 WorldGraph。"""
    from app.world.graph_builder import _event_to_behaviors

    wg = WorldGraph()

    # world_root
    wg.add_node(WorldNode(
        id="world_root",
        type=WorldNodeType.WORLD,
        name="Test World",
    ))

    # area
    wg.add_node(WorldNode(
        id=area_id,
        type=WorldNodeType.AREA,
        name="Town Square",
        state={"visited": True},
    ))
    wg.add_edge("world_root", area_id, WorldEdgeType.CONTAINS.value)

    # events
    for event in events:
        evt_props = {
            "chapter_id": "ch1",
            "narrative_directive": event.narrative_directive,
            "is_required": event.is_required,
            "is_repeatable": event.is_repeatable,
            "importance": event.importance,
        }
        if event.description:
            evt_props["description"] = event.description
        if event.on_complete:
            evt_props["on_complete"] = event.on_complete
        if event.completion_conditions:
            evt_props["completion_conditions"] = event.completion_conditions.model_dump()
        if event.stages:
            evt_props["stages"] = [s.model_dump() for s in event.stages]
        if event.outcomes:
            evt_props["outcomes"] = {k: v.model_dump() for k, v in event.outcomes.items()}
        for key in ("activation_type", "quest_giver", "time_limit", "visibility"):
            val = getattr(event, key, None)
            if val is not None and val != "" and val != "event_driven" and val != "visible":
                evt_props[key] = val

        evt_state = {
            "status": EventStatus.LOCKED,
            "current_stage": None,
            "stage_progress": {},
            "objective_progress": {},
            "outcome": None,
            "activated_at_round": None,
            "rounds_elapsed": 0,
        }

        behaviors = _event_to_behaviors(event, event.id, "ch1")

        wg.add_node(WorldNode(
            id=event.id,
            type=WorldNodeType.EVENT_DEF,
            name=event.name,
            properties=evt_props,
            state=evt_state,
            behaviors=behaviors,
        ))
        wg.add_edge(area_id, event.id, WorldEdgeType.HAS_EVENT.value,
                     key=f"has_event_{event.id}")

    wg.seal()
    return wg


def _make_ctx(wg: WorldGraph, session=None, **overrides) -> TickContext:
    """构建测试用 TickContext。"""
    defaults = {
        "session": session,
        "phase": "pre",
        "player_location": "town_square",
        "game_day": 1,
        "game_hour": 10,
        "active_chapter": "ch1",
        "party_members": [],
        "events_triggered": [],
        "objectives_completed": [],
        "round_count": 5,
        "npc_interactions": {},
        "game_state": "",
    }
    defaults.update(overrides)
    return TickContext(**defaults)


class _MockSession:
    """最小模拟 SessionRuntime，供 _eval_event_state 读取 world_graph。"""
    def __init__(self, wg: WorldGraph):
        self.world_graph = wg
        self._world_graph_failed = False
        self.narrative = NarrativeProgress(
            current_mainline="ml1", current_chapter="ch1",
        )
        self._applied_side_effect_events = set()
        self._behavior_engine = BehaviorEngine(wg)
        self.player = None
        self.player_location = "town_square"
        self.time = None
        self.companions = {}
        self.world = None
        self.current_area = None

    def build_tick_context(self, phase="pre"):
        return _make_ctx(self.world_graph, session=self, phase=phase)

    def mark_narrative_dirty(self):
        pass

    def mark_player_dirty(self):
        pass

    def _sync_tick_to_narrative(self, tick_result):
        """模拟 SessionRuntime._sync_tick_to_narrative。"""
        if not self.narrative or not self.world_graph:
            return
        for nid, changes in tick_result.state_changes.items():
            if changes.get("status") != EventStatus.COMPLETED:
                continue
            node = self.world_graph.get_node(nid)
            if not node or node.type != "event_def":
                continue
            if nid not in self.narrative.events_triggered:
                self.narrative.events_triggered.append(nid)


# =============================================================================
# U21: event_def 属性透传
# =============================================================================


class TestEventPropertiesTransparent:
    def test_event_properties_transparent(self):
        """graph_builder 透传 stages/outcomes/activation_type 到 node.properties"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        node = wg.get_node("evt_goblin_quest")

        assert node is not None
        assert node.properties["importance"] == "main"
        assert node.properties.get("quest_giver") == "guild_master"
        assert node.properties.get("activation_type") == "npc_given"
        assert len(node.properties["stages"]) == 3
        assert "victory" in node.properties["outcomes"]
        assert "mercy" in node.properties["outcomes"]

    def test_simple_event_no_extra_props(self):
        """简单事件不会写入空的 stages/outcomes。"""
        event = _make_simple_event()
        wg = _build_test_graph([event])
        node = wg.get_node("evt_simple")

        assert node is not None
        assert "stages" not in node.properties
        assert "outcomes" not in node.properties


# =============================================================================
# U22: event_def 状态初始化
# =============================================================================


class TestEventStateInitialization:
    def test_event_state_initialization(self):
        """新建事件节点包含所有初始 state 字段。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        node = wg.get_node("evt_goblin_quest")

        assert node.state["status"] == EventStatus.LOCKED
        assert node.state["current_stage"] is None
        assert node.state["stage_progress"] == {}
        assert node.state["objective_progress"] == {}
        assert node.state["outcome"] is None
        assert node.state["activated_at_round"] is None
        assert node.state["rounds_elapsed"] == 0


# =============================================================================
# U4: EVENT_STATE 条件类型
# =============================================================================


class TestEventStateCondition:
    def test_event_state_condition(self):
        """EVENT_STATE 条件正确读取图节点 state。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        ctx = _make_ctx(wg, session=session)

        evaluator = ConditionEvaluator()

        # status == LOCKED → 满足
        cond = Condition(
            type=ConditionType.EVENT_STATE,
            params={"event_id": "evt_goblin_quest", "key": "status", "value": EventStatus.LOCKED},
        )
        result = evaluator.evaluate(ConditionGroup(conditions=[cond]), ctx)
        assert result.satisfied is True

        # status == ACTIVE → 不满足（当前是 LOCKED）
        cond2 = Condition(
            type=ConditionType.EVENT_STATE,
            params={"event_id": "evt_goblin_quest", "key": "status", "value": EventStatus.ACTIVE},
        )
        result2 = evaluator.evaluate(ConditionGroup(conditions=[cond2]), ctx)
        assert result2.satisfied is False

    def test_event_state_condition_no_session(self):
        """ctx.session=None 时 EVENT_STATE 返回 satisfied=False。"""
        ctx = _make_ctx(WorldGraph(), session=None)
        evaluator = ConditionEvaluator()
        cond = Condition(
            type=ConditionType.EVENT_STATE,
            params={"event_id": "evt_x", "key": "status", "value": "locked"},
        )
        result = evaluator.evaluate(ConditionGroup(conditions=[cond]), ctx)
        assert result.satisfied is False

    def test_event_state_condition_node_not_found(self):
        """事件节点不存在时返回 satisfied=False。"""
        wg = WorldGraph()
        wg.add_node(WorldNode(id="world_root", type=WorldNodeType.WORLD, name="W"))
        session = _MockSession(wg)
        ctx = _make_ctx(wg, session=session)

        evaluator = ConditionEvaluator()
        cond = Condition(
            type=ConditionType.EVENT_STATE,
            params={"event_id": "nonexistent", "key": "status", "value": "locked"},
        )
        result = evaluator.evaluate(ConditionGroup(conditions=[cond]), ctx)
        assert result.satisfied is False


# =============================================================================
# 6a: 状态守卫
# =============================================================================


class TestStatusGuards:
    def test_unlock_behavior_status_guard(self):
        """unlock behavior 只在 LOCKED 状态触发。"""
        event = _make_simple_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        # 先手动设为 AVAILABLE
        wg.merge_state("evt_simple", {"status": EventStatus.AVAILABLE})

        ctx = _make_ctx(wg, session=session, player_location="town_square")
        result = engine.tick(ctx)

        # unlock behavior 不应该触发（因为状态不是 LOCKED）
        unlock_fired = any(
            r.behavior_id == "bh_unlock_evt_simple" for r in result.results
        )
        assert not unlock_fired

    def test_complete_behavior_status_guard(self):
        """complete behavior 只在 ACTIVE 状态触发。"""
        event = _make_simple_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        # 设为 LOCKED (初始状态)，complete 不应触发
        ctx = _make_ctx(
            wg, session=session,
            player_location="town_square",
            npc_interactions={"merchant": 1},
        )
        result = engine.tick(ctx)
        complete_fired = any(
            r.behavior_id == "bh_complete_evt_simple" for r in result.results
        )
        assert not complete_fired


# =============================================================================
# U4: 阶段推进
# =============================================================================


class TestStageProgressionAuto:
    def test_stage_progression_auto(self):
        """有 completion_conditions 的 stage，tick 自动推进。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        # 手动设为 ACTIVE + 第一阶段
        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_find",
        })

        # stage_find 需要 OBJECTIVE_COMPLETED(obj_talk_scout)
        ctx = _make_ctx(
            wg, session=session,
            objectives_completed=["obj_talk_scout"],
        )
        result = engine.tick(ctx)

        # stage_find 的 behavior 应该触发，推进到 stage_fight
        node = wg.get_node("evt_goblin_quest")
        assert node.state["current_stage"] == "stage_fight"

    def test_last_stage_completes_event(self):
        """最后阶段完成 → 事件 COMPLETED + 副作用。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        # 设为 ACTIVE + 最后阶段
        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_loot",
        })

        # stage_loot 需要 OBJECTIVE_COMPLETED(obj_open_chest)
        ctx = _make_ctx(
            wg, session=session,
            objectives_completed=["obj_open_chest"],
        )
        result = engine.tick(ctx)

        node = wg.get_node("evt_goblin_quest")
        assert node.state["status"] == EventStatus.COMPLETED
        assert node.state["current_stage"] == "__completed__"

        # 验证有 on_complete 副作用事件（xp_awarded）
        xp_events = [e for e in result.all_events if e.event_type == "xp_awarded"]
        assert len(xp_events) > 0

    def test_activate_initializes_stage(self):
        """activate_event 初始化 current_stage 为第一阶段。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])

        # 模拟激活: 设为 AVAILABLE 先
        wg.merge_state("evt_goblin_quest", {"status": EventStatus.AVAILABLE})

        # 模拟 activate_event 的逻辑
        node = wg.get_node("evt_goblin_quest")
        wg.merge_state("evt_goblin_quest", {"status": EventStatus.ACTIVE})
        stages = node.properties.get("stages", [])
        if stages:
            first_stage_id = stages[0]["id"] if isinstance(stages[0], dict) else stages[0].id
            wg.merge_state("evt_goblin_quest", {"current_stage": first_stage_id})

        node = wg.get_node("evt_goblin_quest")
        assert node.state["current_stage"] == "stage_find"
        assert node.state["status"] == EventStatus.ACTIVE


class TestStageProgressionManual:
    def test_stage_progression_manual(self):
        """无 completion_conditions 的 stage，advance_stage 手动推进。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])

        # stage_fight 没有 completion_conditions → tick 不会自动推进
        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_fight",
        })

        session = _MockSession(wg)
        engine = BehaviorEngine(wg)
        ctx = _make_ctx(wg, session=session)

        result = engine.tick(ctx)

        # 不应该有 stage_fight 相关的推进
        node = wg.get_node("evt_goblin_quest")
        assert node.state["current_stage"] == "stage_fight"


# =============================================================================
# U6: EventObjective 追踪
# =============================================================================


class TestStageObjectivesTracking:
    def test_stage_objectives_tracking(self):
        """objective_progress 正确写入 event_def.state。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_find",
        })

        # 手动标记 objective 完成
        obj_progress = dict(wg.get_node("evt_goblin_quest").state.get("objective_progress", {}))
        obj_progress["obj_talk_scout"] = True
        wg.merge_state("evt_goblin_quest", {"objective_progress": obj_progress})

        node = wg.get_node("evt_goblin_quest")
        assert node.state["objective_progress"]["obj_talk_scout"] is True
        assert node.state["objective_progress"].get("obj_find_map") is None


# =============================================================================
# U5: EventOutcome
# =============================================================================


class TestOutcomeAutoEvaluation:
    def test_outcome_auto_evaluation(self):
        """有条件的 outcome Behavior 自动判定。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        # 设为 COMPLETED + outcome == None
        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.COMPLETED,
            "outcome": None,
        })

        # victory 的条件: EVENT_TRIGGERED(evt_goblin_quest)
        ctx = _make_ctx(
            wg, session=session,
            events_triggered=["evt_goblin_quest"],
        )
        result = engine.tick(ctx)

        node = wg.get_node("evt_goblin_quest")
        assert node.state["outcome"] == "victory"

    def test_outcome_mutual_exclusion(self):
        """多个 outcome 条件同时满足时只选第一个（priority 排序已处理）。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.COMPLETED,
            "outcome": None,
        })

        # victory 条件满足
        ctx = _make_ctx(
            wg, session=session,
            events_triggered=["evt_goblin_quest"],
        )
        result = engine.tick(ctx)

        node = wg.get_node("evt_goblin_quest")
        # outcome 已被设为 victory，mercy 不会触发（因为 outcome != None 了）
        assert node.state["outcome"] == "victory"

        # 再次 tick 不应改变 outcome
        result2 = engine.tick(ctx)
        assert node.state["outcome"] == "victory"


# =============================================================================
# 6b: 同回合阶段连跳防护
# =============================================================================


class TestMultiTickNoStageSkip:
    def test_multi_tick_no_stage_skip(self):
        """同回合 pre+post tick 不会导致意外阶段连跳（单次 tick 内不跳）。"""
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)
        engine = BehaviorEngine(wg)

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_find",
        })

        # stage_find 条件满足
        ctx = _make_ctx(
            wg, session=session,
            objectives_completed=["obj_talk_scout"],
        )

        # 单次 tick: stage_find → stage_fight（stage_fight 无条件，不会连跳）
        result = engine.tick(ctx)
        node = wg.get_node("evt_goblin_quest")
        assert node.state["current_stage"] == "stage_fight"

        # 再次 tick: stage_fight 无 completion_conditions，不会推进
        result2 = engine.tick(ctx)
        assert node.state["current_stage"] == "stage_fight"


# =============================================================================
# P10: 上下文输出
# =============================================================================


class TestContextOutputWithStages:
    def test_context_output_with_stages(self):
        """get_event_summaries_from_graph 输出含 stage/objective/outcome 信息。"""
        from app.runtime.session_runtime import SessionRuntime

        event = _make_staged_event()
        wg = _build_test_graph([event])

        # 设为 ACTIVE + 第一阶段 + 部分 objectives 完成
        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_find",
            "objective_progress": {"obj_talk_scout": True},
        })

        # 创建 minimal SessionRuntime mock
        session = MagicMock()
        session.world_graph = wg
        session.player_location = "town_square"
        session._world_graph_failed = False

        # 直接调用方法
        rt = SessionRuntime.__new__(SessionRuntime)
        rt.world_graph = wg
        summaries = rt.get_event_summaries_from_graph("town_square")

        assert len(summaries) == 1
        entry = summaries[0]
        assert entry["id"] == "evt_goblin_quest"
        assert entry["status"] == EventStatus.ACTIVE
        assert "current_stage" in entry
        assert entry["current_stage"]["id"] == "stage_find"
        assert len(entry["current_stage"]["objectives"]) == 2
        # obj_talk_scout is completed
        scout_obj = next(o for o in entry["current_stage"]["objectives"] if o["id"] == "obj_talk_scout")
        assert scout_obj["completed"] is True
        # obj_find_map is not
        map_obj = next(o for o in entry["current_stage"]["objectives"] if o["id"] == "obj_find_map")
        assert map_obj["completed"] is False
        assert "stage_progress" in entry
        assert "available_outcomes" in entry
        assert len(entry["available_outcomes"]) == 2

    def test_old_snapshot_compat(self):
        """老数据缺少新字段时 get_event_summaries 正常工作。"""
        from app.runtime.session_runtime import SessionRuntime

        wg = WorldGraph()
        wg.add_node(WorldNode(id="world_root", type=WorldNodeType.WORLD, name="W"))
        wg.add_node(WorldNode(
            id="area1", type=WorldNodeType.AREA, name="Area 1",
        ))
        wg.add_edge("world_root", "area1", WorldEdgeType.CONTAINS.value)

        # 老格式事件: 只有 status，无 current_stage/stages/outcomes
        wg.add_node(WorldNode(
            id="evt_old",
            type=WorldNodeType.EVENT_DEF,
            name="Old Event",
            properties={"description": "An old event", "importance": "side"},
            state={"status": EventStatus.ACTIVE},
        ))
        wg.add_edge("area1", "evt_old", WorldEdgeType.HAS_EVENT.value, key="has_evt_old")
        wg.seal()

        rt = SessionRuntime.__new__(SessionRuntime)
        rt.world_graph = wg
        summaries = rt.get_event_summaries_from_graph("area1")

        assert len(summaries) == 1
        entry = summaries[0]
        assert entry["id"] == "evt_old"
        assert entry["status"] == EventStatus.ACTIVE
        # 新字段不应存在（因为老数据没有 stages/outcomes）
        assert "current_stage" not in entry
        assert "available_outcomes" not in entry


# =============================================================================
# 6c: complete_event_objective 工具 + advance_stage 工具 (async)
# =============================================================================


class TestCompleteEventObjectiveTool:
    def _setup(self):
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_find",
        })

        return wg, session

    def test_complete_event_objective_tool(self):
        """事件目标完成写入 event_def.state。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event_objective("evt_goblin_quest", "obj_talk_scout")

        result = asyncio.run(_run())
        assert result["success"] is True
        assert result["objective_id"] == "obj_talk_scout"

        node = wg.get_node("evt_goblin_quest")
        assert node.state["objective_progress"]["obj_talk_scout"] is True

    def test_complete_event_objective_not_found(self):
        """目标不在当前 stage 中时返回错误。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event_objective("evt_goblin_quest", "nonexistent_obj")

        result = asyncio.run(_run())
        assert result["success"] is False

    def test_complete_event_objective_already_done(self):
        """已完成的目标不能重复标记。"""
        wg, session = self._setup()

        wg.merge_state("evt_goblin_quest", {
            "objective_progress": {"obj_talk_scout": True},
        })

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event_objective("evt_goblin_quest", "obj_talk_scout")

        result = asyncio.run(_run())
        assert result["success"] is False


class TestAdvanceStageTool:
    def _setup(self):
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
            "current_stage": "stage_fight",
        })

        return wg, session

    def test_advance_stage_manual(self):
        """advance_stage 手动推进到下一阶段。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.advance_stage("evt_goblin_quest")

        result = asyncio.run(_run())
        assert result["success"] is True
        assert result["new_stage"] == "stage_loot"

        node = wg.get_node("evt_goblin_quest")
        assert node.state["current_stage"] == "stage_loot"
        assert node.state["stage_progress"]["stage_fight"] is True

    def test_advance_stage_validates_objectives(self):
        """required objectives 未完成时拒绝推进。"""
        wg, session = self._setup()

        # 设为 stage_find (有 required objectives)
        wg.merge_state("evt_goblin_quest", {"current_stage": "stage_find"})

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.advance_stage("evt_goblin_quest")

        result = asyncio.run(_run())
        assert result["success"] is False
        assert "obj_talk_scout" in str(result)

    def test_advance_stage_last_auto_completes(self):
        """最后一个 stage 推进时自动调 complete_event。"""
        wg, session = self._setup()

        wg.merge_state("evt_goblin_quest", {
            "current_stage": "stage_loot",
            "objective_progress": {"obj_open_chest": True},
        })

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.advance_stage("evt_goblin_quest")

        result = asyncio.run(_run())
        assert result["success"] is True
        assert result.get("auto_completed") is True

        node = wg.get_node("evt_goblin_quest")
        assert node.state["status"] == EventStatus.COMPLETED

    def test_advance_stage_specific_target(self):
        """advance_stage 可以指定目标 stage_id。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.advance_stage("evt_goblin_quest", stage_id="stage_loot")

        result = asyncio.run(_run())
        assert result["success"] is True
        assert result["new_stage"] == "stage_loot"


# =============================================================================
# U5: complete_event with outcome_key (async)
# =============================================================================


class TestCompleteEventOutcome:
    def _setup(self):
        event = _make_staged_event()
        wg = _build_test_graph([event])
        session = _MockSession(wg)

        wg.merge_state("evt_goblin_quest", {
            "status": EventStatus.ACTIVE,
        })

        return wg, session

    def test_outcome_manual_selection(self):
        """complete_event(outcome_key=...) 手动选择 + 无条件 outcome。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event("evt_goblin_quest", outcome_key="mercy")

        # mercy 没有 conditions → 直接成功
        result = asyncio.run(_run())
        assert result["success"] is True
        assert result["outcome"] == "mercy"

        node = wg.get_node("evt_goblin_quest")
        assert node.state["status"] == EventStatus.COMPLETED
        assert node.state["outcome"] == "mercy"

    def test_outcome_condition_rejected(self):
        """outcome 条件不满足时拒绝 + 状态回滚。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        # victory 需要 EVENT_TRIGGERED(evt_goblin_quest)，但 events_triggered 为空
        session.narrative.events_triggered = []

        async def _run():
            return await registry.complete_event("evt_goblin_quest", outcome_key="victory")

        result = asyncio.run(_run())
        assert result["success"] is False
        assert "conditions not met" in result["error"].lower() or "not met" in result["error"]

        # 状态回滚
        node = wg.get_node("evt_goblin_quest")
        assert node.state["status"] == EventStatus.ACTIVE

    def test_complete_event_outcome_rollback(self):
        """outcome 条件不满足时状态回滚，副作用不落地。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event("evt_goblin_quest", outcome_key="victory")

        result = asyncio.run(_run())
        assert result["success"] is False

        node = wg.get_node("evt_goblin_quest")
        assert node.state["status"] == EventStatus.ACTIVE
        assert node.state.get("outcome") is None

    def test_unknown_outcome_key(self):
        """未知 outcome_key 返回错误。"""
        wg, session = self._setup()

        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
        registry = V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

        async def _run():
            return await registry.complete_event("evt_goblin_quest", outcome_key="nonexistent")

        result = asyncio.run(_run())
        assert result["success"] is False
        assert "available_outcomes" in result
