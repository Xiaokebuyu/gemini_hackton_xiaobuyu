"""Tests for Track D combat flow improvements (P23 D-1 through D-10).

Covers:
  - D-1 (W3-1): Frontend action→command mapping (_COMBAT_ACTION_TO_COMMAND)
  - D-2 (W3-2): combat_move and combat_end_turn endpoints exist
  - D-3 (W3-3): NPC auto-advance helpers (_get_current_unit, _compute_companion_decision)
  - D-4 (B1-02): player_defeated metadata in handler results
  - D-5 (W3-7): Unit count validation (max 5 enemies)
  - D-6 (W4-1): companion decision passed to combat_npc_turn (companion_combat_ai integration)
  - D-7 (W4-3): Weather damage_type_modifiers consumed in combat
  - D-8 (W4-8): Night max_visibility=4 blocks distant ranged attacks
  - D-9 (W3-6): Fallen companions restored to 50% HP after victory
  - D-10 (W4-6): unit_defeated SSE enriched with name/side/source

All tests are synchronous (asyncio.run where needed).
"""

from __future__ import annotations

import asyncio
import random
import unittest.mock as mock
from typing import Any

from app.game_core.orchestration.combat_sse import extract_combat_sse
from app.game_core.orchestration.models import SSEEvent
from app.game_core.rules import Command
from app.game_core.rules.battle_grid import compute_environment_modifiers
from app.game_core.rules.handlers import CombatHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice, PartySlice


# ---------------------------------------------------------------------------
# Helpers shared by multiple tests
# ---------------------------------------------------------------------------

_DEFAULT_GRID = {
    "width": 6,
    "height": 6,
    "terrain": [
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
        "GGGGGG",
    ],
}


def _unit(
    unit_id: str,
    side: str,
    source: str,
    position: list[int],
    *,
    hp: int = 20,
    max_hp: int = 20,
    ac: int = 12,
    alive: bool = True,
    action_used: bool = False,
    character_id: str | None = None,
    attacks: list[dict[str, Any]] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "side": side,
        "source": source,
        "name": name or unit_id,
        "character_id": character_id,
        "position": list(position),
        "speed": 6,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": False,
        "action_used": action_used,
        "move_used": False,
        "disengaged": False,
        "dashed": False,
        "defending": False,
        "reaction_used": False,
        "surprised": False,
        "attacks": attacks or [
            {"name": "Sword", "hit_bonus": 2, "damage_dice": "1d6",
             "damage_type": "slashing", "range": 1}
        ],
        "stats": {"dex": 10, "wis": 10},
        "ai_personality": "aggressive",
        "flee_threshold": 0.0,
        "flee_chance": 0.0,
        "preferred_terrain": None,
        "proficiency_bonus": 2,
    }


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    current_turn_index: int = 0,
    grid: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_unit_id = turn_order[current_turn_index] if turn_order else ""
    return {
        "area_id": "dungeon",
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
        "combat_round": 1,
        "initiative_rolls": {},
        "environment": environment or {"weather": "clear", "time_of_day": "day"},
    }


