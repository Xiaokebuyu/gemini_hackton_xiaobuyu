"""Tests for AreaSlice helpers."""

from __future__ import annotations

from app.game_core.state import StateChange
from app.game_core.state.slices import AreaSlice


class TestAreaSlice:
    def test_find_npc_area_returns_current_bucket(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {"npc_locations": {"npc_alpha": "camp"}},
                    "town": {"npc_locations": {"npc_beta": "square"}},
                }
            }
        )

        assert area_slice.find_npc_area("npc_alpha") == "forest"
        assert area_slice.find_npc_area("npc_beta") == "town"

    def test_find_npc_area_returns_none_when_missing(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {"npc_locations": {}}}})

        assert area_slice.find_npc_area("npc_unknown") is None

    def test_move_npc_moves_between_areas(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {"npc_locations": {"npc_alpha": "camp"}},
                    "town": {"npc_locations": {}},
                }
            }
        )

        area_slice.move_npc("npc_alpha", "town", "square")

        assert "npc_alpha" not in area_slice.get_area("forest").npc_locations
        assert area_slice.get_area("town").npc_locations["npc_alpha"] == "square"
        assert area_slice.find_npc_area("npc_alpha") == "town"

    def test_move_npc_allows_none_location(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {"npc_locations": {}}}})

        area_slice.move_npc("npc_alpha", "forest", None)

        assert area_slice.get_area("forest").npc_locations["npc_alpha"] is None

    def test_move_npc_creates_target_area_when_missing(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {"npc_locations": {"npc_alpha": "camp"}}}})

        area_slice.move_npc("npc_alpha", "new_area", "plaza")

        assert "new_area" in area_slice.areas
        assert area_slice.get_area("new_area").npc_locations["npc_alpha"] == "plaza"

    def test_move_npc_clears_old_room_and_sets_new_room(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {
                        "npc_locations": {"npc_alpha": "camp"},
                        "npc_rooms": {"npc_alpha": "tent"},
                    },
                    "town": {"npc_locations": {}, "npc_rooms": {}},
                }
            }
        )

        area_slice.move_npc("npc_alpha", "town", "inn", room_id="common_room")

        assert "npc_alpha" not in area_slice.get_area("forest").npc_rooms
        assert area_slice.get_area("town").npc_rooms["npc_alpha"] == "common_room"

    def test_apply_state_change_npc_presence_updates_room(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"town": {}}})

        area_slice.apply_state_change(
            StateChange(
                "areas",
                "set",
                "npc_presence.npc_alpha",
                {
                    "area_id": "town",
                    "location_id": "guild",
                    "room_id": "guild_counter",
                    "source": "schedule",
                },
            )
        )

        assert area_slice.get_area("town").npc_locations["npc_alpha"] == "guild"
        assert area_slice.get_area("town").npc_rooms["npc_alpha"] == "guild_counter"

    def test_find_container_area_returns_current_bucket(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {"container_states": {"crate": {"opened": False}}},
                    "town": {"container_states": {"cache": {"opened": True}}},
                }
            }
        )

        assert area_slice.find_container_area("crate") == "forest"
        assert area_slice.find_container_area("cache") == "town"

    def test_upsert_container_state_updates_existing_container(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore(
            {
                "areas": {
                    "forest": {
                        "container_states": {
                            "crate": {
                                "area_id": "forest",
                                "opened": False,
                            }
                        }
                    }
                }
            }
        )

        area_slice.upsert_container_state(
            "crate",
            {
                "opened": True,
                "area_id": "forest",
            },
        )

        container_state = area_slice.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["opened"] is True
        assert container_state["area_id"] == "forest"

    def test_upsert_container_state_creates_target_area_when_payload_has_area(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        area_slice.upsert_container_state(
            "cache",
            {
                "area_id": "camp",
                "opened": False,
            },
        )

        assert "camp" in area_slice.areas
        container_state = area_slice.get_container_state("camp", "cache")
        assert container_state is not None
        assert container_state["area_id"] == "camp"

    def test_apply_state_change_supports_container_states_path(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        area_slice.apply_state_change(
            StateChange(
                "areas",
                "modify",
                "container_states.crate",
                {
                    "area_id": "forest",
                    "opened": True,
                    "remaining_gold": 3,
                },
            )
        )

        container_state = area_slice.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["opened"] is True
        assert container_state["remaining_gold"] == 3

    def test_register_hostile_normalizes_participants_and_effects(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        area_slice.register_hostile(
            "combat_1",
            {
                "area_id": "forest",
                "status": "engaged",
                "participants": [
                    {
                        "monster_id": "goblin",
                        "hp": "7",
                        "active_effects": [
                            {"effect_id": "held"},
                            "bad",
                        ],
                    },
                    "bad_participant",
                ],
            },
        )

        hostile = area_slice.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"] == [
            {
                "monster_id": "goblin",
                "hp": 7,
                "active_effects": [{"effect_id": "held"}],
            }
        ]

    def test_get_hostile_state_returns_deep_copies(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.register_hostile(
            "combat_1",
            {
                "area_id": "forest",
                "participants": [
                    {
                        "monster_id": "goblin",
                        "hp": 7,
                        "active_effects": [{"effect_id": "held"}],
                    }
                ],
            },
        )

        hostile = area_slice.get_hostile_state("combat_1")
        assert hostile is not None
        hostile["participants"][0]["hp"] = 1
        hostile["participants"][0]["active_effects"][0]["effect_id"] = "changed"

        fresh = area_slice.get_hostile_state("combat_1")
        assert fresh is not None
        assert fresh["participants"][0]["hp"] == 7
        assert fresh["participants"][0]["active_effects"][0]["effect_id"] == "held"

    def test_apply_state_change_normalizes_hostile_tracking_path(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        area_slice.apply_state_change(
            StateChange(
                "areas",
                "modify",
                "hostile_tracking.combat_1",
                {
                    "area_id": "forest",
                    "participants": [
                        {"monster_id": "goblin", "hp": "5", "active_effects": "bad"},
                        "bad",
                    ],
                },
            )
        )

        hostile = area_slice.get_hostile_state("combat_1")
        assert hostile is not None
        assert hostile["participants"] == [{"monster_id": "goblin", "hp": 5}]

    def test_get_permanent_slots_returns_deep_copy(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": ["h1"],
            "refresh_queue": [
                {
                    "refresh_at_tick": 12,
                    "used_template_ids": ["h1"],
                }
            ],
        }

        slots = area_slice.get_permanent_slots("forest")
        assert slots is not None
        slots["active_ids"].append("h2")
        slots["refresh_queue"][0]["used_template_ids"].append("h2")

        fresh = area_slice.get_permanent_slots("forest")
        assert fresh == {
            "max_slots": 1,
            "active_ids": ["h1"],
            "refresh_queue": [
                {
                    "refresh_at_tick": 12,
                    "used_template_ids": ["h1"],
                }
            ],
        }

    def test_sync_permanent_hostile_slots_initializes_default_bucket(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        slots = area_slice.sync_permanent_hostile_slots(
            "forest",
            current_tick=10,
            default_max_slots=1,
            clear_refresh_delay=12,
        )

        assert slots == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [],
        }

    def test_sync_permanent_hostile_slots_converts_cleared_active_to_refresh(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.register_hostile(
            "enc_1",
            {
                "area_id": "forest",
                "status": "cleared",
                "cleared": True,
                "cleared_at_tick": 20,
                "template_id": "forest_patrol",
            },
        )
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": ["enc_1"],
            "refresh_queue": [],
        }

        slots = area_slice.sync_permanent_hostile_slots(
            "forest",
            current_tick=20,
            default_max_slots=1,
            clear_refresh_delay=12,
        )

        assert slots == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 32,
                    "used_template_ids": ["forest_patrol"],
                }
            ],
        }

    def test_sync_permanent_hostile_slots_removes_expired_refresh_queue(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 10,
                    "used_template_ids": [],
                }
            ],
        }

        slots = area_slice.sync_permanent_hostile_slots(
            "forest",
            current_tick=10,
            default_max_slots=1,
            clear_refresh_delay=12,
        )

        assert slots == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [],
        }

    def test_occupy_permanent_hostile_slot_respects_capacity(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        first = area_slice.occupy_permanent_hostile_slot(
            "forest",
            "enc_1",
            default_max_slots=1,
        )
        second = area_slice.occupy_permanent_hostile_slot(
            "forest",
            "enc_2",
            default_max_slots=1,
        )

        assert first["active_ids"] == ["enc_1"]
        assert second["active_ids"] == ["enc_1"]

    def test_occupy_permanent_hostile_slot_ignores_refresh_queue_for_capacity(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 20,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

        slots = area_slice.occupy_permanent_hostile_slot(
            "forest",
            "enc_1",
            default_max_slots=1,
        )

        assert slots["active_ids"] == ["enc_1"]

    def test_cooldown_permanent_hostile_slot_adds_refresh_queue_entry(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})

        slots = area_slice.cooldown_permanent_hostile_slot(
            "forest",
            refresh_at_tick=16,
            used_ids=[],
            default_max_slots=1,
        )

        assert slots == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 16,
                    "used_template_ids": [],
                }
            ],
        }

    def test_cooldown_permanent_hostile_slot_merges_matching_template_cooldowns(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 20,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

        slots = area_slice.cooldown_permanent_hostile_slot(
            "forest",
            refresh_at_tick=24,
            used_ids=["forest:ambient"],
            default_max_slots=1,
        )

        assert slots == {
            "max_slots": 1,
            "active_ids": [],
            "refresh_queue": [
                {
                    "refresh_at_tick": 24,
                    "used_template_ids": ["forest:ambient"],
                }
            ],
        }

    def test_validate_reports_nested_hostile_shape_issues(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").hostile_tracking["bad_1"] = {
            "participants": "not_a_list",
        }
        area_slice.get_area("forest").hostile_tracking["bad_2"] = {
            "participants": [
                {"active_effects": "not_a_list"},
                "bad_participant",
                {"active_effects": ["bad_effect"]},
            ]
        }

        issues = area_slice.validate()

        assert any("participants must be a list" in issue for issue in issues)
        assert any("participant 0 active_effects must be a list" in issue for issue in issues)
        assert any("participant 1 must be a mapping" in issue for issue in issues)
        assert any("active_effect 0 must be a mapping" in issue for issue in issues)

    def test_validate_reports_permanent_slot_shape_issues(self) -> None:
        area_slice = AreaSlice()
        area_slice.restore({"areas": {"forest": {}}})
        area_slice.get_area("forest").permanent_hostile_slots["forest"] = {
            "max_slots": -1,
            "active_ids": ["ok", ""],
            "refresh_queue": [
                {
                    "refresh_at_tick": "bad",
                    "used_template_ids": ["", "ok"],
                },
                "bad_entry",
            ],
        }

        issues = area_slice.validate()

        assert any("max_slots must be an integer >= 0" in issue for issue in issues)
        assert any("active_id 1 must be a non-empty string" in issue for issue in issues)
        assert any("refresh_at_tick must be an integer" in issue for issue in issues)
        assert any("used_template_id 0 must be a non-empty string" in issue for issue in issues)
        assert any("refresh entry 1 must be a mapping" in issue for issue in issues)


class TestInitialAreaPayloadNpcPlacement:
    """Verify that StateContainer.create_new populates npc_locations."""

    def _build_world(self) -> "WorldInstance":
        from app.game_core.content import WorldInstance
        from app.game_core.content.registries import (
            CharacterRegistry, MapRegistry,
        )

        world = WorldInstance("test")
        maps = MapRegistry()
        maps.load({
            "town": {
                "id": "town",
                "name": "Town",
                "is_starting_area": True,
                "sub_locations": {
                    "guild": {
                        "id": "guild",
                        "name": "Guild Hall",
                        "resident_npcs": ["alice", "bob"],
                    },
                    "temple": {
                        "id": "temple",
                        "name": "Temple",
                        "resident_npcs": ["carol"],
                    },
                },
            },
            "forest": {
                "id": "forest",
                "name": "Forest",
                "sub_locations": {},
            },
        })
        world.register(maps)

        chars = CharacterRegistry()
        chars.load({
            "alice": {"id": "alice", "name": "Alice", "area_id": "town"},
            "bob": {"id": "bob", "name": "Bob", "area_id": "town"},
            "carol": {"id": "carol", "name": "Carol", "area_id": "town"},
            "dave": {"id": "dave", "name": "Dave", "area_id": "forest"},
            "eve": {"id": "eve", "name": "Eve", "area_id": ""},
        })
        world.register(chars)
        return world

    def test_resident_npcs_placed_in_sub_locations(self) -> None:
        from app.game_core.state import StateContainer

        world = self._build_world()
        container = StateContainer.create_new(world)

        town = container.areas.areas["town"]
        assert town.npc_locations["alice"] == "guild"
        assert town.npc_locations["bob"] == "guild"
        assert town.npc_locations["carol"] == "temple"

    def test_character_registry_fallback_places_at_area_level(self) -> None:
        from app.game_core.state import StateContainer

        world = self._build_world()
        container = StateContainer.create_new(world)

        forest = container.areas.areas["forest"]
        assert forest.npc_locations["dave"] is None

    def test_character_registry_skips_already_placed(self) -> None:
        from app.game_core.state import StateContainer

        world = self._build_world()
        container = StateContainer.create_new(world)

        # alice is placed by resident_npcs in guild, not overwritten
        town = container.areas.areas["town"]
        assert town.npc_locations["alice"] == "guild"

    def test_character_with_empty_area_not_placed(self) -> None:
        from app.game_core.state import StateContainer

        world = self._build_world()
        container = StateContainer.create_new(world)

        all_npcs: set[str] = set()
        for area in container.areas.areas.values():
            all_npcs.update(area.npc_locations.keys())
        assert "eve" not in all_npcs
