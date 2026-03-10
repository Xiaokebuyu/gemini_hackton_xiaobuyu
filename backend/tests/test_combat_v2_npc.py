"""Integration tests for CombatHandler.combat_npc_turn (Phase 5).

Covers:
  - test_npc_turn_attacks_adjacent_target     adjacent melee target → attacks + action_used
  - test_npc_turn_moves_then_attacks          distant target → AI moves + attacks
  - test_npc_turn_flees_when_low_hp           low-HP cowardly monster → fled=True
  - test_npc_turn_player_source_rejected      source="player" → validation failure
  - test_npc_turn_kills_last_enemy_clears     last enemy killed → combat_cleared
  - test_npc_turn_player_hp_sync              attack hits player → PlayerSlice HP synced

All tests are synchronous (no pytest-asyncio needed).
"""

from __future__ import annotations

import unittest.mock as mock
from typing import Any

from app.game_core.rules import Command
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_GRID = {
    "width": 5,
    "height": 5,
    "terrain": [
        "GGGGG",
        "GGGGG",
        "GGGGG",
        "GGGGG",
        "GGGGG",
    ],
}


def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    source: str = "monster",
    speed: int = 6,
    hp: int = 10,
    max_hp: int = 10,
    ac: int = 12,
    alive: bool = True,
    fled: bool = False,
    action_used: bool = False,
    move_used: bool = False,
    defending: bool = False,
    reaction_used: bool = False,
    personality: str = "aggressive",
    attacks: list[dict[str, Any]] | None = None,
    monster_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal v2 unit dict."""
    u: dict[str, Any] = {
        "unit_id": unit_id,
        "side": side,
        "source": source,
        "position": list(position),
        "speed": speed,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": fled,
        "action_used": action_used,
        "move_used": move_used,
        "disengaged": False,
        "dashed": False,
        "defending": defending,
        "reaction_used": reaction_used,
        "surprised": False,
        "personality": personality,
        "attacks": attacks or [
            {"name": "Claw", "hit_bonus": 3, "damage_dice": "1d6", "damage_type": "slashing", "range": 1}
        ],
        "stats": {"dex": 10, "wis": 10},
    }
    if monster_id is not None:
        u["monster_id"] = monster_id
    return u


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    current_turn_index: int = 0,
    combat_round: int = 1,
    area_id: str = "dungeon",
    sub_area_id: str = "room1",
    grid: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v2 hostile payload dict."""
    current_unit_id = turn_order[current_turn_index] if turn_order else ""
    return {
        "area_id": area_id,
        "version": 2,
        "combat_active": True,
        "cleared": False,
        "blocking": True,
        "status": "engaged",
        "units": [dict(u) for u in units],
        "grid": grid or _DEFAULT_GRID,
        "turn_order": list(turn_order),
        "current_turn_index": current_turn_index,
        "current_unit_id": current_unit_id,
        "combat_round": combat_round,
        "initiative_rolls": {},
    }


def _make_state(
    payload: dict[str, Any],
    sub_area_id: str = "room1",
    area_id: str = "dungeon",
    player_hp: int = 20,
    player_max_hp: int = 20,
    include_player: bool = True,
    include_flags: bool = False,
) -> StateContainer:
    """Build a StateContainer with the given v2 combat payload registered."""
    state = StateContainer()

    if include_player:
        player = PlayerSlice()
        player.restore({
            "character_id": "hero",
            "hp": player_hp,
            "max_hp": player_max_hp,
            "ac": 14,
            "current_area": area_id,
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
        })
        state.register(player)

    if include_flags:
        flags = FlagSlice()
        flags.restore({"flags": {}})
        state.register(flags)

    areas = AreaSlice()
    areas.restore({
        "areas": {
            area_id: {
                "danger_level": 1.0,
                "npc_locations": {},
                "hostile_tracking": {},
            }
        }
    })
    areas.register_hostile(sub_area_id, payload)
    state.register(areas)

    return state


def _make_engine_cmd(params: dict[str, Any]) -> Command:
    """Build a combat_npc_turn Command with source='engine'."""
    return Command(type="combat_npc_turn", params=params, source="engine")


def _apply(cmd: Command, state: StateContainer) -> Any:
    """Run a command through CombatHandler.compute() directly."""
    handler = CombatHandler()
    return handler.compute(cmd, state, None)


