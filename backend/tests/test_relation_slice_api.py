"""Tests for RelationSlice Read API (S-2)."""

from __future__ import annotations

import pytest

from app.game_core.state.slices.relations import RelationSlice


def _slice_with_data() -> RelationSlice:
    s = RelationSlice()
    s.modify_disposition("npc1", "approval", 30)
    s.modify_disposition("npc1", "trust", 10)
    s.set_relationship_stage("npc1", "friendly")
    s.modify_faction("faction_a", 50)
    s.update_shop_state("merchant1", {"inventory": [
        {"item_id": "sword", "count": 3},
        {"item_id": "potion", "count": 5},
    ]})
    return s


def test_get_disposition_full() -> None:
    s = _slice_with_data()
    disp = s.get_disposition("npc1")
    assert disp == {"approval": 30, "trust": 10}


def test_get_disposition_single_dimension() -> None:
    s = _slice_with_data()
    assert s.get_disposition("npc1", "approval") == 30
    assert s.get_disposition("npc1", "trust") == 10


def test_get_disposition_unknown_npc_returns_none() -> None:
    s = _slice_with_data()
    assert s.get_disposition("ghost") is None


def test_get_stage() -> None:
    s = _slice_with_data()
    assert s.get_stage("npc1") == "friendly"
    assert s.get_stage("unknown") is None


def test_get_faction() -> None:
    s = _slice_with_data()
    assert s.get_faction("faction_a") == 50
    assert s.get_faction("unknown_faction") == 0


def test_get_shop_state_returns_defensive_copy() -> None:
    s = _slice_with_data()
    shop = s.get_shop_state("merchant1")
    assert shop is not None
    assert len(shop["inventory"]) == 2
    # mutation should not affect slice
    shop["inventory"].clear()
    assert len(s.shop_states["merchant1"]["inventory"]) == 2


def test_get_shop_state_unknown_returns_none() -> None:
    s = RelationSlice()
    assert s.get_shop_state("nobody") is None


def test_reduce_stock_success() -> None:
    s = _slice_with_data()
    assert s.reduce_stock("merchant1", "sword", 2) is True
    assert s.shop_states["merchant1"]["inventory"][0]["count"] == 1


def test_reduce_stock_removes_item_when_depleted() -> None:
    s = _slice_with_data()
    assert s.reduce_stock("merchant1", "sword", 3) is True
    items = s.shop_states["merchant1"]["inventory"]
    assert all(i["item_id"] != "sword" for i in items)


def test_reduce_stock_insufficient() -> None:
    s = _slice_with_data()
    assert s.reduce_stock("merchant1", "sword", 10) is False
    assert s.shop_states["merchant1"]["inventory"][0]["count"] == 3


def test_reduce_stock_unknown_item() -> None:
    s = _slice_with_data()
    assert s.reduce_stock("merchant1", "nonexistent", 1) is False


def test_reduce_stock_raises_on_nonpositive_count() -> None:
    s = _slice_with_data()
    with pytest.raises(ValueError):
        s.reduce_stock("merchant1", "sword", 0)