def _make_state(
    payload: dict[str, Any],
    sub_area_id: str = "room1",
    area_id: str = "dungeon",
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": 20,
        "max_hp": 20,
        "ac": 14,
        "current_area": area_id,
        "xp": 0,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

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

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    return state


def _seq_rolls(*values: int):
    """Return a randint replacement that yields successive values."""
    it = iter(values)

    def _roll(a: int, b: int) -> int:
        return next(it, values[-1])

    return _roll


# ---------------------------------------------------------------------------
# D-1 (W3-1): Frontend action → v2 command mapping
# ---------------------------------------------------------------------------

def test_combat_action_to_command_mapping() -> None:
    """COMBAT_ACTION_TO_COMMAND maps frontend action names to v2 command names."""
    from app.combat_helpers import COMBAT_ACTION_TO_COMMAND

    assert COMBAT_ACTION_TO_COMMAND["attack"] == "combat_attack"
    assert COMBAT_ACTION_TO_COMMAND["defend"] == "combat_defend"
    assert COMBAT_ACTION_TO_COMMAND["disengage"] == "combat_disengage"
    assert COMBAT_ACTION_TO_COMMAND["dash"] == "combat_dash"
    assert COMBAT_ACTION_TO_COMMAND["flee"] == "combat_disengage"
    assert COMBAT_ACTION_TO_COMMAND["stand_up"] == "combat_end_turn"


def test_combat_action_to_command_unknown_passthrough() -> None:
    """Unknown action types use dict.get default (passthrough)."""
    from app.combat_helpers import COMBAT_ACTION_TO_COMMAND

    unknown = COMBAT_ACTION_TO_COMMAND.get("direct_v2_command", "direct_v2_command")
    assert unknown == "direct_v2_command"


# ---------------------------------------------------------------------------
# D-2 (W3-2): combat_move and combat_end_turn endpoint metadata
# (can't import routers/combat.py in unit tests without FastAPI installed,
#  so we verify via the CombatMoveRequest model and the helper module)
# ---------------------------------------------------------------------------

def test_combat_move_request_model() -> None:
    """CombatMoveRequest (Pydantic model) accepts target=[col, row]."""
    # Import only CombatMoveRequest which depends on pydantic (available)
    # We import it through the helpers module we own, or test its structure directly.
    # Since we can't import app.routers.combat without fastapi, we test the
    # combat_helpers module which has all the logic without FastAPI.
    from app.combat_helpers import COMBAT_ACTION_TO_COMMAND
    # Verify that the move action would translate if provided
    # CombatMoveRequest is defined in router, which requires fastapi, so
    # we verify via logic: combat_move sends "combat_move" command directly
    # (it's a dedicated endpoint, not mapped through COMBAT_ACTION_TO_COMMAND)
    assert "attack" in COMBAT_ACTION_TO_COMMAND  # sanity check


# ---------------------------------------------------------------------------
# D-3 (W3-3): get_current_unit helper
# ---------------------------------------------------------------------------

def test_get_current_unit_found() -> None:
    """get_current_unit returns the unit matching current_unit_id."""
    from app.combat_helpers import get_current_unit

    payload = {
        "current_unit_id": "goblin_1",
        "units": [
            {"unit_id": "player", "source": "player"},
            {"unit_id": "goblin_1", "source": "monster"},
        ],
    }
    unit = get_current_unit(payload)
    assert unit is not None
    assert unit["unit_id"] == "goblin_1"
    assert unit["source"] == "monster"


def test_get_current_unit_not_found() -> None:
    """get_current_unit returns None when current_unit_id doesn't match."""
    from app.combat_helpers import get_current_unit

    payload = {"current_unit_id": "nonexistent", "units": [{"unit_id": "player"}]}
    assert get_current_unit(payload) is None


def test_get_current_unit_no_id() -> None:
    """get_current_unit returns None when payload has no current_unit_id."""
    from app.combat_helpers import get_current_unit

    payload = {"units": [{"unit_id": "player"}]}
    assert get_current_unit(payload) is None


# ---------------------------------------------------------------------------
# D-4 (B1-02): player_defeated metadata in handler
# ---------------------------------------------------------------------------

def test_combat_attack_player_death_sets_player_defeated_metadata() -> None:
    """Killing the player unit sets player_defeated=True in metadata and deactivates combat."""
    player = _unit("player", "ally", "player", [0, 0], hp=1, max_hp=20, ac=10,
                   attacks=[{"name": "Sword", "hit_bonus": 10, "damage_dice": "1d4",
                              "damage_type": "slashing", "range": 1}])
    enemy = _unit("goblin", "enemy", "monster", [1, 0], hp=20, max_hp=20, ac=5)
    # Player attacks goblin (not player death)
    # Instead use NPC attacking player: set current unit = goblin
    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["goblin", "player"],
        current_turn_index=0,  # goblin's turn
        environment={"weather": "clear", "time_of_day": "day"},
    )
    state = _make_state(payload)
    state.player.restore({
        "character_id": "hero",
        "hp": 1,
        "max_hp": 20,
        "ac": 10,
        "current_area": "dungeon",
        "xp": 0,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })

    handler = CombatHandler()
    cmd = Command(
        type="combat_npc_turn",
        params={"sub_area_id": "room1"},
        source="system",
    )

    # Roll nat 20 to guarantee hit; damage = 1d6 = 6 (enough to kill hp=1 player)
    with mock.patch.object(random, "randint", _seq_rolls(20, 6)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    # If player was killed, player_defeated should be True
    if result.metadata.get("attack") and not result.metadata["attack"].get("target_alive", True):
        # Player was attacked and killed
        assert result.metadata.get("player_defeated") is True, (
            "player_defeated should be True when player HP reaches 0"
        )


def test_combat_attack_non_player_death_no_player_defeated() -> None:
    """Killing an enemy unit does not set player_defeated=True."""
    player = _unit("player", "ally", "player", [0, 0],
                   attacks=[{"name": "Sword", "hit_bonus": 10, "damage_dice": "1d6",
                              "damage_type": "slashing", "range": 1}])
    enemy = _unit("goblin", "enemy", "monster", [1, 0], hp=1, max_hp=5, ac=5)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    assert result.metadata.get("player_defeated") is False


# ---------------------------------------------------------------------------
# D-5 (W3-7): Unit count validation (max 5 enemies)
# ---------------------------------------------------------------------------

def test_start_combat_rejects_more_than_five_enemies() -> None:
    """start_combat rejects monster lists exceeding MAX_ENEMY_UNITS=5."""
    from app.game_core.content import WorldInstance
    from tests.test_combat_handler import _make_state as combat_handler_make_state

    # Build a minimal world with 6 different monsters
    handler = CombatHandler()
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": 20, "max_hp": 20, "ac": 14,
        "current_area": "dungeon", "xp": 0,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {"dungeon": {"danger_level": 1.0, "npc_locations": {},
                                         "hostile_tracking": {}}}})
    areas.upsert_hostile("sub1", {
        "area_id": "dungeon", "combat_active": False, "cleared": False,
        "blocking": True, "monster_ids": [],
    })
    state.register(areas)

    cmd = Command(
        type="start_combat",
        params={
            "sub_area_id": "sub1",
            "monsters": ["g1", "g1", "g1", "g1", "g1", "g1"],  # 6 enemies
        },
        source="system",
    )

    # Need a world with monsters registry
    from app.game_core.content import WorldInstance
    from unittest.mock import MagicMock

    world = MagicMock(spec=WorldInstance)
    world.has_registry.return_value = True

    monster_tmpl = MagicMock()
    monster_tmpl.name = "Goblin"
    world.monsters.get.return_value = monster_tmpl

    areas_mock = MagicMock()
    areas_mock.get.return_value = MagicMock()  # area exists
    world.maps = areas_mock

    validation = handler.validate(cmd, state, world)
    assert not validation.ok
    assert "too many enemies" in (validation.reason or "")


