"""Tests for Phase A of P28 Wave 4 — CapabilityDescriptor system.

Coverage:
- CapabilityDescriptor snapshot/restore round-trip
- NarrativePlanSlice: assign / revoke / get / prune / replace-same-id
- directive_contracts: assign_capability and revoke_capability validation
- PlannerNpcHandler: compute_assign_capability / compute_revoke_capability
- _build_capability_boundary_prompt: caps injection / empty caps
"""
import asyncio

import pytest

from app.game_core.planning.capabilities import CapabilityDescriptor, VALID_FUNCTIONAL_TYPES
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state import StateContainer
from app.game_core.rules.models import Command
from app.game_core.rules.handlers.planner import PlannerNpcHandler
from app.game_core.orchestration.npc_interaction import _build_capability_boundary_prompt
from app.game_core.content import WorldInstance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state() -> StateContainer:
    state = StateContainer()
    state.register(NarrativePlanSlice())
    return state


def _make_world() -> WorldInstance:
    return WorldInstance("test_world")


def _make_handler() -> PlannerNpcHandler:
    return PlannerNpcHandler()


# Minimal fake executor for prompt tests
class _FakeExecutor:
    tool_registry = None


# ---------------------------------------------------------------------------
# TestCapabilityDescriptor
# ---------------------------------------------------------------------------

class TestCapabilityDescriptor:
    def test_snapshot_restore_round_trip(self):
        cap = CapabilityDescriptor(
            capability_id="help_browse",
            npc_id="guild_girl",
            instruction="帮助查看公告板",
            functional="board_browse",
            functional_params={"board_id": "frontier_board"},
            assigned_tick=10,
            expiry_tick=30,
            source="planner",
        )
        data = cap.snapshot()
        restored = CapabilityDescriptor.restore(data)
        assert restored.capability_id == "help_browse"
        assert restored.npc_id == "guild_girl"
        assert restored.instruction == "帮助查看公告板"
        assert restored.functional == "board_browse"
        assert restored.functional_params == {"board_id": "frontier_board"}
        assert restored.assigned_tick == 10
        assert restored.expiry_tick == 30
        assert restored.source == "planner"

    def test_snapshot_returns_defensive_copy(self):
        cap = CapabilityDescriptor(
            capability_id="c1",
            npc_id="n1",
            instruction="do something",
            functional_params={"key": "val"},
        )
        data = cap.snapshot()
        data["functional_params"]["key"] = "mutated"
        assert cap.functional_params["key"] == "val"

    def test_restore_missing_fields_use_defaults(self):
        restored = CapabilityDescriptor.restore({"capability_id": "c1", "npc_id": "n1", "instruction": "i"})
        assert restored.functional == ""
        assert restored.functional_params == {}
        assert restored.assigned_tick == 0
        assert restored.expiry_tick == 0
        assert restored.source == "planner"

    def test_valid_functional_types_contains_empty(self):
        assert "" in VALID_FUNCTIONAL_TYPES
        assert "trade_browse" in VALID_FUNCTIONAL_TYPES
        # quest_accept removed — GM is a pure narrator, quest handling is via NPC tools
        assert "quest_accept" not in VALID_FUNCTIONAL_TYPES


# ---------------------------------------------------------------------------
# TestNarrativePlanCapabilities
# ---------------------------------------------------------------------------

