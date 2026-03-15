"""Track A: Planner full-chain tests (P23 二次审计).

Covers:
- A-1: create_quest auto-publishes bulletin (W1-3)
- A-1: publish_bulletin rejects orphan quest_id (W1-3)
- A-2: directive validation failure emits planner_directive_rejected SSE (W1-4)
- A-3: planner context includes previous_directive_results (W2-1)
- A-4: planner context NPC summaries include approval/trust (W2-2)
- A-5: quest_id vs milestone_id collision → error (W5-5)
- A-6: QuestExpiryHook retires past-expiry quests and emits SSE (W5-2)
- A-7: FALLBACK_INTERVAL = 4 (W6-1)
- A-7: "time" in _TRIGGER_SLICES (W6-1)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.hooks.quest_expiry import QuestExpiryHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.subsystem import PlannerDispatcher, SubSystemResult
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.handlers.planner import PlannerQuestHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _RecordingPlanner:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    async def plan(self, context: dict[str, Any]) -> Any:
        self.calls.append(dict(context))
        return self.decision


class _StaticBlackboard:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def plan(self, context: dict[str, Any]) -> NarrativePlannerDecision:
        self.calls.append(dict(context))
        return NarrativePlannerDecision(
            directives=[],
            metadata={"status": "noop", "reason": "stable"},
        )


class _EmptyResultSubSystem:
    @property
    def name(self) -> str:
        return "empty_result"

    @property
    def handles(self) -> frozenset[str]:
        return frozenset()

    def accepts_event(self, event: Any) -> bool:
        return event.kind == "tick_settlement"

    async def evaluate(self, event: Any, context: Any) -> SubSystemResult:
        return SubSystemResult(metadata={"status": "noop", "subsystem": self.name})

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool:
        return False


def _make_simple_state(*, with_relations: bool = False) -> StateContainer:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {"ms_1": {"state": "AVAILABLE"}},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1"})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    if with_relations:
        relations = RelationSlice()
        relations.restore({})
        state.register(relations)

    return state


def _make_context(state: StateContainer, world: WorldInstance | None = None) -> SettlementContext:
    if world is None:
        world = WorldInstance("test_world")
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    change_log: list[StateChange] = [
        StateChange("player", "set", "current_area", "forest"),
    ]

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def _make_hook_with_dispatcher(blackboard: Any = None) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook with all default sub-systems."""
    if blackboard is None:
        blackboard = _StaticBlackboard()
    hook = NarrativePlannerHook(blackboard=blackboard)
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=hook._pending_sse))
    hook._dispatcher = dispatcher
    return hook


# ---------------------------------------------------------------------------
# A-1: create_quest auto-publishes bulletin (W1-3)
# ---------------------------------------------------------------------------

def test_a1_create_quest_auto_publishes_bulletin() -> None:
    """create_quest with status=available and delivery_method=board auto-appends bulletin."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_auto",
                "title": "Auto Quest",
                "summary": "Should appear on board",
                "status": "available",
                "delivery_method": "board",
                "area_id": "forest",
                "board_id": "board",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed
    assert result.delta is not None
    state.apply(result.delta)

    # The auto-publish StateChange should add to board_bulletins
    bulletin_changes = [
        c for c in result.delta.changes
        if c.slice == "areas" and "board_bulletins" in c.path
    ]
    assert len(bulletin_changes) >= 1
    area_state = state.areas.areas.get("forest")
    assert area_state is not None
    # The board should have at least one bulletin for dq_auto
    all_bulletins = []
    for entries in area_state.board_bulletins.values():
        all_bulletins.extend(entries)
    quest_bulletins = [b for b in all_bulletins if b.get("quest_id") == "dq_auto"]
    assert len(quest_bulletins) >= 1


def test_a1_create_quest_no_auto_publish_when_not_board_delivery() -> None:
    """create_quest with delivery_method=receptionist does NOT auto-publish bulletin."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_receptionist",
                "title": "Receptionist Quest",
                "summary": "Not on board",
                "status": "available",
                "delivery_method": "receptionist",
                "area_id": "forest",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed
    bulletin_changes = [
        c for c in result.delta.changes
        if c.slice == "areas" and "board_bulletins" in c.path
    ]
    assert len(bulletin_changes) == 0