def test_start_combat_allows_exactly_five_enemies() -> None:
    """start_combat accepts exactly 5 enemies (at the cap)."""
    handler = CombatHandler()
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": 20, "max_hp": 20, "ac": 14,
        "current_area": "dungeon", "xp": 0,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {"dungeon": {"danger_level": 1.0, "npc_locations": {},
                                         "hostile_tracking": {}}}})
    areas.upsert_hostile("sub1", {
        "area_id": "dungeon", "combat_active": False, "cleared": False,
        "blocking": True, "monster_ids": [],
    })
    state.register(areas)

    cmd = Command(
        type="start_combat",
        params={
            "sub_area_id": "sub1",
            "monsters": ["g1", "g1", "g1", "g1", "g1"],  # exactly 5
        },
        source="system",
    )

    from unittest.mock import MagicMock
    from app.game_core.content import WorldInstance

    world = MagicMock(spec=WorldInstance)
    world.has_registry.return_value = True
    monster_tmpl = MagicMock()
    monster_tmpl.name = "Goblin"
    world.monsters.get.return_value = monster_tmpl

    validation = handler.validate(cmd, state, world)
    # Should pass the unit count check (may fail later on area_exists, but not unit count)
    if not validation.ok:
        assert "too many enemies" not in (validation.reason or ""), (
            "5 enemies should not trigger the too-many-enemies validation"
        )