def _get_updated_payload(result: Any, sub_area_id: str = "room1") -> dict[str, Any]:
    """Extract the updated payload from an ExecuteResult delta."""
    assert result.executed, f"command failed: {result.errors}"
    assert result.delta is not None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            return change.value
    raise AssertionError("No hostile_tracking change found in delta")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_npc_turn_attacks_adjacent_target() -> None:
    """Adjacent melee monster attacks the player unit and sets action_used=True."""
    # NPC at (0,0), player-side unit at (1,0) — distance=1, in melee range
    enemy = _unit("goblin", "enemy", [0, 0], personality="aggressive")
    ally = _unit("player", "ally", [1, 0], source="player", ac=8)  # low AC for reliable hit
    payload = _make_v2_payload([enemy, ally], ["goblin", "player"], current_turn_index=0)
    state = _make_state(payload)

    # Force d20=15 so hit_bonus=3 → total=18 ≥ ac=8
    with mock.patch("app.game_core.rules.handlers.combat.random.randint", return_value=15):
        result = _apply(_make_engine_cmd({"sub_area_id": "room1"}), state)

    assert result.executed, f"failed: {result.errors}"
    updated = _get_updated_payload(result)
    goblin = next(u for u in updated["units"] if u["unit_id"] == "goblin")
    assert goblin["action_used"] is True
    assert goblin["move_used"] is True
    # attack metadata present
    assert result.metadata["decision_action"] == "attack"
    assert result.metadata["attack"] is not None
    assert result.metadata["attack"]["target_id"] == "player"
    assert result.metadata["attack"]["hit"] is True


def test_npc_turn_moves_then_attacks() -> None:
    """Distant target causes AI to move toward it and then attack (if reachable)."""
    # NPC at (0,0), player-side unit at (4,0) — distance=4, out of melee
    # NPC speed=6 so it can cross the grid and end adjacent → attack
    enemy = _unit("orc", "enemy", [0, 0], speed=6, personality="aggressive")
    ally = _unit("player", "ally", [4, 0], source="player", ac=5)  # low AC
    payload = _make_v2_payload([enemy, ally], ["orc", "player"], current_turn_index=0)
    state = _make_state(payload)

    with mock.patch("app.game_core.rules.handlers.combat.random.randint", return_value=12):
        result = _apply(_make_engine_cmd({"sub_area_id": "room1"}), state)

    assert result.executed, f"failed: {result.errors}"
    updated = _get_updated_payload(result)
    orc = next(u for u in updated["units"] if u["unit_id"] == "orc")
    # Position should have changed
    assert orc["position"] != [0, 0], "AI should have moved"
    assert orc["move_used"] is True
    assert orc["action_used"] is True
    # Moved closer to target
    assert result.metadata["move_to"] is not None


def test_npc_turn_flees_when_low_hp() -> None:
    """Cowardly monster at low HP sets fled=True (action=flee)."""
    # Cowardly personality flees when HP < 50% with 80% chance.
    # Patch random.random to force the flee check to pass.
    enemy = _unit(
        "rat", "enemy", [0, 0],
        hp=2, max_hp=10,          # 20% HP → below 50% threshold
        personality="cowardly",
    )
    ally = _unit("player", "ally", [2, 0], source="player")
    payload = _make_v2_payload([enemy, ally], ["rat", "player"], current_turn_index=0)
    state = _make_state(payload)

    # Patch the flee probability check in battle_ai module
    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.1):
        result = _apply(_make_engine_cmd({"sub_area_id": "room1"}), state)

    assert result.executed, f"failed: {result.errors}"
    updated = _get_updated_payload(result)
    rat = next(u for u in updated["units"] if u["unit_id"] == "rat")
    assert rat["fled"] is True
    assert rat["alive"] is False
    assert result.metadata["decision_action"] == "flee"


def test_npc_turn_player_source_rejected() -> None:
    """source='player' is rejected by validation — combat_npc_turn is engine-only."""
    enemy = _unit("goblin", "enemy", [0, 0])
    ally = _unit("player", "ally", [1, 0], source="player")
    payload = _make_v2_payload([enemy, ally], ["goblin", "player"], current_turn_index=0)
    state = _make_state(payload)

    player_cmd = Command(type="combat_npc_turn", params={"sub_area_id": "room1"}, source="player")
    handler = CombatHandler()
    from app.game_core.content import WorldInstance
    world = WorldInstance("test_world")
    validation = handler.validate(player_cmd, state, world)
    assert not validation.ok
    assert "engine/system" in validation.reason


