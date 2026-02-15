"""
Tests for BehaviorEngine (C4).

测试:
  - ConditionEvaluator: 9 种条件类型 + 组合逻辑
  - ActionExecutor: 6 种 ActionType
  - BehaviorEngine: tick / handle_event / handle_enter / handle_exit / 级联限制
"""
import pytest

from app.models.narrative import Condition, ConditionGroup, ConditionType
from app.world.behavior_engine import (
    ActionExecutor,
    BehaviorEngine,
    ConditionEvaluator,
    _ActionResult,
)
from app.world.models import (
    Action,
    ActionType,
    Behavior,
    EvalResult,
    EventStatus,
    TickContext,
    TickResult,
    TriggerType,
    WorldEdgeType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Helpers
# =============================================================================


def _ctx(**kwargs) -> TickContext:
    """快捷构建 TickContext。"""
    defaults = {
        "player_location": "area_1",
        "player_sub_location": "loc_guild",
        "game_day": 3,
        "game_hour": 14,
        "active_chapter": "ch_1",
        "party_members": ["npc_a", "npc_b"],
        "events_triggered": ["evt_intro"],
        "objectives_completed": ["obj_talk"],
        "round_count": 5,
        "npc_interactions": {"npc_a": 3, "npc_b": 1},
        "game_state": "exploring",
    }
    defaults.update(kwargs)
    return TickContext(**defaults)


def _make_simple_graph() -> WorldGraph:
    """world_root → region → area_1 → loc_1, with npc and event."""
    wg = WorldGraph()
    wg.add_node(WorldNode(id="world_root", type=WorldNodeType.WORLD, name="World"))
    wg.add_node(WorldNode(id="region_a", type=WorldNodeType.REGION, name="Region"))
    wg.add_node(WorldNode(id="area_1", type=WorldNodeType.AREA, name="Area 1"))
    wg.add_node(WorldNode(id="loc_1", type=WorldNodeType.LOCATION, name="Loc 1"))
    wg.add_node(WorldNode(id="camp", type=WorldNodeType.CAMP, name="Camp"))

    wg.add_edge("world_root", "region_a", WorldEdgeType.CONTAINS.value)
    wg.add_edge("world_root", "camp", WorldEdgeType.CONTAINS.value)
    wg.add_edge("region_a", "area_1", WorldEdgeType.CONTAINS.value)
    wg.add_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value)
    return wg


# =============================================================================
# ConditionEvaluator Tests
# =============================================================================


class TestEvalLocation:
    def test_match(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_1"})
        ])
        result = ev.evaluate(cond, _ctx(player_location="area_1"))
        assert result.satisfied is True

    def test_mismatch(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_2"})
        ])
        result = ev.evaluate(cond, _ctx(player_location="area_1"))
        assert result.satisfied is False

    def test_sub_location_match(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.LOCATION, params={"sub_location": "loc_guild"})
        ])
        result = ev.evaluate(cond, _ctx(player_sub_location="loc_guild"))
        assert result.satisfied is True


class TestEvalNpcInteracted:
    def test_enough_interactions(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.NPC_INTERACTED,
                      params={"npc_id": "npc_a", "min_interactions": 2})
        ])
        result = ev.evaluate(cond, _ctx(npc_interactions={"npc_a": 3}))
        assert result.satisfied is True

    def test_not_enough(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.NPC_INTERACTED,
                      params={"npc_id": "npc_a", "min_interactions": 5})
        ])
        result = ev.evaluate(cond, _ctx(npc_interactions={"npc_a": 3}))
        assert result.satisfied is False


class TestEvalTimePassed:
    def test_day_passed(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.TIME_PASSED, params={"min_day": 2})
        ])
        result = ev.evaluate(cond, _ctx(game_day=3))
        assert result.satisfied is True

    def test_same_day_hour_passed(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.TIME_PASSED, params={"min_day": 3, "min_hour": 10})
        ])
        result = ev.evaluate(cond, _ctx(game_day=3, game_hour=14))
        assert result.satisfied is True

    def test_not_passed(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.TIME_PASSED, params={"min_day": 5})
        ])
        result = ev.evaluate(cond, _ctx(game_day=3))
        assert result.satisfied is False