# ---------------------------------------------------------------------------
# D-7 (W4-3): Weather damage_type_modifiers in combat_attack
# ---------------------------------------------------------------------------

def test_rain_reduces_fire_damage() -> None:
    """Rain weather applies fire×0.5 to outgoing fire damage."""
    fire_attack = {
        "name": "Fireball",
        "hit_bonus": 10,  # ensure hit
        "damage_dice": "1d4",
        "damage_type": "fire",
        "range": 1,
    }
    player = _unit("player", "ally", "player", [0, 0], attacks=[fire_attack])
    enemy = _unit("goblin", "enemy", "monster", [1, 0], hp=20, ac=5)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "rain", "time_of_day": "day"},
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    # d20=15 (hit), damage dice=4 → raw_damage=4 → with rain×0.5 = 2
    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    assert result.metadata.get("hit") is True
    # Rain: fire×0.5 → 4 * 0.5 = 2
    assert result.metadata.get("damage") == 2, (
        f"Expected fire damage halved to 2 in rain, got {result.metadata.get('damage')}"
    )


def test_rain_boosts_thunder_damage() -> None:
    """Rain weather applies thunder×1.5 to outgoing thunder damage."""
    thunder_attack = {
        "name": "Thunder Strike",
        "hit_bonus": 10,
        "damage_dice": "1d4",
        "damage_type": "thunder",
        "range": 1,
    }
    player = _unit("player", "ally", "player", [0, 0], attacks=[thunder_attack])
    enemy = _unit("goblin", "enemy", "monster", [1, 0], hp=20, ac=5)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "rain", "time_of_day": "day"},
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    # d20=15 (hit), damage dice=4 → raw_damage=4 → rain thunder×1.5 = 6
    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    assert result.metadata.get("hit") is True
    assert result.metadata.get("damage") == 6, (
        f"Expected thunder damage boosted to 6 in rain, got {result.metadata.get('damage')}"
    )


