"""Tests for SceneBus (Direction A.1)."""

import pytest
from app.world.scene_bus import BusEntry, BusEntryType, SceneBus


class TestBusEntry:
    def test_create_default(self):
        entry = BusEntry(
            actor="player",
            type=BusEntryType.ACTION,
            content="go to tavern",
        )
        assert entry.actor == "player"
        assert entry.type == BusEntryType.ACTION
        assert entry.content == "go to tavern"
        assert entry.visibility == "public"
        assert len(entry.id) == 8

    def test_private_visibility(self):
        entry = BusEntry(
            actor="player",
            type=BusEntryType.SPEECH,
            content="whisper",
            visibility="private:priestess",
        )
        assert entry.visibility == "private:priestess"

    def test_serialize_deserialize(self):
        entry = BusEntry(
            actor="engine",
            type=BusEntryType.ENGINE_RESULT,
            content="navigate done",
            data={"target": "forest"},
        )
        d = entry.model_dump(mode="json")
        restored = BusEntry(**d)
        assert restored.actor == "engine"
        assert restored.data["target"] == "forest"


class TestSceneBus:
    def test_publish_and_get_entries(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.ACTION, content="enter",
        ))
        bus.publish(BusEntry(
            actor="gm", type=BusEntryType.NARRATIVE, content="you see...",
        ))
        assert len(bus.entries) == 2

    def test_filter_by_actor(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="a"))
        bus.publish(BusEntry(actor="gm", type=BusEntryType.NARRATIVE, content="b"))
        bus.publish(BusEntry(actor="player", type=BusEntryType.SPEECH, content="c"))
        result = bus.get_entries(actor="player")
        assert len(result) == 2

    def test_filter_by_type(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="a"))
        bus.publish(BusEntry(actor="gm", type=BusEntryType.NARRATIVE, content="b"))
        result = bus.get_entries(entry_type=BusEntryType.NARRATIVE)
        assert len(result) == 1

    def test_clear(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="a"))
        bus.clear()
        assert len(bus.entries) == 0

    def test_round_summary(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", actor_name="Hero",
            type=BusEntryType.ACTION, content="walks in",
        ))
        bus.publish(BusEntry(
            actor="gm", type=BusEntryType.NARRATIVE, content="tavern is busy",
        ))
        summary = bus.get_round_summary()
        assert "Hero" in summary
        assert "tavern is busy" in summary

    def test_visibility_filter_public(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="public msg",
        ))
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="secret",
            visibility="private:priestess",
        ))
        # Unrelated viewer sees only public
        visible = bus.get_visible_entries("warrior")
        assert len(visible) == 1
        assert visible[0].content == "public msg"

    def test_visibility_filter_private_target(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="public",
        ))
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="whisper",
            visibility="private:priestess",
        ))
        # Target sees both
        visible = bus.get_visible_entries("priestess")
        assert len(visible) == 2

    def test_visibility_filter_actor_sees_own_private(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.SPEECH, content="whisper",
            visibility="private:priestess",
        ))
        visible = bus.get_visible_entries("player")
        assert len(visible) == 1

    def test_sublocation_update(self):
        bus = SceneBus(area_id="town", sub_location=None)
        assert bus.sub_location is None
        bus.sub_location = "smithy"
        assert bus.sub_location == "smithy"
        # Sub location change doesn't create new instance
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="a"))
        assert len(bus.entries) == 1
        bus.sub_location = None
        assert bus.sub_location is None
        assert len(bus.entries) == 1  # Same instance, entries preserved

    def test_to_from_serializable(self):
        bus = SceneBus(area_id="forest", sub_location="cave", round_number=3)
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="explore"))
        data = bus.to_serializable()
        restored = SceneBus.from_serializable(data)
        assert restored.area_id == "forest"
        assert restored.sub_location == "cave"
        assert restored.round_number == 3
        assert len(restored.entries) == 1

    def test_round_summary_respects_visibility(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", actor_name="Hero",
            type=BusEntryType.SPEECH, content="public talk",
        ))
        bus.publish(BusEntry(
            actor="player", actor_name="Hero",
            type=BusEntryType.SPEECH, content="secret whisper",
            visibility="private:priestess",
        ))
        # Without viewer_id, only public entries
        summary_public = bus.get_round_summary()
        assert "public talk" in summary_public
        assert "secret whisper" not in summary_public

        # With viewer_id as target
        summary_target = bus.get_round_summary(viewer_id="priestess")
        assert "secret whisper" in summary_target

    def test_round_summary_truncation(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(
            actor="player", type=BusEntryType.ACTION,
            content="x" * 3000,
        ))
        summary = bus.get_round_summary(max_length=100)
        assert len(summary) <= 104  # 100 + "..." + newline

    def test_empty_bus_summary(self):
        bus = SceneBus(area_id="tavern")
        assert bus.get_round_summary() == ""


class TestRoundSummaryExcludeActors:
    def test_exclude_actors_filters(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="walks in"))
        bus.publish(BusEntry(actor="gm", type=BusEntryType.NARRATIVE, content="tavern is busy"))
        bus.publish(BusEntry(actor="engine", type=BusEntryType.ENGINE_RESULT, content="navigated"))
        bus.publish(BusEntry(actor="priestess", actor_name="女祭司", type=BusEntryType.REACTION, content="nods"))
        summary = bus.get_round_summary(exclude_actors={"player", "gm"})
        assert "walks in" not in summary
        assert "tavern is busy" not in summary
        assert "navigated" in summary
        assert "nods" in summary

    def test_exclude_all_returns_empty(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hi"))
        assert bus.get_round_summary(exclude_actors={"player"}) == ""

    def test_exclude_none_keeps_all(self):
        bus = SceneBus(area_id="tavern")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hi"))
        bus.publish(BusEntry(actor="gm", type=BusEntryType.NARRATIVE, content="ok"))
        summary = bus.get_round_summary()
        assert "hi" in summary
        assert "ok" in summary


class TestMemberVisibility:
    """get_visible_entries 成员校验测试。"""

    def _make_bus(self):
        bus = SceneBus(area_id="tavern", permanent_members={"priestess"})
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hello"))
        return bus

    def test_non_member_gets_empty(self):
        bus = self._make_bus()
        assert bus.get_visible_entries("outsider") == []

    def test_permanent_member_gets_entries(self):
        bus = self._make_bus()
        entries = bus.get_visible_entries("priestess")
        assert len(entries) == 1
        assert entries[0].content == "hello"

    def test_active_member_gets_entries(self):
        bus = self._make_bus()
        bus.contact("merchant")
        entries = bus.get_visible_entries("merchant")
        assert len(entries) == 1

    def test_system_actor_player_always_sees(self):
        bus = self._make_bus()
        entries = bus.get_visible_entries("player")
        assert len(entries) == 1

    def test_system_actor_gm_always_sees(self):
        bus = self._make_bus()
        entries = bus.get_visible_entries("gm")
        assert len(entries) == 1

    def test_system_actor_engine_always_sees(self):
        bus = self._make_bus()
        entries = bus.get_visible_entries("engine")
        assert len(entries) == 1

    def test_no_viewer_id_returns_all_public(self):
        bus = self._make_bus()
        entries = bus.get_visible_entries()
        assert len(entries) == 1
