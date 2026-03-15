"""Tests for Phase B of P28 Wave 4 — GM Functional Options (post-revert).

After the revert, GM no longer embeds functional UI actions.
_VALID_FUNCTIONAL_TYPES is empty; any functional field in LLM output is silently
dropped. Options must have a check or action field to be valid.

Coverage:
- _VALID_FUNCTIONAL_TYPES is empty frozenset
- _validate_option: functional field is always dropped regardless of type
- Options survive only via check or action; functional-only options are rejected
- GM context NPC capability summary building (unchanged)
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
    def test_is_empty_frozenset(self):
        """GM no longer supports any functional types."""
        assert isinstance(_VALID_FUNCTIONAL_TYPES, frozenset)
        assert len(_VALID_FUNCTIONAL_TYPES) == 0

    def test_known_types_not_present(self):
        """Previously valid types are now absent."""
        for t in ("trade_browse", "board_browse", "navigate", "inspect_item", "rest"):
            assert t not in _VALID_FUNCTIONAL_TYPES

    def test_quest_accept_not_present(self):
        assert "quest_accept" not in _VALID_FUNCTIONAL_TYPES


# ---------------------------------------------------------------------------
# TestValidateOptionFunctional
# ---------------------------------------------------------------------------


class TestValidateOptionFunctional:
    def test_functional_with_action_drops_functional_keeps_action(self):
        """functional field is silently dropped; option is still valid via action."""
        opt = {
            "text": "购买补给品",
            "action": "trade",
            "functional": {"type": "trade_browse", "params": {"npc_id": "merchant"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert result["action"] == "trade"
        assert "functional" not in result

    def test_functional_with_check_drops_functional_keeps_check(self):
        opt = {
            "text": "说服他",
            "check": {"skill": "persuasion", "dc": 14},
            "functional": {"type": "board_browse", "params": {}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert "check" in result
        assert "functional" not in result

    def test_invalid_functional_type_dropped_silently(self):
        """Invalid functional type drops the functional field but keeps the option valid."""
        opt = {
            "text": "做些奇怪的事",
            "action": "something",
            "functional": {"type": "invalid_type_xyz", "params": {}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert "functional" not in result
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

    def test_functional_non_dict_dropped(self):
        opt = {
            "text": "说话",
            "action": "talk",
            "functional": "not_a_dict",
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert "functional" not in result

    def test_functional_only_option_rejected(self):
        """Option with text + functional but no check or action is rejected.
        Since _VALID_FUNCTIONAL_TYPES is empty, functional is dropped and then
        there is no check/action → option is None."""
        opt = {
            "text": "查看公告板",
            "functional": {"type": "board_browse", "params": {"board_id": "guild_board"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is None

    def test_quest_accept_functional_dropped_silently(self):
        """quest_accept is not a valid GM functional type — dropped silently."""
        opt = {
            "text": "接受任务",
            "functional": {"type": "quest_accept", "params": {"quest_id": "q1"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        # functional dropped; no action/check → option rejected
        assert result is None

    def test_option_text_only_without_functional_check_action_rejected(self):
        """Option with text but no functional, check, or action → rejected."""
        opt = {"text": "随便说说"}
        result = SuggestOptionsTool._validate_option(opt)
        assert result is None

    def test_rest_functional_dropped(self):
        """rest was formerly valid; now dropped. Option needs action to survive."""
        opt = {
            "text": "短休",
            "functional": {"type": "rest"},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is None

    def test_navigate_functional_dropped(self):
        opt = {
            "text": "去镇中心",
            "functional": {"type": "navigate", "params": {"location_id": "town_center"}},
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is None

    def test_npc_id_and_message_preserved_on_valid_action(self):
        """npc_id and message are preserved alongside action."""
        opt = {
            "text": "拜托你了",
            "action": "talk",
            "npc_id": "receptionist",
            "message": "我想接任务",
        }
        result = SuggestOptionsTool._validate_option(opt)
        assert result is not None
        assert result["npc_id"] == "receptionist"
        assert result["message"] == "我想接任务"
        assert "functional" not in result


# ---------------------------------------------------------------------------
# TestSuggestOptionsToolExecute
# ---------------------------------------------------------------------------


class TestSuggestOptionsToolExecute:
    def test_execute_functional_options_functional_dropped_action_kept(self):
        async def _run():
            tool = _make_tool()
            params = {
                "options": [
                    {
                        "text": "看看有什么任务",
                        "action": "browse_board",
                        "message": "我想接任务",
                        "functional": {"type": "board_browse", "params": {"board_id": "guild_board"}},
                    },
                    {
                        "text": "离开",
                        "action": "leave",
                    },
                ]
            }
            from unittest.mock import MagicMock
            ctx = MagicMock()
            result = await tool.execute(params, ctx)
            assert result.ok
            validated = result.metadata.get("options", [])
            assert len(validated) == 2
            # functional is dropped from first option; action is kept
            assert "functional" not in validated[0]
            assert validated[0]["action"] == "browse_board"
            # Second option unchanged
            assert validated[1]["action"] == "leave"
        asyncio.run(_run())

    def test_execute_all_functional_only_options_rejected(self):
        """If all options have only functional (no action/check), execute returns failure."""
        async def _run():
            tool = _make_tool()
            params = {
                "options": [
                    {
                        "text": "查看公告板",
                        "functional": {"type": "board_browse"},
                    },
                ]
            }
            from unittest.mock import MagicMock
            ctx = MagicMock()
            result = await tool.execute(params, ctx)
            # functional-only options are rejected → no valid options → ok=False
            assert not result.ok
        asyncio.run(_run())

    def test_execute_invalid_functional_with_action_succeeds(self):
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