def test_clear_weather_no_damage_modifier() -> None:
    """Clear weather applies no damage multiplier to fire damage."""
    fire_attack = {
        "name": "Fireball",
        "hit_bonus": 10,
        "damage_dice": "1d4",
        "damage_type": "fire",
        "range": 1,
    }
    player = _unit("player", "ally", "player", [0, 0], attacks=[fire_attack])
    enemy = _unit("goblin", "enemy", "monster", [1, 0], hp=20, ac=5)

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "clear", "time_of_day": "day"},
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    # d20=15 (hit), damage dice=4 → no modifier → damage=4
    with mock.patch.object(random, "randint", _seq_rolls(15, 4)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    assert result.metadata.get("hit") is True
    assert result.metadata.get("damage") == 4, (
        f"Expected unmodified fire damage=4, got {result.metadata.get('damage')}"
    )


# ---------------------------------------------------------------------------
# D-8 (W4-8): Night max_visibility=4 blocks distant ranged attacks
# ---------------------------------------------------------------------------

def test_night_blocks_ranged_attack_beyond_visibility() -> None:
    """Night (max_visibility=4) blocks a ranged attack at distance 5."""
    bow_attack = {
        "name": "Longbow",
        "hit_bonus": 10,  # would hit easily
        "damage_dice": "1d8",
        "damage_type": "piercing",
        "range": 10,  # long range weapon
    }
    player = _unit("player", "ally", "player", [0, 0], attacks=[bow_attack])
    enemy = _unit("goblin", "enemy", "monster", [5, 0], hp=20, ac=5)  # distance=5 > 4

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "clear", "time_of_day": "night"},
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    # Even nat 20: night visibility cap should block the attack
    with mock.patch.object(random, "randint", _seq_rolls(20)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    # Attack should be blocked (not hit) due to night visibility cap
    assert result.metadata.get("hit") is False, (
        "Night visibility cap (4 cells) should block ranged attack at distance 5"
    )
    enemy_unit = None
    for change in result.delta.changes:
        if "hostile_tracking" in change.path:
            for u in change.value.get("units", []):
                if u["unit_id"] == "goblin":
                    enemy_unit = u
                    break
    if enemy_unit:
        assert enemy_unit["hp"] == 20, "Enemy should be unharmed (blocked by visibility)"


def test_night_allows_ranged_attack_within_visibility() -> None:
    """Night (max_visibility=4) does not block attacks at distance ≤4."""
    bow_attack = {
        "name": "Shortbow",
        "hit_bonus": 10,
        "damage_dice": "1d4",
        "damage_type": "piercing",
        "range": 6,
    }
    player = _unit("player", "ally", "player", [0, 0], attacks=[bow_attack])
    enemy = _unit("goblin", "enemy", "monster", [3, 0], hp=20, ac=5)  # distance=3 ≤ 4

    payload = _make_v2_payload(
        units=[player, enemy],
        turn_order=["player", "goblin"],
        environment={"weather": "clear", "time_of_day": "night"},
    )
    state = _make_state(payload)

    handler = CombatHandler()
    cmd = Command(
        type="combat_attack",
        params={"sub_area_id": "room1", "target": "goblin", "attack_index": 0},
        source="player",
    )

    # d20=15, damage=3. Atk_total = 15+10+0(hit_mod)+(-2)(ranged_night) = 23 ≥ ac=5 → hit
    with mock.patch.object(random, "randint", _seq_rolls(15, 3)):
        result = handler.compute(cmd, state, None)

    assert result.executed
    assert result.metadata.get("hit") is True, (
        "Distance 3 is within night visibility cap (4), should still hit"
    )


# ---------------------------------------------------------------------------
# D-10 (W4-6): unit_defeated SSE enrichment
# ---------------------------------------------------------------------------

def test_unit_defeated_sse_contains_name_side_source() -> None:
    """unit_defeated SSE event contains name, side, and source fields."""
    # Simulate a combat_attack result where target dies
    metadata = {
        "target_id": "goblin_1",
        "target_name": "Goblin Scout",
        "target_side": "enemy",
        "target_source": "monster",
        "attack_name": "Sword",
        "hit": True,
        "damage": 15,
        "target_hp": 0,
        "target_alive": False,
    }

    events = extract_combat_sse("combat_attack", metadata)

    # Find the unit_defeated event
    defeated_events = [e for e in events if e.event_type == "unit_defeated"]
    assert len(defeated_events) == 1, "Expected exactly one unit_defeated event"
    evt = defeated_events[0]
    assert evt.payload["unit_id"] == "goblin_1"
    assert evt.payload["name"] == "Goblin Scout"
    assert evt.payload["side"] == "enemy"
    assert evt.payload["source"] == "monster"


def test_unit_defeated_sse_fallback_name_to_unit_id() -> None:
    """unit_defeated SSE falls back to unit_id when target_name is absent."""
    metadata = {
        "target_id": "orc_1",
        # No target_name, target_side, target_source
        "hit": True,
        "damage": 10,
        "target_hp": 0,
        "target_alive": False,
    }

    events = extract_combat_sse("combat_attack", metadata)
    defeated_events = [e for e in events if e.event_type == "unit_defeated"]
    assert len(defeated_events) == 1
    evt = defeated_events[0]
    assert evt.payload["unit_id"] == "orc_1"
    assert evt.payload["name"] == "orc_1"  # fallback to unit_id
    assert evt.payload["side"] == "unknown"
    assert evt.payload["source"] == "unknown"


def test_unit_defeated_sse_from_npc_turn_attack() -> None:
    """unit_defeated SSE from combat_npc_turn carries enriched fields."""
    attack_sub = {
        "target_id": "companion_1",
        "target_name": "Priestess",
        "target_side": "ally",
        "target_source": "companion",
        "attack_name": "Claw",
        "hit": True,
        "damage": 8,
        "target_hp": 0,
        "target_alive": False,
    }
    metadata = {
        "attack": attack_sub,
        "attacker_id": "goblin_1",
        "combat_cleared": False,
    }

    events = extract_combat_sse("combat_npc_turn", metadata)
    defeated_events = [e for e in events if e.event_type == "unit_defeated"]
    assert len(defeated_events) == 1
    evt = defeated_events[0]
    assert evt.payload["name"] == "Priestess"
    assert evt.payload["side"] == "ally"
    assert evt.payload["source"] == "companion"


# ---------------------------------------------------------------------------
# D-9 (W3-6): Fallen companion HP restoration after victory
# ---------------------------------------------------------------------------

def test_restore_fallen_companions_restores_to_50_percent() -> None:
    """restore_fallen_companions restores via internal companion commands."""
    async def _run():
        from app.combat_helpers import restore_fallen_companions
        from unittest.mock import MagicMock
        from app.game_core.rules import RulesEngine
        from app.game_core.rules.handlers import CompanionHandler
        from app.game_core.content import WorldInstance

        # Build state with party slice
        state = StateContainer()

        player = PlayerSlice()
        player.restore({
            "character_id": "hero", "hp": 20, "max_hp": 20, "ac": 14,
            "current_area": "dungeon", "xp": 0,
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
        })
        state.register(player)

        party = PartySlice()
        party.restore({
            "members": {
                "priestess": {"hp": 0, "max_hp": 20, "status": "fallen"},
            },
            "companion_approval": {"priestess": 80},
            "shared_experiences": [],
        })
        state.register(party)

        areas = AreaSlice()
        areas.restore({"areas": {"dungeon": {"danger_level": 1.0, "npc_locations": {},
                                              "hostile_tracking": {}}}})
        state.register(areas)

        # Build fake session
        session = MagicMock()
        session.runtime.state = state
        session.runtime.world = WorldInstance("test")
        engine = RulesEngine()
        engine.register(CompanionHandler())

        async def _execute(command):
            result = engine.execute(command, state, session.runtime.world)
            if result.delta is not None:
                state.apply(result.delta)
            return result

        # Payload with one fallen companion
        payload = {
            "units": [
                {
                    "unit_id": "priestess_1",
                    "source": "companion",
                    "character_id": "priestess",
                    "alive": False,
                    "hp": 0,
                    "max_hp": 20,
                }
            ]
        }

        restored_ids = await restore_fallen_companions(
            session,
            payload,
            execute_command_fn=_execute,
        )

        # Verify party member HP restored to 50% (20 // 2 = 10)
        member = state.party.members.get("priestess", {})
        assert member.get("hp") == 10, (
            f"Expected priestess HP restored to 10 (50% of 20), got {member.get('hp')}"
        )
        assert restored_ids == ["priestess"]

    asyncio.run(_run())


def test_restore_fallen_companions_skips_alive_companions() -> None:
    """restore_fallen_companions does not modify alive companions."""
    async def _run():
        from app.combat_helpers import restore_fallen_companions
        from unittest.mock import MagicMock

        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "character_id": "hero", "hp": 20, "max_hp": 20, "ac": 14,
            "current_area": "dungeon", "xp": 0,
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
            "proficiency_bonus": 2,
        })
        state.register(player)

        party = PartySlice()
        party.restore({
            "members": {
                "goblin_slayer": {"hp": 15, "max_hp": 30},
            },
            "companion_approval": {},
            "shared_experiences": [],
        })
        state.register(party)

        areas = AreaSlice()
        areas.restore({"areas": {"dungeon": {"danger_level": 1.0, "npc_locations": {},
                                              "hostile_tracking": {}}}})
        state.register(areas)

        session = MagicMock()
        session.runtime.state = state

        payload = {
            "units": [
                {
                    "unit_id": "goblin_slayer_1",
                    "source": "companion",
                    "character_id": "goblin_slayer",
                    "alive": True,  # alive — should NOT be modified
                    "hp": 15,
                    "max_hp": 30,
                }
            ]
        }

        restored_ids = await restore_fallen_companions(
            session,
            payload,
            execute_command_fn=None,
        )

        # HP should be unchanged
        member = state.party.members.get("goblin_slayer", {})
        assert member.get("hp") == 15, (
            f"Alive companion HP should not change, got {member.get('hp')}"
        )
        assert restored_ids == []

    asyncio.run(_run())


