"""tests/test_stats_manager.py — StatsManager 纯函数单元测试。"""
from __future__ import annotations

import pytest

from app.world.constants import PROFICIENCY_BY_LEVEL, XP_BY_LEVEL
from app.world.stats_manager import add_gold, add_hp, add_xp, remove_gold, remove_hp, set_hp, sync_combat_rewards


# ---------------------------------------------------------------------------
# Helpers: lightweight mock player
# ---------------------------------------------------------------------------

class MockPlayer:
    """Minimal duck-type player for stats_manager tests."""

    def __init__(
        self,
        *,
        xp: int = 0,
        level: int = 1,
        gold: int = 0,
        current_hp: int = 20,
        max_hp: int = 20,
        xp_to_next_level: int = 300,
        proficiency_bonus: int = 2,
        abilities: dict | None = None,
        character_class: str = "fighter",
    ):
        self.xp = xp
        self.level = level
        self.gold = gold
        self.current_hp = current_hp
        self.max_hp = max_hp
        self.xp_to_next_level = xp_to_next_level
        self.proficiency_bonus = proficiency_bonus
        self.abilities = abilities or {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8}
        self.character_class = character_class
        self.spell_slots: dict[int, int] = {}

    def ability_modifier(self, ability: str) -> int:
        score = self.abilities.get(ability, 10)
        return (score - 10) // 2

    def add_item(self, item_id: str, name: str, qty: int) -> None:
        if not hasattr(self, "_items"):
            self._items = []
        self._items.append({"item_id": item_id, "name": name, "qty": qty})


# ===========================================================================
# add_xp
# ===========================================================================

class TestAddXP:
    def test_positive(self):
        p = MockPlayer(xp=0)
        result = add_xp(p, 100)
        assert result["old_xp"] == 0
        assert result["new_xp"] == 100
        assert result["leveled_up"] is False
        assert result["new_level"] == 1

    def test_zero_rejected(self):
        p = MockPlayer()
        result = add_xp(p, 0)
        assert result["success"] is False

    def test_negative_rejected(self):
        p = MockPlayer()
        result = add_xp(p, -10)
        assert result["success"] is False

    def test_triggers_level_up(self):
        """300 XP should trigger level 1 → 2."""
        p = MockPlayer(xp=0, level=1, current_hp=12, max_hp=12)
        result = add_xp(p, 300)
        assert result["leveled_up"] is True
        assert result["new_level"] == 2
        assert p.level == 2
        assert result["hp_gained"] > 0
        assert p.max_hp > 12
        assert p.current_hp > 12

    def test_multi_level_jump(self):
        """Enough XP to jump from level 1 to level 3 (threshold 900)."""
        p = MockPlayer(xp=0, level=1, current_hp=12, max_hp=12)
        result = add_xp(p, 1000)
        assert result["leveled_up"] is True
        assert result["new_level"] == 3
        assert p.level == 3

    def test_max_level_20(self):
        """Already at level 20 — XP increases but no level change."""
        p = MockPlayer(xp=350000, level=20, xp_to_next_level=999999)
        old_hp = p.max_hp
        result = add_xp(p, 100000)
        assert result["leveled_up"] is False
        assert result["new_level"] == 20
        assert p.max_hp == old_hp  # no HP gain

    def test_hp_gain_uses_hit_die(self):
        """Fighter (d10) should gain more HP than wizard (d6)."""
        fighter = MockPlayer(xp=0, level=1, current_hp=10, max_hp=10, character_class="fighter")
        wizard = MockPlayer(xp=0, level=1, current_hp=10, max_hp=10, character_class="wizard")

        r_f = add_xp(fighter, 300)
        r_w = add_xp(wizard, 300)

        assert r_f["hp_gained"] > r_w["hp_gained"]

    def test_proficiency_updated(self):
        """Level 5 should get proficiency bonus 3."""
        p = MockPlayer(xp=6000, level=4, xp_to_next_level=6500)
        add_xp(p, 600)  # 6600 >= 6500 → level 5
        assert p.level == 5
        assert p.proficiency_bonus == PROFICIENCY_BY_LEVEL[5]  # 3

    def test_xp_to_next_level_updated(self):
        """After level-up, xp_to_next_level should be updated."""
        p = MockPlayer(xp=0, level=1, xp_to_next_level=300)
        add_xp(p, 300)
        assert p.xp_to_next_level == XP_BY_LEVEL[3]  # 900


# ===========================================================================
# add_gold / remove_gold
# ===========================================================================

