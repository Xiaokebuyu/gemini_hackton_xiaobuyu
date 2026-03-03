"""Tests for state slice Read API completeness (D-S06, D-S07)."""

import asyncio

from app.game_core.state.slices.events import EventSlice
from app.game_core.state.slices.flags import FlagSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.quests import QuestSlice
from app.game_core.state.slices.relations import RelationSlice


# ---------------------------------------------------------------------------
# Fix-1: RelationSlice.reduce_stock() dirty tracking
# ---------------------------------------------------------------------------


class TestReduceStockDirty:
    def _make_slice(self) -> RelationSlice:
        sl = RelationSlice()
        sl.restore(
            {
                "npc_dispositions": {},
                "relationship_stages": {},
                "faction_standings": {},
                "npc_impressions": {},
                "shop_states": {
                    "merchant_a": {
                        "inventory": [
                            {"item_id": "sword", "count": 3},
                            {"item_id": "potion", "count": 1},
                        ]
                    }
                },
            }
        )
        return sl

    def test_reduce_stock_success_marks_dirty(self):
        sl = self._make_slice()
        assert not sl.dirty
        result = sl.reduce_stock("merchant_a", "sword", 2)
        assert result is True
        assert sl.dirty, "reduce_stock() must set _dirty after successful mutation"

    def test_reduce_stock_removes_item_marks_dirty(self):
        sl = self._make_slice()
        result = sl.reduce_stock("merchant_a", "potion", 1)
        assert result is True
        assert sl.dirty

    def test_reduce_stock_failure_does_not_mark_dirty(self):
        sl = self._make_slice()
        result = sl.reduce_stock("merchant_a", "sword", 99)  # insufficient
        assert result is False
        assert not sl.dirty, "failed reduce_stock() must not set _dirty"

    def test_reduce_stock_unknown_npc_does_not_mark_dirty(self):
        sl = self._make_slice()
        result = sl.reduce_stock("unknown_npc", "sword", 1)
        assert result is False
        assert not sl.dirty


# ---------------------------------------------------------------------------
# Fix-2: FlagSlice.get_all()
# ---------------------------------------------------------------------------


class TestFlagGetAll:
    def test_get_all_returns_all_flags(self):
        sl = FlagSlice()
        sl.set("key_a", True)
        sl.set("key_b", 42)
        result = sl.get_all()
        assert result == {"key_a": True, "key_b": 42}

    def test_get_all_returns_defensive_copy(self):
        sl = FlagSlice()
        sl.set("key_a", "original")
        copy = sl.get_all()
        copy["key_a"] = "mutated"
        assert sl.get("key_a") == "original", "get_all() must return a copy"

    def test_get_all_empty(self):
        sl = FlagSlice()
        assert sl.get_all() == {}


# ---------------------------------------------------------------------------
# Fix-3: QuestSlice.get_active_quests() and get_completion()
# ---------------------------------------------------------------------------


class TestQuestReadAPIs:
    def _make_slice(self) -> QuestSlice:
        sl = QuestSlice()
        sl.restore(
            {
                "milestone_states": {},
                "dynamic_quests": {
                    "q1": {"id": "q1", "title": "Quest A", "status": "in_progress"},
                    "q2": {"id": "q2", "title": "Quest B", "status": "accepted"},
                    "q3": {"id": "q3", "title": "Quest C", "status": "completed"},
                    "q4": {"id": "q4", "title": "Quest D", "status": "retired"},
                    "q5": {"id": "q5", "title": "Quest E", "status": "active"},
                },
                "chapter_completion": {"chapter_1": 0.75},
            }
        )
        return sl

    def test_get_active_quests_returns_active_statuses(self):
        sl = self._make_slice()
        active = sl.get_active_quests()
        ids = {q["id"] for q in active}
        assert ids == {"q1", "q2", "q5"}, "must include in_progress/accepted/active"

    def test_get_active_quests_excludes_inactive(self):
        sl = self._make_slice()
        active = sl.get_active_quests()
        ids = {q["id"] for q in active}
        assert "q3" not in ids  # completed
        assert "q4" not in ids  # retired

    def test_get_active_quests_returns_copies(self):
        sl = self._make_slice()
        active = sl.get_active_quests()
        active[0]["title"] = "mutated"
        assert sl.dynamic_quests["q1"]["title"] == "Quest A"

    def test_get_completion_known_chapter(self):
        sl = self._make_slice()
        assert sl.get_completion("chapter_1") == 0.75

    def test_get_completion_unknown_chapter_returns_zero(self):
        sl = self._make_slice()
        assert sl.get_completion("unknown_chapter") == 0.0