class TestEvalRoundsElapsed:
    def test_in_range(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.ROUNDS_ELAPSED,
                      params={"min_rounds": 3, "max_rounds": 10})
        ])
        result = ev.evaluate(cond, _ctx(round_count=5))
        assert result.satisfied is True

    def test_below_min(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.ROUNDS_ELAPSED, params={"min_rounds": 10})
        ])
        result = ev.evaluate(cond, _ctx(round_count=5))
        assert result.satisfied is False


class TestEvalPartyContains:
    def test_member_present(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.PARTY_CONTAINS,
                      params={"character_id": "npc_a"})
        ])
        result = ev.evaluate(cond, _ctx(party_members=["npc_a", "npc_b"]))
        assert result.satisfied is True

    def test_member_absent(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.PARTY_CONTAINS,
                      params={"character_id": "npc_c"})
        ])
        result = ev.evaluate(cond, _ctx(party_members=["npc_a"]))
        assert result.satisfied is False


class TestEvalEventTriggered:
    def test_triggered(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.EVENT_TRIGGERED,
                      params={"event_id": "evt_intro"})
        ])
        result = ev.evaluate(cond, _ctx(events_triggered=["evt_intro"]))
        assert result.satisfied is True

    def test_not_triggered(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.EVENT_TRIGGERED,
                      params={"event_id": "evt_boss"})
        ])
        result = ev.evaluate(cond, _ctx(events_triggered=["evt_intro"]))
        assert result.satisfied is False


class TestEvalObjectiveCompleted:
    def test_completed(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.OBJECTIVE_COMPLETED,
                      params={"objective_id": "obj_talk"})
        ])
        result = ev.evaluate(cond, _ctx(objectives_completed=["obj_talk"]))
        assert result.satisfied is True


class TestEvalGameState:
    def test_match(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"})
        ])
        result = ev.evaluate(cond, _ctx(game_state="exploring"))
        assert result.satisfied is True

    def test_mismatch(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.GAME_STATE, params={"state": "combat"})
        ])
        result = ev.evaluate(cond, _ctx(game_state="exploring"))
        assert result.satisfied is False