class TestGold:
    def test_add_positive(self):
        p = MockPlayer(gold=50)
        result = add_gold(p, 100)
        assert result["success"] is True
        assert result["new_gold"] == 150

    def test_add_zero_rejected(self):
        p = MockPlayer(gold=50)
        result = add_gold(p, 0)
        assert result["success"] is False

    def test_add_negative_rejected(self):
        p = MockPlayer(gold=50)
        result = add_gold(p, -10)
        assert result["success"] is False

    def test_remove_sufficient(self):
        p = MockPlayer(gold=100)
        result = remove_gold(p, 30)
        assert result["success"] is True
        assert result["new_gold"] == 70

    def test_remove_insufficient(self):
        p = MockPlayer(gold=10)
        result = remove_gold(p, 50)
        assert result["success"] is False
        assert "insufficient" in result["error"]

    def test_remove_zero_rejected(self):
        p = MockPlayer(gold=10)
        result = remove_gold(p, 0)
        assert result["success"] is False


# ===========================================================================
# add_hp / remove_hp / set_hp
# ===========================================================================

class TestHP:
    def test_add_normal(self):
        p = MockPlayer(current_hp=10, max_hp=20)
        result = add_hp(p, 5)
        assert result["new_hp"] == 15

    def test_add_clamped_to_max(self):
        p = MockPlayer(current_hp=18, max_hp=20)
        result = add_hp(p, 10)
        assert result["new_hp"] == 20

    def test_add_zero_rejected(self):
        p = MockPlayer()
        result = add_hp(p, 0)
        assert result["success"] is False

    def test_remove_normal(self):
        p = MockPlayer(current_hp=15, max_hp=20)
        result = remove_hp(p, 5)
        assert result["new_hp"] == 10

    def test_remove_clamped_to_zero(self):
        p = MockPlayer(current_hp=5, max_hp=20)
        result = remove_hp(p, 100)
        assert result["new_hp"] == 0

    def test_set_hp_normal(self):
        p = MockPlayer(current_hp=20, max_hp=20)
        result = set_hp(p, 10)
        assert result["new_hp"] == 10

    def test_set_hp_clamps_above_max(self):
        p = MockPlayer(current_hp=10, max_hp=20)
        result = set_hp(p, 999)
        assert result["new_hp"] == 20

    def test_set_hp_clamps_below_zero(self):
        p = MockPlayer(current_hp=10, max_hp=20)
        result = set_hp(p, -5)
        assert result["new_hp"] == 0


# ===========================================================================
# sync_combat_rewards
# ===========================================================================

class TestSyncCombatRewards:
    def test_victory_full_rewards(self):
        p = MockPlayer(current_hp=20, max_hp=20, xp=0, gold=0)
        payload = {
            "player_state": {"hp_remaining": 12},
            "final_result": {
                "result": "victory",
                "rewards": {"xp": 100, "gold": 50, "items": ["sword_01"]},
            },
        }
        result = sync_combat_rewards(p, payload)
        assert result["hp_set"] is True
        assert p.current_hp == 12
        assert result["xp_added"] == 100
        assert p.xp == 100
        assert result["gold_added"] == 50
        assert p.gold == 50
        assert result["items_added"] == ["sword_01"]

    def test_defeat_hp_only(self):
        """Defeat: HP syncs but no rewards."""
        p = MockPlayer(current_hp=20, max_hp=20, xp=0, gold=0)
        payload = {
            "player_state": {"hp_remaining": 0},
            "final_result": {"result": "defeat", "rewards": {"xp": 100}},
        }
        result = sync_combat_rewards(p, payload)
        assert result["hp_set"] is True
        assert p.current_hp == 0
        assert result["xp_added"] == 0
        assert result["gold_added"] == 0

    def test_no_player_state(self):
        """Missing player_state: no HP change."""
        p = MockPlayer(current_hp=15, max_hp=20)
        payload = {
            "final_result": {"result": "victory", "rewards": {"xp": 50}},
        }
        result = sync_combat_rewards(p, payload)
        assert result["hp_set"] is False
        assert p.current_hp == 15
        assert result["xp_added"] == 50

    def test_empty_payload(self):
        p = MockPlayer(current_hp=15, max_hp=20, xp=10, gold=10)
        result = sync_combat_rewards(p, {})
        assert result["hp_set"] is False
        assert result["xp_added"] == 0
        assert result["gold_added"] == 0
        assert result["items_added"] == []
        assert p.current_hp == 15
        assert p.xp == 10

    def test_result_fallback_key(self):
        """Supports 'result' key as fallback for 'final_result'."""
        p = MockPlayer(current_hp=20, max_hp=20, xp=0, gold=0)
        payload = {
            "result": {"result": "victory", "rewards": {"gold": 30}},
        }
        result = sync_combat_rewards(p, payload)
        assert result["gold_added"] == 30

    def test_non_dict_final_result(self):
        """Non-dict final_result is safely ignored."""
        p = MockPlayer(current_hp=20, max_hp=20)
        payload = {"final_result": "some_string"}
        result = sync_combat_rewards(p, payload)
        assert result["xp_added"] == 0
