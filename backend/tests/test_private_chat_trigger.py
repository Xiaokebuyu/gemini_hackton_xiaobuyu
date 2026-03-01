"""Tests for PrivateChatTriggerHook — NPC-initiated private chat (Phase B).

All async calls wrapped with asyncio.run() — no pytest-asyncio installed.
"""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration.hooks.private_chat_trigger import (
    COOLDOWN_TICKS,
    BasicPrivateChatTriggerEvaluator,
    NullPrivateChatTriggerEvaluator,
    PrivateChatTriggerHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import FlagSlice, RelationSlice, TimeSlice
from app.game_core.state.slices import SceneSlice


# ------------------------------------------------------------------
# Fixtures / helpers
# ------------------------------------------------------------------


def _world_with_npc(npc_id: str = "merchant_tom", name: str = "Merchant Tom") -> WorldInstance:
    world = WorldInstance("test_world")
    registry = CharacterRegistry()
    registry.load({npc_id: {"id": npc_id, "name": name, "personality": "A merchant."}})
    world.register(registry)
    return world


def _world_empty() -> WorldInstance:
    return WorldInstance("empty_world")


def _make_context(
    *,
    dispositions: dict[str, dict[str, int]] | None = None,
    stages: dict[str, str] | None = None,
    flag_data: dict[str, object] | None = None,
    current_day: int = 1,
    current_slot: int = 8,
    include_relations: bool = True,
    include_flags: bool = True,
    include_time: bool = True,
    world: WorldInstance | None = None,
) -> SettlementContext:
    state = StateContainer()
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    if include_relations:
        rel = RelationSlice()
        rel.restore({
            "npc_dispositions": dispositions or {},
            "relationship_stages": stages or {},
        })
        state.register(rel)

    if include_flags:
        flags = FlagSlice()
        flags.restore({"flags": flag_data or {}})
        state.register(flags)

    if include_time:
        time_slice = TimeSlice()
        time_slice.restore({"day": current_day, "slot": current_slot})
        state.register(time_slice)

    if world is None:
        world = _world_empty()

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


def _hook(evaluator=None) -> PrivateChatTriggerHook:
    return PrivateChatTriggerHook(evaluator=evaluator)


# ------------------------------------------------------------------
# TestBasicPrivateChatTriggerEvaluator
# ------------------------------------------------------------------


class TestBasicPrivateChatTriggerEvaluator:
    def _eval(self) -> BasicPrivateChatTriggerEvaluator:
        return BasicPrivateChatTriggerEvaluator()

    def test_triggers_on_romance_threshold(self) -> None:
        """romance == 60 satisfies threshold → (True, 'romance')."""
        triggered, reason = self._eval().should_initiate(
            "npc_1", {"romance": 60, "trust": 10}, "acquaintance",
        )
        assert triggered is True
        assert reason == "romance"

    def test_triggers_on_trust_threshold(self) -> None:
        """trust == 50 satisfies threshold (romance below) → (True, 'trust')."""
        triggered, reason = self._eval().should_initiate(
            "npc_1", {"romance": 10, "trust": 50}, "acquaintance",
        )
        assert triggered is True
        assert reason == "trust"

    def test_triggers_on_intimate_stage(self) -> None:
        """stage=='intimate' triggers even with low numeric values."""
        triggered, reason = self._eval().should_initiate(
            "npc_1", {"romance": 5, "trust": 5}, "intimate",
        )
        assert triggered is True
        assert reason == "intimate"

    def test_no_trigger_below_all_thresholds(self) -> None:
        """All below threshold → (False, '')."""
        triggered, reason = self._eval().should_initiate(
            "npc_1", {"romance": 10, "trust": 10}, "stranger",
        )
        assert triggered is False
        assert reason == ""

    def test_romance_takes_priority_over_trust(self) -> None:
        """Both thresholds met → romance reason wins (checked first)."""
        triggered, reason = self._eval().should_initiate(
            "npc_1", {"romance": 65, "trust": 55}, "acquaintance",
        )
        assert triggered is True
        assert reason == "romance"


# ------------------------------------------------------------------
# TestNullEvaluator
# ------------------------------------------------------------------


class TestNullEvaluator:
    def test_null_evaluator_never_triggers(self) -> None:
        """NullPrivateChatTriggerEvaluator always returns (False, '')."""
        ev = NullPrivateChatTriggerEvaluator()
        for dispositions, stage in [
            ({"romance": 100, "trust": 100}, "intimate"),
            ({}, "stranger"),
        ]:
            triggered, reason = ev.should_initiate("npc_1", dispositions, stage)
            assert triggered is False
            assert reason == ""


# ------------------------------------------------------------------
# TestPrivateChatTriggerHook
# ------------------------------------------------------------------


class TestPrivateChatTriggerHook:
    def test_no_relations_slice_returns_skipped(self) -> None:
        """Context without RelationSlice → empty sse_events, 'skipped' metadata."""
        ctx = _make_context(include_relations=False)
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []
        assert result.metadata.get("skipped") == "no_relations"

    def test_npc_romance_above_threshold_emits_event(self) -> None:
        """NPC with romance=65 → 'npc_wants_to_chat' SSE event."""
        ctx = _make_context(
            dispositions={"merchant_tom": {"romance": 65}},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        evt = result.sse_events[0]
        assert evt.event_type == "npc_wants_to_chat"
        assert evt.payload["npc_id"] == "merchant_tom"
        assert evt.payload["reason"] == "romance"

    def test_npc_trust_above_threshold_emits_event(self) -> None:
        """NPC with trust=55 (romance below) → SSE event with reason='trust'."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 10, "trust": 55}},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        assert result.sse_events[0].payload["reason"] == "trust"

    def test_npc_below_threshold_no_event(self) -> None:
        """NPC with romance=20, trust=30, stage='stranger' → no events."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 20, "trust": 30}},
            stages={"npc_1": "stranger"},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_cooldown_active_suppresses_event(self) -> None:
        """Flag cooldown_until > current_tick → event suppressed."""
        # current absolute_tick = (1-1)*24 + 8 = 8; set cooldown_until = 20 (future)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            flag_data={"private_chat_cooldown_npc_1": 20},
            current_day=1,
            current_slot=8,     # absolute_tick = 8 < 20
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_cooldown_expired_allows_retrigger(self) -> None:
        """Expired cooldown (current_tick >= stored) → event emitted, stale flag cleared."""
        # current absolute_tick = 8; stored = 5 (expired)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            flag_data={"private_chat_cooldown_npc_1": 5},
            current_day=1,
            current_slot=8,     # absolute_tick = 8 >= 5
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        # Stale flag removed, new cooldown set
        new_val = ctx.state.flags.get("private_chat_cooldown_npc_1", None)
        assert new_val is not None
        assert new_val > 8  # current_tick + COOLDOWN_TICKS

    def test_cooldown_set_after_trigger(self) -> None:
        """After trigger, FlagSlice contains cooldown = current_tick + COOLDOWN_TICKS."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            current_day=1,
            current_slot=8,     # absolute_tick = 8
        )
        asyncio.run(_hook().execute(ctx))
        expected = 8 + COOLDOWN_TICKS
        assert ctx.state.flags.get("private_chat_cooldown_npc_1") == expected

    def test_multiple_npcs_multiple_events(self) -> None:
        """Two NPCs both above threshold (no cooldown) → two SSE events."""
        ctx = _make_context(
            dispositions={
                "npc_a": {"romance": 70},
                "npc_b": {"trust": 60},
            },
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 2
        triggered_ids = {e.payload["npc_id"] for e in result.sse_events}
        assert triggered_ids == {"npc_a", "npc_b"}

    def test_npc_name_populated_from_registry(self) -> None:
        """npc_name in SSE payload comes from characters registry."""
        world = _world_with_npc("merchant_tom", "Merchant Tom")
        ctx = _make_context(
            dispositions={"merchant_tom": {"romance": 65}},
            world=world,
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        assert result.sse_events[0].payload["npc_name"] == "Merchant Tom"

    def test_npc_name_falls_back_to_id_when_no_registry(self) -> None:
        """No characters registry → npc_name falls back to npc_id."""
        ctx = _make_context(
            dispositions={"unknown_npc": {"romance": 70}},
            world=_world_empty(),
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        assert result.sse_events[0].payload["npc_name"] == "unknown_npc"

    def test_hook_with_null_evaluator_emits_nothing(self) -> None:
        """NullPrivateChatTriggerEvaluator → no events even with high dispositions."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 100, "trust": 100}},
            stages={"npc_1": "intimate"},
        )
        result = asyncio.run(_hook(NullPrivateChatTriggerEvaluator()).execute(ctx))
        assert result.sse_events == []

    def test_metadata_contains_triggered_count(self) -> None:
        """HookResult.metadata['triggered'] reflects number of emitted events."""
        ctx = _make_context(
            dispositions={
                "npc_a": {"romance": 70},
                "npc_b": {"trust": 60},
            },
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.metadata["triggered"] == 2

    def test_no_flags_slice_still_triggers(self) -> None:
        """Without FlagSlice, cooldown is skipped but trigger still fires."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            include_flags=False,
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