class TestEvalFlashEvaluate:
    def test_returns_pending(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(conditions=[
            Condition(type=ConditionType.FLASH_EVALUATE,
                      params={"description": "test"})
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is True
        assert len(result.pending_flash) == 1


class TestGroupOperators:
    def test_and_all_true(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_1"}),
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is True

    def test_and_one_false(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_999"}),
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is False

    def test_or_one_true(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(operator="or", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_999"}),
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is True

    def test_not_negates(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(operator="not", conditions=[
            Condition(type=ConditionType.LOCATION, params={"area_id": "area_999"}),
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is True

    def test_nested_groups(self):
        ev = ConditionEvaluator()
        cond = ConditionGroup(operator="and", conditions=[
            Condition(type=ConditionType.GAME_STATE, params={"state": "exploring"}),
            ConditionGroup(operator="or", conditions=[
                Condition(type=ConditionType.LOCATION, params={"area_id": "area_999"}),
                Condition(type=ConditionType.PARTY_CONTAINS, params={"character_id": "npc_a"}),
            ]),
        ])
        result = ev.evaluate(cond, _ctx())
        assert result.satisfied is True

    def test_none_conditions_satisfied(self):
        ev = ConditionEvaluator()
        result = ev.evaluate(None, _ctx())
        assert result.satisfied is True

    def test_empty_conditions_satisfied(self):
        ev = ConditionEvaluator()
        result = ev.evaluate(ConditionGroup(conditions=[]), _ctx())
        assert result.satisfied is True


# =============================================================================
# ActionExecutor Tests
# =============================================================================


class TestActionChangeState:
    def test_merge_state(self):
        wg = _make_simple_graph()
        wg.set_state("area_1", "visited", False)
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.CHANGE_STATE,
            target="self",
            params={"updates": {"visited": True, "visit_count": 1}, "merge": True},
        )
        result = executor.execute(action, "area_1", _ctx())
        assert result.state_changes == {"area_1": {"visited": True, "visit_count": 1}}
        assert wg.get_node("area_1").state["visited"] is True

    def test_target_resolution_parent(self):
        wg = _make_simple_graph()
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.CHANGE_STATE,
            target="parent",
            params={"updates": {"alert": True}, "merge": True},
        )
        result = executor.execute(action, "loc_1", _ctx())
        # loc_1 的父是 area_1
        assert "area_1" in result.state_changes


class TestActionEmitEvent:
    def test_creates_event(self):
        wg = _make_simple_graph()
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.EMIT_EVENT,
            params={"event_type": "test_fired", "data": {"x": 1}, "visibility": "scope"},
        )
        result = executor.execute(action, "area_1", _ctx())
        assert result.emitted_event is not None
        assert result.emitted_event.event_type == "test_fired"
        assert result.emitted_event.origin_node == "area_1"


class TestActionNarrativeHint:
    def test_collects_text(self):
        wg = _make_simple_graph()
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.NARRATIVE_HINT,
            params={"text": "A dark shadow appears..."},
        )
        result = executor.execute(action, "area_1", _ctx())
        assert result.narrative_hint == "A dark shadow appears..."


class TestActionSpawn:
    def test_creates_node(self):
        wg = _make_simple_graph()
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.SPAWN,
            params={
                "node": {"id": "patrol_1", "type": "npc", "name": "Patrol"},
                "parent": "area_1",
            },
        )
        executor.execute(action, "area_1", _ctx())
        assert wg.has_node("patrol_1")
        assert "patrol_1" in wg.get_children("area_1")


class TestActionRemove:
    def test_removes_node(self):
        wg = _make_simple_graph()
        wg.add_node(WorldNode(id="temp_1", type="npc", name="Temp"))
        assert wg.has_node("temp_1")
        executor = ActionExecutor(wg)
        action = Action(type=ActionType.REMOVE, target="temp_1")
        executor.execute(action, "area_1", _ctx())
        assert not wg.has_node("temp_1")


class TestActionChangeEdge:
    def test_updates_edge(self):
        wg = _make_simple_graph()
        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.CHANGE_EDGE,
            params={
                "source": "area_1",
                "target": "loc_1",
                "key": WorldEdgeType.CONTAINS.value,
                "updates": {"weight": 0.5},
            },
        )
        executor.execute(action, "area_1", _ctx())
        edge = wg.get_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value)
        assert edge["weight"] == 0.5


# =============================================================================
# BehaviorEngine Tests
# =============================================================================


class TestTickBasic:
    def test_simple_behavior_triggers(self):
        """ON_TICK behavior with satisfied conditions fires."""
        wg = _make_simple_graph()
        # 给 area_1 添加一个简单 behavior
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_test",
            trigger=TriggerType.ON_TICK,
            conditions=None,  # 永真
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"tested": True}, "merge": True},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        ctx = _ctx(player_location="loc_1")
        result = engine.tick(ctx)

        assert len(result.results) >= 1
        assert area_node.state.get("tested") is True


class TestTickPriority:
    def test_higher_priority_first(self):
        """高优先级 behavior 先执行。"""
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        execution_order = []

        # 两个 behavior，priority 不同
        area_node.behaviors.append(Behavior(
            id="bh_low",
            trigger=TriggerType.ON_TICK,
            conditions=None,
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"order": "low"}, "merge": True},
            )],
            priority=1,
        ))
        area_node.behaviors.append(Behavior(
            id="bh_high",
            trigger=TriggerType.ON_TICK,
            conditions=None,
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"order": "high"}, "merge": True},
            )],
            priority=10,
        ))

        engine = BehaviorEngine(wg)
        result = engine.tick(_ctx(player_location="loc_1"))

        # 高优先级先执行，但低优先级后执行覆盖了 order
        # 检查执行顺序: results 列表中 bh_high 在 bh_low 之前
        ids = [r.behavior_id for r in result.results if r.node_id == "area_1"]
        assert ids.index("bh_high") < ids.index("bh_low")


