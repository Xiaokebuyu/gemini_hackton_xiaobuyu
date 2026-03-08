from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.game_core import GameRuntime
from app.world_seed import _shell_world_seed
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
        world_data=_shell_world_seed("goblin_slayer"),
        force_reload=True,
    )
    session = asyncio.run(runtime.create_session("goblin_slayer"))
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_area", "guild_hall")
    )
    session.runtime.state.areas.update_npc_location("merchant", "counter")
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

    assert result.completed is False
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

    assert result.completed is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == ["interaction_rejected"]
    assert result.events[0].payload["code"] == "npc_not_present"


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

    assert result.completed is True
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

    assert result.completed is True
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

    assert result.completed is True
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "shop_snapshot",
    ]
    assert any(item["item_id"] == "bandage" for item in result.events[1].payload["stock"])


def test_interaction_service_handles_pipeline_failure() -> None:
    runtime, session = _interaction_session()

    async def _execute(_session, _request):
        return SimpleNamespace(
            executed=False,
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

    assert result.completed is False
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

    assert result.completed is False
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

    assert result.completed is False
    assert result.reason == "interaction_rejected"
    assert [event.event_type for event in result.events] == [
        "interaction_resolved",
        "interaction_rejected",
    ]


# ------------------------------------------------------------------
# Shop snapshot enrichment
# ------------------------------------------------------------------


def test_shop_snapshot_includes_player_gold() -> None:
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

    assert result.completed is True
    shop_payload = result.events[1].payload
    assert "player_gold" in shop_payload
    assert isinstance(shop_payload["player_gold"], int)


def test_shop_snapshot_stock_enriched_with_item_details() -> None:
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

    assert result.completed is True
    shop_payload = result.events[1].payload
    stock = shop_payload["stock"]
    assert len(stock) > 0
    for item in stock:
        assert "name" in item
        assert "type" in item
        assert "rarity" in item


def test_shop_snapshot_includes_player_sellable_items() -> None:
    runtime, session = _interaction_session()
    session.runtime.state.player.add_item("training_sword", count=1)
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

    assert result.completed is True
    shop_payload = result.events[1].payload
    sellable = shop_payload["player_sellable_items"]
    assert any(item["item_id"] == "training_sword" for item in sellable)
    sword = next(item for item in sellable if item["item_id"] == "training_sword")
    assert sword["name"] == "Training Sword"
    assert isinstance(sword["base_price"], int)


# ------------------------------------------------------------------
# Talk snapshot enrichment
# ------------------------------------------------------------------


def test_talk_snapshot_includes_available_intents() -> None:
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

    assert result.completed is True
    talk_payload = result.events[1].payload
    intents = talk_payload["available_intents"]
    assert "talk" in intents
    assert "greet" in intents


def test_talk_snapshot_merchant_has_shop_intents() -> None:
    runtime, session = _interaction_session()
    # Refresh shop first to populate shop_states
    service = _service(runtime)
    asyncio.run(
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
    # Now get talk snapshot
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

    assert result.completed is True
    intents = result.events[1].payload["available_intents"]
    assert "browse" in intents
    assert "buy" in intents
    assert "sell" in intents


# ------------------------------------------------------------------
# inspect_item
# ------------------------------------------------------------------


def test_inspect_item_returns_item_details() -> None:
    runtime, session = _interaction_session()
    session.runtime.state.player.add_item("bandage", count=3)
    service = _service(runtime)

    result = asyncio.run(
        service.execute(
            session,
            {
                "status": "resolved",
                "target_kind": "npc",
                "target_id": "merchant",
                "intent": "inspect_item",
                "item_id": "bandage",
                "quest_id": None,
                "count": 1,
                "execution": {
                    "kind": "snapshot",
                    "snapshot_type": "inspect_item",
                    "item_id": "bandage",
                },
            },
        )
    )

    assert result.completed is True
    assert [e.event_type for e in result.events] == [
        "interaction_resolved",
        "inspect_item",
    ]
    item_data = result.events[1].payload["item"]
    assert item_data["item_id"] == "bandage"
    assert item_data["name"] == "Bandage"
    assert item_data["base_price"] == 5
    assert item_data["player_owned_count"] == 3


def test_inspect_item_rejected_without_item_id() -> None:
    from app.game_core.adapters.inbound import FastAPIInputPort

    port = FastAPIInputPort()
    result = asyncio.run(
        port.process_action(
            {
                "channel": "interaction",
                "payload": {
                    "target_kind": "npc",
                    "target_id": "merchant",
                    "intent": "inspect_item",
                },
            }
        )
    )

    assert result["status"] == "rejected"
    assert result["code"] == "missing_item"
