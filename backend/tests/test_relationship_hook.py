"""Tests for RelationshipHook (O-G02)."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.relationship import RelationshipHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.relations import RelationSlice


def _make_context(
    *,
    npc_dispositions: dict | None = None,
    relationship_stages: dict | None = None,
    shared_experiences: list | None = None,  # None → PartySlice not registered
    change_log: list | None = None,
) -> SettlementContext:
    state = StateContainer()

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    relations_slice = RelationSlice()
    relations_slice.restore({
        "npc_dispositions": npc_dispositions or {},
        "relationship_stages": relationship_stages or {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(relations_slice)

    if shared_experiences is not None:
        party_slice = PartySlice()
        party_slice.restore({
            "members": {},
            "companion_approval": {},
            "shared_experiences": shared_experiences,
        })
        state.register(party_slice)

    return SettlementContext(
        change_log=change_log if change_log is not None else [],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


def _make_context_no_relations(change_log: list | None = None) -> SettlementContext:
    """Context with no RelationSlice registered."""
    state = StateContainer()
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    return SettlementContext(
        change_log=change_log or [],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


def _relation_change(slice_name: str = "relations") -> StateChange:
    return StateChange(slice=slice_name, operation="set", path="x", value="y")


class TestRelationshipHookBasics:
    def test_missing_relations_slice_returns_noop(self) -> None:
        context = _make_context_no_relations()
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "missing_relations_slice"
        assert result.metadata["checked_npc_count"] == 0
        assert result.sse_events == []

    def test_should_skip_when_no_relevant_changes(self) -> None:
        hook = RelationshipHook()
        change_log = [_relation_change("player"), _relation_change("time")]
        assert hook.should_skip(change_log) is True

    def test_should_not_skip_when_relations_changed(self) -> None:
        hook = RelationshipHook()
        assert hook.should_skip([_relation_change("relations")]) is False

    def test_should_not_skip_when_party_changed(self) -> None:
        hook = RelationshipHook()
        assert hook.should_skip([_relation_change("party")]) is False

    def test_no_npcs_returns_noop(self) -> None:
        context = _make_context()
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert result.metadata["checked_npc_count"] == 0
        assert result.metadata["transitioned_count"] == 0


class TestRelationshipStageTransitions:
    def test_stranger_advances_to_acquaintance(self) -> None:
        context = _make_context(
            npc_dispositions={"aria": {"approval": 11, "trust": 0, "romance": 0}},
            relationship_stages={"aria": "stranger"},
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "applied"
        assert result.metadata["transitioned_count"] == 1
        assert result.metadata["transitions"][0] == {
            "npc_id": "aria",
            "old_stage": "stranger",
            "new_stage": "acquaintance",
        }
        assert context.state.relations.get_stage("aria") == "acquaintance"
        assert len(result.sse_events) == 1
        assert result.sse_events[0].event_type == "relationship_stage_changed"
        assert result.sse_events[0].payload["new_stage"] == "acquaintance"

    def test_stranger_stays_with_boundary_approval(self) -> None:
        # approval must be strictly greater than 10; approval==10 does not qualify
        context = _make_context(
            npc_dispositions={"aria": {"approval": 10}},
            relationship_stages={"aria": "stranger"},
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert context.state.relations.get_stage("aria") == "stranger"

    def test_acquaintance_advances_to_friend(self) -> None:
        # approval > 30, trust > 20, 3 shared experiences
        shared_experiences = [
            {"participants": ["aria", "player"], "type": "combat", "critical_moment": False},
            {"participants": ["aria", "player"], "type": "dialogue", "critical_moment": False},
            {"participants": ["aria", "player"], "type": "exploration", "critical_moment": False},
        ]
        context = _make_context(
            npc_dispositions={"aria": {"approval": 31, "trust": 21, "romance": 0}},
            relationship_stages={"aria": "acquaintance"},
            shared_experiences=shared_experiences,
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "applied"
        assert context.state.relations.get_stage("aria") == "friend"

    def test_acquaintance_does_not_advance_without_enough_shared_experiences(self) -> None:
        # only 2 shared experiences, need 3
        shared_experiences = [
            {"participants": ["aria", "player"], "type": "combat", "critical_moment": False},
            {"participants": ["aria", "player"], "type": "dialogue", "critical_moment": False},
        ]
        context = _make_context(
            npc_dispositions={"aria": {"approval": 31, "trust": 21}},
            relationship_stages={"aria": "acquaintance"},
            shared_experiences=shared_experiences,
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert context.state.relations.get_stage("aria") == "acquaintance"

    def test_friend_advances_to_close_friend(self) -> None:
        # trust > 60, ≥1 critical moment, ≥10 shared experiences
        shared_experiences = [
            {
                "participants": ["aria", "player"],
                "type": "crisis",
                "critical_moment": i == 0,
            }
            for i in range(10)
        ]
        context = _make_context(
            npc_dispositions={"aria": {"approval": 0, "trust": 61, "romance": 0}},
            relationship_stages={"aria": "friend"},
            shared_experiences=shared_experiences,
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "applied"
        assert context.state.relations.get_stage("aria") == "close_friend"

    def test_friend_does_not_advance_without_critical_moment(self) -> None:
        # 10 shared experiences but none are critical moments
        shared_experiences = [
            {"participants": ["aria", "player"], "type": "dialogue", "critical_moment": False}
            for _ in range(10)
        ]
        context = _make_context(
            npc_dispositions={"aria": {"trust": 61}},
            relationship_stages={"aria": "friend"},
            shared_experiences=shared_experiences,
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert context.state.relations.get_stage("aria") == "friend"

    def test_close_friend_advances_to_intimate(self) -> None:
        # trust > 80, romance > 60
        context = _make_context(
            npc_dispositions={"aria": {"trust": 81, "romance": 61}},
            relationship_stages={"aria": "close_friend"},
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "applied"
        assert context.state.relations.get_stage("aria") == "intimate"

    def test_intimate_stage_has_no_further_transition(self) -> None:
        context = _make_context(
            npc_dispositions={"aria": {"approval": 100, "trust": 100, "romance": 100}},
            relationship_stages={"aria": "intimate"},
        )
        result = asyncio.run(RelationshipHook().execute(context))
        assert result.metadata["status"] == "noop"
        assert context.state.relations.get_stage("aria") == "intimate"