class TestTickOnce:
    def test_once_behavior_fires_once(self):
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_once",
            trigger=TriggerType.ON_TICK,
            conditions=None,
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"fired_count": 1}, "merge": True},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        ctx = _ctx(player_location="loc_1")

        # 第一次 tick
        r1 = engine.tick(ctx)
        fired_1 = [r for r in r1.results if r.behavior_id == "bh_once"]
        assert len(fired_1) == 1

        # 第二次 tick — once behavior 不再触发
        r2 = engine.tick(ctx)
        fired_2 = [r for r in r2.results if r.behavior_id == "bh_once"]
        assert len(fired_2) == 0


class TestTickCooldown:
    def test_cooldown_skips_then_fires(self):
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_cd",
            trigger=TriggerType.ON_TICK,
            conditions=None,
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "cooldown test"},
            )],
            cooldown_ticks=2,
        ))

        engine = BehaviorEngine(wg)
        ctx = _ctx(player_location="loc_1")

        # Tick 1: fires
        r1 = engine.tick(ctx)
        assert any(r.behavior_id == "bh_cd" for r in r1.results)

        # Tick 2: cooldown=2 → 跳过
        r2 = engine.tick(ctx)
        assert not any(r.behavior_id == "bh_cd" for r in r2.results)

        # Tick 3: cooldown=1 → 跳过
        r3 = engine.tick(ctx)
        assert not any(r.behavior_id == "bh_cd" for r in r3.results)

        # Tick 4: cooldown expired → fires again
        r4 = engine.tick(ctx)
        assert any(r.behavior_id == "bh_cd" for r in r4.results)


class TestTickDisabled:
    def test_disabled_behavior_skipped(self):
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_disabled",
            trigger=TriggerType.ON_TICK,
            conditions=None,
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "should not fire"},
            )],
            enabled=False,
        ))

        engine = BehaviorEngine(wg)
        result = engine.tick(_ctx(player_location="loc_1"))
        assert not any(r.behavior_id == "bh_disabled" for r in result.results)


class TestActiveScope:
    def test_includes_scope_chain(self):
        wg = _make_simple_graph()
        engine = BehaviorEngine(wg)
        active = engine._get_active_nodes(_ctx(player_location="loc_1"))
        assert "loc_1" in active
        assert "area_1" in active
        assert "region_a" in active
        assert "world_root" in active
        assert "camp" in active

    def test_includes_chapters(self):
        wg = _make_simple_graph()
        wg.add_node(WorldNode(id="ch_1", type=WorldNodeType.CHAPTER, name="Ch1"))
        wg.add_edge("world_root", "ch_1", WorldEdgeType.CONTAINS.value)

        engine = BehaviorEngine(wg)
        active = engine._get_active_nodes(_ctx(player_location="loc_1"))
        assert "ch_1" in active


class TestHandleEvent:
    def test_event_triggers_on_event_behavior(self):
        wg = _make_simple_graph()
        # area_1 有一个 ON_EVENT behavior
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_on_event",
            trigger=TriggerType.ON_EVENT,
            event_filter="combat_started",
            conditions=None,
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"alert": True}, "merge": True},
            )],
        ))

        engine = BehaviorEngine(wg)
        event = WorldEvent(
            event_type="combat_started",
            origin_node="loc_1",
            visibility="scope",
        )
        result = engine.handle_event(event, _ctx(player_location="loc_1"))

        # area_1 是 loc_1 的父节点，事件传播到 area_1 应触发 behavior
        assert any(r.behavior_id == "bh_on_event" for r in result.results)
        assert area_node.state.get("alert") is True