def test_a1_publish_bulletin_rejects_nonexistent_quest_id() -> None:
    """publish_bulletin with quest_id pointing to non-existent quest is rejected (S3-03)."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.areas.restore({"areas": {"forest": {}}})
    handler = PlannerQuestHandler()

    result = handler.validate(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "board",
                "area_id": "forest",
                "title": "Orphan Bulletin",
                "content": "No quest",
                "metadata": {"quest_id": "dq_nonexistent"},
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert not result.ok
    assert "dq_nonexistent" in (result.reason or "")


def test_a1_publish_bulletin_accepts_existing_quest_id() -> None:
    """publish_bulletin with quest_id that exists in dynamic_quests succeeds."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {},
        "dynamic_quests": {"dq_real": {"status": "available", "title": "Real"}},
        "chapter_completion": {},
    })
    state.areas.restore({"areas": {"forest": {}}})
    handler = PlannerQuestHandler()

    result = handler.validate(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "board",
                "area_id": "forest",
                "title": "Real Bulletin",
                "content": "Real",
                "metadata": {"quest_id": "dq_real"},
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.ok


def test_a1_publish_bulletin_rejects_nonexistent_root_quest_id() -> None:
    """publish_bulletin with root quest_id still rejects orphan quest ids."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.areas.restore({"areas": {"forest": {}}})
    handler = PlannerQuestHandler()

    result = handler.validate(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "board",
                "area_id": "forest",
                "quest_id": "dq_nonexistent",
                "title": "Orphan Bulletin",
                "content": "No quest",
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert not result.ok
    assert "dq_nonexistent" in (result.reason or "")


def test_a1_publish_bulletin_preserves_root_quest_id_in_board_entry() -> None:
    """publish_bulletin with root quest_id stores it in the board entry and metadata."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {},
        "dynamic_quests": {"dq_real": {"status": "available", "title": "Real"}},
        "chapter_completion": {},
    })
    state.areas.restore({"areas": {"forest": {}}})
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "board",
                "area_id": "forest",
                "quest_id": "dq_real",
                "title": "Real Bulletin",
                "content": "Real",
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed
    assert result.delta is not None
    state.apply(result.delta)
    entries = state.areas.get_board_bulletins("forest", "board")
    assert entries[-1]["quest_id"] == "dq_real"
    assert result.metadata["quest_id"] == "dq_real"


