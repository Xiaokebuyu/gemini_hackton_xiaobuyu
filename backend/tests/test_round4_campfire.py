"""Tests for Phase 5: CampfireHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.campfire import (
    CampfireHook,
    _generate_campfire_line,
    _has_major_experience_today,
    _is_long_rest,
    _select_memory,
    _teammate_eligible,
)
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice, TimeSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.narrative.companion_runtime import TickRecord


class _FakeCompanionInstance:
    def __init__(self, records: list[TickRecord]) -> None:
        self._records = records

    def get_recent_events(self, n: int = 10) -> list[TickRecord]:
        return list(self._records[-n:])


class _FakeCompanionManager:
    def __init__(self, member_id: str, instance: _FakeCompanionInstance) -> None:
        self._member_id = member_id
        self._instance = instance

    def get(self, member_id: str) -> _FakeCompanionInstance | None:
        if member_id == self._member_id:
            return self._instance
        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_context(
    *,
    action_log: list | None = None,
    engine_tags: list[str] | None = None,
    accumulated: float = 0.0,
    members: dict | None = None,
    stages: dict | None = None,
    dispositions: dict | None = None,
    experiences: list | None = None,
    companion_manager: object | None = None,
    with_party: bool = True,
    with_relations: bool = True,
) -> SettlementContext:
    effective_tags = engine_tags or []
    effective_action_log = list(action_log or [])
    if not effective_action_log and "LONG_REST" in effective_tags:
        effective_action_log = [{"type": "rest_long", "time_cost": 1.0}]
        if accumulated == 0.0:
            accumulated = 1.0

    state = StateContainer()
    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)
    scene_bus = SceneBus(scene_sl)
    time_slice = TimeSlice()
    if effective_action_log and any(isinstance(item, dict) and item.get("type") == "rest_long" for item in effective_action_log):
        time_slice.restore({"day": 1, "slot": 22, "accumulated": accumulated or 1.0})
    else:
        time_slice.restore({"day": 1, "slot": 22, "accumulated": accumulated})
    state.register(time_slice)

    if engine_tags:
        scene_bus.add_entry({
            "source": "ENGINE",
            "content": "[test]",
            "visibility": "system",
            "tags": effective_tags,
        })

    if with_relations:
        rel = RelationSlice()
        rel.restore({
            "npc_dispositions": dispositions or {},
            "relationship_stages": stages or {},
            "faction_standings": {},
            "npc_impressions": {},
            "shop_states": {},
        })
        state.register(rel)

    if with_party:
        party = PartySlice()
        party.restore({
            "members": members or {"hero": {}},
            "companion_approval": {},
            "shared_experiences": experiences or [],
        })
        state.register(party)

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test"),
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: None,
        action_log=effective_action_log,
        companion_manager=companion_manager,
    )


# ------------------------------------------------------------------
# _is_long_rest
# ------------------------------------------------------------------


def test_is_long_rest_from_scene_bus_tag() -> None:
    ctx = _make_context(engine_tags=["LONG_REST"])
    assert _is_long_rest(ctx)


def test_is_long_rest_from_action_log() -> None:
    ctx = _make_context(action_log=[{"type": "rest_long"}])
    assert _is_long_rest(ctx)


def test_is_long_rest_false_when_neither() -> None:
    ctx = _make_context(action_log=[{"type": "navigate"}])
    assert not _is_long_rest(ctx)


# ------------------------------------------------------------------
# _has_major_experience_today
# ------------------------------------------------------------------


def test_major_experience_from_combat_end_tag() -> None:
    ctx = _make_context(engine_tags=["COMBAT_END"])
    assert _has_major_experience_today(ctx)


def test_major_experience_from_quest_progress_tag() -> None:
    ctx = _make_context(engine_tags=["QUEST_PROGRESS"])
    assert _has_major_experience_today(ctx)


def test_major_experience_from_action_log() -> None:
    ctx = _make_context(action_log=[{"type": "end_combat"}])
    assert _has_major_experience_today(ctx)


def test_major_experience_false_when_none() -> None:
    ctx = _make_context(action_log=[{"type": "navigate"}])
    assert not _has_major_experience_today(ctx)


# ------------------------------------------------------------------
# _teammate_eligible
# ------------------------------------------------------------------


def test_eligible_acquaintance_positive_approval() -> None:
    ctx = _make_context(
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": 10}},
    )
    assert _teammate_eligible("hero", ctx)


def test_not_eligible_stranger() -> None:
    ctx = _make_context(
        stages={"hero": "stranger"},
        dispositions={"hero": {"approval": 50}},
    )
    assert not _teammate_eligible("hero", ctx)


def test_not_eligible_cold_stage() -> None:
    ctx = _make_context(
        stages={"hero": "cold"},
        dispositions={"hero": {"approval": -5}},
    )
    assert not _teammate_eligible("hero", ctx)


def test_not_eligible_negative_approval() -> None:
    ctx = _make_context(
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": -1}},
    )
    assert not _teammate_eligible("hero", ctx)


def test_not_eligible_no_relations_slice() -> None:
    ctx = _make_context(with_relations=False)
    assert not _teammate_eligible("hero", ctx)


# ------------------------------------------------------------------
# _select_memory
# ------------------------------------------------------------------


def test_select_memory_returns_none_when_no_experiences() -> None:
    ctx = _make_context(experiences=[])
    assert _select_memory("hero", ctx, today=3) is None


def test_select_memory_prefers_todays_major_experience() -> None:
    exps = [
        {"type": "rest", "day": 1, "participants": ["hero"], "summary": "old rest"},
        {"type": "combat", "day": 3, "participants": ["hero"], "summary": "today combat"},
    ]
    ctx = _make_context(experiences=exps)
    mem = _select_memory("hero", ctx, today=3)
    assert mem is not None
    assert mem["summary"] == "today combat"


def test_select_memory_critical_moment_ranked_higher() -> None:
    exps = [
        {"type": "combat", "day": 1, "participants": ["hero"], "summary": "old combat",
         "critical_moment": True},
        {"type": "rest", "day": 3, "participants": ["hero"], "summary": "today rest"},
    ]
    ctx = _make_context(experiences=exps)
    mem = _select_memory("hero", ctx, today=3)
    assert mem is not None
    # today rest (+50) vs old critical combat (+30+10=40) → today wins
    assert mem["summary"] == "today rest"


def test_select_memory_includes_companion_recent_events() -> None:
    records = [
        TickRecord(
            tick=1,
            action_type="navigate",
            executed=True,
            summary="Crossed the old bridge.",
            tags=["NAVIGATION"],
            involved_npcs=[],
            has_rolls=False,
            event_transitions=[],
        )
    ]
    manager = _FakeCompanionManager("hero", _FakeCompanionInstance(records))
    ctx = _make_context(experiences=[], companion_manager=manager)
    mem = _select_memory("hero", ctx, today=1, companion_manager=manager)
    assert mem is not None
    assert mem["type"] == "exploration"
    assert "Crossed the old bridge." in mem["summary"]


# ------------------------------------------------------------------
# _generate_campfire_line
# ------------------------------------------------------------------


def test_generate_campfire_line_combat_uses_summary() -> None:
    memory = {"type": "combat", "summary": "Day 3 combat at forest"}
    line = _generate_campfire_line(memory)
    assert "Day 3 combat at forest" in line


def test_generate_campfire_line_unknown_type_falls_back_to_rest() -> None:
    memory = {"type": "unknown_type", "summary": "some event"}
    line = _generate_campfire_line(memory)
    assert isinstance(line, str) and len(line) > 0


# ------------------------------------------------------------------
# CampfireHook.execute() integration
# ------------------------------------------------------------------


def test_execute_returns_empty_when_no_long_rest() -> None:
    ctx = _make_context(action_log=[{"type": "navigate"}])
    result = asyncio.run(CampfireHook().execute(ctx))
    assert isinstance(result, HookResult)
    assert not result.sse_events


def test_execute_returns_empty_when_no_party() -> None:
    ctx = _make_context(
        engine_tags=["LONG_REST"],
        with_party=False,
    )
    result = asyncio.run(CampfireHook().execute(ctx))
    assert not result.sse_events


def test_execute_returns_empty_when_no_eligible_member() -> None:
    # hero is stranger → not eligible
    ctx = _make_context(
        engine_tags=["LONG_REST", "COMBAT_END"],
        stages={"hero": "stranger"},
        dispositions={"hero": {"approval": 20}},
        experiences=[
            {"type": "combat", "day": 1, "participants": ["hero"],
             "summary": "Day 1 combat at forest"},
        ],
    )
    result = asyncio.run(CampfireHook().execute(ctx))
    assert not result.sse_events


def test_execute_emits_campfire_dialogue_sse() -> None:
    ctx = _make_context(
        engine_tags=["LONG_REST", "COMBAT_END"],
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": 10}},
        experiences=[
            {"type": "combat", "day": 1, "participants": ["hero"],
             "summary": "Day 1 combat at forest"},
        ],
    )
    result = asyncio.run(CampfireHook().execute(ctx))
    assert len(result.sse_events) == 1
    ev = result.sse_events[0]
    assert ev.event_type == "campfire_dialogue"
    assert ev.payload["teammate_id"] == "hero"
    assert ev.payload["memory_type"] == "combat"


def test_execute_bumps_approval_plus_5() -> None:
    ctx = _make_context(
        engine_tags=["LONG_REST", "COMBAT_END"],
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": 10}},
        experiences=[
            {"type": "combat", "day": 1, "participants": ["hero"],
             "summary": "Day 1 combat at forest"},
        ],
    )
    asyncio.run(CampfireHook().execute(ctx))
    approval = ctx.state.relations.get_disposition("hero", "approval")
    assert approval == 15


def test_execute_uses_companion_events_when_shared_experiences_empty() -> None:
    manager = _FakeCompanionManager(
        "hero",
        _FakeCompanionInstance([
            TickRecord(
                tick=5,
                action_type="navigate",
                executed=True,
                summary="We walked through ancient ruins.",
                tags=["NAVIGATION"],
                involved_npcs=[],
                has_rolls=False,
                event_transitions=[],
            ),
        ]),
    )
    ctx = _make_context(
        engine_tags=["LONG_REST", "COMBAT_END"],
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": 10}},
        experiences=[],
        companion_manager=manager,
    )
    result = asyncio.run(CampfireHook().execute(ctx))
    assert len(result.sse_events) == 1
    payload = result.sse_events[0].payload
    assert payload["memory_type"] == "exploration"
    assert "ancient ruins" in payload["memory_summary"]


def test_execute_skips_mid_rest_slots() -> None:
    ctx = _make_context(
        action_log=[{"type": "rest_long", "time_cost": 1.0}],
        accumulated=4.0,
        engine_tags=["LONG_REST", "COMBAT_END"],
        stages={"hero": "acquaintance"},
        dispositions={"hero": {"approval": 10}},
        experiences=[
            {"type": "combat", "day": 1, "participants": ["hero"],
             "summary": "Day 1 combat at forest"},
        ],
    )
    result = asyncio.run(CampfireHook().execute(ctx))
    assert not result.sse_events