# ---------------------------------------------------------------------------
# Fix-4: PartySlice.get_shared_experiences(with_character) and count_critical_moments()
# ---------------------------------------------------------------------------


class TestPartyReadAPIs:
    def _make_slice(self) -> PartySlice:
        sl = PartySlice()
        sl.restore(
            {
                "members": {},
                "companion_approval": {},
                "shared_experiences": [
                    {
                        "participants": ["paladin", "rogue"],
                        "type": "combat",
                        "critical_moment": True,
                    },
                    {
                        "participants": ["paladin", "wizard"],
                        "type": "dialogue",
                        "critical_moment": False,
                    },
                    {
                        "participants": ["rogue"],
                        "type": "exploration",
                        "critical_moment": True,
                    },
                    {
                        "participants": ["paladin", "rogue"],
                        "type": "crisis",
                        "critical_moment": True,
                    },
                ],
            }
        )
        return sl

    def test_get_shared_experiences_no_filter_returns_all(self):
        sl = self._make_slice()
        exps = sl.get_shared_experiences()
        assert len(exps) == 4

    def test_get_shared_experiences_filter_by_participant(self):
        sl = self._make_slice()
        paladin_exps = sl.get_shared_experiences(with_character="paladin")
        assert len(paladin_exps) == 3  # paladin is in 3 experiences

    def test_get_shared_experiences_filter_excludes_non_participant(self):
        sl = self._make_slice()
        wizard_exps = sl.get_shared_experiences(with_character="wizard")
        assert len(wizard_exps) == 1

    def test_get_shared_experiences_filter_returns_copies(self):
        sl = self._make_slice()
        exps = sl.get_shared_experiences(with_character="paladin")
        exps[0]["type"] = "mutated"
        assert sl.shared_experiences[0]["type"] == "combat"

    def test_count_critical_moments_with_shared_character(self):
        sl = self._make_slice()
        # paladin has 3 experiences; 2 of them have critical_moment=True
        assert sl.count_critical_moments("paladin") == 2

    def test_count_critical_moments_solo_character(self):
        sl = self._make_slice()
        # rogue has 3 experiences (paladin+rogue twice + solo once); 3 are critical
        assert sl.count_critical_moments("rogue") == 3

    def test_count_critical_moments_unknown_character(self):
        sl = self._make_slice()
        assert sl.count_critical_moments("unknown_char") == 0


# ---------------------------------------------------------------------------
# Fix-A/B: EventSlice validate() state whitelist + trigger() method (D-S07)
# ---------------------------------------------------------------------------


class TestEventSliceValidateAndTrigger:
    def _make_event(self, event_id: str, state: str) -> dict:
        return {
            "id": event_id,
            "event_id": event_id,
            "state": state,
            "status": state,
            "event_type": "generic",
            "conditions": [],
            "payload": {},
            "metadata": {},
            "source": "quest",
        }

    def test_validate_rejects_invalid_state(self):
        sl = EventSlice()
        sl.restore(
            {
                "active_events": {"ev1": self._make_event("ev1", "flying_spaghetti")},
                "pending_events": [],
                "rumors": [],
            }
        )
        issues = sl.validate()
        assert any("not a valid event state" in msg for msg in issues)

    def test_validate_accepts_all_valid_states(self):
        for state in ("dormant", "triggered", "active", "resolved", "expired", "cancelled"):
            sl = EventSlice()
            sl.restore(
                {
                    "active_events": {"ev1": self._make_event("ev1", state)},
                    "pending_events": [],
                    "rumors": [],
                }
            )
            issues = sl.validate()
            state_issues = [m for m in issues if "not a valid event state" in m]
            assert not state_issues, f"state '{state}' should be valid but got: {state_issues}"

    def test_trigger_sets_triggered_state(self):
        sl = EventSlice()
        sl.restore(
            {
                "active_events": {"ev1": self._make_event("ev1", "dormant")},
                "pending_events": [],
                "rumors": [],
            }
        )
        sl.trigger("ev1")
        assert sl.active_events["ev1"]["state"] == "triggered"
        assert sl.active_events["ev1"]["status"] == "triggered"
        assert sl.dirty


