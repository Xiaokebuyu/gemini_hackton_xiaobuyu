from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.main as api_main
from app.game_core import GameRuntime
from app.game_core.adapters.persistence import NullPersistencePort
from app.game_core.adapters.session_store import SaveStore
from app.game_core.state import StateChange
from app.interaction_service import InteractionService


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def _interaction_session(*, location_id: str | None = "counter"):
    runtime = _runtime()
    runtime.get_world(
        "goblin_slayer",
        world_data=api_main._shell_world_seed("goblin_slayer"),
        force_reload=True,
    )
    session = asyncio.run(runtime.create_session("goblin_slayer"))
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_area", "guild_hall")
    )
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_location", location_id)
    )
    return runtime, session


def _pipeline_executor(runtime: GameRuntime):
    async def _execute(session, request):
        result = await session.runtime.tick_coordinator.process(
            {
                "action_type": request.action_type,
                "params": dict(request.params),
                "source": request.source,
                "context": (
                    dict(request.context)
                    if isinstance(getattr(request, "context", None), dict)
                    else None
                ),
            }
        )
        await runtime.save_session(session)
        return result

    return _execute


def _service(runtime: GameRuntime, *, execute_structured_action=None) -> InteractionService:
    return InteractionService(
        execute_structured_action=execute_structured_action or _pipeline_executor(runtime),
        save_session=runtime.save_session,
    )


def test_interaction_service_returns_rejection_without_resolved_event() -> None:
    runtime, session = _interaction_session()
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "rejected",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "code": "npc_not_present",
                "message": "npc is not in the current location: merchant",
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == ["interaction_rejected"]


def test_interaction_service_rejects_presence_before_resolving() -> None:
    runtime, session = _interaction_session(location_id=None)
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {"kind": "snapshot", "snapshot_type": "talk"},
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == ["interaction_rejected"]
    assert result.events[0].payload["code"] == "npc_not_present"


def test_interaction_service_rejects_board_precheck_before_resolving() -> None:
    runtime, session = _interaction_session(location_id="board")
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "board",
                "target_id": "board",
                "intent": "accept",
                "item_id": None,
                "quest_id": "dq_report_in",
                "count": 1,
                "execution": {
                    "kind": "pipeline_action",
                    "action_type": "advance_quest",
                    "params": {
                        "quest_id": "dq_report_in",
                        "to_state": "active",
                        "quest_kind": "dynamic",
                    },
                    "post_snapshot": "board",
                },
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == ["interaction_rejected"]
    assert result.events[0].payload["code"] == "quest_not_listed"


def test_interaction_service_executes_talk_snapshot() -> None:
    runtime, session = _interaction_session()
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {"kind": "snapshot", "snapshot_type": "talk"},
            },
        )
    )

    assert result.success is True
    assert result.reason == "completed"
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "talk_snapshot",
    ]


def test_interaction_service_executes_quest_brief_snapshot() -> None:
    runtime, session = _interaction_session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "available",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
        },
    )
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "ask_quest",
                "item_id": None,
                "quest_id": "dq_report_in",
                "count": 1,
                "execution": {
                    "kind": "snapshot",
                    "snapshot_type": "quest_brief",
                    "quest_id": "dq_report_in",
                },
            },
        )
    )

    assert result.success is True
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "quest_brief",
    ]


def test_interaction_service_executes_shop_refresh() -> None:
    runtime, session = _interaction_session()
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "browse",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {"kind": "shop_refresh"},
            },
        )
    )

    assert result.success is True
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "shop_snapshot",
    ]
    assert any(item["item_id"] == "bandage" for item in result.events[1].payload["stock"])


def test_interaction_service_executes_pipeline_action_and_post_snapshot() -> None:
    runtime, session = _interaction_session(location_id="board")
    session.runtime.state.narrative_plan.add_bulletin(
        {
            "board_id": "board",
            "title": "New Lead Posted",
            "content": "A fresh lead is available: Report In.",
            "metadata": {"quest_id": "dq_report_in"},
            "published_at_tick": 9,
            "source": "test",
        }
    )
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "available",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
        },
    )
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "board",
                "target_id": "board",
                "intent": "accept",
                "item_id": None,
                "quest_id": "dq_report_in",
                "count": 1,
                "execution": {
                    "kind": "pipeline_action",
                    "action_type": "advance_quest",
                    "params": {
                        "quest_id": "dq_report_in",
                        "to_state": "active",
                        "quest_kind": "dynamic",
                    },
                    "post_snapshot": "board",
                },
            },
        )
    )

    assert result.success is True
    assert result.events[0].event_type == "interaction_resolved"
    assert result.events[1].event_type == "action_result"
    assert result.events[-1].event_type == "board_snapshot"
    matching_entries = [
        entry
        for entry in result.events[-1].payload["entries"]
        if entry["quest_id"] == "dq_report_in"
    ]
    assert matching_entries
    assert matching_entries[0]["quest_status"] == "active"


def test_interaction_service_handles_pipeline_failure() -> None:
    runtime, session = _interaction_session()

    async def _execute(_session, _request):
        return SimpleNamespace(
            success=False,
            errors=["interaction failed"],
            time_cost=0.0,
            metadata={},
            narrative_hints=[],
            sse_events=[],
        )

    service = _service(runtime, execute_structured_action=_execute)
    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "greet",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {
                    "kind": "pipeline_action",
                    "action_type": "add_knowledge",
                    "params": {
                        "npc_id": "merchant",
                        "impression": "Shared a brief greeting.",
                    },
                    "post_snapshot": "talk",
                },
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "interaction_rejected",
    ]


def test_interaction_service_rejects_unknown_snapshot_type() -> None:
    runtime, session = _interaction_session()
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {"kind": "snapshot", "snapshot_type": "missing"},
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "interaction_rejected",
    ]


def test_interaction_service_rejects_unknown_execution_kind() -> None:
    runtime, session = _interaction_session()
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "talk",
                "item_id": None,
                "quest_id": None,
                "count": 1,
                "execution": {"kind": "missing"},
            },
        )
    )

    assert result.success is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "interaction_rejected",
    ]