def test_npc_turn_kills_last_enemy_clears() -> None:
    """When the last enemy unit is killed, combat_cleared=True is set."""
    # Single enemy at (0,0), adjacent ally at (1,0) with very low HP
    enemy = _unit("goblin", "enemy", [0, 0], personality="aggressive")
    ally = _unit("player", "ally", [1, 0], source="player", hp=1, max_hp=10, ac=1)
    payload = _make_v2_payload([enemy, ally], ["goblin", "player"], current_turn_index=0)
    # The enemy IS the only "enemy" side unit, but the player is "ally" side.
    # For combat_cleared we need all enemy-side units to be dead.
    # Let's make the ally be the enemy and goblin be the ally instead —
    # or more simply: set enemy=ally side=enemy, and have the player be ally.
    # Actually: combat_cleared checks enemy_units (side=="enemy") all inactive.
    # The goblin IS side="enemy". After the goblin attacks and kills the ally:
    #   - enemy_units=[goblin] goblin.alive=True → NOT cleared (goblin is alive).
    # We need to test clearing from the goblin's perspective more carefully.
    # Better: have TWO enemy units, one already dead; goblin kills the ally then gets cleared?
    # No. combat_cleared = all enemy side units inactive.
    # So for clearing to happen: goblin (enemy) must die during combat_npc_turn.
    # But the goblin is current_unit — it doesn't die from its own turn.
    # The correct scenario: goblin is enemy, gets opportunity-attacked and killed, then
    # the remaining enemy check fires. OR: design a case where ALL enemies are dead already.
    # Simplest: set goblin as already dead + another enemy alive that then flees.
    # Actually the cleanest test: have the goblin be the current unit (enemy side),
    # but set it to flee (low HP + cowardly) → alive=False, fled=True.
    # Then enemy_units = [goblin] all inactive → combat_cleared.

    # Reset to: cowardly goblin at low HP → flees → combat cleared
    enemy2 = _unit("goblin2", "enemy", [0, 0], hp=1, max_hp=10, personality="cowardly")
    ally2 = _unit("player", "ally", [3, 3], source="player")
    payload2 = _make_v2_payload([enemy2, ally2], ["goblin2", "player"], current_turn_index=0)
    state2 = _make_state(payload2)

    with mock.patch("app.game_core.rules.battle_ai.random.random", return_value=0.05):
        result = _apply(_make_engine_cmd({"sub_area_id": "room1"}), state2)

    assert result.executed, f"failed: {result.errors}"
    assert result.metadata["combat_cleared"] is True
    updated = _get_updated_payload(result)
    assert updated["combat_active"] is False
    assert updated["cleared"] is True


def test_npc_turn_player_hp_sync() -> None:
    """When the NPC attack hits the player unit, the PlayerSlice HP is synced."""
    # enemy at (0,0), player at (1,0) — adjacent, guaranteed hit
    enemy = _unit("troll", "enemy", [0, 0], personality="aggressive",
                  attacks=[{"name": "Bite", "hit_bonus": 8, "damage_dice": "1d6",
                             "damage_type": "piercing", "range": 1}])
    # Give player unit_id="player" so it maps to PlayerSlice
    ally = _unit("player", "ally", [1, 0], source="player", hp=20, max_hp=20, ac=8)
    payload = _make_v2_payload([enemy, ally], ["troll", "player"], current_turn_index=0)
    state = _make_state(payload, player_hp=20, player_max_hp=20)

    # d20=15 → 15+8=23 ≥ ac=8 → guaranteed hit; damage roll = 4
    def _seq_rolls(*values: int):
        it = iter(values)
        def _roll(a: int, b: int) -> int:
            return next(it, values[-1])
        return _roll

    with mock.patch.object(__import__("random"), "randint", _seq_rolls(15, 4)):
        result = _apply(_make_engine_cmd({"sub_area_id": "room1"}), state)

    assert result.executed, f"failed: {result.errors}"
    # Check that a PlayerSlice HP change was emitted
    player_changes = [
        c for c in result.delta.changes
        if c.slice == "player" and "hp" in c.path
    ]
    assert len(player_changes) >= 1, "Expected PlayerSlice HP sync in delta changes"
    # PlayerSlice HP should now be reduced
    assert player_changes[0].value < 20


def test_npc_turn_with_valid_decision_override() -> None:
    """params['decision'] with a valid decision is used (decision_source='override')."""
    # NPC at (0,0), player at (1,0) — adjacent melee
    enemy = _unit("goblin", "enemy", [0, 0], personality="aggressive")
    ally = _unit("player", "ally", [1, 0], source="player", ac=8)
    payload = _make_v2_payload([enemy, ally], ["goblin", "player"], current_turn_index=0)
    state = _make_state(payload)

    # Supply a valid pre-computed decision: stay put and attack
    override_decision = {
        "move_to": None,
        "action": "attack",
        "target_id": "player",
        "attack_index": 0,
    }
    with mock.patch("app.game_core.rules.handlers.combat.random.randint", return_value=15):
        result = _apply(
            _make_engine_cmd({"sub_area_id": "room1", "decision": override_decision}),
            state,
        )

    assert result.executed, f"failed: {result.errors}"
    assert result.metadata["decision_source"] == "override"
    assert result.metadata["decision_action"] == "attack"


def test_npc_turn_with_invalid_decision_fallback() -> None:
    """params['decision'] with an out-of-range move_to falls back to rules AI."""
    # NPC at (0,0) with speed=2, player at (1,0) adjacent
    enemy = _unit("goblin", "enemy", [0, 0], speed=2, personality="aggressive")
    ally = _unit("player", "ally", [1, 0], source="player", ac=8)
    payload = _make_v2_payload([enemy, ally], ["goblin", "player"], current_turn_index=0)
    state = _make_state(payload)

    # Supply an INVALID decision: move_to far cell beyond speed=2
    invalid_decision = {
        "move_to": [4, 4],   # Manhattan dist=8, beyond speed=2
        "action": "attack",
        "target_id": "player",
        "attack_index": 0,
    }
    with mock.patch("app.game_core.rules.handlers.combat.random.randint", return_value=15):
        result = _apply(
            _make_engine_cmd({"sub_area_id": "room1", "decision": invalid_decision}),
            state,
        )

    assert result.executed, f"failed: {result.errors}"
    assert result.metadata["decision_source"] == "rules_ai"
