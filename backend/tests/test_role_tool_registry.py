"""Tests for RoleToolRegistry traits filtering (N-5).

Verifies that get_tools_for() correctly filters tools by NPC profile tags,
and that OfferTradeTool is only visible to merchant-tagged NPCs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.narrative.character_tools import (
    OfferTradeTool,
    SpeakTool,
    register_npc_tools,
)
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.narrative.tools import AgentTool


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_registry() -> RoleToolRegistry:
    registry = RoleToolRegistry()
    register_npc_tools(registry)
    return registry


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_traits_none_returns_all_tools() -> None:
    """traits=None → backward-compatible, no filtering."""
    registry = _make_registry()
    tools = registry.get_tools_for("npc", None)
    names = {t.name for t in tools}
    assert "offer_trade" in names
    assert "speak" in names


def test_traits_empty_list_filters_trait_restricted_tools() -> None:
    """traits=[] → NPC with no tags does NOT get trait-restricted tools."""
    registry = _make_registry()
    tools = registry.get_tools_for("npc", [])
    names = {t.name for t in tools}
    assert "offer_trade" not in names  # requires merchant trait
    assert "speak" in names  # no trait restriction


def test_merchant_tag_includes_offer_trade() -> None:
    """traits=["merchant"] → OfferTradeTool visible (all required traits present)."""
    registry = _make_registry()
    tools = registry.get_tools_for("npc", ["merchant"])
    names = {t.name for t in tools}
    assert "offer_trade" in names
    # Unrestricted tools still visible
    assert "speak" in names
    assert "emote" in names


def test_guard_tag_excludes_offer_trade() -> None:
    """traits=["guard"] → OfferTradeTool hidden (merchant tag not satisfied)."""
    registry = _make_registry()
    tools = registry.get_tools_for("npc", ["guard"])
    names = {t.name for t in tools}
    assert "offer_trade" not in names
    # Unrestricted tools still visible
    assert "speak" in names


def test_tool_with_empty_applicable_traits_visible_always() -> None:
    """A tool with applicable_traits=[] is always visible regardless of traits."""
    registry = _make_registry()
    speak = SpeakTool()
    assert speak.applicable_traits == []

    for trait_set in [None, [], ["guard"], ["wizard"], ["merchant"]]:
        tools = registry.get_tools_for("npc", trait_set)
        names = {t.name for t in tools}
        assert "speak" in names, f"speak should be visible for traits={trait_set}"


def test_offer_trade_applicable_traits_is_merchant() -> None:
    """OfferTradeTool.applicable_traits == ['merchant']."""
    tool = OfferTradeTool()
    assert tool.applicable_traits == ["merchant"]
