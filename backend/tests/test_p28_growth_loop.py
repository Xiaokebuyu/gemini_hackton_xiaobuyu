"""Tests for P28 Wave 2 Track A — growth loop backend.

Covers:
- _compute_combat_loot(): loot_table + gold_drop mechanics
- CombatHandler.combat_attack: metadata contains loot_items + gold_dropped
- CombatHandler.combat_attack: inventory/gold StateChanges applied
- XpAdvancementHook: xp >= threshold triggers level_up + SSE
- Planner _SYSTEM_PROMPT: rewards guidance present

Decision record: P28 §4-1, §4-2, §4-4.
"""

from __future__ import annotations

import asyncio
import random
import unittest.mock as mock
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.monsters import LootEntry, MonsterTemplate
from app.game_core.orchestration.hooks.xp_advancement import XpAdvancementHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import CombatHandler, GrowthHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer, StateDelta
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice, SceneSlice


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────

_DEFAULT_GRID = {
    "width": 6,
    "height": 6,
    "terrain": ["GGGGGG"] * 6,
}


def _unit(
    unit_id: str,
    side: str,
    position: list[int],
    *,
    hp: int = 10,
    max_hp: int = 10,
    ac: int = 5,
    alive: bool = True,
    fled: bool = False,
    action_used: bool = False,
    monster_id: str | None = None,
    attacks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    u: dict[str, Any] = {
        "unit_id": unit_id,
        "side": side,
        "source": "monster",
        "position": list(position),
        "speed": 6,
        "hp": hp,
        "max_hp": max_hp,
        "ac": ac,
        "alive": alive,
        "fled": fled,
        "action_used": action_used,
        "move_used": False,
        "disengaged": False,
        "dashed": False,
        "defending": False,
        "reaction_used": False,
        "surprised": False,
        "attacks": attacks or [
            {"name": "Claw", "hit_bonus": 10, "damage_dice": "1d4",
             "damage_type": "slashing", "range": 1},
        ],
        "stats": {"dex": 10, "wis": 10},
    }
    if monster_id is not None:
        u["monster_id"] = monster_id
    return u


def _make_v2_payload(
    units: list[dict[str, Any]],
    turn_order: list[str],
    area_id: str = "forest",
    sub_area_id: str = "combat_1",
) -> dict[str, Any]:
    return {
        "area_id": area_id,
        "version": 2,
        "combat_active": True,
        "cleared": False,
        "blocking": True,
        "status": "engaged",
        "units": [dict(u) for u in units],
        "grid": _DEFAULT_GRID,
        "turn_order": list(turn_order),
        "current_turn_index": 0,
        "current_unit_id": turn_order[0] if turn_order else "",
        "combat_round": 1,
        "initiative_rolls": {},
        "monster_ids": [u["monster_id"] for u in units if u.get("monster_id")],
    }


def _make_combat_state(
    payload: dict[str, Any],
    sub_area_id: str = "combat_1",
    area_id: str = "forest",
    player_gold: int = 0,
    player_inventory: list[dict[str, Any]] | None = None,
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
        "gold": player_gold,
        "inventory": player_inventory or [],
        "stats": {"str": 14, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {area_id: {"danger_level": 1.0, "npc_locations": {}, "hostile_tracking": {}}}})
    areas.register_hostile(sub_area_id, payload)
    state.register(areas)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    return state


def _seq_rolls(*values: int):
    it = iter(values)
    def _roll(a: int, b: int) -> int:
        return next(it, values[-1])
    return _roll


def _make_world_with_monster(
    monster_id: str,
    loot_table: list[LootEntry] | None = None,
    gold_drop: str = "0",
) -> WorldInstance:
    """Create a WorldInstance with a single MonsterRegistry containing one monster."""
    from app.game_core.content.registries.monsters import MonsterRegistry

    world = WorldInstance("test_world")
    registry = MonsterRegistry()
    # Inject the template directly into the registry's internal store
    template = MonsterTemplate(
        id=monster_id,
        name=monster_id,
        hp=10,
        max_hp=10,
        ac=5,
        xp_reward=50,
        loot_table=loot_table or [],
        gold_drop=gold_drop,
    )
    registry._items[monster_id] = template  # type: ignore[attr-defined]
    world.register(registry)
    return world


# ─────────────────────────────────────────────────────────────
# TestComputeCombatLoot
# ─────────────────────────────────────────────────────────────


class TestComputeCombatLoot:
    def test_drops_item_when_chance_hits(self) -> None:
        """Monster with loot_table entry → item dropped when roll succeeds."""
        handler = CombatHandler()
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
        )
        participants = [
            {"monster_id": "goblin", "alive": False, "fled": False},
        ]
        items, gold = handler._compute_combat_loot(participants, world)
        assert items == [("cheap_shortsword", 1)]
        assert gold == 0

    def test_gold_drop_dice_rolled(self) -> None:
        """Monster with gold_drop dice expression → gold awarded."""
        handler = CombatHandler()
        world = _make_world_with_monster("goblin", gold_drop="1d4")
        participants = [
            {"monster_id": "goblin", "alive": False, "fled": False},
        ]
        _, gold = handler._compute_combat_loot(participants, world)
        assert gold >= 1

    def test_no_loot_table_returns_empty(self) -> None:
        """Monster without loot_table → no items, no gold."""
        handler = CombatHandler()
        world = _make_world_with_monster("goblin")
        participants = [{"monster_id": "goblin", "alive": False, "fled": False}]
        items, gold = handler._compute_combat_loot(participants, world)
        assert items == []
        assert gold == 0

    def test_fled_monster_yields_no_loot(self) -> None:
        """Fled monster (fled=True) → skipped by loot computation."""
        handler = CombatHandler()
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
            gold_drop="10",
        )
        participants = [
            {"monster_id": "goblin", "alive": False, "fled": True},
        ]
        items, gold = handler._compute_combat_loot(participants, world)
        assert items == []
        assert gold == 0

    def test_alive_monster_yields_no_loot(self) -> None:
        """Alive monster → skipped."""
        handler = CombatHandler()
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
        )
        participants = [{"monster_id": "goblin", "alive": True, "fled": False}]
        items, gold = handler._compute_combat_loot(participants, world)
        assert items == []

    def test_chance_zero_never_drops(self) -> None:
        """LootEntry with chance=0.0 → never drops."""
        handler = CombatHandler()
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="rare_gem", chance=0.0, count="1")],
        )
        participants = [{"monster_id": "goblin", "alive": False, "fled": False}]
        items, _ = handler._compute_combat_loot(participants, world)
        assert items == []