class TestHandleEnterExit:
    def test_on_enter_fires(self):
        wg = _make_simple_graph()
        loc_node = wg.get_node("loc_1")
        loc_node.behaviors.append(Behavior(
            id="bh_enter",
            trigger=TriggerType.ON_ENTER,
            conditions=None,
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "Welcome!"},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        result = engine.handle_enter("player", "loc_1", _ctx())
        assert any(r.behavior_id == "bh_enter" for r in result.results)
        assert "Welcome!" in result.narrative_hints


class TestEventFilterWildcard:
    def test_wildcard_match(self):
        assert BehaviorEngine._matches_event_filter(
            Behavior(id="b", trigger=TriggerType.ON_EVENT, event_filter="combat_*"),
            WorldEvent(event_type="combat_started", origin_node="x"),
        ) is True

    def test_wildcard_no_match(self):
        assert BehaviorEngine._matches_event_filter(
            Behavior(id="b", trigger=TriggerType.ON_EVENT, event_filter="combat_*"),
            WorldEvent(event_type="quest_completed", origin_node="x"),
        ) is False

    def test_no_filter_matches_all(self):
        assert BehaviorEngine._matches_event_filter(
            Behavior(id="b", trigger=TriggerType.ON_EVENT, event_filter=None),
            WorldEvent(event_type="anything", origin_node="x"),
        ) is True


class TestTimeFilter:
    def test_hour_gte(self):
        bh = Behavior(id="b", trigger=TriggerType.ON_TIME,
                       time_condition={"hour_gte": 18})
        assert BehaviorEngine._matches_time_filter(bh, _ctx(game_hour=20)) is True
        assert BehaviorEngine._matches_time_filter(bh, _ctx(game_hour=10)) is False

    def test_overnight(self):
        bh = Behavior(id="b", trigger=TriggerType.ON_TIME,
                       time_condition={"hour_gte": 22, "hour_lte": 6})
        assert BehaviorEngine._matches_time_filter(bh, _ctx(game_hour=23)) is True
        assert BehaviorEngine._matches_time_filter(bh, _ctx(game_hour=3)) is True
        assert BehaviorEngine._matches_time_filter(bh, _ctx(game_hour=12)) is False


class TestDispositionFilter:
    def test_gte_threshold(self):
        node = WorldNode(id="npc", type="npc", name="N",
                         state={"dispositions": {"player": {"trust": 60}}})
        bh = Behavior(id="b", trigger=TriggerType.ON_DISPOSITION,
                       disposition_filter={"dimension": "trust", "gte": 50})
        assert BehaviorEngine._matches_disposition_filter(bh, node) is True

    def test_below_threshold(self):
        node = WorldNode(id="npc", type="npc", name="N",
                         state={"dispositions": {"player": {"trust": 30}}})
        bh = Behavior(id="b", trigger=TriggerType.ON_DISPOSITION,
                       disposition_filter={"dimension": "trust", "gte": 50})
        assert BehaviorEngine._matches_disposition_filter(bh, node) is False


class TestCascadingLimit:
    def test_max_events_per_tick(self):
        """级联事件不超过 MAX_EVENTS_PER_TICK。"""
        wg = _make_simple_graph()
        # 给 area_1 挂一个 ON_EVENT behavior 会无限 emit
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_loop",
            trigger=TriggerType.ON_EVENT,
            event_filter="chain_*",
            conditions=None,
            actions=[Action(
                type=ActionType.EMIT_EVENT,
                params={"event_type": "chain_next", "visibility": "scope"},
            )],
        ))

        engine = BehaviorEngine(wg)
        event = WorldEvent(
            event_type="chain_start",
            origin_node="loc_1",
            visibility="scope",
        )
        result = engine.handle_event(event, _ctx(player_location="loc_1"))

        # 事件总数应受限
        assert len(result.all_events) <= engine.MAX_EVENTS_PER_TICK + 1  # +1 for initial


