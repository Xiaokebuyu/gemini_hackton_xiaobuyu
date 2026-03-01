"""Tests for PartySlice Read API (S-1)."""

from __future__ import annotations

from app.game_core.state.slices.party import PartySlice


def test_get_members_returns_defensive_copy() -> None:
    s = PartySlice()
    s.add_member("alice", {"level": 3, "class": "fighter"})
    members = s.get_members()
    assert members == {"alice": {"level": 3, "class": "fighter"}}
    # mutation of returned copy should not affect slice
    members["alice"]["level"] = 99
    assert s.members["alice"]["level"] == 3


def test_get_approval_returns_zero_for_unknown() -> None:
    s = PartySlice()
    assert s.get_approval("nobody") == 0
    s.modify_approval("bob", 5)
    assert s.get_approval("bob") == 5


def test_get_shared_experiences_returns_defensive_copy() -> None:
    s = PartySlice()
    s.record_experience({"type": "battle", "result": "win"})
    exps = s.get_shared_experiences()
    assert len(exps) == 1
    assert exps[0]["type"] == "battle"
    # mutation should not affect slice
    exps[0]["type"] = "mutated"
    assert s.shared_experiences[0]["type"] == "battle"
