"""Tests for GmNarrationHook."""

from __future__ import annotations

import asyncio
import logging

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.gm_narration import (
    GmNarrationDecision,
    GmNarrationHook,
    NullGmNarrator,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange
from app.game_core.state.slices import PlayerSlice, SceneSlice, TimeSlice
from app.game_core.state.base import StateContainer


class RecordingNarrator:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    async def compose(self, summary, scene_snapshot, session_id: str = ""):
        self.calls.append({"summary": dict(summary), "scene_snapshot": dict(scene_snapshot)})
        return self.decision


class ExplodingNarrator:
    async def compose(self, summary, scene_snapshot, session_id: str = ""):
        del summary, scene_snapshot, session_id
        raise RuntimeError("narrator unavailable")


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    include_scene: bool = True,
    include_state_changes: bool = True,
    scene_change_slice: str = "flags",
    scene_entries: list[dict[str, object]] | None = None,
    action_log: list[dict[str, object]] | None = None,
    accumulated: float = 0.0,
) -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9, "accumulated": accumulated})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "camp"})
    state.register(player)

    scene_bus: SceneBus | None = None
    if include_scene:
        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)
        scene_bus = SceneBus(scene_slice)
        if include_state_changes:
            scene_bus.record_state_change(
                {
                    "slice": scene_change_slice,
                    "operation": "set",
                    "path": f"{scene_change_slice}.changed",
                    "value": True,
                }
            )
        for entry in scene_entries or []:
            scene_bus.add_entry(dict(entry))
    else:
        orphan_scene = SceneSlice()
        orphan_scene.restore({})
        scene_bus = SceneBus(orphan_scene)

    return SettlementContext(
        change_log=list(
            change_log
            or [StateChange("flags", "set", "flags.quest_started", True)]
        ),
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
        action_log=list(action_log or []),
    )