class TestNarrativePlanCapabilities:
    def test_assign_and_get(self):
        np = NarrativePlanSlice()
        cap = {"capability_id": "c1", "npc_id": "npc_a", "instruction": "do x"}
        np.assign_capability("npc_a", cap)
        caps = np.get_capabilities("npc_a")
        assert len(caps) == 1
        assert caps[0]["capability_id"] == "c1"
        assert np._dirty

    def test_get_returns_defensive_copy(self):
        np = NarrativePlanSlice()
        cap = {"capability_id": "c1", "npc_id": "npc_a", "instruction": "do x"}
        np.assign_capability("npc_a", cap)
        caps = np.get_capabilities("npc_a")
        caps[0]["instruction"] = "mutated"
        # Original should be unchanged
        assert np.npc_capabilities["npc_a"][0]["instruction"] == "do x"

    def test_assign_replaces_same_capability_id(self):
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "original"})
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "updated"})
        caps = np.get_capabilities("npc_a")
        assert len(caps) == 1
        assert caps[0]["instruction"] == "updated"

    def test_assign_multiple_different_ids(self):
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "a"})
        np.assign_capability("npc_a", {"capability_id": "c2", "npc_id": "npc_a", "instruction": "b"})
        caps = np.get_capabilities("npc_a")
        assert len(caps) == 2

    def test_revoke_existing(self):
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "x"})
        result = np.revoke_capability("npc_a", "c1")
        assert result is True
        assert np.get_capabilities("npc_a") == []
        # npc_a key should be cleaned up
        assert "npc_a" not in np.npc_capabilities

    def test_revoke_nonexistent_returns_false(self):
        np = NarrativePlanSlice()
        result = np.revoke_capability("npc_a", "no_such_cap")
        assert result is False

    def test_prune_expired(self):
        np = NarrativePlanSlice()
        # expiry_tick=5, current_tick=10 → expired
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "x", "expiry_tick": 5})
        # expiry_tick=0 → never expires
        np.assign_capability("npc_a", {"capability_id": "c2", "npc_id": "npc_a", "instruction": "y", "expiry_tick": 0})
        np._dirty = False  # reset to detect prune sets dirty
        pruned = np.prune_expired_capabilities(current_tick=10)
        assert pruned == 1
        caps = np.get_capabilities("npc_a")
        assert len(caps) == 1
        assert caps[0]["capability_id"] == "c2"
        assert np._dirty

    def test_prune_no_expired(self):
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "x", "expiry_tick": 0})
        np._dirty = False
        pruned = np.prune_expired_capabilities(current_tick=100)
        assert pruned == 0
        assert not np._dirty

    def test_snapshot_restore_round_trip(self):
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {
            "capability_id": "c1", "npc_id": "npc_a", "instruction": "do x", "expiry_tick": 50
        })
        data = np.snapshot()
        np2 = NarrativePlanSlice()
        np2.restore(data)
        caps = np2.get_capabilities("npc_a")
        assert len(caps) == 1
        assert caps[0]["capability_id"] == "c1"
        assert caps[0]["expiry_tick"] == 50

    def test_apply_state_change_assign(self):
        from app.game_core.state.delta import StateChange
        np = NarrativePlanSlice()
        cap_dict = {"capability_id": "c1", "npc_id": "npc_a", "instruction": "do x"}
        change = StateChange("narrative_plan", "set", "npc_capabilities.assign", cap_dict)
        np.apply_state_change(change)
        assert len(np.get_capabilities("npc_a")) == 1

    def test_apply_state_change_revoke(self):
        from app.game_core.state.delta import StateChange
        np = NarrativePlanSlice()
        np.assign_capability("npc_a", {"capability_id": "c1", "npc_id": "npc_a", "instruction": "x"})
        change = StateChange("narrative_plan", "remove", "npc_capabilities.revoke", {"npc_id": "npc_a", "capability_id": "c1"})
        np.apply_state_change(change)
        assert np.get_capabilities("npc_a") == []


# ---------------------------------------------------------------------------
# TestAssignCapabilityContract
# ---------------------------------------------------------------------------

class TestAssignCapabilityContract:
    def _validate(self, payload: dict) -> object:
        return validate_planner_directive({"kind": "assign_capability", "payload": payload})

    def test_valid_minimal(self):
        result = self._validate({"npc_id": "n1", "capability_id": "c1", "instruction": "do x"})
        assert result.ok is True

    def test_valid_with_functional(self):
        result = self._validate({
            "npc_id": "n1", "capability_id": "c1", "instruction": "do x",
            "functional": "board_browse",
        })
        assert result.ok is True

    def test_quest_accept_functional_now_invalid(self):
        # quest_accept removed from VALID_FUNCTIONAL_TYPES — capability descriptors using it should fail
        result = self._validate({
            "npc_id": "n1", "capability_id": "c1", "instruction": "do x",
            "functional": "quest_accept",
        })
        assert result.ok is False
        assert result.reason_code == "invalid_functional"

    def test_valid_with_expiry_ticks(self):
        result = self._validate({
            "npc_id": "n1", "capability_id": "c1", "instruction": "do x",
            "expiry_ticks": 20,
        })
        assert result.ok is True
        assert result.payload["expiry_ticks"] == 20

    def test_missing_npc_id(self):
        result = self._validate({"capability_id": "c1", "instruction": "do x"})
        assert result.ok is False
        assert result.reason_code == "missing_npc_id"

    def test_missing_capability_id(self):
        result = self._validate({"npc_id": "n1", "instruction": "do x"})
        assert result.ok is False
        assert result.reason_code == "missing_capability_id"

    def test_missing_instruction(self):
        result = self._validate({"npc_id": "n1", "capability_id": "c1"})
        assert result.ok is False
        assert result.reason_code == "missing_instruction"

    def test_invalid_functional(self):
        result = self._validate({
            "npc_id": "n1", "capability_id": "c1", "instruction": "do x",
            "functional": "unknown_type",
        })
        assert result.ok is False
        assert result.reason_code == "invalid_functional"

    def test_empty_string_functional_is_valid(self):
        result = self._validate({
            "npc_id": "n1", "capability_id": "c1", "instruction": "do x",
            "functional": "",
        })
        assert result.ok is True


# ---------------------------------------------------------------------------
# TestRevokeCapabilityContract
# ---------------------------------------------------------------------------

