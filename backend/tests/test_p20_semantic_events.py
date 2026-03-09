"""Tests for semantic planner-event collection."""

from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.semantic_events import collect_planner_events
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    action_log: list[dict[str, Any]] | None = None,
    area_payload: dict[str, Any] | None = None,
    quest_payload: dict[str, Any] | None = None,
    event_payload: dict[str, Any] | None = None,
    relation_payload: dict[str, Any] | None = None,
    narrative_plan_payload: dict[str, Any] | None = None,
) -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": {},
            **(quest_payload or {}),
        }
    )
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore(narrative_plan_payload or {})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore(area_payload or {"areas": {"forest": {}}})
    state.register(areas)

    relations = RelationSlice()
    relations.restore(relation_payload or {})
    state.register(relations)

    events = EventSlice()
    events.restore(event_payload or {})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    active_change_log = list(change_log or [])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=active_change_log,
        action_log=list(action_log or []),
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=_apply_delta,
    )


def test_collect_planner_events_prefers_action_semantics_and_orders_events() -> None:
    context = _make_context(
        change_log=[
            StateChange(
                "quests",
                "modify",
                "dynamic_quests.dq_1",
                {"status": "active", "title": "Wanted", "summary": "Find the courier"},
            ),
            StateChange("player", "set", "current_area", "forest"),
            StateChange("player", "set", "current_location", None),
            StateChange(
                "relations",
                "modify",
                "shop_states.merchant",
                {"inventory": [{"item_id": "potion", "count": 1}]},
            ),
        ],
        action_log=[
            {
                "type": "board_accept_quest",
                "params": {"quest_id": "dq_1", "board_id": "board"},
                "executed": True,
            },
            {
                "type": "move_area",
                "params": {"area_id": "forest"},
                "executed": True,
            },
            {
                "type": "refresh_shop",
                "params": {"npc_id": "merchant"},
                "executed": True,
            },
        ],
        area_payload={
            "areas": {
                "forest": {
                    "temporary_sub_areas": [
                        {"id": "camp", "label": "Camp", "expiry": 6},
                    ]
                }
            }
        },
        relation_payload={
            "shop_states": {
                "merchant": {"inventory": [{"item_id": "potion", "count": 1}]}
            }
        },
    )

    events = collect_planner_events(context, current_tick=context.state.time.absolute_tick())

    assert [event.kind for event in events] == [
        "quest_accepted",
        "area_entered",
        "scene_changed",
        "shop_refreshed",
        "tick_settlement",
    ]
    assert sum(1 for event in events if event.payload.get("quest_id") == "dq_1") == 1
    assert events[0].source == "action_log"
    assert events[1].payload["area_id"] == "forest"
    assert events[3].payload["npc_id"] == "merchant"


def test_collect_planner_events_derives_change_and_snapshot_events() -> None:
    context = _make_context(
        change_log=[
            StateChange(
                "quests",
                "set",
                "milestone_states.ms_1",
                {"state": "COMPLETED"},
            ),
            StateChange(
                "quests",
                "modify",
                "dynamic_quests.dq_old",
                {"status": "expired", "title": "Old Trouble", "summary": "Too late"},
            ),
            StateChange("player", "set", "current_location", "tavern"),
            StateChange(
                "relations",
                "modify",
                "shop_states.vendor",
                {"inventory": []},
            ),
            StateChange("events", "set", "state.evt_alarm", "active"),
        ],
        narrative_plan_payload={
            "escalation_level": 1,
            "ticks_since_milestone_progress": 7,
        },
        relation_payload={"shop_states": {"vendor": {"inventory": []}}},
        event_payload={
            "active_events": {
                "evt_alarm": {
                    "id": "evt_alarm",
                    "event_id": "evt_alarm",
                    "event_type": "alarm",
                    "title": "Town Alarm",
                    "state": "active",
                    "from_state": "available",
                    "reason": "guard_triggered",
                }
            }
        },
    )

    events = collect_planner_events(context, current_tick=context.state.time.absolute_tick())

    assert [event.kind for event in events] == [
        "milestone_completed",
        "quest_expired",
        "sub_location_entered",
        "scene_changed",
        "shop_inventory_changed",
        "world_event_active",
        "area_sparse",
        "stagnation_threshold_reached",
        "tick_settlement",
    ]
    world_event = next(event for event in events if event.kind == "world_event_active")
    assert world_event.payload["event_id"] == "evt_alarm"
    assert world_event.payload["title"] == "Town Alarm"
    assert world_event.payload["reason"] == "guard_triggered"
    assert world_event.payload["to_state"] == "active"


def test_collect_planner_events_supports_windowed_replay_rounds() -> None:
    context = _make_context(
        change_log=[
            StateChange("quests", "set", "dynamic_quests.dq_new", {
                "quest_id": "dq_new",
                "status": "available",
                "title": "Fresh Trouble",
                "summary": "A new lead",
                "created_at_tick": 9,
            }),
            StateChange("quests", "set", "dynamic_quests.dq_new.objectives.0.completed", True),
            StateChange("relations", "set", "relationship_stages.npc_guard", "friend"),
            StateChange("events", "set", "state.evt_alarm", "resolved"),
        ],
        event_payload={
            "active_events": {
                "evt_alarm": {
                    "id": "evt_alarm",
                    "event_id": "evt_alarm",
                    "event_type": "alarm",
                    "title": "Town Alarm",
                    "state": "resolved",
                    "from_state": "active",
                    "reason": "guards_arrived",
                }
            }
        },
        narrative_plan_payload={
            "escalation_level": 0,
            "ticks_since_milestone_progress": 5,
        },
    )

    events = collect_planner_events(
        context,
        current_tick=context.state.time.absolute_tick(),
        change_window_start=0,
        round_index=1,
        include_action_log=False,
        include_tick_event=False,
    )

    assert [event.kind for event in events] == [
        "quest_created",
        "quest_objective_completed",
        "relationship_stage_changed",
        "world_event_resolved",
        "area_sparse",
        "stagnation_threshold_reached",
    ]
    assert all(event.round_index == 1 for event in events)
    assert all(event.emitter.startswith("planner_replay") for event in events)
