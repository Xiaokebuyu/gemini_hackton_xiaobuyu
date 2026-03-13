"""Tests for PrivateChatTriggerHook — NPC-initiated private chat (Phase B).

All async calls wrapped with asyncio.run() — no pytest-asyncio installed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration import hooks
from app.game_core.orchestration.hooks.private_chat_trigger import (
    COOLDOWN_TICKS,
    BasicPrivateChatTriggerEvaluator,
    NullPrivateChatTriggerEvaluator,
    PrivateChatTriggerHook,
)
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers.world_state import WorldStateHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    PartySlice,
    PlayerSlice,
    RelationSlice,
    TimeSlice,
)
from app.game_core.orchestration.scene_bus import SceneBus


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
    accumulated: float = 0.0,
    include_relations: bool = True,
    include_flags: bool = True,
    include_time: bool = True,
    include_player: bool = True,
    include_area: bool = True,
    include_party: bool = True,
    area_id: str = "test_area",
    current_area: str = "test_area",
    player_location: str | None = "campfire",
    player_room: str | None = None,
    area_npc_locations: dict[str, str | None] | None = None,
    area_npc_rooms: dict[str, str | None] | None = None,
    party_members: dict[str, dict[str, object]] | None = None,
    include_scene_tags: bool = True,
    scene_tags: list[str] | None = None,
    action_log: list[dict[str, object]] | None = None,
    world: WorldInstance | None = None,
) -> SettlementContext:
    effective_scene_tags = ["REST", "LONG_REST"] if scene_tags is None else scene_tags
    effective_action_log = list(action_log or [])
    if not effective_action_log and include_scene_tags and "LONG_REST" in effective_scene_tags:
        effective_action_log = [{"type": "rest_long", "time_cost": 1.0}]
        if accumulated == 0.0:
            accumulated = 1.0

    state = StateContainer()
    scene_slice = SceneSlice()
    entries: list[dict[str, object]] = []
    if include_scene_tags:
        entries.append({
            "source": "ENGINE",
            "content": "[test_hook]",
            "visibility": "system",
            "tags": effective_scene_tags,
        })
    scene_slice.restore({"entries": entries, "state_changes": []})
    state.register(scene_slice)

    if include_player:
        player = PlayerSlice()
        player.restore({
            "current_area": current_area,
            "current_location": player_location,
            "current_room": player_room,
        })
        state.register(player)

    if include_area:
        if area_npc_locations is None:
            if dispositions:
                # Default: NPCs placed at player_location so they are co-located
                area_npc_locations = {npc_id: player_location for npc_id in dispositions}
            else:
                area_npc_locations = {}
        area = AreaSlice()
        area.restore({
            "areas": {
                area_id: {
                    "npc_locations": area_npc_locations,
                    "npc_rooms": area_npc_rooms or {},
                },
            },
        })
        state.register(area)

    if include_party:
        party = PartySlice()
        party.restore({"members": party_members or {}})
        state.register(party)

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
        time_slice.restore({
            "day": current_day,
            "slot": current_slot,
            "accumulated": accumulated,
        })
        state.register(time_slice)

    if world is None:
        world = _world_empty()

    engine = RulesEngine()
    engine.register(WorldStateHandler())

    def _apply_delta(delta) -> None:
        if delta is None:
            return
        state.apply(delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=engine,
        _apply_delta=_apply_delta,
        action_log=effective_action_log,
    )


def _hook(evaluator=None) -> PrivateChatTriggerHook:
    return PrivateChatTriggerHook(evaluator=evaluator)


def _force_random(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(
        hooks.private_chat_trigger.random,
        "random",
        lambda: value,
    )


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

    def test_npc_romance_above_threshold_emits_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NPC with romance=65 → 'npc_wants_to_chat' SSE event."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"merchant_tom": {"romance": 65}},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        evt = result.sse_events[0]
        assert evt.event_type == "npc_wants_to_chat"
        assert evt.payload["npc_id"] == "merchant_tom"
        assert evt.payload["reason"] == "romance"

    def test_npc_trust_above_threshold_emits_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NPC with trust=55 (romance below) → SSE event with reason='trust'."""
        _force_random(monkeypatch, 0.0)
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

    def test_cooldown_active_suppresses_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag cooldown_until > current_tick → event suppressed."""
        # current absolute_tick = (1-1)*24 + 8 = 8; set cooldown_until = 20 (future)
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            flag_data={"private_chat_cooldown_npc_1": 20},
            current_day=1,
            current_slot=8,     # absolute_tick = 8 < 20
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_cooldown_expired_allows_retrigger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Expired cooldown (current_tick >= stored) → event emitted, stale flag cleared."""
        # current absolute_tick = 8; stored = 5 (expired)
        _force_random(monkeypatch, 0.0)
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

    def test_cooldown_set_after_trigger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After trigger, FlagSlice contains cooldown = current_tick + COOLDOWN_TICKS."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            current_day=1,
            current_slot=8,     # absolute_tick = 8
        )
        asyncio.run(_hook().execute(ctx))
        expected = 8 + COOLDOWN_TICKS
        assert ctx.state.flags.get("private_chat_cooldown_npc_1") == expected

    def test_multiple_npcs_multiple_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two NPCs both above threshold (no cooldown) → two SSE events."""
        _force_random(monkeypatch, 0.0)
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

    def test_npc_name_populated_from_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """npc_name in SSE payload comes from characters registry."""
        _force_random(monkeypatch, 0.0)
        world = _world_with_npc("merchant_tom", "Merchant Tom")
        ctx = _make_context(
            dispositions={"merchant_tom": {"romance": 65}},
            world=world,
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        assert result.sse_events[0].payload["npc_name"] == "Merchant Tom"

    def test_npc_name_falls_back_to_id_when_no_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No characters registry → npc_name falls back to npc_id."""
        _force_random(monkeypatch, 0.0)
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

    def test_metadata_contains_triggered_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HookResult.metadata['triggered'] reflects number of emitted events."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={
                "npc_a": {"romance": 70},
                "npc_b": {"trust": 60},
            },
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.metadata["triggered"] == 2

    def test_no_flags_slice_still_triggers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without FlagSlice, cooldown is skipped but trigger still fires."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            include_flags=False,
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1

    def test_not_rest_tick_is_skipped(self) -> None:
        """Hook only runs on REST/LONG_REST engine tags."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            scene_tags=["NAVIGATION"],
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []
        assert result.metadata.get("skipped") == "not_rest_tick"

    def test_player_in_private_location_is_skipped(self) -> None:
        """Private location prefix '_private_' prevents triggers."""
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            player_location="_private_merchant_room",
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []
        assert result.metadata.get("skipped") == "already_in_private_chat"

    def test_npc_scene_filter_only_reachable_npcs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only co-located NPCs (same sub-location as player) and party members are considered.

        npc_local is in the same area but a different sub-location (inn_bench vs campfire)
        so it is NOT co-located and will NOT trigger under the co-location filter.
        npc_remote is not in the area at all, also skipped.
        npc_party is a party member, always treated as co-located.
        """
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={
                "npc_local": {"romance": 70},
                "npc_remote": {"romance": 70},
                "npc_party": {"trust": 70},
            },
            area_npc_locations={"npc_local": "inn_bench"},
            party_members={"npc_party": {"role": "ally"}},
            scene_tags=["REST", "LONG_REST"],
            player_location="campfire",
            area_id="inn",
            current_area="inn",
        )
        result = asyncio.run(_hook().execute(ctx))
        triggered_ids = {event.payload["npc_id"] for event in result.sse_events}
        # npc_local is in area but not co-located (inn_bench != campfire) → filtered out
        # npc_party is always co-located (party member)
        assert triggered_ids == {"npc_party"}

    def test_stage_pre_filter_blocks_non_chatable_stage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """stranger/cold/hostile/nemesis/enemy should bypass evaluator."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_enemy": {"romance": 90}},
            stages={"npc_enemy": "enemy"},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_trigger_roll_blocked_when_high_for_romance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Romance-based roll uses ROMANCE chance and may suppress trigger."""
        _force_random(monkeypatch, 0.99)
        ctx = _make_context(dispositions={"npc_1": {"romance": 90}})
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_trigger_roll_allows_when_low_for_intimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Intimate stage uses intimate chance, allowing deterministic roll pass."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_1": {}},
            stages={"npc_1": "intimate"},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        assert result.sse_events[0].payload["reason"] == "intimate"

    def test_skip_if_player_slice_missing(self) -> None:
        """Missing player slice fails with explicit skip metadata."""
        ctx = _make_context(include_player=False, include_area=False)
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []
        assert result.metadata.get("skipped") == "no_player_slice"

    def test_skip_if_no_reachable_npcs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No matching area/party NPCs means no trigger even with data."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_remote": {"trust": 70}},
            area_npc_locations={"npc_other": "barn"},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []

    def test_mid_rest_slot_is_skipped_until_final_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_1": {"romance": 70}},
            accumulated=4.0,
            action_log=[{"type": "rest_long", "time_cost": 1.0}],
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []
        assert result.metadata.get("skipped") == "not_final_rest_slot"

    def test_different_room_does_not_count_as_colocated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Player in a room only receives triggers from NPCs in the same room."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_roommate": {"romance": 70}},
            player_location="guild_hall",
            player_room="office",
            area_npc_locations={"npc_roommate": "guild_hall"},
            area_npc_rooms={"npc_roommate": "counter"},
        )
        result = asyncio.run(_hook().execute(ctx))
        assert result.sse_events == []