def test_a1_publish_bulletin_no_quest_id_is_ok() -> None:
    """publish_bulletin without quest_id in metadata is allowed."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.areas.restore({"areas": {"forest": {}}})
    handler = PlannerQuestHandler()

    result = handler.validate(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "board",
                "area_id": "forest",
                "title": "General Bulletin",
                "content": "Info",
                "metadata": {},
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.ok


# ---------------------------------------------------------------------------
# A-2: directive validation failure emits planner_directive_rejected SSE (W1-4)
# ---------------------------------------------------------------------------

def test_a2_invalid_contract_directive_emits_planner_directive_rejected_sse() -> None:
    """An invalid_contract directive emits planner_directive_rejected SSE (A-2/S1-02)."""
    # spawn_quest_npc without area_id is invalid_contract (missing_area_id)
    planner = _RecordingPlanner(NarrativePlannerDecision(
        directives=[
            # This is invalid_contract: spawn_quest_npc requires area_id
            {"kind": "spawn_quest_npc", "payload": {"npc_id": "npc_test", "role": "quest_giver"}},
        ],
    ))
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)

    blackboard = _RecordingPlanner(NarrativePlannerDecision(directives=[], metadata={}))
    hook = _make_hook_with_dispatcher(blackboard=blackboard)
    # Inject an invalid directive directly into _apply_directive_batch via fake planner
    # We test by calling _apply_directive_batch directly
    summary = hook._apply_directive_batch(
        [{"kind": "spawn_quest_npc", "payload": {"npc_id": "npc_x", "role": "guard"}}],
        context,
        current_tick=10,
        allowed_directives={"spawn_quest_npc"},
        source="blackboard",
        subsystem_name="test",
        round_index=0,
    )

    assert summary["skipped_invalid_count"] == 1
    rejected_sse = [e for e in hook._pending_sse if e.event_type == "planner_directive_rejected"]
    assert len(rejected_sse) >= 1
    assert rejected_sse[0].payload["kind"] == "spawn_quest_npc"
    assert rejected_sse[0].payload["status"] == "invalid_contract"


def test_a2_legacy_direct_npc_payload_is_normalized_and_applied() -> None:
    """Legacy root-level direct_npc fields should be normalized instead of rejected."""
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = _make_hook_with_dispatcher()

    summary = hook._apply_directive_batch(
        [
            {
                "kind": "direct_npc",
                "payload": {
                    "npc_id": "cow_girl",
                    "behavior": "welcoming_with_relief",
                    "topic": "new_bond_and_safety",
                    "goal": "solidify_acquaintance_bond",
                    "interactable": True,
                },
            },
        ],
        context,
        current_tick=10,
        allowed_directives={"direct_npc"},
        source="subsystem",
        subsystem_name="npc_director",
        round_index=0,
    )

    assert summary["applied_count"] == 1
    assert summary["skipped_invalid_count"] == 0
    stored = state.narrative_plan.npc_directives[-1]
    assert stored["npc_id"] == "cow_girl"
    assert stored["directive"]["kind"] == "talk"
    assert stored["directive"]["behavior"] == "welcoming_with_relief"
    assert not any(e.event_type == "planner_directive_rejected" for e in hook._pending_sse)


def test_a2_legacy_fill_area_locations_are_expanded_and_applied() -> None:
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = _make_hook_with_dispatcher()

    summary = hook._apply_directive_batch(
        [
            {
                "kind": "fill_area",
                "payload": {
                    "area_id": "forest",
                    "locations": [
                        {
                            "id": "forest_ruin",
                            "name": "Forest Ruin",
                            "description": "Broken stones under ivy.",
                            "traits": ["ruins", "quiet"],
                        },
                        {
                            "id": "forest_watch",
                            "name": "Old Watch Post",
                            "description": "A collapsed lookout platform.",
                        },
                    ],
                },
            },
        ],
        context,
        current_tick=10,
        allowed_directives={"fill_area"},
        source="subsystem",
        subsystem_name="world_builder",
        round_index=0,
    )

    assert summary["requested_count"] == 2
    assert summary["applied_count"] == 2
    created = state.areas.areas["forest"].temporary_sub_areas
    assert [entry["id"] for entry in created] == ["forest_ruin", "forest_watch"]
    assert created[0]["label"] == "Forest Ruin"
    assert created[0]["tags"] == ["ruins", "quiet"]
    assert not any(e.event_type == "planner_directive_rejected" for e in hook._pending_sse)


def test_a2_legacy_environmental_elements_are_expanded_and_applied() -> None:
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = _make_hook_with_dispatcher()

    summary = hook._apply_directive_batch(
        [
            {
                "kind": "plant_environmental",
                "payload": {
                    "area_id": "forest",
                    "elements": [
                        {
                            "id": "mist",
                            "name": "Morning Mist",
                            "description": "晨雾地带",
                            "persistence": 3,
                        },
                        {
                            "id": "birdsong",
                            "description": "鸟鸣林间",
                        },
                    ],
                },
            },
        ],
        context,
        current_tick=10,
        allowed_directives={"plant_environmental"},
        source="subsystem",
        subsystem_name="world_builder",
        round_index=0,
    )

    assert summary["requested_count"] == 2
    assert summary["applied_count"] == 2
    created = state.areas.areas["forest"].temporary_sub_areas
    assert [entry["id"] for entry in created] == ["mist", "birdsong"]
    assert created[0]["description"] == "晨雾地带"
    assert created[0]["expiry"] == 3
    assert created[1]["description"] == "鸟鸣林间"
    assert not any(e.event_type == "planner_directive_rejected" for e in hook._pending_sse)


def test_a2_unsupported_directive_does_not_emit_sse() -> None:
    """An unsupported directive does NOT emit planner_directive_rejected SSE (no noise)."""
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = _make_hook_with_dispatcher()

    summary = hook._apply_directive_batch(
        [{"kind": "unknown_bogus_kind", "payload": {}}],
        context,
        current_tick=10,
        allowed_directives={"unknown_bogus_kind"},
        source="blackboard",
        subsystem_name="test",
        round_index=0,
    )

    assert summary["skipped_unsupported_count"] == 1
    # No SSE for unsupported
    assert not any(e.event_type == "planner_directive_rejected" for e in hook._pending_sse)


# ---------------------------------------------------------------------------
# A-3: planner context includes previous_directive_results (W2-1)
# ---------------------------------------------------------------------------

def test_a3_build_planner_context_includes_previous_directive_results() -> None:
    """_build_planner_context injects previous_directive_results from last trace."""
    state = _make_simple_state()
    # Simulate a previous run trace with directive_audit entries
    state.narrative_plan.restore({
        "current_chapter": "ch1",
        "last_planner_replay_trace": {
            "directive_audit": [
                {"kind": "create_quest", "status": "applied", "reason_code": None},
                {"kind": "direct_npc", "status": "subsystem_rejected", "reason_code": "npc_not_found"},
                {"kind": "publish_bulletin", "status": "invalid_contract", "reason_code": "missing_board_id"},
            ],
        },
    })
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = NarrativePlannerHook()

    planner_ctx = hook._build_planner_context(context, current_tick=5)

    prev_results = planner_ctx.get("previous_directive_results", [])
    assert isinstance(prev_results, list)
    assert len(prev_results) == 3
    assert prev_results[0] == {"kind": "create_quest", "status": "applied", "reason_code": None}
    assert prev_results[1] == {"kind": "direct_npc", "status": "subsystem_rejected", "reason_code": "npc_not_found"}
    assert prev_results[2] == {"kind": "publish_bulletin", "status": "invalid_contract", "reason_code": "missing_board_id"}


def test_a3_build_planner_context_previous_results_empty_on_first_run() -> None:
    """previous_directive_results is empty when last_planner_replay_trace is empty."""
    state = _make_simple_state()
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = NarrativePlannerHook()

    planner_ctx = hook._build_planner_context(context, current_tick=1)
    assert planner_ctx["previous_directive_results"] == []


def test_a3_build_planner_context_previous_results_truncated_to_10() -> None:
    """previous_directive_results is truncated to last 10 entries."""
    state = _make_simple_state()
    state.narrative_plan.restore({
        "current_chapter": "ch1",
        "last_planner_replay_trace": {
            "directive_audit": [
                {"kind": "create_quest", "status": "applied", "reason_code": None}
                for _ in range(15)
            ],
        },
    })
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = NarrativePlannerHook()

    planner_ctx = hook._build_planner_context(context, current_tick=1)
    assert len(planner_ctx["previous_directive_results"]) == 10


# ---------------------------------------------------------------------------
# A-4: planner context NPC summaries include approval/trust (W2-2)
# ---------------------------------------------------------------------------

def test_a4_planner_context_includes_area_npc_summaries() -> None:
    """_build_planner_context includes area_npc_summaries with approval/trust (A-4/S1-06)."""
    from app.game_core.state.slices.relations import RelationSlice

    # Build state with NPC in area and disposition values
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "ch1"})
    state.register(narrative_plan)

    # Area with NPC in npc_locations
    areas = AreaSlice()
    areas.restore({
        "areas": {
            "forest": {
                "npc_locations": {
                    "npc_guard": {"sub_location": "gate", "x": 0, "y": 0}
                }
            }
        }
    })
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    relations = RelationSlice()
    relations.restore({
        "npc_dispositions": {"npc_guard": {"approval": 25, "trust": 40}},
    })
    state.register(relations)

    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = NarrativePlannerHook()

    planner_ctx = hook._build_planner_context(context, current_tick=1)

    summaries = planner_ctx.get("area_npc_summaries", [])
    assert len(summaries) >= 1
    guard_entry = next((s for s in summaries if s.get("id") == "npc_guard"), None)
    assert guard_entry is not None
    assert guard_entry.get("approval") == 25
    assert guard_entry.get("trust") == 40


def test_a4_planner_context_quest_includes_objectives_and_rewards() -> None:
    """dynamic_quests in planner context includes objectives and rewards fields (A-4)."""
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {},
        "dynamic_quests": {
            "dq_rich": {
                "status": "active",
                "title": "Rich Quest",
                "summary": "Full data",
                "objectives": [{"type": "kill", "target": "goblin", "count": 5}],
                "rewards": {"gold": 100, "xp": 200},
                "area_id": "forest",
                "giver_npc": "npc_receptionist",
            }
        },
        "chapter_completion": {},
    })
    world = WorldInstance("test_world")
    context = _make_context(state, world)
    hook = NarrativePlannerHook()

    planner_ctx = hook._build_planner_context(context, current_tick=1)

    quest_data = planner_ctx["quests"]["dynamic_quests"].get("dq_rich")
    assert quest_data is not None
    assert quest_data["objectives"] == [{"type": "kill", "target": "goblin", "count": 5}]
    assert quest_data["rewards"] == {"gold": 100, "xp": 200}
    assert quest_data["area_id"] == "forest"
    assert quest_data["giver_npc"] == "npc_receptionist"


# ---------------------------------------------------------------------------
# A-5: quest_id vs milestone_id collision → error (W5-5)
# ---------------------------------------------------------------------------

def test_a5_create_quest_rejects_quest_id_that_collides_with_milestone_id() -> None:
    """create_quest rejects a quest_id that matches an existing milestone_id (S3-06)."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {"ms_main": {"state": "AVAILABLE"}},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "ms_main",  # collides with milestone_id
                "title": "Collision Quest",
                "summary": "Should be rejected",
                "status": "available",
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert not result.executed
    assert any("ms_main" in e for e in result.errors)


