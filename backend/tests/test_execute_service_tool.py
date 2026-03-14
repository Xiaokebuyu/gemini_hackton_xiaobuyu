"""Tests for ExecuteServiceTool (Phase 3: execute_service NPC tool).

Decision record: D-Svc03 (narrative.md)

Test categories
---------------
1. basic execute — restore_hp service, gold deduction, scene entry written
2. gold check — insufficient gold blocks execution
3. unknown service_id — graceful rejection
4. missing service_id param — validation error
5. multi-effect atoms — multiple atoms applied in order
6. each canonical effect atom type — grant_item, apply_effect, etc.
7. one_shot flag — service revoked after use
8. no role_data — graceful degradation (empty services)
9. no player slice — gold check fails gracefully
10. price=0 — no deduction step, no gold check
11. already at full HP — restore_hp heals 0 (still succeeds)
12. character_id missing — _no_character_id path
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.service_tool import ExecuteServiceTool
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.player import PlayerSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _player_slice(*, hp: int = 20, max_hp: int = 30, gold: int = 100) -> PlayerSlice:
    s = PlayerSlice()
    s.restore({"hp": hp, "max_hp": max_hp, "gold": gold})
    return s


def _state(
    *,
    hp: int = 20,
    max_hp: int = 30,
    gold: int = 100,
    with_player: bool = True,
    with_scene: bool = True,
) -> StateContainer:
    state = StateContainer()
    if with_player:
        state.register(_player_slice(hp=hp, max_hp=max_hp, gold=gold))
    if with_scene:
        scene = SceneSlice()
        scene.restore({})
        state.register(scene)
    return state


def _recording_executor(
    *,
    succeed_types: set[str] | None = None,
) -> tuple[list[Command], Any]:
    """Return (log, executor).  Succeeds for all command types unless succeed_types
    is given — then only listed types succeed."""
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        if succeed_types is not None and cmd.type not in succeed_types:
            return ExecuteResult.error(f"no handler for {cmd.type}")
        return ExecuteResult(executed=True, metadata={})

    return log, execute


def _failing_executor(
    fail_types: set[str],
) -> tuple[list[Command], Any]:
    """Return an executor that fails for the specified command types."""
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        if cmd.type in fail_types:
            return ExecuteResult.error(f"handler rejected {cmd.type}")
        return ExecuteResult(executed=True, metadata={})

    return log, execute


def _real_executor(state: StateContainer) -> Any:
    """Return an executor that actually applies deltas via ServiceEffectHandler."""
    from app.game_core.rules.defaults import register_default_rules_handlers
    from app.game_core.rules.engine import RulesEngine
    world = WorldInstance("test")
    engine = RulesEngine()
    register_default_rules_handlers(engine)

    def execute(cmd: Command) -> ExecuteResult:
        result = engine.execute(cmd, state, world)
        if result.executed and result.delta:
            state.apply(result.delta)
        return result

    return execute


def _ctx(
    *,
    character_id: str = "npc_healer",
    services: list[dict[str, Any]] | None = None,
    state: StateContainer | None = None,
    execute_command: Any = None,
) -> AgentContext:
    if state is None:
        state = _state()
    role_data: dict[str, Any] = {}
    if services is not None:
        role_data["services"] = services
    return AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": character_id, "role_data": role_data},
        execute_command=execute_command,
    )


def _heal_service(
    *,
    service_id: str = "heal",
    label: str = "治疗",
    price: int = 10,
    amount: int = 15,
    one_shot: bool = False,
) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "npc_id": "npc_healer",
        "label": label,
        "price": price,
        "effects": [{"type": "restore_hp", "amount": amount}],
        "preconditions": {},
        "one_shot": one_shot,
    }


TOOL = ExecuteServiceTool()


# ---------------------------------------------------------------------------
# 1. Basic execute: restore_hp + gold deduction + scene entry
# ---------------------------------------------------------------------------

def test_execute_service_restores_hp_and_deducts_gold() -> None:
    st = _state(hp=10, max_hp=30, gold=50)
    executor = _real_executor(st)
    ctx = _ctx(
        services=[_heal_service(price=10, amount=20)],
        state=st,
        execute_command=executor,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is True
    assert result.metadata["event_type"] == "service_executed"
    assert result.metadata["service_id"] == "heal"
    assert "restore_hp" in result.metadata["effects_applied"]
    assert result.metadata["price_paid"] == 10
    # HP restored: 10 + 20 = 30 (= max_hp)
    assert st.player.hp == 30
    # Gold deducted: 50 - 10 = 40
    assert st.player.gold == 40


def test_execute_service_writes_scene_entry() -> None:
    st = _state()
    executor = _real_executor(st)
    ctx = _ctx(
        services=[_heal_service(price=0, label="神圣治疗")],
        state=st,
        execute_command=executor,
    )

    asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    entries = st.scene.entries
    assert any("execute_service" in e.tags for e in entries)
    assert any("神圣治疗" in e.content for e in entries)


# ---------------------------------------------------------------------------
# 2. Gold check — insufficient gold blocks execution
# ---------------------------------------------------------------------------

def test_execute_service_rejects_when_gold_insufficient() -> None:
    st = _state(gold=5)
    ctx = _ctx(
        services=[_heal_service(price=10)],
        state=st,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "insufficient_gold"
    assert result.metadata["required"] == 10
    assert result.metadata["current"] == 5
    # HP unchanged
    assert st.player.hp == 20


# ---------------------------------------------------------------------------
# 3. Unknown service_id
# ---------------------------------------------------------------------------

def test_execute_service_rejects_unknown_service_id() -> None:
    ctx = _ctx(services=[_heal_service()])

    result = asyncio.run(TOOL.execute({"service_id": "resurrection"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "unknown_service"


# ---------------------------------------------------------------------------
# 4. Missing / blank service_id param
# ---------------------------------------------------------------------------

def test_execute_service_rejects_missing_service_id() -> None:
    ctx = _ctx(services=[_heal_service()])

    result = asyncio.run(TOOL.execute({}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


def test_execute_service_rejects_blank_service_id() -> None:
    ctx = _ctx(services=[_heal_service()])

    result = asyncio.run(TOOL.execute({"service_id": "  "}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


# ---------------------------------------------------------------------------
# 5. Multi-effect atoms
# ---------------------------------------------------------------------------

def test_execute_service_applies_multiple_effects() -> None:
    service = {
        "service_id": "full_restore",
        "npc_id": "npc_healer",
        "label": "完全恢复",
        "price": 0,
        "effects": [
            {"type": "restore_hp", "amount": 999},
            {"type": "modify_gold", "amount": 20},
        ],
        "preconditions": {},
        "one_shot": False,
    }
    st = _state(hp=1, max_hp=30, gold=100)
    executor = _real_executor(st)
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "full_restore"}, ctx))

    assert result.ok is True
    assert st.player.hp == 30    # capped at max_hp
    assert st.player.gold == 120 # 100 + 20


# ---------------------------------------------------------------------------
# 6. Canonical effect atom types — command routing
# ---------------------------------------------------------------------------

def test_execute_service_grant_item_uses_pick_up_command() -> None:
    service = {
        "service_id": "give_potion",
        "npc_id": "npc_merchant",
        "label": "给予药水",
        "price": 0,
        "effects": [{"type": "grant_item", "item_id": "health_potion", "count": 1}],
        "preconditions": {},
        "one_shot": False,
    }
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "give_potion"}, ctx))

    assert result.ok is True
    assert any(c.type == "pick_up" for c in log)
    pick_up_cmd = next(c for c in log if c.type == "pick_up")
    assert pick_up_cmd.params["item_id"] == "health_potion"


def test_execute_service_remove_item_uses_drop_command() -> None:
    service = {
        "service_id": "take_ingredient",
        "npc_id": "npc_alchemist",
        "label": "取走材料",
        "price": 0,
        "effects": [{"type": "remove_item", "item_id": "herb", "count": 2}],
        "preconditions": {},
        "one_shot": False,
    }
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "take_ingredient"}, ctx))

    assert result.ok is True
    assert any(c.type == "drop" for c in log)


def test_execute_service_apply_effect_uses_apply_effect_command() -> None:
    service = {
        "service_id": "blessing",
        "npc_id": "npc_priest",
        "label": "祝福",
        "price": 5,
        "effects": [{"type": "apply_effect", "effect_id": "blessed", "duration_ticks": 6}],
        "preconditions": {},
        "one_shot": False,
    }
    st = _state(gold=50)
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "blessing"}, ctx))

    assert result.ok is True
    assert any(c.type == "apply_effect" for c in log)
    apply_cmd = next(c for c in log if c.type == "apply_effect")
    assert apply_cmd.params.get("effect_id") == "blessed"


def test_execute_service_add_xp_uses_add_xp_command() -> None:
    service = {
        "service_id": "training",
        "npc_id": "npc_trainer",
        "label": "训练",
        "price": 30,
        "effects": [{"type": "add_xp", "amount": 100}],
        "preconditions": {},
        "one_shot": False,
    }
    st = _state(gold=100)
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "training"}, ctx))

    assert result.ok is True
    assert any(c.type == "add_xp" for c in log)


def test_execute_service_remove_effect_uses_remove_effect_command() -> None:
    service = {
        "service_id": "cure_curse",
        "npc_id": "npc_cleric",
        "label": "解咒",
        "price": 20,
        "effects": [{"type": "remove_effect", "effect_id": "cursed"}],
        "preconditions": {},
        "one_shot": False,
    }
    st = _state(gold=100)
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "cure_curse"}, ctx))

    assert result.ok is True
    assert any(c.type == "remove_effect" for c in log)


def test_execute_service_add_knowledge_uses_add_knowledge_command() -> None:
    service = {
        "service_id": "lore_lesson",
        "npc_id": "npc_sage",
        "label": "传授知识",
        "price": 0,
        "effects": [{"type": "add_knowledge", "knowledge": "ancient history"}],
        "preconditions": {},
        "one_shot": False,
    }
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "lore_lesson"}, ctx))

    assert result.ok is True
    assert any(c.type == "add_knowledge" for c in log)


# ---------------------------------------------------------------------------
# 7. one_shot: revoke service after use
# ---------------------------------------------------------------------------

def test_execute_service_one_shot_issues_revoke_command() -> None:
    service = _heal_service(price=0, one_shot=True)
    st = _state()
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is True
    revoke_cmds = [c for c in log if c.type == "planner_revoke_service"]
    assert len(revoke_cmds) == 1
    assert revoke_cmds[0].params["npc_id"] == "npc_healer"
    assert revoke_cmds[0].params["service_id"] == "heal"


def test_execute_service_non_one_shot_does_not_issue_revoke() -> None:
    service = _heal_service(price=0, one_shot=False)
    st = _state()
    log, executor = _recording_executor()
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    revoke_cmds = [c for c in log if c.type == "planner_revoke_service"]
    assert len(revoke_cmds) == 0


# ---------------------------------------------------------------------------
# 8. No role_data — graceful degradation
# ---------------------------------------------------------------------------

def test_execute_service_no_role_data_returns_unknown_service() -> None:
    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=_state(),
        metadata={"character_id": "npc_healer"},  # no role_data key
        execute_command=None,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "unknown_service"


def test_execute_service_empty_services_list_returns_unknown_service() -> None:
    ctx = _ctx(services=[])  # empty list

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "unknown_service"


# ---------------------------------------------------------------------------
# 9. No player slice — gold check fails gracefully
# ---------------------------------------------------------------------------

def test_execute_service_no_player_slice_rejects_priced_service() -> None:
    st = _state(with_player=False)
    ctx = _ctx(services=[_heal_service(price=10)], state=st)

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "state_unavailable"


# ---------------------------------------------------------------------------
# 10. price=0 — no gold check or deduction
# ---------------------------------------------------------------------------

def test_execute_service_free_service_needs_no_gold() -> None:
    st = _state(hp=10, max_hp=30, gold=0)  # zero gold
    executor = _real_executor(st)
    ctx = _ctx(
        services=[_heal_service(price=0, amount=10)],
        state=st,
        execute_command=executor,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is True
    assert st.player.gold == 0    # unchanged
    assert st.player.hp == 20     # healed


# ---------------------------------------------------------------------------
# 11. Already at full HP — restore_hp succeeds but heals 0
# ---------------------------------------------------------------------------

def test_execute_service_restore_hp_at_full_hp_succeeds() -> None:
    st = _state(hp=30, max_hp=30, gold=100)
    executor = _real_executor(st)
    ctx = _ctx(
        services=[_heal_service(price=10, amount=20)],
        state=st,
        execute_command=executor,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is True
    assert st.player.hp == 30  # unchanged (was already full)
    # Gold still deducted for the service
    assert st.player.gold == 90


# ---------------------------------------------------------------------------
# 12. missing character_id
# ---------------------------------------------------------------------------

def test_execute_service_no_character_id_fails() -> None:
    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=_state(),
        metadata={},  # no character_id
        execute_command=None,
    )

    result = asyncio.run(TOOL.execute({"service_id": "heal"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "missing_character_id"


# ---------------------------------------------------------------------------
# 13. modify_gold atom — direct effect
# ---------------------------------------------------------------------------

def test_execute_service_modify_gold_positive() -> None:
    service = {
        "service_id": "bonus",
        "npc_id": "npc_giver",
        "label": "奖励金币",
        "price": 0,
        "effects": [{"type": "modify_gold", "amount": 25}],
        "preconditions": {},
        "one_shot": False,
    }
    st = _state(gold=10)
    executor = _real_executor(st)
    ctx = _ctx(services=[service], state=st, execute_command=executor)

    result = asyncio.run(TOOL.execute({"service_id": "bonus"}, ctx))

    assert result.ok is True
    assert st.player.gold == 35


# ---------------------------------------------------------------------------
# 14. Tool metadata
# ---------------------------------------------------------------------------

def test_execute_service_tool_metadata() -> None:
    assert TOOL.name == "execute_service"
    assert TOOL.allowed_roles == ["npc"]
    assert TOOL.applicable_traits == []
    schema = TOOL.parameters
    assert "service_id" in schema["properties"]
    assert "service_id" in schema.get("required", [])
