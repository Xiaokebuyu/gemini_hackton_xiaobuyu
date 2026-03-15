from __future__ import annotations

import asyncio

from app.game_core import GameRuntime
from app.game_core.adapters.persistence import NullPersistencePort
from app.game_core.adapters.session_store import SaveStore
from app.game_core.state import StateChange
from app.interaction_service import build_interaction_view_context
from app.interaction_views import (
    build_quest_brief_payload,
    build_quest_location_payload,
    build_quest_progress_payload,
    build_quest_requirements_payload,
    build_quest_reward_payload,
    build_shop_snapshot_payload,
    build_talk_snapshot_payload,
)
from app.world_seed import _shell_world_seed


def _runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def _session(*, location_id: str | None = "counter"):
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
    session.runtime.state.areas.update_npc_location("receptionist", "counter")
    session.runtime.state.player.apply_state_change(
        StateChange("player", "set", "current_location", location_id)
    )
    return session


def _view_context(session) -> object:
    return build_interaction_view_context(session.runtime.state, session.runtime.world)


def test_build_talk_snapshot_payload_for_merchant_keeps_global_intent_order() -> None:
    session = _session()
    # Non-empty stock triggers browse/buy/sell/inspect_item.
    session.runtime.state.relations.shop_states["merchant"] = {
        "current_stock": [{"item_id": "training_sword", "price": 12, "count": 1}],
    }

    payload = build_talk_snapshot_payload(_view_context(session), "merchant")

    assert payload["available_intents"] == [
        "talk",
        "greet",
        "browse",
        "buy",
        "sell",
        "inspect_item",
    ]


def test_build_talk_snapshot_payload_for_receptionist_orders_accept_and_report_last() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_available",
        {
            "status": "available",
            "title": "Available Lead",
            "summary": "A lead that can be accepted.",
        },
    )
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "completed",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "requires_report": True,
        },
    )

    payload = build_talk_snapshot_payload(_view_context(session), "receptionist")

    assert payload["available_intents"] == [
        "talk",
        "greet",
        "ask_quest",
        "ask_progress",
        "ask_location",
        "ask_requirements",
        "ask_reward",
        "accept_quest",
        "report_quest",
    ]


def test_build_interaction_view_context_precomputes_normalized_quest_views() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "completed",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "requires_report": True,
        },
    )

    context = _view_context(session)

    quest_view = context.dynamic_quest_views["dq_report_in"]
    assert quest_view["quest_id"] == "dq_report_in"
    assert quest_view["can_report"] is True
    assert quest_view["ui_state"] == "ready_to_report"
    assert quest_view["badge"] == {"key": "ready_to_report", "label": "待汇报"}


def test_build_quest_brief_payload_reads_source_milestone_from_board_metadata() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "available",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
        },
    )
    session.runtime.state.areas.add_board_bulletin(
        "guild_hall",
        "board",
        {
            "board_id": "board",
            "quest_id": "dq_report_in",
            "metadata": {"source_milestone": "report_in"},
        },
    )

    payload = build_quest_brief_payload(_view_context(session), "receptionist", "dq_report_in")

    assert payload["quest"]["source_milestone"] == "report_in"
    assert payload["quest"]["ui_state"] == "available"
    assert payload["quest"]["badge"] == {"key": "available", "label": "可接取"}


def test_build_quest_progress_payload_marks_reportable_quests_ready_to_report() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "completed",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "requires_report": True,
            "reported": False,
        },
    )

    payload = build_quest_progress_payload(_view_context(session), "receptionist", "dq_report_in")

    assert payload["quest"]["can_report"] is True
    assert payload["quest"]["ui_state"] == "ready_to_report"
    assert payload["quest"]["badge"] == {"key": "ready_to_report", "label": "待汇报"}


def test_build_quest_location_payload_reports_known_locations() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "available",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "area_id": "frontier",
            "location_id": "camp",
        },
    )

    payload = build_quest_location_payload(_view_context(session), "merchant", "dq_report_in")

    assert payload["quest"]["location_known"] is True
    assert payload["quest"]["area_id"] == "frontier"
    assert payload["quest"]["location_id"] == "camp"


def test_build_quest_requirements_payload_preserves_active_gating_reason() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "active",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "requirements": ["bring proof", "", "return alive"],
        },
    )

    payload = build_quest_requirements_payload(_view_context(session), "merchant", "dq_report_in")

    assert payload["quest"]["requirements"] == ["bring proof", "return alive"]
    assert payload["quest"]["can_accept"] is False
    assert payload["quest"]["gating_reason"] == "quest is already active"


def test_build_quest_reward_payload_enriches_items_and_falls_back_to_item_id() -> None:
    session = _session()
    session.runtime.state.quests.add_dynamic_quest(
        "dq_report_in",
        {
            "status": "available",
            "title": "Lead: Report In",
            "summary": "Follow the new lead tied to report_in.",
            "rewards": {
                "gold": 25,
                "items": [
                    {"item_id": "bandage", "count": 2},
                    {"item_id": "mystery_item", "count": 1},
                ],
            },
        },
    )

    payload = build_quest_reward_payload(_view_context(session), "merchant", "dq_report_in")

    assert payload["quest"]["reward_known"] is True
    assert payload["quest"]["gold"] == 25
    assert payload["quest"]["items"] == [
        {
            "item_id": "bandage",
            "count": 2,
            "name": "Bandage",
            "type": "",
            "rarity": "",
        },
        {
            "item_id": "mystery_item",
            "count": 1,
            "name": "mystery_item",
            "type": "",
            "rarity": "",
        },
    ]
    assert payload["quest"]["ui_state"] == "available"
    assert payload["quest"]["badge"] == {"key": "available", "label": "可接取"}


def test_build_shop_snapshot_payload_exposes_daily_refresh_policy() -> None:
    session = _session()
    session.runtime.state.relations.shop_states["merchant"] = {
        "npc_id": "merchant",
        "current_stock": [],
        "refresh_on": "daily",
        "last_refresh_tick": 8,
    }

    payload = build_shop_snapshot_payload(_view_context(session), "merchant")

    assert payload["refresh_policy"] == {
        "mode": "daily",
        "configured_modes": ["daily"],
        "auto_refresh": True,
        "last_refresh_tick": 8,
    }
    assert payload["next_refresh_hint"] == "Refreshes automatically when day 2 begins."


def test_build_shop_snapshot_payload_keeps_long_rest_hint_conservative() -> None:
    session = _session()
    session.runtime.state.relations.shop_states["merchant"] = {
        "npc_id": "merchant",
        "current_stock": [],
        "refresh_on": "long_rest",
        "last_refresh_tick": 8,
    }

    payload = build_shop_snapshot_payload(_view_context(session), "merchant")

    assert payload["refresh_policy"] == {
        "mode": "long_rest",
        "configured_modes": ["long_rest"],
        "auto_refresh": False,
        "last_refresh_tick": 8,
    }
    assert payload["next_refresh_hint"] == "Configured to refresh after a long rest."
