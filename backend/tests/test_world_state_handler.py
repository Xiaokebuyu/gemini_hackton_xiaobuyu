"""Tests for WorldStateHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, MapRegistry, QuestRegistry
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    TimeSlice,
)


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")

    characters = CharacterRegistry()
    characters.load(
        {
            "npc_guard": {"id": "npc_guard", "name": "Guard"},
            "companion_1": {"id": "companion_1", "name": "Companion"},
        }
    )
    world.register(characters)

    maps = MapRegistry()
    maps.load(
        {
            "forest": {
                "id": "forest",
                "name": "Forest",
                "sub_locations": {
                    "camp": {"id": "camp", "name": "Campsite"},
                },
            }
        }
    )
    world.register(maps)

    quests = QuestRegistry()
    quests.load(
        {
            "milestones": {
                "ms_1": {"id": "ms_1", "chapter_id": "chapter_1"},
                "ms_done": {"id": "ms_done", "chapter_id": "chapter_1"},
            },
            "chapters": [{"id": "chapter_1"}],
        }
    )
    world.register(quests)

    return world


def _make_state() -> StateContainer:
    state = StateContainer()

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    relations = RelationSlice()
    relations.restore(
        {
            "npc_dispositions": {
                "npc_guard": {
                    "approval": 0,
                    "trust": 0,
                    "fear": 0,
                    "romance": 0,
                }
            },
            "npc_impressions": {"npc_guard": []},
        }
    )
    state.register(relations)

    party = PartySlice()
    party.restore(
        {
            "members": {"companion_1": {"id": "companion_1"}},
            "companion_approval": {"companion_1": 0},
        }
    )
    state.register(party)

    quests = QuestSlice()
    quests.restore(
        {
            "milestone_states": {
                "ms_1": {"state": "AVAILABLE"},
                "ms_done": {"state": "COMPLETED"},
            },
            "dynamic_quests": {
                "dyn_1": {"id": "dyn_1", "status": "active"},
                "dyn_done": {"id": "dyn_done", "status": "completed"},
            },
            "chapter_completion": {"chapter_1": 0.0},
        }
    )
    state.register(quests)

    events = EventSlice()
    events.restore({})
    state.register(events)

    player = PlayerSlice()
    player.restore({})
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {
                    "danger_level": 1.0,
                    "properties": {},
                    "npc_locations": {},
                }
            }
        }
    )
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 8})
    state.register(time_slice)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(WorldStateHandler())
    return engine


def _execute(command: Command, state: StateContainer, world: WorldInstance):
    return _make_engine().execute(command, state, world)


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


class TestWorldStateHandler:
    def test_modify_disposition_accepts_target_alias_and_applies_delta(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="modify_disposition",
                params={"target": "npc_guard", "dimension": "trust", "delta": 10},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        assert state.relations.npc_dispositions["npc_guard"]["trust"] == 10

    def test_modify_disposition_rejects_out_of_range_delta(self) -> None:
        result = _execute(
            Command(
                type="modify_disposition",
                params={"npc_id": "npc_guard", "dimension": "trust", "delta": 51},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["delta must be between -50 and 50"]

    def test_modify_approval_accepts_character_alias(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="modify_approval",
                params={"character": "companion_1", "delta": 5},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        assert state.party.companion_approval["companion_1"] == 5

    def test_modify_approval_rejects_non_party_character(self) -> None:
        result = _execute(
            Command(
                type="modify_approval",
                params={"character_id": "npc_guard", "delta": 5},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["character not in party: npc_guard"]

    def test_advance_quest_allows_valid_milestone_transition(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="advance_quest",
                params={"quest_id": "ms_1", "to_state": "active", "tick": 10},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        milestone = state.quests.get_milestone("ms_1")
        assert milestone is not None
        assert milestone.state == "ACTIVE"
        assert milestone.activated_tick == 10

    def test_advance_quest_rejects_terminal_milestone_transition(self) -> None:
        result = _execute(
            Command(
                type="advance_quest",
                params={"quest_id": "ms_done", "to_state": "ACTIVE"},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["invalid milestone transition: COMPLETED -> ACTIVE"]

    def test_advance_quest_allows_dynamic_transition(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="advance_quest",
                params={"quest_id": "dyn_1", "to_state": "completed", "quest_kind": "dynamic"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        assert state.quests.dynamic_quests["dyn_1"]["status"] == "completed"

    def test_advance_quest_rejects_invalid_dynamic_transition(self) -> None:
        result = _execute(
            Command(
                type="advance_quest",
                params={"quest_id": "dyn_done", "to_state": "active", "quest_kind": "dynamic"},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["invalid dynamic quest transition: completed -> active"]

    def test_schedule_event_accepts_absolute_trigger_condition(self) -> None:
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_abs",
                    "trigger_condition": {"type": "absolute_tick", "tick": 42},
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        pending = result.delta.changes[0].value
        assert pending["trigger_condition"] == {"type": "absolute_tick", "tick": 42}
        assert isinstance(pending.get("created_at"), dict)

    def test_schedule_event_accepts_time_slots_elapsed_condition(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_rel",
                    "trigger_condition": {"type": "time_slots_elapsed", "count": 3},
                },
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        pending = result.delta.changes[0].value
        # Condition is preserved as-is (not pre-computed to tick)
        assert pending["trigger_condition"] == {"type": "time_slots_elapsed", "count": 3}
        assert isinstance(pending.get("created_at"), dict)

    def test_schedule_event_rejects_unsupported_trigger_condition_type(self) -> None:
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_bad",
                    "trigger_condition": {"type": "moon_phase", "phase": "full"},
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["unsupported trigger_condition.type: moon_phase"]

    def test_schedule_event_accepts_period_reached_condition(self) -> None:
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_period",
                    "trigger_condition": {"type": "period_reached", "period": "day"},
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        pending = result.delta.changes[0].value
        assert pending["trigger_condition"] == {"type": "period_reached", "period": "day"}

    def test_schedule_event_accepts_location_entered_condition(self) -> None:
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_location",
                    "trigger_condition": {"type": "location_entered", "area_id": "forest"},
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        pending = result.delta.changes[0].value
        assert pending["trigger_condition"] == {"type": "location_entered", "area_id": "forest"}

    def test_schedule_event_accepts_flag_set_condition(self) -> None:
        result = _execute(
            Command(
                type="schedule_event",
                params={
                    "event_id": "evt_flag",
                    "trigger_condition": {"type": "flag_set", "key": "quest_started"},
                },
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        pending = result.delta.changes[0].value
        assert pending["trigger_condition"] == {"type": "flag_set", "key": "quest_started"}

    def test_create_rumor_accepts_content_alias_and_adds_created_at(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="create_rumor",
                source="ai_osiris",
                params={
                    "content": "Watch the tree line",
                    "spread_to": ["forest", "npc:npc_guard"],
                    "source_npc_id": "npc_guard",
                },
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        assert result.delta is not None
        rumor = result.delta.changes[0].value
        assert rumor["text"] == "Watch the tree line"
        assert rumor["content"] == "Watch the tree line"
        assert rumor["spread_to"] == ["forest", "npc:npc_guard"]
        assert rumor["created_at"] == {"day": 1, "slot": 8, "period": "day"}

    def test_create_rumor_rejects_invalid_spread_to_target(self) -> None:
        result = _execute(
            Command(
                type="create_rumor",
                params={"text": "Bad rumor", "spread_to": ["unknown_target"]},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["spread_to target not found: unknown_target"]

    def test_modify_location_allows_engine_player_location_updates(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="modify_location",
                source="engine",
                params={"area_id": "forest", "location_id": "camp"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        assert state.player.current_area == "forest"
        assert state.player.current_location == "camp"

    def test_modify_location_rejects_ai_osiris_player_location_updates(self) -> None:
        result = _execute(
            Command(
                type="modify_location",
                source="ai_osiris",
                params={"area_id": "forest", "location_id": "camp"},
            ),
            _make_state(),
            _make_world(),
        )

        assert result.success is False
        assert result.errors == ["ai_osiris cannot modify player location directly"]

    def test_modify_location_area_property_mode_updates_area_slice(self) -> None:
        state = _make_state()
        result = _execute(
            Command(
                type="modify_location",
                source="ai_osiris",
                params={"area_id": "forest", "key": "weather", "value": "rain"},
            ),
            state,
            _make_world(),
        )

        assert result.success is True
        _apply(result, state)
        assert state.areas.get_area("forest").properties["weather"] == "rain"

    def test_modify_completion_enforces_bounds(self) -> None:
        success_result = _execute(
            Command(
                type="modify_completion",
                params={"chapter_id": "chapter_1", "delta": 0.5},
            ),
            _make_state(),
            _make_world(),
        )
        error_result = _execute(
            Command(
                type="modify_completion",
                params={"chapter_id": "chapter_1", "delta": 0.6},
            ),
            _make_state(),
            _make_world(),
        )

        assert success_result.success is True
        assert error_result.success is False
        assert error_result.errors == ["delta must be between -0.2 and 0.5"]

    def test_adjust_danger_enforces_bounds(self) -> None:
        state = _make_state()
        success_result = _execute(
            Command(
                type="adjust_danger",
                params={"area_id": "forest", "delta": 0.5},
            ),
            state,
            _make_world(),
        )
        error_result = _execute(
            Command(
                type="adjust_danger",
                params={"area_id": "forest", "delta": 1.0},
            ),
            _make_state(),
            _make_world(),
        )

        assert success_result.success is True
        _apply(success_result, state)
        assert state.areas.get_danger("forest") == 1.5
        assert error_result.success is False
        assert error_result.errors == ["delta must be between -0.5 and 0.5"]