class TestFullTickCycle:
    def test_event_unlock_chain(self):
        """事件完成 → EMIT_EVENT → 另一事件节点的 ON_EVENT behavior 触发。"""
        wg = _make_simple_graph()

        # 事件 A: ON_TICK 完成 → emit event_unlocked
        evt_a = WorldNode(
            id="evt_a", type=WorldNodeType.EVENT_DEF, name="Event A",
            state={"status": EventStatus.LOCKED},
            behaviors=[Behavior(
                id="bh_unlock_a",
                trigger=TriggerType.ON_TICK,
                conditions=None,
                actions=[
                    Action(type=ActionType.CHANGE_STATE, target="self",
                           params={"updates": {"status": EventStatus.AVAILABLE}, "merge": True}),
                    Action(type=ActionType.EMIT_EVENT,
                           params={"event_type": "event_unlocked",
                                   "data": {"event_id": "evt_b"},
                                   "visibility": "scope"}),
                ],
                once=True,
            )],
        )
        wg.add_node(evt_a)
        wg.add_edge("area_1", "evt_a", WorldEdgeType.HAS_EVENT.value, key="he_a")

        # 事件 B: ON_EVENT 收到 event_unlocked → 自身解锁
        evt_b = WorldNode(
            id="evt_b", type=WorldNodeType.EVENT_DEF, name="Event B",
            state={"status": EventStatus.LOCKED},
            behaviors=[Behavior(
                id="bh_listen_b",
                trigger=TriggerType.ON_EVENT,
                event_filter="event_unlocked",
                conditions=None,
                actions=[Action(
                    type=ActionType.CHANGE_STATE, target="self",
                    params={"updates": {"status": EventStatus.AVAILABLE}, "merge": True},
                )],
                once=True,
            )],
        )
        wg.add_node(evt_b)
        wg.add_edge("area_1", "evt_b", WorldEdgeType.HAS_EVENT.value, key="he_b")

        engine = BehaviorEngine(wg)
        result = engine.tick(_ctx(player_location="loc_1"))

        # 事件 A 解锁了
        assert evt_a.state["status"] == EventStatus.AVAILABLE
        # 事件 B 也通过级联解锁了
        assert evt_b.state["status"] == EventStatus.AVAILABLE


# =============================================================================
# 补充测试: pending_flash / handle_exit / cascade rounds / CHANGE_EDGE add+remove
# =============================================================================


class TestPendingFlashPropagation:
    def test_tick_collects_pending_flash(self):
        """含 FLASH_EVALUATE 条件的 behavior 触发后，pending_flash 应出现在 TickResult 中。"""
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_flash",
            trigger=TriggerType.ON_TICK,
            conditions=ConditionGroup(operator="and", conditions=[
                Condition(type=ConditionType.FLASH_EVALUATE,
                          params={"description": "Is the tavern crowded?"}),
            ]),
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "flash behavior fired"},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        result = engine.tick(_ctx(player_location="loc_1"))

        # FLASH_EVALUATE → satisfied=True + pending_flash
        assert len(result.pending_flash) >= 1
        assert "flash behavior fired" in result.narrative_hints

    def test_handle_event_collects_pending_flash(self):
        """handle_event() 中含 FLASH_EVALUATE 的 ON_EVENT behavior 也应收集 pending_flash。"""
        wg = _make_simple_graph()
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_flash_on_event",
            trigger=TriggerType.ON_EVENT,
            event_filter="test_event",
            conditions=ConditionGroup(conditions=[
                Condition(type=ConditionType.FLASH_EVALUATE,
                          params={"description": "NPC reacts?"}),
            ]),
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "flash on event"},
            )],
        ))

        engine = BehaviorEngine(wg)
        event = WorldEvent(
            event_type="test_event",
            origin_node="loc_1",
            visibility="scope",
        )
        result = engine.handle_event(event, _ctx(player_location="loc_1"))

        assert len(result.pending_flash) >= 1


