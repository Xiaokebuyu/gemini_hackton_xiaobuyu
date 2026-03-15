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


def test_execute_persists_unified_trace_with_blackboard_audit() -> None:
    """Phase 3d: execute() stores a simplified trace (no replay rounds).

    The unified planner runs a single LLM call (blackboard.plan()), not multi-round
    replay.  The trace shape is preserved for backward compatibility but round_count==0.
    Blackboard directives with errors are still recorded in directive_audit.
    """
    blackboard = StaticBlackboard(
        NarrativePlannerDecision(
            directives=[
                {"kind": "adjust_pacing", "payload": {"frozen": True}},
                {"kind": "unknown_kind", "payload": {}},
                # duplicate dq_existing → command handler rejects → subsystem_rejected
                {"kind": "create_quest", "payload": {"quest_id": "dq_existing"}},
            ],
            metadata={"provider": "trace_test"},
        )
    )
    hook = build_test_hook(blackboard=blackboard)
    context = make_context(
        change_log=[StateChange("flags", "set", "flags.trace", True)],
        narrative_plan_payload={"last_run_tick": 0},
    )

    result = asyncio.run(hook.execute(context))

    # adjust_pacing applied; unknown_kind unsupported; dq_existing duplicate → rejected
    assert result.metadata["applied_count"] == 1
    trace = context.state.narrative_plan.last_planner_replay_trace
    # Unified trace: round_count == 0, rounds is empty
    assert trace["round_count"] == 0
    assert trace["rounds"] == []
    assert trace["stop_reason"] == "unified"
    assert isinstance(trace["directive_audit"], list)
    assert trace["blackboard_summary"]["applied_directive_count"] == 1
    assert trace["blackboard_summary"]["planner_metadata"] == {"provider": "trace_test"}

    statuses = {entry["status"] for entry in trace["directive_audit"]}
    assert "applied" in statuses
    assert "unsupported" in statuses
    # dq_existing duplicate is rejected by the command handler (subsystem_rejected)
    assert "subsystem_rejected" in statuses


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
