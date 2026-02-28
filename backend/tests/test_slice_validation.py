"""Tests for StateSlice validate() implementations."""

from __future__ import annotations

from app.game_core.state.slices.events import EventSlice
from app.game_core.state.slices.flags import FlagSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.scene import SceneEntry, SceneSlice


class TestFlagSliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = FlagSlice()
        s.restore({"flags": {"quest_started": True, "door_open": False}})
        assert s.validate() == []

    def test_validate_reports_bad_key(self) -> None:
        s = FlagSlice()
        s.flags = {123: "bad"}  # type: ignore[dict-item]
        issues = s.validate()
        assert any("flag keys" in msg for msg in issues)


class TestPartySliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = PartySlice()
        s.restore({
            "members": {"npc_1": {"name": "Astarion"}},
            "companion_approval": {"npc_1": 5},
            "shared_experiences": [{"event": "camp_talk"}],
        })
        assert s.validate() == []

    def test_validate_reports_issues(self) -> None:
        s = PartySlice()
        s.members = {"npc_1": "not_a_dict"}  # type: ignore[dict-item]
        s.companion_approval = {"npc_1": "high"}  # type: ignore[dict-item]
        s.shared_experiences = "not_a_list"  # type: ignore[assignment]
        issues = s.validate()
        assert any("members[npc_1]" in msg for msg in issues)
        assert any("companion_approval[npc_1]" in msg for msg in issues)
        assert any("shared_experiences must be a list" in msg for msg in issues)


class TestSceneSliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = SceneSlice()
        s.restore({
            "entries": [{"source": "gm", "content": "hello", "visibility": "public"}],
            "state_changes": [{"type": "hp_change"}],
        })
        assert s.validate() == []

    def test_validate_reports_issues(self) -> None:
        s = SceneSlice()
        s.entries = [SceneEntry(source="", content="test", visibility="invalid")]
        s.state_changes = ["not_a_dict"]  # type: ignore[list-item]
        issues = s.validate()
        assert any("source must not be empty" in msg for msg in issues)
        assert any("visibility must be public/private/system" in msg for msg in issues)
        assert any("state_changes[0] must be a dict" in msg for msg in issues)


class TestRelationSliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = RelationSlice()
        s.restore({
            "npc_dispositions": {"npc_1": {"trust": 5}},
            "relationship_stages": {"npc_1": "friendly"},
            "faction_standings": {"guild": 10},
            "npc_impressions": {"npc_1": ["kind"]},
            "shop_states": {"npc_1": {"stock": []}},
        })
        assert s.validate() == []

    def test_validate_reports_issues(self) -> None:
        s = RelationSlice()
        s.npc_dispositions = {"npc_1": {"trust": "high"}}  # type: ignore[dict-item]
        s.faction_standings = {"guild": 3.5}  # type: ignore[dict-item]
        issues = s.validate()
        assert any("npc_dispositions[npc_1].trust must be an integer" in msg for msg in issues)
        assert any("faction_standings[guild] must be an integer" in msg for msg in issues)


class TestEventSliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = EventSlice()
        s.restore({
            "active_events": {
                "evt_1": {"state": "active", "status": "active"},
            },
            "pending_events": [{"trigger_tick": 5}],
            "rumors": [{"text": "rumor"}],
        })
        assert s.validate() == []

    def test_validate_reports_issues(self) -> None:
        s = EventSlice()
        s.active_events = {
            "evt_1": {"id": "evt_1", "event_id": "evt_1", "state": "active", "status": "dormant"},
        }
        s.pending_events = [{"trigger_tick": "not_int"}]  # type: ignore[list-item]
        issues = s.validate()
        assert any("state and status must match" in msg for msg in issues)
        assert any("trigger_tick must be an integer" in msg for msg in issues)


class TestNarrativePlanSliceValidation:
    def test_validate_clean_returns_empty(self) -> None:
        s = NarrativePlanSlice()
        s.restore({
            "chapter_completion": 0.5,
            "escalation_level": 2,
            "ticks_since_milestone_progress": 3,
            "last_run_tick": 10,
            "behavior_window": [{"action": "explore"}],
            "npc_directives": [{"npc": "guard"}],
            "active_bulletins": [],
            "quest_history": [],
            "play_style_tags": ["combat"],
        })
        assert s.validate() == []

    def test_validate_reports_issues(self) -> None:
        s = NarrativePlanSlice()
        s.chapter_completion = 1.5
        s.escalation_level = -1
        s.behavior_window = [{}] * 25
        issues = s.validate()
        assert any("chapter_completion must be between" in msg for msg in issues)
        assert any("escalation_level must be an integer >= 0" in msg for msg in issues)
        assert any("behavior_window must not exceed 24" in msg for msg in issues)