class TestGmNarrationHook:
    def test_should_skip_depends_on_change_log(self) -> None:
        hook = GmNarrationHook()

        assert hook.should_skip([]) is False
        assert hook.should_skip([StateChange("flags", "set", "flags.x", True)]) is False

    def test_missing_scene_or_empty_scene_changes_returns_noop(self) -> None:
        missing_scene = asyncio.run(
            GmNarrationHook().execute(_make_context(include_scene=False))
        )
        empty_changes = asyncio.run(
            GmNarrationHook().execute(_make_context(include_state_changes=False))
        )

        assert missing_scene.metadata["status"] == "noop"
        assert missing_scene.metadata["evaluated"] is False
        assert empty_changes.metadata["status"] == "noop"
        assert empty_changes.metadata["evaluated"] is False

    def test_default_narrator_generates_one_template_entry(self) -> None:
        context = _make_context()

        result = asyncio.run(GmNarrationHook().execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        assert result.metadata["status"] == "applied"
        assert result.metadata["evaluated"] is True
        assert result.metadata["generated_entry_count"] == 1
        assert result.metadata["public_entry_count"] == 1
        assert result.metadata["narrator_metadata"] == {
            "status": "templated",
            "template_key": "generic",
        }
        assert entries[0]["source"] == "gm"
        assert entries[0]["visibility"] == "public"
        assert [event.event_type for event in result.sse_events] == [
            "gm_narration",
            "gm_narration_added",
        ]
        assert result.sse_events[0].payload["content"] == entries[0]["content"]

    def test_system_entries_keep_hook_active_without_state_changes(self) -> None:
        context = _make_context(
            include_state_changes=False,
            scene_entries=[
                {
                    "source": "ai_osiris",
                    "content": "A rumor of wolves spreads through camp.",
                    "visibility": "system",
                    "tags": ["ai_osiris", "visible_consequence", "create_rumor"],
                    "timestamp": 9.0,
                }
            ],
        )

        result = asyncio.run(GmNarrationHook().execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        public_entries = [entry for entry in entries if entry["source"] == "gm"]
        assert result.metadata["status"] == "applied"
        assert len(public_entries) == 1
        assert public_entries[0]["content"] == "A rumor of wolves spreads through camp."

    def test_system_entries_take_priority_over_generic_templates(self) -> None:
        context = _make_context(
            scene_change_slice="player",
            scene_entries=[
                {
                    "source": "ai_osiris",
                    "content": "Chapter progress changed: chapter_1.",
                    "visibility": "system",
                    "tags": ["ai_osiris", "visible_consequence", "modify_completion"],
                    "timestamp": 9.0,
                }
            ],
        )

        result = asyncio.run(GmNarrationHook().execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        public_entries = [entry for entry in entries if entry["source"] == "gm"]
        assert result.metadata["status"] == "applied"
        assert len(public_entries) == 1
        assert public_entries[0]["content"] == "Chapter progress changed: chapter_1."

    def test_quiet_long_rest_slot_does_not_emit_generic_narration(self) -> None:
        context = _make_context(
            change_log=[StateChange("time", "set", "slot", 10)],
            scene_change_slice="time",
            action_log=[{"type": "rest_long", "time_cost": 1.0}],
            accumulated=4.0,
        )

        result = asyncio.run(GmNarrationHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "quiet_rest_slot"
        assert result.sse_events == []

    def test_default_narrator_uses_player_template_when_player_changes(self) -> None:
        context = _make_context(scene_change_slice="player")

        result = asyncio.run(GmNarrationHook().execute(context))

        assert result.metadata["status"] == "applied"
        assert result.metadata["narrator_metadata"] == {
            "status": "templated",
            "template_key": "player",
        }
        assert len(context.scene_bus.snapshot()["entries"]) == 1

    def test_explicit_null_narrator_remains_supported(self) -> None:
        context = _make_context()

        result = asyncio.run(GmNarrationHook(narrator=NullGmNarrator()).execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["generated_entry_count"] == 0
        assert context.scene_bus.snapshot()["entries"] == []

    def test_valid_public_entry_is_written_and_emits_sse(self) -> None:
        narrator = RecordingNarrator(
            GmNarrationDecision(
                entries=[
                    {
                        "content": "The wind shifts through the trees.",
                        "visibility": "public",
                        "tags": ["atmosphere"],
                    }
                ],
                metadata={"mode": "template"},
            )
        )
        context = _make_context()

        result = asyncio.run(GmNarrationHook(narrator=narrator).execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        assert narrator.calls[0]["summary"]["change_count"] == 1
        assert narrator.calls[0]["summary"]["system_entries"] == []
        assert narrator.calls[0]["summary"]["location"] == {
            "area_id": "forest",
            "location_id": "camp",
        }
        assert result.metadata["status"] == "applied"
        assert result.metadata["generated_entry_count"] == 1
        assert result.metadata["public_entry_count"] == 1
        assert result.metadata["private_entry_count"] == 0
        assert result.metadata["narrator_metadata"] == {"mode": "template"}
        assert entries[0]["source"] == "gm"
        assert entries[0]["content"] == "The wind shifts through the trees."
        assert [event.event_type for event in result.sse_events] == [
            "gm_narration",
            "gm_narration_added",
        ]
        assert result.sse_events[0].payload["content"] == entries[0]["content"]

    def test_private_entry_requires_audience(self) -> None:
        narrator = RecordingNarrator(
            {
                "entries": [
                    {
                        "content": "You notice a hidden mark.",
                        "visibility": "private",
                        "audience": ["player_1"],
                    }
                ]
            }
        )
        context = _make_context()

        result = asyncio.run(GmNarrationHook(narrator=narrator).execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        assert result.metadata["status"] == "applied"
        assert result.metadata["private_entry_count"] == 1
        assert entries[0]["visibility"] == "private"
        assert entries[0]["audience"] == ["player_1"]
        assert [event.event_type for event in result.sse_events] == ["gm_narration_added"]

    def test_invalid_entries_are_skipped_and_output_is_truncated(self) -> None:
        narrator = RecordingNarrator(
            {
                "entries": [
                    {"content": ""},
                    {"content": "One"},
                    {"content": "Private", "visibility": "private"},
                    {"content": "Two", "visibility": "weird"},
                    {"content": "Three"},
                    {"content": "Four"},
                ]
            }
        )
        context = _make_context()

        result = asyncio.run(GmNarrationHook(narrator=narrator).execute(context))

        entries = context.scene_bus.snapshot()["entries"]
        assert result.metadata["status"] == "applied"
        assert result.metadata["generated_entry_count"] == 3
        assert result.metadata["truncated_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 2
        assert [entry["content"] for entry in entries] == ["One", "Two", "Three"]
        assert entries[1]["visibility"] == "public"
        assert [event.event_type for event in result.sse_events] == [
            "gm_narration",
            "gm_narration",
            "gm_narration",
            "gm_narration_added",
        ]

    def test_narrator_error_returns_sse_without_writing_entries(self, caplog) -> None:
        context = _make_context()

        with caplog.at_level(logging.ERROR):
            result = asyncio.run(
                GmNarrationHook(narrator=ExplodingNarrator()).execute(context)
            )

        assert result.metadata["status"] == "narrator_error"
        assert result.metadata["evaluated"] is False
        assert result.sse_events[0].event_type == "gm_narration_error"
        assert context.scene_bus.snapshot()["entries"] == []
        assert any(
            record.message == "hook failed: gm_narration"
            and getattr(record, "hook_name", "") == "gm_narration"
            and record.exc_info is not None
            for record in caplog.records
        )