# ------------------------------------------------------------------
# A7: Co-location tests
# ------------------------------------------------------------------


class TestA7CoLocationFilter:
    """Tests for co-location filtering added in A7."""

    def test_colocated_npc_event_has_colocated_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NPC in same sub-location as player → colocated=True in SSE payload."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_same_loc": {"romance": 70}},
            area_npc_locations={"npc_same_loc": "campfire"},  # same as player_location
            player_location="campfire",
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        payload = result.sse_events[0].payload
        assert payload["colocated"] is True
        assert "npc_location" not in payload

    def test_noncolocated_npc_does_not_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NPC in different sub-location → no event (co-location filter blocks trigger).

        Non-colocated NPCs are excluded from trigger candidates to prevent the UX problem
        of a player receiving a chat request from an NPC they can't reach (which would
        result in a 'npc_not_present' error when trying to respond).
        """
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_elsewhere": {"romance": 70}},
            area_npc_locations={"npc_elsewhere": "inn_bench"},  # different from player_location
            player_location="campfire",
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 0

    def test_party_member_always_colocated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Party members always count as co-located regardless of explicit location."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"party_npc": {"trust": 60}},
            area_npc_locations={},  # not explicitly in area locations
            party_members={"party_npc": {"role": "ally"}},
            player_location="campfire",
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        payload = result.sse_events[0].payload
        assert payload["colocated"] is True

    def test_mixed_colocated_and_noncolocated_npcs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two NPCs: one co-located, one not — only co-located NPC triggers."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={
                "npc_here": {"romance": 70},
                "npc_far": {"romance": 70},
            },
            area_npc_locations={
                "npc_here": "campfire",   # same as player_location → triggers
                "npc_far": "stable",      # different from player_location → filtered out
            },
            player_location="campfire",
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        payload = result.sse_events[0].payload
        assert payload["npc_id"] == "npc_here"
        assert payload["colocated"] is True

    def test_npc_with_none_location_and_player_at_area_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both NPC location and player location are None (area root) → colocated."""
        _force_random(monkeypatch, 0.0)
        ctx = _make_context(
            dispositions={"npc_root": {"romance": 70}},
            area_npc_locations={"npc_root": None},  # area main scene
            player_location=None,                    # also at area main scene
        )
        result = asyncio.run(_hook().execute(ctx))
        assert len(result.sse_events) == 1
        payload = result.sse_events[0].payload
        assert payload["colocated"] is True