def test_a5_create_quest_succeeds_with_unique_quest_id() -> None:
    """create_quest with unique quest_id not in milestones succeeds."""
    world = WorldInstance("test_world")
    state = _make_simple_state()
    state.quests.restore({
        "milestone_states": {"ms_main": {"state": "AVAILABLE"}},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_unique",  # does not collide
                "title": "Unique Quest",
                "summary": "Fine",
                "status": "available",
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed


# ---------------------------------------------------------------------------
# A-6: QuestExpiryHook (W5-2)
# ---------------------------------------------------------------------------

def _make_quest_expiry_context(*, current_tick: int = 10) -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    # absolute_tick = (day - 1) * 24 + slot
    # So: day = current_tick // 24 + 1, slot = current_tick % 24
    day = current_tick // 24 + 1
    slot = current_tick % 24
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest"})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {},
        "dynamic_quests": {
            "dq_expired": {
                "quest_id": "dq_expired",
                "status": "active",
                "title": "Expired Quest",
                "summary": "Past its expiry",
                "created_at_tick": current_tick - 6,
                "expiry_ticks": 5,
            },
            "dq_future": {
                "quest_id": "dq_future",
                "status": "active",
                "title": "Future Quest",
                "summary": "Not yet expired",
                "created_at_tick": current_tick - 2,
                "expiry_ticks": 5,
            },
            "dq_no_expiry": {
                "quest_id": "dq_no_expiry",
                "status": "active",
                "title": "Endless Quest",
                "summary": "No expiry",
            },
        },
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


def test_a6_quest_expiry_hook_retires_past_expiry_quests() -> None:
    """QuestExpiryHook retires quests past their expiry_ticks (A-6/S3-02)."""
    context = _make_quest_expiry_context(current_tick=10)
    hook = QuestExpiryHook()

    result = asyncio.run(hook.execute(context))

    # dq_expired should be retired
    assert result.metadata["expired_count"] == 1
    assert "dq_expired" in result.metadata["expired_quest_ids"]

    # Verify state mutation
    expired_quest = context.state.quests.get_dynamic_quest("dq_expired")
    assert expired_quest is not None
    assert expired_quest["status"] == "retired"

    # dq_future and dq_no_expiry should be untouched
    future = context.state.quests.get_dynamic_quest("dq_future")
    assert future is not None
    assert future["status"] == "active"

    no_exp = context.state.quests.get_dynamic_quest("dq_no_expiry")
    assert no_exp is not None
    assert no_exp["status"] == "active"


def test_a6_quest_expiry_hook_emits_quest_expired_sse() -> None:
    """QuestExpiryHook emits quest_expired SSE for each retired quest."""
    context = _make_quest_expiry_context(current_tick=10)
    hook = QuestExpiryHook()

    result = asyncio.run(hook.execute(context))

    expired_events = [e for e in result.sse_events if e.event_type == "quest_expired"]
    assert len(expired_events) == 1
    assert expired_events[0].payload["quest_id"] == "dq_expired"


def test_a6_quest_expiry_hook_skips_completed_quests() -> None:
    """QuestExpiryHook does not retire completed/retired quests even if past expiry."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 0, "slot": 10})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest"})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {},
        "dynamic_quests": {
            "dq_completed_old": {
                "status": "completed",
                "title": "Old Completed",
                "created_at_tick": 0,
                "expiry_ticks": 5,
            },
            "dq_retired_old": {
                "status": "retired",
                "title": "Old Retired",
                "created_at_tick": 0,
                "expiry_ticks": 3,
            },
        },
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)
    events = EventSlice()
    events.restore({})
    state.register(events)
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    context = SettlementContext(
        change_log=change_log,
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )

    hook = QuestExpiryHook()
    result = asyncio.run(hook.execute(context))

    assert result.metadata["expired_count"] == 0
    assert result.sse_events == []


def test_a6_quest_expiry_hook_registered_in_defaults() -> None:
    """QuestExpiryHook is registered in DEFAULT_SETTLEMENT_HOOK_TYPES."""
    from app.game_core.orchestration.defaults import DEFAULT_SETTLEMENT_HOOK_TYPES
    hook_names = {h.HOOK_NAME for h in DEFAULT_SETTLEMENT_HOOK_TYPES}
    assert "quest_expiry" in hook_names


def test_a6_empty_subsystem_result_does_not_increment_accepted_event_count() -> None:
    """Phase 3d: unified execute() has no replay rounds; trace reflects single-shot flow."""
    state = _make_simple_state()
    hook = NarrativePlannerHook(blackboard=_StaticBlackboard())
    dispatcher = PlannerDispatcher()
    dispatcher.register(_EmptyResultSubSystem())
    hook._dispatcher = dispatcher

    context = _make_context(state)
    asyncio.run(hook.execute(context))

    trace = context.state.narrative_plan.last_planner_replay_trace
    # Phase 3d: dispatcher is NOT called in execute(); unified single-shot trace
    assert trace["round_count"] == 0
    assert trace["stop_reason"] == "unified"
    assert trace["rounds"] == []


# ---------------------------------------------------------------------------
# A-7: FALLBACK_INTERVAL = 4, "time" in _TRIGGER_SLICES (W6-1)
# ---------------------------------------------------------------------------

def test_a7_fallback_interval_is_1() -> None:
    """NarrativePlannerHook.FALLBACK_INTERVAL should be 1 (P32-1: every settlement tick)."""
    assert NarrativePlannerHook.FALLBACK_INTERVAL == 1


def test_a7_time_in_trigger_slices() -> None:
    """'time' should be in NarrativePlannerHook._TRIGGER_SLICES (A-7)."""
    assert "time" in NarrativePlannerHook._TRIGGER_SLICES


def test_a7_time_slice_change_triggers_planner() -> None:
    """A change on the 'time' slice should trigger planner evaluation (not be on cooldown)."""
    state = _make_simple_state()
    # Set last_run_tick to recent (within cooldown range)
    state.narrative_plan.restore({"current_chapter": "ch1", "last_run_tick": 8})
    world = WorldInstance("test_world")
    blackboard = _StaticBlackboard()
    hook = _make_hook_with_dispatcher(blackboard=blackboard)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    # current_tick = day=0*12 + slot=9 = 9, ticks_since_last_run = 9-8 = 1 < FALLBACK_INTERVAL=4
    # Without time in trigger slices, cooldown would suppress. With it: triggers.
    change_log: list[StateChange] = [
        StateChange("time", "set", "slot", 9),  # time slice change → should trigger
    ]

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    context = SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )

    result = asyncio.run(hook.execute(context))

    # Should have evaluated (time change triggered despite cooldown)
    assert result.metadata.get("evaluated") is True
    assert result.metadata.get("reason") == "trigger"
    assert len(blackboard.calls) >= 1