class TestRevokeCapabilityContract:
    def _validate(self, payload: dict) -> object:
        return validate_planner_directive({"kind": "revoke_capability", "payload": payload})

    def test_valid(self):
        result = self._validate({"npc_id": "n1", "capability_id": "c1"})
        assert result.ok is True

    def test_missing_capability_id(self):
        result = self._validate({"npc_id": "n1"})
        assert result.ok is False
        assert result.reason_code == "missing_capability_id"

    def test_missing_npc_id(self):
        result = self._validate({"capability_id": "c1"})
        assert result.ok is False
        assert result.reason_code == "missing_npc_id"


# ---------------------------------------------------------------------------
# TestPlannerNpcHandler
# ---------------------------------------------------------------------------

class TestPlannerNpcHandler:
    def test_compute_assign_capability(self):
        state = _make_state()
        world = _make_world()
        handler = _make_handler()
        cmd = Command(
            type="planner_assign_capability",
            params={
                "npc_id": "guild_girl",
                "capability_id": "help_browse_board",
                "instruction": "帮助查看公告板任务",
                "functional": "board_browse",
                "current_tick": 5,
                "expiry_ticks": 20,
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is True
        assert result.delta is not None
        assert len(result.delta.changes) == 1
        change = result.delta.changes[0]
        assert change.path == "npc_capabilities.assign"
        assert change.value["capability_id"] == "help_browse_board"
        assert change.value["npc_id"] == "guild_girl"
        # expiry_tick = current_tick + expiry_ticks = 5 + 20 = 25
        assert change.value["expiry_tick"] == 25

    def test_compute_assign_no_expiry(self):
        state = _make_state()
        world = _make_world()
        handler = _make_handler()
        cmd = Command(
            type="planner_assign_capability",
            params={
                "npc_id": "npc_a",
                "capability_id": "c1",
                "instruction": "do something",
                "source": "narrative_planner",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is True
        assert result.delta is not None
        change = result.delta.changes[0]
        assert change.value["expiry_tick"] == 0  # no expiry

    def test_compute_revoke_capability(self):
        state = _make_state()
        world = _make_world()
        handler = _make_handler()
        cmd = Command(
            type="planner_revoke_capability",
            params={
                "npc_id": "guild_girl",
                "capability_id": "help_accept_quest",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is True
        assert result.delta is not None
        assert len(result.delta.changes) == 1
        change = result.delta.changes[0]
        assert change.path == "npc_capabilities.revoke"
        assert change.operation == "remove"
        assert change.value["npc_id"] == "guild_girl"
        assert change.value["capability_id"] == "help_accept_quest"

    def test_validate_assign_missing_npc_id(self):
        state = _make_state()
        world = _make_world()
        handler = _make_handler()
        cmd = Command(
            type="planner_assign_capability",
            params={"capability_id": "c1", "instruction": "x"},
            source="narrative_planner",
        )
        result = handler.validate(cmd, state, world)
        assert result.ok is False
        assert "npc_id" in result.reason

    def test_validate_assign_missing_instruction(self):
        state = _make_state()
        world = _make_world()
        handler = _make_handler()
        cmd = Command(
            type="planner_assign_capability",
            params={"npc_id": "n1", "capability_id": "c1"},
            source="narrative_planner",
        )
        result = handler.validate(cmd, state, world)
        assert result.ok is False
        assert "instruction" in result.reason


# ---------------------------------------------------------------------------
# TestCapabilityPrompt
# ---------------------------------------------------------------------------

class TestCapabilityPrompt:
    def test_injection_with_caps(self):
        executor = _FakeExecutor()
        caps = [
            {"capability_id": "help_quest", "instruction": "帮助接取任务"},
            {"capability_id": "share_info", "instruction": "分享情报"},
        ]
        result = _build_capability_boundary_prompt(executor, [], capabilities=caps)
        assert "你的特殊能力" in result
        assert "help_quest" in result
        assert "帮助接取任务" in result
        assert "share_info" in result
        assert "分享情报" in result

    def test_injection_empty_caps(self):
        executor = _FakeExecutor()
        result = _build_capability_boundary_prompt(executor, [], capabilities=[])
        assert "你当前的能力" in result
        # No special capabilities section when list is empty
        assert "你的特殊能力" not in result

    def test_injection_no_caps_arg(self):
        executor = _FakeExecutor()
        # Default argument (no capabilities param)
        result = _build_capability_boundary_prompt(executor, [])
        assert "你当前的能力" in result
        assert "你的特殊能力" not in result

    def test_instruction_added_to_can_do(self):
        executor = _FakeExecutor()
        caps = [
            {"capability_id": "sell_potions", "instruction": "展示和销售药水"},
        ]
        result = _build_capability_boundary_prompt(executor, [], capabilities=caps)
        # instruction should appear in the 你可以 section as part of can_do
        assert "展示和销售药水" in result
