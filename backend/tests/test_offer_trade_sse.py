"""Tests for offer_trade SSE event emission and enrichment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent_orchestration import _enrich_offer_trade_events, _npc_result_to_sse
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.orchestration.models import SSEEvent


# ---------------------------------------------------------------------------
# _npc_result_to_sse: offer_trade branch
# ---------------------------------------------------------------------------


def test_npc_result_to_sse_emits_offer_trade():
    """_npc_result_to_sse produces an offer_trade event for OfferTradeTool results."""
    tr = ToolResult(
        ok=True,
        message="Merchandise displayed.",
        metadata={
            "status": "ok",
            "event_type": "offer_trade",
            "character_id": "blacksmith",
            "shop": {"current_stock": [{"item_id": "iron_sword", "base_price": 50}]},
        },
    )
    result = AgentResult(text="", tool_results=[tr])
    events = _npc_result_to_sse("blacksmith", result)

    assert len(events) == 1
    assert events[0].event_type == "offer_trade"
    assert events[0].payload["npc_id"] == "blacksmith"
    assert "shop_raw" in events[0].payload


def test_npc_result_to_sse_does_not_affect_other_events():
    """Adding offer_trade branch doesn't break existing speech handling."""
    speech_tr = ToolResult(
        ok=True,
        message="Welcome, traveller!",
        metadata={"event_type": "speech"},
    )
    result = AgentResult(text="", tool_results=[speech_tr])
    events = _npc_result_to_sse("shopkeeper", result)

    assert len(events) == 1
    assert events[0].event_type == "npc_response"
    assert events[0].payload["content"] == "Welcome, traveller!"


# ---------------------------------------------------------------------------
# _enrich_offer_trade_events
# ---------------------------------------------------------------------------


def _make_mock_session(shop_states=None, player_gold=100):
    """Build a minimal mock ManagedSession for enrichment tests."""
    session = MagicMock()
    # state.player
    player = SimpleNamespace(
        current_area="frontier_town",
        current_location="market",
        gold=player_gold,
    )
    # state.relations
    relations = SimpleNamespace(
        shop_states=shop_states or {},
    )
    state = MagicMock()
    state.player = player
    state.relations = relations
    state.has_slice = MagicMock(return_value=True)

    session.runtime.state = state
    session.runtime.world = MagicMock()
    return session


def test_enrich_noop_when_no_offer_trade():
    """_enrich_offer_trade_events returns events unchanged if no offer_trade."""
    events = [
        SSEEvent(event_type="npc_response", payload={"content": "hi"}),
        SSEEvent(event_type="gm_comment", payload={"content": "ok"}),
    ]
    session = _make_mock_session()
    result = _enrich_offer_trade_events(events, session)
    assert result is events  # same object, short-circuited


def test_enrich_replaces_offer_trade_with_shop_snapshot():
    """_enrich_offer_trade_events replaces offer_trade with enriched shop_snapshot."""
    events = [
        SSEEvent(event_type="npc_response", payload={"content": "Here are my wares."}),
        SSEEvent(event_type="offer_trade", payload={"npc_id": "blacksmith", "shop_raw": {}}),
    ]
    mock_payload = {
        "npc_id": "blacksmith",
        "player_gold": 100,
        "stock": [{"item_id": "iron_sword", "name": "Iron Sword", "count": 5, "base_price": 50}],
        "player_sellable_items": [],
    }

    with patch(
        "app.agent_orchestration.build_shop_snapshot_payload",
        return_value=mock_payload,
    ) as mock_build, patch(
        "app.agent_orchestration.build_interaction_view_context",
        return_value=MagicMock(),
    ):
        session = _make_mock_session()
        result = _enrich_offer_trade_events(events, session)

    assert len(result) == 2
    # First event unchanged
    assert result[0].event_type == "npc_response"
    # Second event enriched
    assert result[1].event_type == "shop_snapshot"
    assert result[1].payload["npc_id"] == "blacksmith"
    assert result[1].payload["player_gold"] == 100
    mock_build.assert_called_once()
