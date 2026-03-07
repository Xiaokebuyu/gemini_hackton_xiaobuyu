"""Tests for NpcScheduleHook."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, MapRegistry
from app.game_core.orchestration.hooks import NpcScheduleHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PartySlice, SceneSlice, TimeSlice


class StaticProvider:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def plan(self, context):
        self.calls.append(dict(context))
        return self.decision


class ExplodingProvider:
    def plan(self, context):
        del context
        raise RuntimeError("provider unavailable")


def _make_world(
    *,
    include_characters: bool = True,
    include_maps: bool = True,
) -> WorldInstance:
    world = WorldInstance("test_world")
    if include_maps:
        maps = MapRegistry()
        maps.load(
            {
                "forest": {
                    "id": "forest",
                    "sub_locations": {
                        "camp": {"id": "camp"},
                        "hut": {"id": "hut"},
                    },
                },
                "town": {
                    "id": "town",
                    "sub_locations": [
                        {"id": "square"},
                        {"id": "inn"},
                    ],
                },
                "wilds": {"id": "wilds"},
            }
        )
        world.register(maps)
    if include_characters:
        characters = CharacterRegistry()
        characters.load(
            {
                "npc_alpha": {
                    "id": "npc_alpha",
                    "name": "Alpha",
                    "area_id": "forest",
                    "schedule": {
                        "dawn": "camp",
                        "day": "hut",
                        "dusk": {"area": "town", "location": "square"},
                        "night": {"area": "town", "location": "inn"},
                    },
                },
                "npc_beta": {
                    "id": "npc_beta",
                    "name": "Beta",
                    "current_area": "town",
                    "schedule": {
                        "dawn": "inn",
                        "day": "square",
                        "dusk": "square",
                        "night": "inn",
                    },
                },
                "companion_1": {
                    "id": "companion_1",
                    "name": "Companion",
                    "area_id": "forest",
                },
            }
        )
        world.register(characters)
    return world


def _make_context(
    *,
    slot: int = 17,
    include_time: bool = True,
    include_areas: bool = True,
    include_characters: bool = True,
    include_maps: bool = True,
    include_party: bool = True,
) -> SettlementContext:
    state = StateContainer()
    if include_time:
        time_slice = TimeSlice()
        time_slice.restore({"day": 1, "slot": slot})
        state.register(time_slice)

    if include_areas:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {
                        "npc_locations": {
                            "npc_alpha": "camp",
                            "companion_1": "camp",
                        }
                    },
                    "town": {"npc_locations": {}},
                    "wilds": {"npc_locations": {}},
                }
            }
        )
        state.register(area_slice)

    if include_party:
        party = PartySlice()
        party.restore({"members": {"companion_1": {"id": "companion_1"}}})
        state.register(party)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    return SettlementContext(
        change_log=[],
        state=state,
        world=_make_world(
            include_characters=include_characters,
            include_maps=include_maps,
        ),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


class TestNpcScheduleHook:
    def test_missing_time_returns_stable_noop(self) -> None:
        result = asyncio.run(NpcScheduleHook().execute(_make_context(include_time=False)))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is False
        assert result.metadata["current_period"] == ""
        assert result.metadata["next_period"] == ""
        assert result.sse_events == []

    def test_missing_areas_returns_noop(self) -> None:
        result = asyncio.run(
            NpcScheduleHook().execute(_make_context(include_areas=False, slot=17))
        )

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is False
        assert result.metadata["current_period"] == "day"
        assert result.metadata["next_period"] == "dusk"
        assert result.metadata["period_change_pending"] is True

    def test_same_period_skips_provider(self) -> None:
        provider = StaticProvider({"moves": []})

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(_make_context(slot=9)))

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is False
        assert result.metadata["current_period"] == "day"
        assert result.metadata["next_period"] == "day"
        assert result.metadata["period_change_pending"] is False
        assert provider.calls == []

    def test_missing_character_registry_returns_noop(self) -> None:
        result = asyncio.run(
            NpcScheduleHook().execute(_make_context(include_characters=False, slot=17))
        )

        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is False
        assert result.metadata["candidate_count"] == 0

    def test_default_provider_moves_npcs_to_town_at_dusk(self) -> None:
        context = _make_context(slot=17)

        result = asyncio.run(NpcScheduleHook().execute(context))

        assert result.metadata["status"] == "applied"
        assert result.metadata["evaluated"] is True
        assert result.metadata["candidate_count"] == 2
        assert result.metadata["moved_npc_count"] == 2
        assert result.metadata["provider_metadata"]["status"] == "deterministic"
        assert result.metadata["provider_metadata"]["branch"] == "schedule"
        assert context.state.areas.find_npc_area("npc_alpha") == "town"
        assert context.state.areas.get_area("town").npc_locations["npc_alpha"] == "square"
        assert context.state.areas.find_npc_area("npc_beta") == "town"
        assert context.state.areas.get_area("town").npc_locations["npc_beta"] == "square"
        assert result.sse_events[0].event_type == "npc_schedule_updated"
        assert context.scene_bus.snapshot()["entries"] == []

    def test_default_provider_moves_npcs_back_home_at_dawn(self) -> None:
        context = _make_context(slot=4)
        context.state.areas.move_npc("npc_alpha", "town", None)
        context.state.areas.move_npc("npc_beta", "wilds", None)
        context.state.areas.clear_dirty()

        result = asyncio.run(NpcScheduleHook().execute(context))

        assert result.metadata["status"] == "applied"
        assert result.metadata["provider_metadata"]["status"] == "deterministic"
        assert result.metadata["provider_metadata"]["branch"] == "schedule"
        # npc_alpha dawn schedule → forest/camp
        assert context.state.areas.find_npc_area("npc_alpha") == "forest"
        assert context.state.areas.get_area("forest").npc_locations["npc_alpha"] == "camp"
        # npc_beta dawn schedule → town/inn
        assert context.state.areas.find_npc_area("npc_beta") == "town"
        assert context.state.areas.get_area("town").npc_locations["npc_beta"] == "inn"

    def test_default_provider_is_noop_when_target_area_unavailable(self) -> None:
        context = _make_context(slot=17)
        del context.state.areas.areas["town"]

        result = asyncio.run(NpcScheduleHook().execute(context))

        # npc_alpha dusk schedule points to town (removed) → move rejected by _normalize_move
        # npc_beta current_area=town (removed from state) → no valid target
        assert result.metadata["moved_npc_count"] == 0

    def test_valid_move_updates_area_and_emits_sse(self) -> None:
        context = _make_context(slot=17)
        provider = StaticProvider(
            {
                "moves": [
                    {
                        "character_id": "npc_alpha",
                        "area_id": "town",
                        "location_id": "square",
                    }
                ]
            }
        )

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert context.state.areas.find_npc_area("npc_alpha") == "town"
        assert "npc_alpha" not in context.state.areas.get_area("forest").npc_locations
        assert context.state.areas.get_area("town").npc_locations["npc_alpha"] == "square"
        assert result.metadata["status"] == "applied"
        assert result.metadata["moved_npc_count"] == 1
        assert result.metadata["updated_area_count"] == 1
        assert result.sse_events[0].event_type == "npc_schedule_updated"
        assert result.sse_events[0].payload["npc_ids"] == ["npc_alpha"]
        assert context.scene_bus.snapshot()["entries"] == []

    def test_area_is_inferred_from_existing_location(self) -> None:
        context = _make_context(slot=17)
        provider = StaticProvider({"moves": [{"character_id": "npc_alpha", "location_id": "hut"}]})

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert result.metadata["moved_npc_count"] == 1
        assert context.state.areas.find_npc_area("npc_alpha") == "forest"
        assert context.state.areas.get_area("forest").npc_locations["npc_alpha"] == "hut"

    def test_area_is_inferred_from_template_when_npc_not_placed(self) -> None:
        context = _make_context(slot=17)
        context.state.areas.get_area("forest").npc_locations.pop("npc_alpha", None)
        provider = StaticProvider({"moves": [{"character_id": "npc_alpha"}]})

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert result.metadata["moved_npc_count"] == 1
        assert context.state.areas.find_npc_area("npc_alpha") == "forest"
        assert context.state.areas.get_area("forest").npc_locations["npc_alpha"] is None

    def test_invalid_character_area_and_location_type_are_skipped(self) -> None:
        context = _make_context(slot=17)
        provider = StaticProvider(
            {
                "moves": [
                    {
                        "character_id": "npc_missing",
                        "area_id": "town",
                        "location_id": "square",
                    },
                    {
                        "character_id": "npc_alpha",
                        "area_id": "unknown_area",
                        "location_id": "square",
                    },
                    {
                        "character_id": "npc_beta",
                        "location_id": 3,
                    },
                ]
            }
        )

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["moved_npc_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 3

    def test_unknown_location_is_rejected_when_map_can_validate(self) -> None:
        context = _make_context(slot=17)
        provider = StaticProvider(
            {
                "moves": [
                    {
                        "character_id": "npc_alpha",
                        "area_id": "forest",
                        "location_id": "unknown_location",
                    }
                ]
            }
        )

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["skipped_invalid_count"] == 1
        assert context.state.areas.get_area("forest").npc_locations["npc_alpha"] == "camp"

    def test_duplicate_moves_only_apply_first_valid_move(self) -> None:
        context = _make_context(slot=17)
        provider = StaticProvider(
            {
                "moves": [
                    {
                        "character_id": "npc_alpha",
                        "area_id": "town",
                        "location_id": "square",
                    },
                    {
                        "character_id": "npc_alpha",
                        "area_id": "forest",
                        "location_id": "hut",
                    },
                ]
            }
        )

        result = asyncio.run(NpcScheduleHook(provider=provider).execute(context))

        assert result.metadata["moved_npc_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        assert context.state.areas.find_npc_area("npc_alpha") == "town"
        assert context.state.areas.get_area("town").npc_locations["npc_alpha"] == "square"

    def test_provider_exception_returns_error_sse_without_changes(self) -> None:
        context = _make_context(slot=17)

        result = asyncio.run(NpcScheduleHook(provider=ExplodingProvider()).execute(context))

        assert result.metadata["status"] == "provider_error"
        assert result.metadata["evaluated"] is False
        assert result.sse_events[0].event_type == "npc_schedule_error"
        assert context.state.areas.find_npc_area("npc_alpha") == "forest"
        assert context.scene_bus.snapshot()["entries"] == []
