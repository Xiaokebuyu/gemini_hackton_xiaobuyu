from __future__ import annotations

import asyncio

from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerDecision
from app.game_core.state import StateChange

from tests.planner_test_utils import (
    PlannerAgentAdapter,
    RecordingPlanner,
    StaticBlackboard,
    build_test_hook,
    make_context,
)


def test_execute_persists_full_replay_trace_with_subsystem_and_blackboard_audit() -> None:
    planner = RecordingPlanner(
        NarrativePlannerDecision(
            directives=[
                {"kind": "create_quest", "payload": {}},
                {"kind": "unknown_kind", "payload": {}},
                {"kind": "create_quest", "payload": {"quest_id": "dq_existing"}},
            ]
        )
    )
    blackboard = StaticBlackboard(
        NarrativePlannerDecision(
            directives=[{"kind": "adjust_pacing", "payload": {"frozen": True}}],
            metadata={"provider": "trace_test"},
        )
    )
    hook = build_test_hook(
        blackboard=blackboard,
        quest_agent=PlannerAgentAdapter(
            planner,
            allowed_directives={"create_quest", "unknown_kind"},
        ),
    )
    context = make_context(
        change_log=[StateChange("flags", "set", "flags.trace", True)],
        narrative_plan_payload={"last_run_tick": 0},
    )

    result = asyncio.run(hook.execute(context))

    assert result.metadata["applied_count"] == 1
    trace = context.state.narrative_plan.last_planner_replay_trace
    assert trace["round_count"] >= 1
    assert isinstance(trace["directive_audit"], list)
    assert trace["blackboard_summary"]["applied_directive_count"] == 1
    assert trace["blackboard_summary"]["planner_metadata"] == {"provider": "trace_test"}

    statuses = {entry["status"] for entry in trace["directive_audit"]}
    assert statuses == {
        "applied",
        "unsupported",
        "invalid_contract",
        "subsystem_rejected",
    }

    first_round = trace["rounds"][0]
    assert first_round["subsystems"]
    quest_summary = next(
        summary for summary in first_round["subsystems"] if summary["name"] == "quest_manager"
    )
    assert quest_summary["requested_directive_count"] == 3
    assert quest_summary["applied_directive_count"] == 0
    assert quest_summary["skipped_unsupported_count"] == 1
    assert quest_summary["skipped_invalid_count"] == 2
    assert {entry["status"] for entry in quest_summary["directive_audit"]} == {
        "unsupported",
        "invalid_contract",
        "subsystem_rejected",
    }


def test_bootstrap_trace_uses_same_shape_as_normal_execute() -> None:
    planner = RecordingPlanner(
        NarrativePlannerDecision(
            directives=[
                {
                    "kind": "create_quest",
                    "payload": {"quest_id": "dq_bootstrap", "title": "Opening Lead"},
                }
            ]
        )
    )
    hook = build_test_hook(planner=planner)
    context = make_context(
        narrative_plan_payload={"last_run_tick": 0},
        change_log=[],
        inject_semantic_seed=False,
    )

    result = asyncio.run(hook.bootstrap(context))

    assert result.metadata["status"] == "updated"
    trace = context.state.narrative_plan.last_planner_replay_trace
    assert set(trace.keys()) >= {
        "hook_priority",
        "current_tick",
        "reason",
        "round_count",
        "stop_reason",
        "rounds",
        "directive_audit",
        "blackboard_summary",
    }
    assert trace["reason"] == "bootstrap"
    assert trace["rounds"][0]["subsystems"]
    assert isinstance(trace["blackboard_summary"]["directive_audit"], list)


def test_blackboard_summary_records_kind_not_allowed_as_unsupported() -> None:
    blackboard = StaticBlackboard(
        NarrativePlannerDecision(
            directives=[{"kind": "adjust_pacing", "payload": {"frozen": True}}],
            metadata={"provider": "bootstrap_trace"},
        )
    )
    hook = build_test_hook(blackboard=blackboard)
    context = make_context(
        narrative_plan_payload={"last_run_tick": 0},
        change_log=[],
        inject_semantic_seed=False,
    )

    result = asyncio.run(hook.bootstrap(context))

    assert result.metadata["applied_count"] == 0
    assert result.metadata["skipped_unsupported_count"] == 1
    trace = context.state.narrative_plan.last_planner_replay_trace
    summary = trace["blackboard_summary"]
    assert summary["requested_directive_count"] == 1
    assert summary["applied_directive_count"] == 0
    assert summary["skipped_unsupported_count"] == 1
    assert summary["directive_audit"][0]["status"] == "unsupported"
    assert summary["directive_audit"][0]["reason_code"] == "kind_not_allowed"