# ─────────────────────────────────────────────────────────────
# TestCombatLootIntegration
# ─────────────────────────────────────────────────────────────


class TestCombatLootIntegration:
    def test_metadata_contains_loot_fields(self) -> None:
        """combat_cleared result → metadata has loot_items + gold_dropped."""
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
            gold_drop="0",
        )
        attacker = _unit("player", "ally", [0, 0])
        target = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1, monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        state = _make_combat_state(payload)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result = handler.compute(cmd, state, world)

        assert result.executed is True
        assert result.metadata["combat_cleared"] is True
        assert "loot_items" in result.metadata
        assert "gold_dropped" in result.metadata
        # chance=1.0 guaranteed drop
        assert result.metadata["loot_items"] == [{"item_id": "cheap_shortsword", "count": 1}]

    def test_inventory_state_change_applied(self) -> None:
        """combat_cleared → StateChange sets inventory containing loot item."""
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
        )
        attacker = _unit("player", "ally", [0, 0])
        target = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1, monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        state = _make_combat_state(payload)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result = handler.compute(cmd, state, world)

        assert result.executed is True
        assert result.delta is not None
        state.apply(result.delta)

        inv = state.player.inventory
        assert any(
            stack.snapshot().get("item_id") == "cheap_shortsword"
            for stack in inv
        )

    def test_gold_state_change_applied(self) -> None:
        """combat_cleared with gold_drop → StateChange adds gold to player."""
        world = _make_world_with_monster("goblin", gold_drop="1d4")
        attacker = _unit("player", "ally", [0, 0])
        target = _unit("goblin_1", "enemy", [1, 0], hp=1, ac=1, monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        state = _make_combat_state(payload, player_gold=10)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        # d20=15 → hit; damage 1d4=1; gold roll 1d4=3
        with mock.patch.object(random, "randint", _seq_rolls(15, 1, 3)):
            result = handler.compute(cmd, state, world)

        assert result.executed is True
        assert result.delta is not None
        state.apply(result.delta)
        assert state.player.gold == 13  # 10 existing + 3 gold drop

    def test_no_loot_when_combat_not_cleared(self) -> None:
        """When monster survives (not cleared), loot_items/gold_dropped are empty."""
        world = _make_world_with_monster(
            "goblin",
            loot_table=[LootEntry(item_id="cheap_shortsword", chance=1.0, count="1")],
            gold_drop="10",
        )
        attacker = _unit("player", "ally", [0, 0])
        # monster has enough HP to survive 1 hit
        target = _unit("goblin_1", "enemy", [1, 0], hp=20, ac=1, monster_id="goblin")
        payload = _make_v2_payload([attacker, target], ["player"])
        state = _make_combat_state(payload)

        cmd = Command(type="combat_attack",
                      params={"sub_area_id": "combat_1", "target": "goblin_1"},
                      source="player")
        handler = CombatHandler()
        with mock.patch.object(random, "randint", _seq_rolls(15, 1)):
            result = handler.compute(cmd, state, world)

        assert result.executed is True
        assert result.metadata["combat_cleared"] is False
        assert result.metadata["loot_items"] == []
        assert result.metadata["gold_dropped"] == 0


# ─────────────────────────────────────────────────────────────
# TestXpAdvancementHook
# ─────────────────────────────────────────────────────────────


def _make_player_state(level: int, xp: int) -> StateContainer:
    state = StateContainer()

    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)

    player = PlayerSlice()
    player.restore({
        "character_id": "hero",
        "hp": 20,
        "max_hp": 20,
        "ac": 14,
        "level": level,
        "xp": xp,
        "gold": 0,
        "stats": {"str": 14, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "proficiency_bonus": 2,
    })
    state.register(player)

    return state


def _make_xp_context(state: StateContainer) -> SettlementContext:
    engine = RulesEngine()
    engine.register(GrowthHandler())
    world = WorldInstance("test_world")

    def _apply_delta(delta: Any) -> None:
        if delta is not None:
            state.apply(delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(state.scene),
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


class TestXpAdvancementHook:
    def test_xp_at_threshold_triggers_level_up_sse(self) -> None:
        """Player with xp >= level*1000 → level_up command executed + SSE emitted."""
        async def _run():
            state = _make_player_state(level=1, xp=1000)
            context = _make_xp_context(state)
            hook = XpAdvancementHook()
            result = await hook.execute(context)
            assert result.sse_events
            sse = result.sse_events[0]
            assert sse.event_type == "player_level_up"
            assert sse.payload["from_level"] == 1
            assert sse.payload["to_level"] == 2
        asyncio.run(_run())

    def test_xp_below_threshold_is_noop(self) -> None:
        """Player with xp < level*1000 → no action, no SSE."""
        async def _run():
            state = _make_player_state(level=1, xp=999)
            context = _make_xp_context(state)
            hook = XpAdvancementHook()
            result = await hook.execute(context)
            assert result.sse_events == []
            assert result.metadata.get("reason") == "xp_below_threshold"
        asyncio.run(_run())

    def test_asi_available_flag_set_at_level_4(self) -> None:
        """Player leveling up to 4 → asi_available=True in SSE."""
        async def _run():
            state = _make_player_state(level=3, xp=3000)
            context = _make_xp_context(state)
            hook = XpAdvancementHook()
            result = await hook.execute(context)
            assert result.sse_events
            sse = result.sse_events[0]
            assert sse.payload["to_level"] == 4
            assert sse.payload["asi_available"] is True
        asyncio.run(_run())

    def test_asi_not_available_at_non_asi_level(self) -> None:
        """Player leveling up to 3 → asi_available=False."""
        async def _run():
            state = _make_player_state(level=2, xp=2000)
            context = _make_xp_context(state)
            hook = XpAdvancementHook()
            result = await hook.execute(context)
            assert result.sse_events
            sse = result.sse_events[0]
            assert sse.payload["to_level"] == 3
            assert sse.payload["asi_available"] is False
        asyncio.run(_run())

    def test_no_player_slice_returns_noop(self) -> None:
        """Missing player slice → hook returns noop without crash."""
        async def _run():
            # State with only scene slice, no player
            state = StateContainer()
            scene_sl = SceneSlice()
            scene_sl.restore({})
            state.register(scene_sl)

            engine = RulesEngine()
            engine.register(GrowthHandler())
            world = WorldInstance("test_world")

            def _apply_delta(delta: Any) -> None:
                if delta is not None:
                    state.apply(delta)

            context = SettlementContext(
                change_log=[],
                state=state,
                world=world,
                scene_bus=SceneBus(scene_sl),
                _rules_engine=engine,
                _apply_delta=_apply_delta,
            )
            hook = XpAdvancementHook()
            result = await hook.execute(context)
            assert result.sse_events == []
            assert result.metadata.get("reason") == "missing_player_slice"
        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# TestPlannerRewardPrompt
# ─────────────────────────────────────────────────────────────


class TestPlannerRewardPrompt:
    def test_system_prompt_contains_rewards_rule(self) -> None:
        """AgenticNarrativePlanner._SYSTEM_PROMPT should contain reward guidance."""
        from app.narrators import AgenticNarrativePlanner
        assert "任务奖励规则" in AgenticNarrativePlanner._SYSTEM_PROMPT

    def test_system_prompt_contains_reward_examples(self) -> None:
        """Prompt includes concrete gold/xp values for different difficulty tiers."""
        from app.narrators import AgenticNarrativePlanner
        prompt = AgenticNarrativePlanner._SYSTEM_PROMPT
        assert "rewards" in prompt
        assert "200" in prompt  # simple quest xp
        assert "400" in prompt  # medium quest xp
        assert "800" in prompt  # hard quest xp
