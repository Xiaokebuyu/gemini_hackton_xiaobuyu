"""Tests for Phase B of P28 Wave 4 — GM Functional Options.

Coverage:
- SuggestOptionsTool: functional field schema + validation
- _validate_option: valid / invalid / missing type / with params / functional-only option
- GM context NPC capability summary building
- functional field preserved through validate round-trip
"""
import asyncio

import pytest

from app.game_core.narrative.gm_tools import SuggestOptionsTool, _VALID_FUNCTIONAL_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool() -> SuggestOptionsTool:
    return SuggestOptionsTool()


# ---------------------------------------------------------------------------
# TestValidFunctionalTypes
# ---------------------------------------------------------------------------


class TestValidFunctionalTypes:
    def test_expected_types_present(self):
        for t in ("trade_browse", "board_browse", "navigate", "inspect_item", "rest"):
            assert t in _VALID_FUNCTIONAL_TYPES

    def test_quest_accept_not_present(self):
        # GM is a pure narrator — quest_accept is handled by NPC tools, not GM functional options
        assert "quest_accept" not in _VALID_FUNCTIONAL_TYPES

    def test_is_frozenset(self):
        assert isinstance(_VALID_FUNCTIONAL_TYPES, frozenset)

    def test_empty_string_not_included(self):
        # GM-generated functional must not be empty string
        assert "" not in _VALID_FUNCTIONAL_TYPES


# ---------------------------------------------------------------------------
# TestValidateOptionFunctional
# ---------------------------------------------------------------------------


