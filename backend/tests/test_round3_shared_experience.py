"""Tests for Phase 3 (SharedExperienceHook) + Phase 4 (_should_teammate_respond)."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.shared_experience import SharedExperienceHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.npc_interaction import _should_teammate_respond
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Context builders
# ------------------------------------------------------------------


def _make_scene_bus() -> tuple[SceneBus, SceneSlice]:
    sl = SceneSlice()
    sl.restore({})
    return SceneBus(sl), sl


def _make_context(
    *,
    members: dict | None = None,
    action_log: list | None = None,
    engine_tags: list[str] | None = None,
    with_party: bool = True,
    with_player: bool = True,
    with_time: bool = False,
) -> tuple[SettlementContext, SceneBus]:
    state = StateContainer()
    scene_bus, scene_sl = _make_scene_bus()
    state.register(scene_sl)

    if with_player:
        pl = PlayerSlice()
        pl.restore({"current_area": "forest_camp"})
        state.register(pl)

    if with_time:
        ts = TimeSlice()
        ts.restore({"day": 3, "accumulated": 0.0, "absolute_tick": 0})
        state.register(ts)

    if with_party:
        ps = PartySlice()
        ps.restore({
            "members": members or {"hero": {}, "wizard": {}},
            "companion_approval": {},
            "shared_experiences": [],
        })
        state.register(ps)

    # Inject ENGINE-tagged entries into the SceneBus if requested
    if engine_tags:
        scene_bus.add_entry({
            "source": "ENGINE",
            "content": "[test]",
            "visibility": "system",
            "tags": engine_tags,
        })

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test"),
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
        action_log=action_log or [],
    )
    return ctx, scene_bus


# ------------------------------------------------------------------
# SharedExperienceHook._detect_experience
# ------------------------------------------------------------------


def test_detect_combat_via_bus_tag() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["COMBAT_END"])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "combat"
    assert "danger" in exp["emotion_tags"]


def test_detect_combat_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "end_combat"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "combat"


def test_detect_quest_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "advance_quest"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "discovery"
    assert "achievement" in exp["emotion_tags"]


def test_detect_quest_via_bus_tag() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["QUEST_PROGRESS"])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "discovery"


def test_detect_rest_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "rest_long"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "rest"
    assert "rest" in exp["emotion_tags"]


def test_detect_rest_via_bus_tag() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["LONG_REST"])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "rest"


def test_detect_exploration_via_bus_tag() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["NAVIGATION"])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "exploration"


def test_detect_dialogue_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "talk"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "dialogue"


def test_detect_dialogue_turn_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "dialogue_turn"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "dialogue"


def test_detect_private_chat_turn_via_action_log() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "private_chat_turn"}])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "dialogue"


def test_detect_betrayal_from_tag() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["BETRAYAL"])
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "betrayal"


def test_detect_returns_none_for_no_relevant_event() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "wait"}])
    exp = hook._detect_experience(ctx)
    assert exp is None


def test_detect_combat_beats_quest_priority() -> None:
    """COMBAT_END should take priority over QUEST_PROGRESS."""
    hook = SharedExperienceHook()
    ctx, _ = _make_context(
        engine_tags=["COMBAT_END", "QUEST_PROGRESS"],
    )
    exp = hook._detect_experience(ctx)
    assert exp is not None
    assert exp["type"] == "combat"


def test_detect_experience_includes_location_from_player() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["COMBAT_END"])
    exp = hook._detect_experience(ctx)
    assert exp["location"] == "forest_camp"


def test_detect_experience_participants_from_party() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["COMBAT_END"], members={"a": {}, "b": {}})
    exp = hook._detect_experience(ctx)
    assert set(exp["participants"]) == {"a", "b"}


# ------------------------------------------------------------------
# SharedExperienceHook.execute
# ------------------------------------------------------------------


def test_execute_calls_record_experience() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(engine_tags=["COMBAT_END"])
    asyncio.run(hook.execute(ctx))
    experiences = ctx.state.party.get_shared_experiences()
    assert len(experiences) == 1
    assert experiences[0]["type"] == "combat"


def test_execute_returns_empty_hook_result_no_party() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(with_party=False)
    result = asyncio.run(hook.execute(ctx))
    assert isinstance(result, HookResult)
    assert not result.commands
    assert not result.sse_events


def test_execute_returns_empty_when_no_members() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(members={})
    result = asyncio.run(hook.execute(ctx))
    assert isinstance(result, HookResult)


def test_execute_returns_empty_when_no_event_detected() -> None:
    hook = SharedExperienceHook()
    ctx, _ = _make_context(action_log=[{"type": "wait"}])
    asyncio.run(hook.execute(ctx))
    experiences = ctx.state.party.get_shared_experiences()
    assert len(experiences) == 0


# ------------------------------------------------------------------
# Phase 4: _should_teammate_respond scene_entries adjustments
# ------------------------------------------------------------------


def _world_with_char(char_id: str, tendency: float) -> WorldInstance:
    from app.game_core.bootstrap import build_default_world
    return build_default_world("test", world_data={
        "characters": {
            char_id: {
                "id": char_id,
                "name": char_id,
                "personality": "",
                "response_tendency": tendency,
            }
        }
    })


def test_combat_end_tag_raises_probability() -> None:
    """COMBAT_END in scene entries should increase tendency by 0.3."""
    world = _world_with_char("warrior", 0.0)
    # tendency=0 → clamped to 0.05 without COMBAT_END
    # tendency=0.3 with COMBAT_END → > 0.05
    entries = [{"source": "ENGINE", "tags": ["COMBAT_END"], "content": ""}]
    # 1000 trials — with tendency=0.3 we should get > 0 positives
    hits = sum(
        _should_teammate_respond(world, "warrior", scene_entries=entries)
        for _ in range(1000)
    )
    assert hits > 0, "COMBAT_END should increase response rate above 0"


def test_trivial_tag_lowers_probability() -> None:
    """TRIVIAL in scene entries should decrease tendency by 0.2."""
    # Use no character registry so tendency defaults to 0.3.
    # With TRIVIAL: 0.3 - 0.2 = 0.1 → ~100/1000 hits.
    # Without TRIVIAL: 0.3 → ~300/1000 hits.
    world = WorldInstance("empty")
    entries = [{"source": "ENGINE", "tags": ["TRIVIAL"], "content": ""}]
    hits_with = sum(
        _should_teammate_respond(world, "warrior", scene_entries=entries)
        for _ in range(1000)
    )
    hits_without = sum(
        _should_teammate_respond(world, "warrior", scene_entries=None)
        for _ in range(1000)
    )
    assert hits_with < hits_without


def test_recent_speaks_lowers_probability() -> None:
    """Each recent speak by the same teammate reduces tendency by 0.15."""
    world = _world_with_char("warrior", 0.3)
    # 2 recent speaks → tendency = 0.3 - 2*0.15 = 0.0 → clamped to 0.05
    entries = [
        {"source": "TEAMMATE:warrior", "tags": [], "content": "I spoke!"},
        {"source": "TEAMMATE:warrior", "tags": [], "content": "I spoke again!"},
    ]
    hits = sum(
        _should_teammate_respond(world, "warrior", scene_entries=entries)
        for _ in range(1000)
    )
    # With tendency~0.05, expect ~50 hits in 1000
    assert hits < 200


def test_no_scene_entries_uses_base_tendency() -> None:
    """No scene_entries → purely based on profile tendency."""
    world = _world_with_char("warrior", 0.3)
    # 1000 trials with tendency=0.3 → ~300 hits expected
    hits = sum(
        _should_teammate_respond(world, "warrior", scene_entries=None)
        for _ in range(1000)
    )
    assert 150 < hits < 500


def test_empty_scene_entries_is_same_as_none() -> None:
    """Empty list of scene_entries → no adjustment."""
    world = _world_with_char("warrior", 0.3)
    hits_none = sum(
        _should_teammate_respond(world, "warrior", scene_entries=None)
        for _ in range(500)
    )
    hits_empty = sum(
        _should_teammate_respond(world, "warrior", scene_entries=[])
        for _ in range(500)
    )
    # Both should be roughly similar (within noise)
    assert abs(hits_none - hits_empty) < 150