# ===========================================================================
# Phase 2: AreaSlice — interactable_states + Read API
# ===========================================================================


class TestAreaSlicePhase2:
    """Tests for interactable_states field and new read API methods."""

    def _make_slice(self) -> "AreaSlice":
        from app.game_core.state.slices.area import AreaSlice
        return AreaSlice()

    def test_interactable_states_restore_snapshot_roundtrip(self):
        sl = self._make_slice()
        sl.restore({
            "areas": {
                "dungeon": {
                    "interactable_states": {
                        "lever_a": {"area_id": "dungeon", "used": True},
                        "altar":   {"area_id": "dungeon", "used": False},
                    },
                }
            }
        })
        snap = sl.snapshot()
        dungeon = snap["areas"]["dungeon"]
        assert dungeon["interactable_states"]["lever_a"]["used"] is True
        assert dungeon["interactable_states"]["altar"]["used"] is False

        # 旧存档无 interactable_states → 默认空 dict，不 crash
        sl2 = self._make_slice()
        sl2.restore({"areas": {"town": {"exploration": "visited"}}})
        assert sl2.snapshot()["areas"]["town"].get("interactable_states", {}) == {}

    def test_get_discovered_returns_set(self):
        sl = self._make_slice()
        assert sl.get_discovered("forest") == set()

        sl.mark_discovery("forest", "hidden_shrine")
        sl.mark_discovery("forest", "old_well")
        result = sl.get_discovered("forest")
        assert result == {"hidden_shrine", "old_well"}
        assert isinstance(result, set)

    def test_is_hostile_cleared(self):
        from app.game_core.state.slices.area import AreaSlice
        sl = self._make_slice()
        sl.upsert_hostile("bandit_camp", {
            "area_id": "forest",
            "status": "active",
        })
        assert sl.is_hostile_cleared("forest", "bandit_camp") is False

        sl.mark_cleared("bandit_camp", tick=5)
        assert sl.is_hostile_cleared("forest", "bandit_camp") is True

        # 不存在的 sub_area → False，不 crash
        assert sl.is_hostile_cleared("forest", "nonexistent") is False

    def test_is_container_opened_and_trap_detected(self):
        sl = self._make_slice()
        assert sl.is_container_opened("dungeon", "chest_1") is False
        assert sl.is_trap_detected("dungeon", "chest_1") is False

        sl.init_container("dungeon", "chest_1", {
            "opened": True,
            "trap_detected": True,
            "remaining_items": [],
        })
        assert sl.is_container_opened("dungeon", "chest_1") is True
        assert sl.is_trap_detected("dungeon", "chest_1") is True

        # looted=True 也算 opened
        sl.init_container("dungeon", "chest_2", {"looted": True})
        assert sl.is_container_opened("dungeon", "chest_2") is True

    def test_is_interactable_used_and_apply_state_change(self):
        from app.game_core.state.delta import StateChange
        sl = self._make_slice()

        # 初始为 False
        assert sl.is_interactable_used("temple", "door_lever") is False

        # mark_interactable_used 写入
        sl.mark_interactable_used("temple", "door_lever")
        assert sl.is_interactable_used("temple", "door_lever") is True
        assert sl.dirty

        # StateDelta apply 路径
        sl2 = self._make_slice()
        change = StateChange(
            slice="areas",
            operation="set",
            path="interactable_states.altar_switch",
            value={"area_id": "temple", "used": True},
        )
        sl2.apply_state_change(change)
        assert sl2.is_interactable_used("temple", "altar_switch") is True