class TestValidateOptionFunctional:
    def test_valid_functional_type_preserved(self):
        opt = {
            "text": "查看公告板",
            "message": "我想看任务",
            "functional": {"type": "board_browse", "params": {"board_id": "frontier_board"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        # functional-only option (no check or action) should be returned
        assert result is not None
        assert result["text"] == "查看公告板"
        assert result["message"] == "我想看任务"
        assert "functional" in result
        assert result["functional"]["type"] == "board_browse"
        assert result["functional"]["params"] == {"board_id": "frontier_board"}

    def test_valid_functional_with_action(self):
        opt = {
            "text": "购买补给品",
            "action": "trade",
            "functional": {"type": "trade_browse", "params": {"npc_id": "merchant"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert result["action"] == "trade"
        assert result["functional"]["type"] == "trade_browse"

    def test_invalid_functional_type_dropped_silently(self):
        """Invalid functional type drops the functional field but keeps the option valid."""
        opt = {
            "text": "做些奇怪的事",
            "action": "something",
            "functional": {"type": "invalid_type_xyz", "params": {}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        # functional with invalid type is silently dropped
        assert "functional" not in result
        # but the option itself is still valid via action
        assert result["action"] == "something"

    def test_missing_type_in_functional_dropped(self):
        """functional dict without type is invalid → functional dropped."""
        opt = {
            "text": "神秘操作",
            "action": "do_it",
            "functional": {"params": {"x": "y"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert "functional" not in result

    def test_functional_with_empty_params(self):
        opt = {
            "text": "短休",
            "functional": {"type": "rest"},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert result["functional"]["type"] == "rest"
        assert result["functional"]["params"] == {}

    def test_functional_non_dict_dropped(self):
        opt = {
            "text": "说话",
            "action": "talk",
            "functional": "not_a_dict",
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert "functional" not in result

    def test_option_with_only_functional_and_text_is_valid(self):
        """An option with text + valid functional (no check or action) should be accepted."""
        opt = {
            "text": "查看公告板",
            "functional": {"type": "board_browse", "params": {"board_id": "guild_board"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert result["functional"]["type"] == "board_browse"

    def test_quest_accept_functional_dropped_silently(self):
        """quest_accept is no longer a valid GM functional type — dropped silently."""
        opt = {
            "text": "接受任务",
            "functional": {"type": "quest_accept", "params": {"quest_id": "q1"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        # quest_accept is no longer valid → functional dropped; no action/check either → option rejected
        assert result is None

    def test_option_text_only_without_functional_check_action_rejected(self):
        """Option with text but no functional, check, or action → rejected."""
        opt = {"text": "随便说说"}
        result = SuggestOptionsTool._validate_option(opt)
        assert result is None

    def test_all_valid_functional_types_accepted(self):
        for func_type in _VALID_FUNCTIONAL_TYPES:
            opt = {
                "text": f"执行 {func_type}",
                "functional": {"type": func_type, "params": {}},
            }
            result = SuggestOptionsTool._validate_option(opt)
            assert result is not None, f"Expected valid option for type={func_type}"
            assert result["functional"]["type"] == func_type


# ---------------------------------------------------------------------------
# TestSuggestOptionsToolExecute
# ---------------------------------------------------------------------------


class TestSuggestOptionsToolExecute:
    def test_execute_with_functional_options_succeeds(self):
        async def _run():
            tool = _make_tool()
            params = {
                "options": [
                    {
                        "text": "看看有什么任务",
                        "message": "我想接任务",
                        "functional": {"type": "board_browse", "params": {"board_id": "guild_board"}},
                    },
                    {
                        "text": "离开",
                        "action": "leave",
                    },
                ]
            }
            # We don't need a real context for this test — execute only
            # calls _validate_option which is pure logic
            from unittest.mock import MagicMock
            ctx = MagicMock()
            result = await tool.execute(params, ctx)
            assert result.ok
            validated = result.metadata.get("options", [])
            assert len(validated) == 2
            # First option should have functional
            assert validated[0]["functional"]["type"] == "board_browse"
            # Second option has action only
            assert "action" in validated[1]
            assert "functional" not in validated[1]
        asyncio.run(_run())

    def test_execute_all_invalid_functional_falls_back_to_action(self):
        """Options with invalid functional type still work if they have action."""
        async def _run():
            tool = _make_tool()
            params = {
                "options": [
                    {
                        "text": "做坏事",
                        "action": "mischief",
                        "functional": {"type": "evil_action"},  # invalid type
                    },
                ]
            }
            from unittest.mock import MagicMock
            ctx = MagicMock()
            result = await tool.execute(params, ctx)
            assert result.ok
            opts = result.metadata.get("options", [])
            assert len(opts) == 1
            assert "functional" not in opts[0]
            assert opts[0]["action"] == "mischief"
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# TestGmNpcCapabilityContext
# ---------------------------------------------------------------------------


class TestGmNpcCapabilityContext:
    """Test the _build_gm_npc_capability_context helper."""

    def test_returns_empty_for_npc_without_capabilities(self):
        from app.agent_orchestration import _build_gm_npc_capability_context
        from unittest.mock import MagicMock

        world = MagicMock()
        world.has_registry.return_value = False

        state = MagicMock()
        state.has_slice.return_value = False

        result = _build_gm_npc_capability_context(world, state, "unknown_npc")
        assert result == ""

    def test_returns_section_for_merchant_npc(self):
        from app.agent_orchestration import _build_gm_npc_capability_context
        from unittest.mock import MagicMock

        world = MagicMock()
        world.has_registry.return_value = True
        npc_profile = MagicMock()
        npc_profile.name = "商人"
        npc_profile.tags = ["merchant"]
        world.characters.get.return_value = npc_profile

        state = MagicMock()
        # narrative_plan slice: no dynamic caps
        state.has_slice.side_effect = lambda name: name in ("narrative_plan",)
        state.narrative_plan.get_capabilities.return_value = []
        # areas and player slices not available
        state.has_slice.side_effect = lambda name: name == "narrative_plan"
        state.narrative_plan.get_capabilities.return_value = []

        result = _build_gm_npc_capability_context(world, state, "merchant_npc")
        assert "当前 NPC 的能力" in result
        assert "trade_browse" in result
        assert "merchant_npc" in result

    def test_returns_dynamic_caps_in_section(self):
        from app.agent_orchestration import _build_gm_npc_capability_context
        from unittest.mock import MagicMock

        world = MagicMock()
        world.has_registry.return_value = True
        npc_profile = MagicMock()
        npc_profile.name = "柜台小姐"
        npc_profile.tags = ["guild", "receptionist"]
        world.characters.get.return_value = npc_profile

        state = MagicMock()
        state.has_slice.side_effect = lambda name: name == "narrative_plan"
        state.narrative_plan.get_capabilities.return_value = [
            {
                "capability_id": "help_browse_board",
                "instruction": "帮助查看公告板",
                "functional": "board_browse",
                "expiry_tick": 0,
            }
        ]

        result = _build_gm_npc_capability_context(world, state, "guild_girl")
        assert "当前 NPC 的能力" in result
        assert "help_browse_board" in result
        assert "帮助查看公告板" in result
        assert "functional=board_browse" in result

    def test_includes_nearby_npc_section_for_merchant(self):
        from app.agent_orchestration import _build_gm_npc_capability_context
        from unittest.mock import MagicMock

        world = MagicMock()
        world.has_registry.return_value = True

        # Focus NPC: guild girl
        guild_profile = MagicMock()
        guild_profile.name = "柜台小姐"
        guild_profile.tags = ["guild"]

        # Nearby NPC: merchant
        merchant_profile = MagicMock()
        merchant_profile.name = "商人"
        merchant_profile.tags = ["merchant"]

        def _get_char(npc_id):
            if npc_id == "guild_girl":
                return guild_profile
            if npc_id == "merchant_npc":
                return merchant_profile
            return None

        world.characters.get.side_effect = _get_char

        # State with areas and player slices
        state = MagicMock()

        area_state = MagicMock()
        area_state.npc_locations = {"guild_girl": "counter", "merchant_npc": "stall"}

        state.has_slice.side_effect = lambda name: name in ("narrative_plan", "areas", "player")
        state.player.current_area = "frontier_town"
        state.areas.areas = {"frontier_town": area_state}
        state.narrative_plan.get_capabilities.return_value = []

        result = _build_gm_npc_capability_context(world, state, "guild_girl")
        assert "附近其他 NPC" in result
        assert "merchant_npc" in result
        assert "trade_browse" in result
