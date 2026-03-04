"""Tests for EncounterHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.hooks import EncounterHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import EncounterHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import AreaSlice, PlayerSlice, SceneSlice, TimeSlice


class StaticDetector:
    def __init__(self, probe) -> None:
        self.probe = probe
        self.calls: list[dict[str, object]] = []

    def plan(self, context):
        self.calls.append(dict(context))
        return self.probe


class ExplodingDetector:
    def plan(self, context):
        del context
        raise RuntimeError("detector unavailable")


_DEFAULT_FOREST_MAP: dict[str, object] = {
    "id": "forest",
    "encounter_slot_capacity": 1,
    "encounter_table": [
        {"id": "forest:ambient", "monster_ids": ["goblin"], "weight": 1.0},
    ],
}


def _make_world(
    *,
    include_maps: bool = True,
    forest_map: dict[str, object] | None = None,
) -> WorldInstance:
    world = WorldInstance("test_world")
    if include_maps:
        maps = MapRegistry()
        maps.load(
            {
                "forest": forest_map or _DEFAULT_FOREST_MAP,
                "town": {"id": "town"},
            }
        )
        world.register(maps)
    return world


def _make_context(
    *,
    include_player: bool = True,
    include_areas: bool = True,
    include_time: bool = True,
    register_handler: bool = True,
    area_id: str = "forest",
    location_id: str | None = None,
    danger_level: float = 1.0,
    slot: int = 18,
    world: WorldInstance | None = None,
) -> SettlementContext:
    world = world or _make_world()
    state = StateContainer()

    if include_player:
        player = PlayerSlice()
        player.restore({"current_area": area_id, "current_location": location_id})
        state.register(player)

    if include_areas:
        areas = AreaSlice()
        areas.restore(
            {
                "areas": {
                    "forest": {
                        "danger_level": danger_level,
                        "npc_locations": {},
                        "hostile_tracking": {},
                    },
                    "town": {
                        "danger_level": 0.0,
                        "npc_locations": {},
                        "hostile_tracking": {},
                    },
                }
            }
        )
        state.register(areas)

    if include_time:
        time_slice = TimeSlice()
        time_slice.restore({"day": 1, "slot": slot})
        state.register(time_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    if register_handler:
        rules_engine.register(EncounterHandler())

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class TestEncounterHook:
    def test_missing_required_slices_return_noop(self) -> None:
        missing_player = asyncio.run(
            EncounterHook().execute(_make_context(include_player=False))
        )
        missing_areas = asyncio.run(
            EncounterHook().execute(_make_context(include_areas=False))
        )
        missing_time = asyncio.run(
            EncounterHook().execute(_make_context(include_time=False))
        )

        assert missing_player.metadata["status"] == "noop"
        assert missing_player.metadata["evaluated"] is False
        assert missing_areas.metadata["status"] == "noop"
        assert missing_areas.metadata["evaluated"] is False
        assert missing_time.metadata["status"] == "noop"
        assert missing_time.metadata["evaluated"] is False

    def test_current_sub_location_and_zero_danger_skip_hook(self) -> None:
        in_sub_location = asyncio.run(
            EncounterHook().execute(_make_context(location_id="camp"))
        )
        zero_danger = asyncio.run(
            EncounterHook().execute(_make_context(danger_level=0.0))
        )

        assert in_sub_location.metadata["status"] == "noop"
        assert in_sub_location.metadata["evaluated"] is False
        assert zero_danger.metadata["status"] == "noop"
        assert zero_danger.metadata["evaluated"] is False
        assert zero_danger.metadata["danger_level"] == 0.0

    def test_default_detector_triggers_in_high_risk_window(self) -> None:
        context = _make_context()

        result = asyncio.run(EncounterHook().execute(context))

        hostile_area = context.state.areas.get_area("forest")
        assert result.metadata["status"] == "triggered"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is True
        assert result.metadata["detector_metadata"] == {
            "status": "deterministic",
            "provider": "default_detector",
            "branch": "template_probe_window",
            "probe_threshold": 0.75,
            "selected_template_id": "forest:ambient",
            "selected_template_source": "encounter",
            "trigger_score": 1.0,
            "period_multiplier": 1.0,
            "available_slot_count": 1,
        }
        assert result.metadata["encounter_result"]["triggered"] is True
        assert result.metadata["encounter_result"]["template_id"] == "forest:ambient"
        assert len(hostile_area.hostile_tracking) == 1
        hostile = hostile_area.hostile_tracking[result.metadata["encounter_result"]["sub_area_id"]]
        assert hostile["template_id"] == "forest:ambient"
        assert hostile_area.permanent_hostile_slots["forest"] == {
            "max_slots": 1,
            "active_ids": [result.metadata["encounter_result"]["sub_area_id"]],
            "refresh_queue": [],
        }
        assert result.sse_events[0].event_type == "encounter_spotted"
        assert context.scene_bus.snapshot()["entries"] == []

    def test_default_detector_can_probe_without_triggering_encounter(self) -> None:
        context = _make_context(danger_level=0.8, slot=18)

        result = asyncio.run(EncounterHook().execute(context))

        forest = context.state.areas.get_area("forest")
        assert result.metadata["status"] == "checked"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is True
        assert result.metadata["encounter_result"]["triggered"] is False
        assert result.sse_events == []
        assert forest.hostile_tracking == {}
        assert forest.permanent_hostile_slots["forest"] == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

    def test_default_detector_skips_below_probe_threshold(self) -> None:
        context = _make_context(danger_level=0.7, slot=18)

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is False
        assert result.metadata["detector_metadata"] == {
            "status": "noop",
            "provider": "default_detector",
            "reason": "below_probe_threshold",
        }
        assert context.state.areas.get_area("forest").hostile_tracking == {}

    def test_default_detector_skips_when_active_hostile_exists(self) -> None:
        context = _make_context()
        context.state.areas.register_hostile(
            "existing_hostile",
            {
                "area_id": "forest",
                "status": "active",
                "cleared": False,
                "blocking": False,
            },
        )

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is False
        assert result.metadata["detector_metadata"] == {
            "status": "noop",
            "provider": "default_detector",
            "reason": "active_hostile_present",
        }
        assert len(context.state.areas.get_area("forest").hostile_tracking) == 1

    def test_default_detector_skips_when_selected_template_is_cooling(self) -> None:
        context = _make_context(danger_level=0.8, slot=18)
        context.state.areas.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is False
        assert result.metadata["detector_metadata"] == {
            "status": "noop",
            "provider": "default_detector",
            "reason": "template_cooldown",
        }
        assert context.state.areas.get_area("forest").hostile_tracking == {}

    def test_default_detector_syncs_cleared_slots_into_template_refresh_queue(self) -> None:
        context = _make_context(danger_level=1.0, slot=18)
        context.state.areas.register_hostile(
            "enc_1",
            {
                "area_id": "forest",
                "status": "cleared",
                "cleared": True,
                "cleared_at_tick": 12,
                "template_id": "forest:ambient",
            },
        )
        context.state.areas.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": ["enc_1"],
            "refresh_queue": [],
        }

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is False
        assert result.metadata["detector_metadata"] == {
            "status": "noop",
            "provider": "default_detector",
            "reason": "template_cooldown",
        }
        assert context.state.areas.get_area("forest").permanent_hostile_slots["forest"] == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

    def test_default_detector_uses_next_available_template_when_profile_has_multiple(self) -> None:
        world = _make_world(
            forest_map={
                "id": "forest",
                "encounter_slot_capacity": 1,
                "encounter_table": [
                    {"id": "forest_patrol", "monster_ids": ["goblin"], "weight": 1.0},
                    {"id": "forest_ambush", "monster_ids": ["wolf"], "weight": 1.0},
                ],
            }
        )
        context = _make_context(danger_level=0.8, slot=18, world=world)
        context.state.areas.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest_patrol"],
                }
            ],
        }

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "checked"
        assert result.metadata["detector_metadata"]["selected_template_id"] == "forest_ambush"
        assert context.state.areas.get_area("forest").permanent_hostile_slots["forest"] == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest_patrol"],
                },
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest_ambush"],
                },
            ],
        }

    def test_default_detector_noops_when_all_templates_are_cooling(self) -> None:
        world = _make_world(
            forest_map={
                "id": "forest",
                "encounter_slot_capacity": 1,
                "encounter_table": [
                    {"id": "forest_patrol", "monster_ids": ["goblin"], "weight": 1.0},
                    {"id": "forest_ambush", "monster_ids": ["wolf"], "weight": 1.0},
                ],
            }
        )
        context = _make_context(danger_level=0.8, slot=18, world=world)
        context.state.areas.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest_patrol"],
                },
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest_ambush"],
                },
            ],
        }

        result = asyncio.run(EncounterHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is False
        assert result.metadata["detector_metadata"] == {
            "status": "noop",
            "provider": "default_detector",
            "reason": "template_cooldown",
        }

    def test_should_skip_only_runs_on_player_location_changes(self) -> None:
        hook = EncounterHook()

        assert hook.should_skip([]) is True
        assert hook.should_skip(
            [StateChange("flags", "set", "quest_started", True)]
        ) is True
        assert hook.should_skip(
            [StateChange("player", "set", "current_area", "frontier")]
        ) is False
        assert hook.should_skip(
            [StateChange("player", "set", "current_location", None)]
        ) is False

    def test_detector_can_trigger_checked_without_encounter(self) -> None:
        context = _make_context(danger_level=0.4)
        detector = StaticDetector(
            {
                "should_check": True,
                "command_params": {"force_triggered": False},
                "metadata": {"mode": "dry"},
            }
        )

        result = asyncio.run(EncounterHook(detector=detector).execute(context))

        assert len(detector.calls) == 1
        assert detector.calls[0]["area_id"] == "forest"
        assert detector.calls[0]["period"] == "dusk"
        assert result.metadata["status"] == "checked"
        assert result.metadata["evaluated"] is True
        assert result.metadata["checked"] is True
        assert result.metadata["detector_metadata"] == {"mode": "dry"}
        assert result.metadata["encounter_result"]["triggered"] is False
        assert result.sse_events == []

    def test_force_triggered_detector_creates_hostile_and_emits_sse(self) -> None:
        context = _make_context(danger_level=0.4)
        detector = StaticDetector(
            {
                "should_check": True,
                "command_params": {
                    "force_triggered": True,
                    "sub_area_id": "forced_encounter",
                    "source": "hook",
                },
            }
        )

        result = asyncio.run(EncounterHook(detector=detector).execute(context))

        hostile = context.state.areas.get_hostile_state("forced_encounter")
        assert result.metadata["status"] == "triggered"
        assert result.metadata["encounter_result"]["triggered"] is True
        assert hostile is not None
        assert hostile["area_id"] == "forest"
        assert hostile["status"] == "spotted"
        assert hostile["source"] == "hook"
        assert result.sse_events[0].event_type == "encounter_spotted"
        payload = result.sse_events[0].payload
        assert payload["area_id"] == "forest"
        assert payload["sub_area_id"] == "forced_encounter"
        assert payload["blocking"] is False
        assert payload["source"] == "hook"
        # enriched fields
        assert "name" in payload
        assert "description" in payload
        assert "threat_level" in payload
        assert "monster_count" in payload
        assert "options" in payload
        assert payload["options"][0]["action"] == "enter"
        assert payload["threat_level"] == hostile["threat_level"]
        assert context.scene_bus.snapshot()["entries"] == []

    def test_detector_error_returns_sse_without_mutating_state(self) -> None:
        context = _make_context()

        result = asyncio.run(EncounterHook(detector=ExplodingDetector()).execute(context))

        assert result.metadata["status"] == "detector_error"
        assert result.metadata["evaluated"] is False
        assert result.sse_events[0].event_type == "encounter_error"
        assert context.state.areas.get_area("forest").hostile_tracking == {}
        assert context.scene_bus.snapshot()["entries"] == []

    def test_missing_handler_surfaces_command_failed(self) -> None:
        context = _make_context(register_handler=False)
        detector = StaticDetector({"should_check": True, "command_params": {}})

        result = asyncio.run(EncounterHook(detector=detector).execute(context))

        assert result.metadata["status"] == "command_failed"
        assert result.metadata["checked"] is True
        assert result.metadata["encounter_result"] == {}
        assert context.state.areas.get_area("forest").hostile_tracking == {}
        assert context.state.areas.get_area("forest").permanent_hostile_slots["forest"] == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [],
        }
