"""Tests for Phase 4c — GM immersive tools (C1).

Validates:
- 15 GM tools call correct SessionRuntime methods
- complete_event rename from conclude_quest
- create_memory graph_store integration
- RoleRegistry role isolation
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.world.immersive_tools import (
    AgenticContext,
    activate_event,
    add_item,
    add_xp,
    advance_chapter,
    advance_stage,
    complete_event,
    complete_event_objective,
    complete_objective,
    create_memory,
    damage_player,
    fail_event,
    heal_player,
    remove_item,
    report_flash_evaluation,
    update_disposition,
)
from app.world.role_registry import RoleRegistry


def _run(coro):
    return asyncio.run(coro)


def _make_ctx(**overrides):
    session = MagicMock()
    session.game_state = None
    session.flash_results = {}
    session.chapter_id = "ch1"
    session.area_id = "area1"
    ctx = AgenticContext(
        session=session,
        agent_id="gm",
        role="gm",
        scene_bus=MagicMock(),
        world_id="w1",
        chapter_id="ch1",
        area_id="area1",
        **overrides,
    )
    return ctx


# =========================================================================
# Player state tools
# =========================================================================


class TestHealPlayer:
    def test_calls_session_heal(self):
        ctx = _make_ctx()
        ctx.session.heal.return_value = {"success": True, "hp": 50}
        result = _run(heal_player(ctx=ctx, amount=10))
        ctx.session.heal.assert_called_once_with(10)
        assert result["success"] is True

    def test_int_coercion(self):
        ctx = _make_ctx()
        ctx.session.heal.return_value = {"success": True}
        _run(heal_player(ctx=ctx, amount=10.7))
        ctx.session.heal.assert_called_once_with(10)


class TestDamagePlayer:
    def test_calls_session_damage(self):
        ctx = _make_ctx()
        ctx.session.damage.return_value = {"success": True, "hp": 40}
        result = _run(damage_player(ctx=ctx, amount=5))
        ctx.session.damage.assert_called_once_with(5)
        assert result["success"] is True


class TestAddXp:
    def test_calls_session_add_xp(self):
        ctx = _make_ctx()
        ctx.session.add_xp.return_value = {"success": True, "xp": 100}
        result = _run(add_xp(ctx=ctx, amount=50))
        ctx.session.add_xp.assert_called_once_with(50)
        assert result["success"] is True


class TestAddItem:
    def test_calls_session_add_item(self):
        ctx = _make_ctx()
        ctx.session.add_item.return_value = {"success": True}
        result = _run(add_item(ctx=ctx, item_id="potion", item_name="治疗药水", quantity=3))
        ctx.session.add_item.assert_called_once_with("potion", "治疗药水", 3)
        assert result["success"] is True

    def test_default_quantity(self):
        ctx = _make_ctx()
        ctx.session.add_item.return_value = {"success": True}
        _run(add_item(ctx=ctx, item_id="sword", item_name="铁剑"))
        ctx.session.add_item.assert_called_once_with("sword", "铁剑", 1)


class TestRemoveItem:
    def test_calls_session_remove_item(self):
        ctx = _make_ctx()
        ctx.session.remove_item.return_value = {"success": True}
        result = _run(remove_item(ctx=ctx, item_id="potion", quantity=2))
        ctx.session.remove_item.assert_called_once_with("potion", 2)
        assert result["success"] is True


# =========================================================================
# Event system tools
# =========================================================================


class TestActivateEvent:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.activate_event.return_value = {"success": True}
        result = _run(activate_event(ctx=ctx, event_id="ev1"))
        ctx.session.activate_event.assert_called_once_with("ev1")
        assert result["success"] is True


class TestCompleteEvent:
    def test_calls_session_with_outcome(self):
        ctx = _make_ctx()
        ctx.session.complete_event.return_value = {"success": True}
        result = _run(complete_event(ctx=ctx, event_id="ev1", outcome_key="victory"))
        ctx.session.complete_event.assert_called_once_with("ev1", "victory")
        assert result["success"] is True

    def test_no_session_returns_stub(self):
        ctx = _make_ctx()
        ctx.session = None
        result = _run(complete_event(ctx=ctx, event_id="ev1"))
        assert result.get("stub") is True

    def test_exception_returns_error(self):
        ctx = _make_ctx()
        ctx.session.complete_event.side_effect = ValueError("not found")
        result = _run(complete_event(ctx=ctx, event_id="ev1"))
        assert result["success"] is False
        assert "not found" in result["error"]


class TestCompleteObjective:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.complete_objective.return_value = {"success": True}
        result = _run(complete_objective(ctx=ctx, objective_id="obj1"))
        ctx.session.complete_objective.assert_called_once_with("obj1")


class TestAdvanceStage:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.advance_stage.return_value = {"success": True}
        result = _run(advance_stage(ctx=ctx, event_id="ev1", stage_id="s2"))
        ctx.session.advance_stage.assert_called_once_with("ev1", "s2")


class TestCompleteEventObjective:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.complete_event_objective.return_value = {"success": True}
        result = _run(complete_event_objective(ctx=ctx, event_id="ev1", objective_id="obj1"))
        ctx.session.complete_event_objective.assert_called_once_with("ev1", "obj1")


# =========================================================================
# Time tool
# =========================================================================


# =========================================================================
# Stub fills: advance_chapter, fail_event, report_flash_evaluation
# =========================================================================


class TestAdvanceChapter:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.advance_chapter.return_value = {"success": True, "chapter_id": "ch2"}
        result = _run(advance_chapter(ctx=ctx, target_chapter_id="ch2", transition_type="branch"))
        ctx.session.advance_chapter.assert_called_once_with("ch2", "branch")
        assert result["success"] is True

    def test_no_session_returns_stub(self):
        ctx = _make_ctx()
        ctx.session = None
        result = _run(advance_chapter(ctx=ctx, target_chapter_id="ch2"))
        assert result.get("stub") is True


class TestFailEvent:
    def test_calls_session(self):
        ctx = _make_ctx()
        ctx.session.fail_event.return_value = {"success": True}
        result = _run(fail_event(ctx=ctx, event_id="ev1", reason="timeout"))
        ctx.session.fail_event.assert_called_once_with("ev1", "timeout")

    def test_no_session_returns_stub(self):
        ctx = _make_ctx()
        ctx.session = None
        result = _run(fail_event(ctx=ctx, event_id="ev1"))
        assert result.get("stub") is True


class TestReportFlashEvaluation:
    def test_stores_result(self):
        ctx = _make_ctx()
        result = _run(report_flash_evaluation(ctx=ctx, prompt="Is it raining?", result=True))
        assert result["success"] is True
        assert result["stored"] is True
        assert ctx.session.flash_results["Is it raining?"] is True

    def test_empty_prompt_error(self):
        ctx = _make_ctx()
        result = _run(report_flash_evaluation(ctx=ctx, prompt="", result=True))
        assert result["success"] is False

    def test_no_flash_results_attr(self):
        ctx = _make_ctx()
        del ctx.session.flash_results
        result = _run(report_flash_evaluation(ctx=ctx, prompt="test", result=False))
        assert result["success"] is False


# =========================================================================
# Disposition tool (GM version)
# =========================================================================


class TestUpdateDisposition:
    def test_calls_session_with_deltas(self):
        ctx = _make_ctx()
        ctx.session.update_disposition.return_value = {"success": True, "npc_id": "npc1"}
        result = _run(update_disposition(ctx=ctx, npc_id="npc1", deltas={"approval": 10}, reason="helped"))
        ctx.session.update_disposition.assert_called_once_with("npc1", {"approval": 10}, "helped")
        assert result["success"] is True


# =========================================================================
# Create memory tool
# =========================================================================


class TestCreateMemory:
    def test_empty_content_error(self):
        ctx = _make_ctx()
        result = _run(create_memory(ctx=ctx, content=""))
        assert result["success"] is False

    def test_no_graph_store_stub(self):
        ctx = _make_ctx(graph_store=None)
        result = _run(create_memory(ctx=ctx, content="something happened"))
        assert result.get("stub") is True

    def test_area_scope_upsert(self):
        gs = AsyncMock()
        ctx = _make_ctx(graph_store=gs)
        result = _run(create_memory(ctx=ctx, content="found a cave", scope="area"))
        assert result["success"] is True
        assert result["scope"] == "area"
        gs.upsert_node_v2.assert_called_once()
        call_kwargs = gs.upsert_node_v2.call_args[1]
        assert call_kwargs["world_id"] == "w1"
        assert "area" in str(call_kwargs["scope"])

    def test_character_scope(self):
        gs = AsyncMock()
        ctx = _make_ctx(graph_store=gs)
        result = _run(create_memory(ctx=ctx, content="personal memory", scope="character"))
        assert result["success"] is True
        call_kwargs = gs.upsert_node_v2.call_args[1]
        assert "character" in str(call_kwargs["scope"]).lower() or "player" in str(call_kwargs["scope"]).lower()

    def test_importance_clamped(self):
        gs = AsyncMock()
        ctx = _make_ctx(graph_store=gs)
        _run(create_memory(ctx=ctx, content="test", importance=2.0))
        node = gs.upsert_node_v2.call_args[1]["node"]
        assert node.importance <= 1.0


# =========================================================================
# RoleRegistry isolation
# =========================================================================


class TestRoleIsolation:
    def test_gm_has_new_tools(self):
        ctx = _make_ctx()
        tools = RoleRegistry.get_tools("gm", ctx=ctx)
        names = {t.__name__ for t in tools}
        expected = {
            "heal_player", "damage_player", "add_xp", "add_item", "remove_item",
            "activate_event", "complete_event", "complete_objective",
            "advance_stage", "complete_event_objective",
            "update_disposition", "create_memory", "advance_chapter",
            "fail_event", "report_flash_evaluation", "generate_scene_image",
        }
        assert expected.issubset(names), f"missing: {expected - names}"

    def test_npc_lacks_gm_tools(self):
        ctx = _make_ctx()
        tools = RoleRegistry.get_tools("npc", ctx=ctx)
        names = {t.__name__ for t in tools}
        gm_only = {
            "heal_player", "damage_player", "add_xp",
            "activate_event", "update_disposition", "create_memory",
        }
        assert not (gm_only & names), f"leaked: {gm_only & names}"

    def test_complete_event_registered(self):
        """conclude_quest 已重命名为 complete_event。"""
        ctx = _make_ctx()
        tools = RoleRegistry.get_tools("gm", ctx=ctx)
        names = {t.__name__ for t in tools}
        assert "complete_event" in names
        assert "conclude_quest" not in names
