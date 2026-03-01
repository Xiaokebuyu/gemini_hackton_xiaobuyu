"""Tests for RoleStateProxy (N-8): role-based StateContainer read isolation."""

from __future__ import annotations

import pytest

from app.game_core.bootstrap import build_default_state
from app.game_core.narrative.role_proxy import RoleStateProxy


def _proxy(role: str) -> RoleStateProxy:
    return RoleStateProxy(build_default_state(), role)


def test_gm_full_access_flags() -> None:
    """GM role can access any slice, including flags."""
    proxy = _proxy("gm")
    _ = proxy.flags  # should not raise


def test_gm_full_access_narrative_plan() -> None:
    """GM role can access narrative_plan slice."""
    proxy = _proxy("gm")
    _ = proxy.narrative_plan  # should not raise


def test_npc_allows_scene() -> None:
    """NPC role can access scene slice."""
    proxy = _proxy("npc")
    _ = proxy.scene  # should not raise


def test_npc_allows_relations() -> None:
    """NPC role can access relations slice."""
    proxy = _proxy("npc")
    _ = proxy.relations  # should not raise


def test_npc_blocks_flags() -> None:
    """NPC role cannot access flags slice."""
    proxy = _proxy("npc")
    with pytest.raises(AttributeError, match="npc"):
        _ = proxy.flags


def test_npc_blocks_narrative_plan() -> None:
    """NPC role cannot access narrative_plan slice."""
    proxy = _proxy("npc")
    with pytest.raises(AttributeError, match="npc"):
        _ = proxy.narrative_plan


def test_teammate_allows_party() -> None:
    """Teammate role can access party slice (not available to NPC)."""
    proxy = _proxy("teammate")
    _ = proxy.party  # should not raise


def test_teammate_blocks_flags() -> None:
    """Teammate role cannot access flags slice."""
    proxy = _proxy("teammate")
    with pytest.raises(AttributeError, match="teammate"):
        _ = proxy.flags


def test_has_slice_open_to_all_roles() -> None:
    """has_slice() never raises regardless of role — returns bool."""
    for role in ("gm", "npc", "teammate"):
        proxy = _proxy(role)
        result = proxy.has_slice("flags")
        assert isinstance(result, bool)


def test_proxy_is_read_only() -> None:
    """Attribute assignment on proxy raises AttributeError."""
    proxy = _proxy("gm")
    with pytest.raises(AttributeError):
        proxy.scene = None  # type: ignore[assignment]