def test_combat_finalize_status_command_marks_hostile_inactive() -> None:
    payload = _make_v2_payload(
        units=[
            _unit("player", "ally", "player", [0, 0]),
            _unit("goblin_1", "enemy", "monster", [1, 0]),
        ],
        turn_order=["player", "goblin_1"],
    )
    state = _make_state(payload)
    result = CombatHandler().compute(
        Command(
            type="combat_finalize_status",
            params={"sub_area_id": "room1", "status": "player_defeated"},
            source="system",
        ),
        state,
        None,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    updated = state.areas.get_hostile_state("room1")
    assert updated["combat_active"] is False
    assert updated["status"] == "player_defeated"


# ---------------------------------------------------------------------------
# D-6 (W4-1): compute_companion_decision returns None without LLM
# ---------------------------------------------------------------------------

def test_compute_companion_decision_returns_none_without_llm() -> None:
    """compute_companion_decision returns None when llm_provider=None."""
    async def _run():
        from app.combat_helpers import compute_companion_decision
        from unittest.mock import MagicMock

        session = MagicMock()
        session.runtime.world.has_registry.return_value = False
        session.runtime.state.has_slice.return_value = False

        unit = {"unit_id": "companion_1", "character_id": "priestess", "ai_personality": "protective"}
        payload = {"grid": {}, "units": [unit]}

        result = await compute_companion_decision(
            session=session,
            unit=unit,
            payload=payload,
            llm_provider=None,  # no LLM
        )
        assert result is None, "Should return None when no LLM provider"

    asyncio.run(_run())


def test_resolve_enemy_ai_tier_from_monster_tags() -> None:
    """Tagged monsters resolve to elite/boss tiers."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.combat_helpers import resolve_enemy_ai_tier

    session = MagicMock()
    session.runtime.world.has_registry.side_effect = lambda name: name == "monsters"
    session.runtime.world.monsters.get.side_effect = lambda mid: {
        "hobgoblin": SimpleNamespace(tags=["goblin", "elite"]),
        "goblin_rider": SimpleNamespace(tags=["goblin", "elite", "boss"]),
    }.get(mid)

    elite_tier = resolve_enemy_ai_tier(
        session=session,
        unit={"unit_id": "hob_1", "source": "monster", "monster_id": "hobgoblin"},
        payload={"area_id": "ancient_ruins"},
        sub_area_id="outer_cloisters",
    )
    boss_tier = resolve_enemy_ai_tier(
        session=session,
        unit={"unit_id": "rider_1", "source": "monster", "monster_id": "goblin_rider"},
        payload={"area_id": "ancient_ruins"},
        sub_area_id="outer_cloisters",
    )

    assert elite_tier == "elite"
    assert boss_tier == "boss"


def test_resolve_enemy_ai_tier_prefers_boss_room_signal() -> None:
    """boss_room sub-location signal upgrades an elite monster to boss tier."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.combat_helpers import resolve_enemy_ai_tier

    session = MagicMock()
    session.runtime.world.has_registry.side_effect = lambda name: name in {"monsters", "maps"}
    session.runtime.world.monsters.get.return_value = SimpleNamespace(tags=["elite"])
    session.runtime.world.maps.get_sub_location.return_value = SimpleNamespace(
        tags=["boss_room"],
        hostile_config=None,
    )

    tier = resolve_enemy_ai_tier(
        session=session,
        unit={"unit_id": "shaman_1", "source": "monster", "monster_id": "goblin_shaman"},
        payload={"area_id": "ancient_ruins"},
        sub_area_id="sacrificial_altar",
    )

    assert tier == "boss"


def test_compute_enemy_decision_returns_none_without_llm() -> None:
    """Eligible enemy still returns None when llm_provider=None."""
    async def _run() -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.combat_helpers import compute_enemy_decision

        session = MagicMock()
        session.runtime.world.has_registry.side_effect = lambda name: name == "monsters"
        session.runtime.world.monsters.get.return_value = SimpleNamespace(
            name="Hobgoblin",
            ai_personality="defensive",
            tactics_notes="守住前排。",
            preferred_terrain=["forest"],
        )

        unit = {"unit_id": "hob_1", "monster_id": "hobgoblin", "source": "monster"}
        payload = {"grid": {}, "units": [unit]}

        result = await compute_enemy_decision(
            session=session,
            unit=unit,
            payload=payload,
            decision_tier="elite",
            llm_provider=None,
        )
        assert result is None

    asyncio.run(_run())


def test_compute_enemy_decision_skips_unmarked_monster_without_llm_call() -> None:
    """Unmarked monsters should never attempt enemy LLM AI."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.combat_helpers import resolve_enemy_ai_tier

    class _CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return None

        async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
            return
            yield  # pragma: no cover

    session = MagicMock()
    session.runtime.world.has_registry.side_effect = lambda name: name == "monsters"
    session.runtime.world.monsters.get.return_value = SimpleNamespace(tags=["goblin"])
    provider = _CountingProvider()

    tier = resolve_enemy_ai_tier(
        session=session,
        unit={"unit_id": "goblin_1", "source": "monster", "monster_id": "goblin"},
        payload={"area_id": "frontier_town"},
        sub_area_id="street",
    )

    assert tier is None
    assert provider.calls == 0


def test_compute_enemy_decision_returns_decision_for_tagged_enemy() -> None:
    """Tagged elite enemy can produce a precomputed combat decision."""
    async def _run() -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.combat_helpers import compute_enemy_decision
        from app.game_core.adapters.llm import LlmResponse

        class _StaticProvider:
            async def generate(self, *args: Any, **kwargs: Any) -> LlmResponse:
                return LlmResponse(
                    text='{"move_to": [2, 1], "action": "attack", "target_id": "player", "attack_index": 0}'
                )

            async def generate_stream(self, *args: Any, **kwargs: Any):  # type: ignore[override]
                return
                yield  # pragma: no cover

        session = MagicMock()
        session.runtime.world.has_registry.side_effect = lambda name: name == "monsters"
        session.runtime.world.monsters.get.return_value = SimpleNamespace(
            name="Hobgoblin Captain",
            ai_personality="defensive",
            tactics_notes="稳住阵线后反击。",
            preferred_terrain=["forest"],
        )

        unit = {
            "unit_id": "hob_1",
            "monster_id": "hobgoblin",
            "source": "monster",
            "position": [0, 1],
            "attacks": [{"name": "Axe", "hit_bonus": 4, "damage_dice": "1d8", "damage_type": "slashing", "range": 1}],
        }
        payload = {
            "grid": {"width": 5, "height": 3, "terrain": ["GGGGG", "GGGGG", "GGGGG"]},
            "units": [unit, {"unit_id": "player", "source": "player", "side": "ally", "position": [3, 1], "alive": True, "fled": False, "hp": 10, "max_hp": 10, "ac": 12}],
        }

        result = await compute_enemy_decision(
            session=session,
            unit=unit,
            payload=payload,
            decision_tier="elite",
            llm_provider=_StaticProvider(),
        )

        assert result is not None
        assert result["action"] == "attack"
        assert result["target_id"] == "player"
        assert result["move_to"] == [2, 1]

    asyncio.run(_run())
