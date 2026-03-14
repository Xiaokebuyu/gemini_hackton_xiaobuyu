"""Tests for Phase 6: Receptionist reward service via quest_completed event.

Coverage:
1. quest_completed event triggers assign_service directive for receptionist
2. Generated directive contains correct effect atoms (gold, xp, items)
3. No receptionist NPC → graceful degradation (empty directives)
4. Quest has no rewards → graceful degradation (empty directives)
5. Service already assigned → idempotency (empty SubSystemResult, no duplicate)
6. precondition check: quest not completed → execute_service refuses
7. precondition check: dynamic quest completed → execute_service allows
8. precondition check: milestone COMPLETED → execute_service allows
9. precondition check: dynamic quest status "reported" → allows
10. precondition check: no quests slice → refuses
11. _rewards_to_effects: xp-only, gold-only, items-only, combined
12. _rewards_to_effects: empty rewards dict → empty effects
13. one_shot: service revoked after successful execution (end-to-end)

Decision record: D-Svc06 (narrative.md)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry, CharacterTemplate
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.service_tool import ExecuteServiceTool
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.subsystem import PlannerEvent
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.engine import RulesEngine
from app.game_core.rules.handlers.planner import PlannerNpcHandler
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateDelta, StateChange, StateContainer
from app.game_core.state.slices import NarrativePlanSlice, SceneSlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.quests import QuestSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECEPTIONIST_ID = "receptionist"


def _make_char_registry(*, with_receptionist: bool = True) -> CharacterRegistry:
    reg = CharacterRegistry()
    data: dict[str, Any] = {}
    if with_receptionist:
        data[_RECEPTIONIST_ID] = {
            "id": _RECEPTIONIST_ID,
            "name": "柜台小姐",
            "tags": ["receptionist", "guild_staff"],
        }
    reg.load(data)
    return reg


def _make_world(*, with_receptionist: bool = True) -> WorldInstance:
    world = WorldInstance("test")
    reg = _make_char_registry(with_receptionist=with_receptionist)
    world.register(reg)
    return world


def _make_state(
    *,
    with_narrative_plan: bool = True,
    with_quests: bool = False,
    with_player: bool = False,
    with_scene: bool = False,
) -> StateContainer:
    state = StateContainer()
    if with_narrative_plan:
        np_slice = NarrativePlanSlice()
        np_slice.restore({})
        state.register(np_slice)
    if with_quests:
        q_slice = QuestSlice()
        q_slice.restore({})
        state.register(q_slice)
    if with_player:
        p_slice = PlayerSlice()
        p_slice.restore({"hp": 10, "max_hp": 30, "gold": 200})
        state.register(p_slice)
    if with_scene:
        sc_slice = SceneSlice()
        sc_slice.restore({})
        state.register(sc_slice)
    return state


def _make_context(
    state: StateContainer,
    world: WorldInstance,
) -> SettlementContext:
    engine = RulesEngine()
    register_default_rules_handlers(engine)
    engine.register(PlannerNpcHandler())
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(state.scene)
    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


def _quest_completed_event(quest_id: str, tick: int = 1) -> PlannerEvent:
    return PlannerEvent(
        kind="quest_completed",
        tick=tick,
        source="test",
        payload={"quest_id": quest_id},
    )


DIRECTOR = NpcDirectorSubSystem()
TOOL = ExecuteServiceTool()


# ---------------------------------------------------------------------------
# 1. quest_completed event triggers assign_service directive
# ---------------------------------------------------------------------------

def test_quest_completed_triggers_assign_service_directive() -> None:
    """When quest has rewards and a receptionist exists, directive is generated."""
    world = _make_world()
    state = _make_state(with_narrative_plan=True, with_quests=True)
    state.quests.add_dynamic_quest("q_test", {
        "quest_id": "q_test",
        "title": "Test Quest",
        "status": "completed",
        "rewards": {"gold": 50, "xp": 200},
    })
    ctx = _make_context(state, world)

    event = _quest_completed_event("q_test")
    result = asyncio.run(DIRECTOR.evaluate(event, ctx))

    assert len(result.directives) == 1
    directive = result.directives[0]
    assert directive["kind"] == "assign_service"
    payload = directive["payload"]
    assert payload["npc_id"] == _RECEPTIONIST_ID
    assert payload["service_id"] == "reward_q_test"
    assert payload["label"] == "领取任务报酬"
    assert payload["price"] == 0
    assert payload["one_shot"] is True
    assert payload["preconditions"] == {"quest_completed": "q_test"}


# ---------------------------------------------------------------------------
# 2. Generated directive contains correct effect atoms
# ---------------------------------------------------------------------------

def test_quest_completed_directive_effects_gold_xp_items() -> None:
    """Effects list has gold, xp and item entries in correct order."""
    world = _make_world()
    state = _make_state(with_narrative_plan=True, with_quests=True)
    state.quests.add_dynamic_quest("q_full", {
        "quest_id": "q_full",
        "status": "completed",
        "rewards": {
            "gold": 100,
            "xp": 500,
            "items": [{"item_id": "healing_potion", "count": 2}],
        },
    })
    ctx = _make_context(state, world)

    event = _quest_completed_event("q_full")
    result = asyncio.run(DIRECTOR.evaluate(event, ctx))

    assert len(result.directives) == 1
    effects = result.directives[0]["payload"]["effects"]
    types = [e["type"] for e in effects]
    assert "modify_gold" in types
    assert "add_xp" in types
    assert "grant_item" in types

    gold_atom = next(e for e in effects if e["type"] == "modify_gold")
    assert gold_atom["amount"] == 100

    xp_atom = next(e for e in effects if e["type"] == "add_xp")
    assert xp_atom["amount"] == 500

    item_atom = next(e for e in effects if e["type"] == "grant_item")
    assert item_atom["item_id"] == "healing_potion"
    assert item_atom["count"] == 2


# ---------------------------------------------------------------------------
# 3. No receptionist NPC → graceful degradation
# ---------------------------------------------------------------------------

def test_quest_completed_no_receptionist_returns_empty() -> None:
    """When no receptionist is in the world, SubSystemResult has no directives."""
    world = _make_world(with_receptionist=False)
    state = _make_state(with_narrative_plan=True, with_quests=True)
    state.quests.add_dynamic_quest("q_no_npc", {
        "quest_id": "q_no_npc",
        "status": "completed",
        "rewards": {"gold": 50},
    })
    ctx = _make_context(state, world)

    event = _quest_completed_event("q_no_npc")
    result = asyncio.run(DIRECTOR.evaluate(event, ctx))

    assert result.directives == []


# ---------------------------------------------------------------------------
# 4. Quest has no rewards → graceful degradation
# ---------------------------------------------------------------------------

def test_quest_completed_no_rewards_returns_empty() -> None:
    """When quest has no rewards, no assign_service directive is generated."""
    world = _make_world()
    state = _make_state(with_narrative_plan=True, with_quests=True)
    state.quests.add_dynamic_quest("q_no_reward", {
        "quest_id": "q_no_reward",
        "status": "completed",
        "rewards": {},
    })
    ctx = _make_context(state, world)

    event = _quest_completed_event("q_no_reward")
    result = asyncio.run(DIRECTOR.evaluate(event, ctx))

    assert result.directives == []


# ---------------------------------------------------------------------------
# 5. Service already assigned → idempotency
# ---------------------------------------------------------------------------

def test_quest_completed_idempotency_no_duplicate() -> None:
    """If reward service already exists in NarrativePlanSlice, no new directive."""
    world = _make_world()
    state = _make_state(with_narrative_plan=True, with_quests=True)
    state.quests.add_dynamic_quest("q_idem", {
        "quest_id": "q_idem",
        "status": "completed",
        "rewards": {"gold": 50},
    })
    # Pre-assign the service
    state.narrative_plan.assign_service(_RECEPTIONIST_ID, {
        "service_id": "reward_q_idem",
        "label": "已存在的服务",
        "price": 0,
    })
    ctx = _make_context(state, world)

    event = _quest_completed_event("q_idem")
    result = asyncio.run(DIRECTOR.evaluate(event, ctx))

    assert result.directives == []


# ---------------------------------------------------------------------------
# 6. precondition check: quest not completed → service_tool refuses
# ---------------------------------------------------------------------------

def test_precondition_quest_not_completed_refuses() -> None:
    """execute_service is refused when quest status is 'in_progress'."""
    state = _make_state(with_narrative_plan=True, with_quests=True, with_scene=True)
    state.quests.add_dynamic_quest("q_check", {
        "quest_id": "q_check",
        "status": "in_progress",
        "rewards": {"gold": 50},
    })

    service = {
        "service_id": "reward_q_check",
        "npc_id": _RECEPTIONIST_ID,
        "label": "领取报酬",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 50}],
        "preconditions": {"quest_completed": "q_check"},
        "one_shot": True,
    }

    def _execute(cmd: Command) -> ExecuteResult:
        return ExecuteResult(executed=True, metadata={})

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_q_check"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "quest_not_completed"


# ---------------------------------------------------------------------------
# 7. precondition check: dynamic quest "completed" → allows execution
# ---------------------------------------------------------------------------

def test_precondition_quest_completed_dynamic_allows() -> None:
    """execute_service succeeds when dynamic quest status is 'completed'."""
    state = _make_state(with_narrative_plan=True, with_quests=True, with_scene=True)
    state.quests.add_dynamic_quest("q_done", {
        "quest_id": "q_done",
        "status": "completed",
        "rewards": {"gold": 30},
    })

    service = {
        "service_id": "reward_q_done",
        "npc_id": _RECEPTIONIST_ID,
        "label": "领取报酬",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 30}],
        "preconditions": {"quest_completed": "q_done"},
        "one_shot": False,
    }

    def _execute(cmd: Command) -> ExecuteResult:
        return ExecuteResult(executed=True, metadata={})

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_q_done"}, ctx))

    assert result.ok is True
    assert result.metadata["status"] == "ok"


# ---------------------------------------------------------------------------
# 8. precondition check: milestone COMPLETED → allows execution
# ---------------------------------------------------------------------------

def test_precondition_milestone_completed_allows() -> None:
    """execute_service succeeds when milestone state is COMPLETED."""
    state = _make_state(with_narrative_plan=True, with_quests=True, with_scene=True)
    # Use milestone rather than dynamic quest
    state.quests.advance_milestone("ms_test", "COMPLETED", tick=1)

    service = {
        "service_id": "reward_ms_test",
        "npc_id": _RECEPTIONIST_ID,
        "label": "里程碑报酬",
        "price": 0,
        "effects": [{"type": "add_xp", "amount": 300}],
        "preconditions": {"quest_completed": "ms_test"},
        "one_shot": False,
    }

    def _execute(cmd: Command) -> ExecuteResult:
        return ExecuteResult(executed=True, metadata={})

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_ms_test"}, ctx))

    assert result.ok is True
    assert result.metadata["status"] == "ok"


# ---------------------------------------------------------------------------
# 9. precondition check: dynamic quest status "reported" → allows
# ---------------------------------------------------------------------------

def test_precondition_quest_reported_allows() -> None:
    """execute_service succeeds when dynamic quest status is 'reported'."""
    state = _make_state(with_narrative_plan=True, with_quests=True, with_scene=True)
    state.quests.add_dynamic_quest("q_reported", {
        "quest_id": "q_reported",
        "status": "reported",
        "rewards": {"gold": 20},
    })

    service = {
        "service_id": "reward_q_reported",
        "npc_id": _RECEPTIONIST_ID,
        "label": "已上报任务报酬",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 20}],
        "preconditions": {"quest_completed": "q_reported"},
        "one_shot": False,
    }

    def _execute(cmd: Command) -> ExecuteResult:
        return ExecuteResult(executed=True, metadata={})

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_q_reported"}, ctx))

    assert result.ok is True


# ---------------------------------------------------------------------------
# 10. precondition check: no quests slice → refuses
# ---------------------------------------------------------------------------

def test_precondition_no_quests_slice_refuses() -> None:
    """execute_service is refused when quests slice is not registered."""
    state = _make_state(with_narrative_plan=True, with_quests=False, with_scene=True)

    service = {
        "service_id": "reward_unknown",
        "npc_id": _RECEPTIONIST_ID,
        "label": "报酬",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 10}],
        "preconditions": {"quest_completed": "some_quest"},
        "one_shot": False,
    }

    def _execute(cmd: Command) -> ExecuteResult:
        return ExecuteResult(executed=True, metadata={})

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_unknown"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "quest_not_completed"


# ---------------------------------------------------------------------------
# 11. _rewards_to_effects: various combinations
# ---------------------------------------------------------------------------

def test_rewards_to_effects_gold_only() -> None:
    effects = NpcDirectorSubSystem._rewards_to_effects({"gold": 75})
    assert len(effects) == 1
    assert effects[0] == {"type": "modify_gold", "amount": 75}


def test_rewards_to_effects_xp_only() -> None:
    effects = NpcDirectorSubSystem._rewards_to_effects({"xp": 300})
    assert len(effects) == 1
    assert effects[0] == {"type": "add_xp", "amount": 300}


def test_rewards_to_effects_items_only() -> None:
    effects = NpcDirectorSubSystem._rewards_to_effects({
        "items": [{"item_id": "sword", "count": 1}],
    })
    assert len(effects) == 1
    assert effects[0] == {"type": "grant_item", "item_id": "sword", "count": 1}


def test_rewards_to_effects_combined() -> None:
    effects = NpcDirectorSubSystem._rewards_to_effects({
        "gold": 100,
        "xp": 500,
        "items": [{"item_id": "potion", "count": 3}],
    })
    assert len(effects) == 3
    types = [e["type"] for e in effects]
    assert "modify_gold" in types
    assert "add_xp" in types
    assert "grant_item" in types


# ---------------------------------------------------------------------------
# 12. _rewards_to_effects: empty rewards → empty effects
# ---------------------------------------------------------------------------

def test_rewards_to_effects_empty_rewards() -> None:
    effects = NpcDirectorSubSystem._rewards_to_effects({})
    assert effects == []


def test_rewards_to_effects_zero_gold_and_xp() -> None:
    """Zero-value gold and xp are not included in effects."""
    effects = NpcDirectorSubSystem._rewards_to_effects({"gold": 0, "xp": 0})
    assert effects == []


# ---------------------------------------------------------------------------
# 13. one_shot: service is revoked after successful execution (end-to-end)
# ---------------------------------------------------------------------------

def test_one_shot_service_revoked_after_execution() -> None:
    """After executing a one_shot reward service, it is removed from the slice."""
    state = _make_state(
        with_narrative_plan=True,
        with_quests=True,
        with_scene=True,
        with_player=True,
    )
    state.quests.add_dynamic_quest("q_oneshot", {
        "quest_id": "q_oneshot",
        "status": "completed",
        "rewards": {"gold": 50},
    })

    service = {
        "service_id": "reward_q_oneshot",
        "npc_id": _RECEPTIONIST_ID,
        "label": "领取报酬",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 50}],
        "preconditions": {"quest_completed": "q_oneshot"},
        "one_shot": True,
    }

    # Pre-assign service into the slice
    state.narrative_plan.assign_service(_RECEPTIONIST_ID, service)

    # Build a full engine so revoke command actually executes
    engine = RulesEngine()
    register_default_rules_handlers(engine)
    engine.register(PlannerNpcHandler())
    world = WorldInstance("test")

    def _execute(cmd: Command) -> ExecuteResult:
        result = engine.execute(cmd, state, world)
        if result.executed and result.delta:
            state.apply(result.delta)
        return result

    ctx = AgentContext(
        role="npc",
        world=world,
        state=state,
        metadata={"character_id": _RECEPTIONIST_ID, "role_data": {"services": [service]}},
        execute_command=_execute,
    )

    result = asyncio.run(TOOL.execute({"service_id": "reward_q_oneshot"}, ctx))

    assert result.ok is True
    # After one_shot execution, service should be revoked from the slice
    remaining = state.narrative_plan.get_services(_RECEPTIONIST_ID)
    assert not any(s["service_id"] == "reward_q_oneshot" for s in remaining)