class TestHandleExit:
    def test_on_exit_fires(self):
        """ON_EXIT behavior 触发。"""
        wg = _make_simple_graph()
        loc_node = wg.get_node("loc_1")
        loc_node.behaviors.append(Behavior(
            id="bh_exit",
            trigger=TriggerType.ON_EXIT,
            conditions=None,
            actions=[Action(
                type=ActionType.NARRATIVE_HINT,
                params={"text": "Farewell!"},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        result = engine.handle_exit("player", "loc_1", _ctx())
        assert any(r.behavior_id == "bh_exit" for r in result.results)
        assert "Farewell!" in result.narrative_hints


class TestCascadingRoundsLimit:
    def test_max_cascading_rounds(self):
        """级联轮次不超过 MAX_CASCADING_ROUNDS。"""
        wg = _make_simple_graph()

        # area_1 上挂一个 ON_EVENT → emit 新事件的 behavior（无限级联）
        # 但用不同的 event_type 确保每轮都匹配
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_cascade",
            trigger=TriggerType.ON_EVENT,
            event_filter=None,  # 匹配所有事件
            conditions=None,
            actions=[Action(
                type=ActionType.EMIT_EVENT,
                params={"event_type": "cascade_next", "visibility": "scope"},
            )],
        ))

        engine = BehaviorEngine(wg)
        # 设置较小的限制以便测试
        engine.MAX_CASCADING_ROUNDS = 3
        engine.MAX_EVENTS_PER_TICK = 100  # 放宽事件数限制，测试轮次限制

        event = WorldEvent(
            event_type="cascade_start",
            origin_node="loc_1",
            visibility="scope",
        )
        result = engine.handle_event(event, _ctx(player_location="loc_1"))

        # 事件应有限（不会爆炸），总数应明显受轮次限制
        assert len(result.all_events) < 100


class TestHandleEventOriginCascade:
    def test_origin_emitted_events_propagated(self):
        """handle_event() 中 origin 产生的新事件也应被传播。"""
        wg = _make_simple_graph()

        # loc_1 有 ON_EVENT → emit new_event
        loc_node = wg.get_node("loc_1")
        loc_node.behaviors.append(Behavior(
            id="bh_origin_emit",
            trigger=TriggerType.ON_EVENT,
            event_filter="trigger_event",
            conditions=None,
            actions=[Action(
                type=ActionType.EMIT_EVENT,
                params={"event_type": "origin_spawned", "visibility": "scope"},
            )],
            once=True,
        ))

        # area_1 监听 origin_spawned
        area_node = wg.get_node("area_1")
        area_node.behaviors.append(Behavior(
            id="bh_area_listen",
            trigger=TriggerType.ON_EVENT,
            event_filter="origin_spawned",
            conditions=None,
            actions=[Action(
                type=ActionType.CHANGE_STATE,
                target="self",
                params={"updates": {"heard_origin_spawned": True}, "merge": True},
            )],
            once=True,
        ))

        engine = BehaviorEngine(wg)
        event = WorldEvent(
            event_type="trigger_event",
            origin_node="loc_1",
            visibility="scope",
        )
        result = engine.handle_event(event, _ctx(player_location="loc_1"))

        # area_1 应通过级联收到 origin_spawned 事件
        assert area_node.state.get("heard_origin_spawned") is True


class TestChangeEdgeAddRemove:
    def test_add_edge(self):
        wg = _make_simple_graph()
        # 添加新节点用于测试
        wg.add_node(WorldNode(id="area_2", type=WorldNodeType.AREA, name="Area 2"))
        wg.add_edge("region_a", "area_2", WorldEdgeType.CONTAINS.value)

        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.CHANGE_EDGE,
            params={
                "operation": "add",
                "source": "area_1",
                "target": "area_2",
                "relation": WorldEdgeType.CONNECTS.value,
                "key": "conn_1_2",
            },
        )
        executor.execute(action, "area_1", _ctx())

        # 边应存在
        edge = wg.get_edge("area_1", "area_2", "conn_1_2")
        assert edge is not None

    def test_remove_edge(self):
        wg = _make_simple_graph()
        # 先确认边存在
        assert wg.get_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value) is not None

        executor = ActionExecutor(wg)
        action = Action(
            type=ActionType.CHANGE_EDGE,
            params={
                "operation": "remove",
                "source": "area_1",
                "target": "loc_1",
                "key": WorldEdgeType.CONTAINS.value,
            },
        )
        executor.execute(action, "area_1", _ctx())

        # 边应已删除
        assert wg.get_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value) is None
