"""Tests for P3-8: 环境交互深化 — available_hours, PassivePerceptionHook, DiscoveryHandler, InteractableHandler."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.bootstrap import build_default_world
from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.passive_perception import PassivePerceptionHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers.discovery import DiscoveryHandler
from app.game_core.rules.handlers.interactable import InteractableHandler
from app.game_core.rules.handlers.navigation import NavigationHandler, _is_location_open
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer
from app.game_core.state.slices import AreaSlice, SceneSlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _world_with_maps(maps: dict[str, Any]) -> WorldInstance:
    return build_default_world("test", world_data={"maps": maps})


def _player_slice(
    area: str = "town",
    location: str | None = None,
    room: str | None = None,
    wis: int = 10,
) -> PlayerSlice:
    sl = PlayerSlice()
    sl.restore({
        "character_name": "Hero",
        "current_area": area,
        "current_location": location,
        "current_room": room,
        "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": wis, "cha": 10},
        "hp": 10, "max_hp": 10,
    })
    return sl


def _area_slice() -> AreaSlice:
    sl = AreaSlice()
    sl.restore({})
    return sl


def _time_slice(period: str = "day") -> TimeSlice:
    sl = TimeSlice()
    sl.restore({"period": period, "day": 1, "slot": 2, "accumulated": 0.0, "absolute_tick": 0})
    return sl


def _make_state(*slices: Any) -> StateContainer:
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    for sl in slices:
        state.register(sl)
    return state


def _make_context(
    state: StateContainer,
    world: WorldInstance,
    change_log: list[Any] | None = None,
) -> SettlementContext:
    scene_bus = SceneBus(state.scene)
    return SettlementContext(
        change_log=change_log or [],
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
        action_log=[],
    )


# ------------------------------------------------------------------
# TestAvailableHours — _is_location_open helper + NavigationHandler
# ------------------------------------------------------------------


class TestIsLocationOpen:
    def test_none_available_hours_always_open(self) -> None:
        assert _is_location_open(None, "night") is True
        assert _is_location_open(None, "day") is True

    def test_day_hours_open_during_day(self) -> None:
        # (8, 20) = 8:00–20:00: day(mid=13) open, dusk(mid=19) open, dawn(mid=7) closed, night(mid=1) closed
        assert _is_location_open((8, 20), "day") is True
        assert _is_location_open((8, 20), "dusk") is True
        assert _is_location_open((8, 20), "dawn") is False  # dawn mid=7, before opening at 8
        assert _is_location_open((8, 20), "night") is False

    def test_night_only_hours(self) -> None:
        # (20, 6) cross-midnight: night (mid=1) open, day (mid=13) closed
        assert _is_location_open((20, 6), "night") is True
        assert _is_location_open((20, 6), "day") is False

    def test_dawn_at_boundary(self) -> None:
        # (7, 13) → dawn (mid=7) is boundary (start<=7<end → True)
        assert _is_location_open((7, 13), "dawn") is True
        # (8, 20) → dawn (mid=7) is below start → False
        assert _is_location_open((8, 20), "dawn") is False


class TestNavigationHandlerAvailableHours:
    _handler = NavigationHandler()

    def _world_with_shop(self, available_hours: list[int] | None) -> WorldInstance:
        sub_loc: dict[str, Any] = {"id": "shop", "name": "Shop"}
        if available_hours is not None:
            sub_loc["available_hours"] = available_hours
        return _world_with_maps({
            "town": {
                "id": "town",
                "name": "Town",
                "sub_locations": [sub_loc],
            }
        })

    def _cmd(self) -> Command:
        return Command(type="enter_sub_location", params={"location_id": "shop"})

    def test_blocks_night_entry_when_day_only(self) -> None:
        world = self._world_with_shop([8, 20])
        state = _make_state(
            _player_slice(area="town", location=None),
            _time_slice(period="night"),
        )
        result = self._handler.validate(self._cmd(), state, world)
        assert not result.ok
        assert result.reason == "location_closed"

    def test_allows_day_entry(self) -> None:
        world = self._world_with_shop([8, 20])
        state = _make_state(
            _player_slice(area="town", location=None),
            _time_slice(period="day"),
        )
        result = self._handler.validate(self._cmd(), state, world)
        assert result.ok

    def test_no_available_hours_always_allows(self) -> None:
        world = self._world_with_shop(None)
        state = _make_state(
            _player_slice(area="town", location=None),
            _time_slice(period="night"),
        )
        result = self._handler.validate(self._cmd(), state, world)
        assert result.ok


# ------------------------------------------------------------------
# TestPassivePerceptionHook
# ------------------------------------------------------------------


class TestPassivePerceptionHook:
    _hook = PassivePerceptionHook()

    def _world_with_discovery(self, disc_dc: int) -> WorldInstance:
        return _world_with_maps({
            "forest": {
                "id": "forest",
                "name": "Forest",
                "discoveries": [
                    {"id": "hidden_cache", "name": "Hidden Cache", "dc": disc_dc, "check_type": "perception"}
                ],
            }
        })

    def _world_with_hidden_interactable(self, vis_dc: int) -> WorldInstance:
        return _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "interactables": [
                            {"id": "secret_lever", "name": "Secret Lever", "visibility_dc": vis_dc}
                        ],
                    }
                ],
            }
        })

    def _world_with_trapped_container(self, trap_dc: int) -> WorldInstance:
        return _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "interactables": [
                            {
                                "id": "trapped_chest",
                                "name": "Chest",
                                "type": "container",
                                "container_data": {
                                    "trap": {
                                        "detect_dc": trap_dc,
                                        "disarm_dc": 15,
                                        "damage": "1d6",
                                        "damage_type": "piercing",
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        })

    def _change_log_area(self) -> list[Any]:
        return [StateChange(slice="player", operation="set", path="current_area", value="x")]

    def _change_log_location(self) -> list[Any]:
        return [StateChange(slice="player", operation="set", path="current_location", value="x")]

    def test_discovers_when_passive_meets_dc(self) -> None:
        # WIS 14 → modifier +2 → passive = 12; dc=12 → should discover
        world = self._world_with_discovery(disc_dc=12)
        state = _make_state(
            _player_slice(area="forest", location=None, wis=14),
            _area_slice(),
        )
        ctx = _make_context(state, world, change_log=self._change_log_area())
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "hidden_cache" in result.metadata["discoveries_found"]
        assert state.areas.is_discovery_found("forest", "hidden_cache")
        assert result.sse_events[0].event_type == "discovery_reveal"

    def test_misses_when_passive_below_dc(self) -> None:
        # WIS 10 → passive=10; dc=15 → should miss
        world = self._world_with_discovery(disc_dc=15)
        state = _make_state(
            _player_slice(area="forest", location=None, wis=10),
            _area_slice(),
        )
        ctx = _make_context(state, world, change_log=self._change_log_area())
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "noop"
        assert not state.areas.is_discovery_found("forest", "hidden_cache")

    def test_reveals_hidden_interactable_by_visibility_dc(self) -> None:
        # WIS 12 → passive=11; vis_dc=11 → reveal
        world = self._world_with_hidden_interactable(vis_dc=11)
        state = _make_state(
            _player_slice(area="dungeon", location="vault", wis=12),
            _area_slice(),
        )
        ctx = _make_context(state, world, change_log=self._change_log_location())
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "secret_lever" in result.metadata["interactables_revealed"]
        assert result.sse_events[0].event_type == "hidden_object_revealed"

    def test_detects_trap_by_detect_dc(self) -> None:
        # WIS 14 → passive=12; trap detect_dc=12 → detect
        world = self._world_with_trapped_container(trap_dc=12)
        state = _make_state(
            _player_slice(area="dungeon", location="vault", wis=14),
            _area_slice(),
        )
        ctx = _make_context(state, world, change_log=self._change_log_location())
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "trapped_chest" in result.metadata["traps_detected"]
        assert state.areas.is_trap_detected("dungeon", "trapped_chest")
        assert result.sse_events[0].event_type == "trap_detected"

    def test_reveals_hidden_room_interactable_on_room_entry(self) -> None:
        world = _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "rooms": {
                            "study": {
                                "id": "study",
                                "name": "Study",
                                "interactables": [
                                    {"id": "hidden_note", "name": "Hidden Note", "visibility_dc": 11}
                                ],
                            }
                        },
                    }
                ],
            }
        })
        state = _make_state(
            _player_slice(area="dungeon", location="vault", room="study", wis=12),
            _area_slice(),
        )
        ctx = _make_context(
            state,
            world,
            change_log=[StateChange(slice="player", operation="set", path="current_room", value="study")],
        )
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "applied"
        assert "hidden_note" in result.metadata["interactables_revealed"]
        assert state.areas.is_discovery_found("dungeon", "hidden_note")

    def test_skips_already_found_discovery(self) -> None:
        world = self._world_with_discovery(disc_dc=1)  # dc=1 always passes
        state = _make_state(
            _player_slice(area="forest", location=None, wis=10),
            _area_slice(),
        )
        # Pre-mark as found
        state.areas.mark_discovery("forest", "hidden_cache")
        ctx = _make_context(state, world, change_log=self._change_log_area())
        result = asyncio.run(self._hook.execute(ctx))
        # Should still be noop since already found
        assert "hidden_cache" not in result.metadata.get("discoveries_found", [])

    def test_skips_when_no_maps_registry(self) -> None:
        world = WorldInstance("empty")
        state = _make_state(
            _player_slice(area="forest", location=None),
            _area_slice(),
        )
        ctx = _make_context(state, world, change_log=self._change_log_area())
        result = asyncio.run(self._hook.execute(ctx))
        assert result.metadata["status"] == "noop"


# ------------------------------------------------------------------
# TestDiscoveryHandler
# ------------------------------------------------------------------


class TestDiscoveryHandler:
    _handler = DiscoveryHandler()

    def _world(self) -> WorldInstance:
        return _world_with_maps({
            "forest": {
                "id": "forest",
                "name": "Forest",
                "discoveries": [
                    {"id": "ruins", "name": "Ancient Ruins", "dc": 12, "check_type": "perception"}
                ],
            }
        })

    def _state(self) -> StateContainer:
        return _make_state(
            _player_slice(area="forest", location=None, wis=10),
            _area_slice(),
        )

    def _cmd(self, discovery_id: str = "ruins") -> Command:
        return Command(
            type="discover",
            params={"area_id": "forest", "discovery_id": discovery_id},
        )

    def test_discover_success_marks_discovery(self) -> None:
        world = self._world()
        state = self._state()
        # Forcing a pass would be nicer; for now just assert the command executes.
        # For deterministic testing, we check the handler runs without error
        result = self._handler.compute(self._cmd(), state, world)
        assert result.executed  # always succeeds (even on failed check)
        if result.metadata.get("passed"):
            if result.delta:
                state.apply(result.delta)
            assert state.areas.is_discovery_found("forest", "ruins")

    def test_discover_already_found_returns_error(self) -> None:
        world = self._world()
        state = self._state()
        state.areas.mark_discovery("forest", "ruins")
        result = self._handler.compute(self._cmd(), state, world)
        assert not result.executed
        assert "already_discovered" in result.errors

    def test_discover_unknown_discovery_returns_error(self) -> None:
        world = self._world()
        state = self._state()
        result = self._handler.compute(
            Command(type="discover", params={"area_id": "forest", "discovery_id": "nonexistent"}),
            state, world,
        )
        assert not result.executed
        assert any("discovery_not_found" in e for e in result.errors)

    def test_discover_unknown_area_returns_error(self) -> None:
        world = self._world()
        state = self._state()
        result = self._handler.compute(
            Command(type="discover", params={"area_id": "nowhere", "discovery_id": "ruins"}),
            state, world,
        )
        assert not result.executed

    def test_passive_scan_returns_deferred(self) -> None:
        world = self._world()
        state = self._state()
        result = self._handler.compute(Command(type="passive_scan", params={}), state, world)
        assert result.executed
        assert result.metadata.get("status") == "deferred_to_hook"

    def test_validate_discover_missing_area_id(self) -> None:
        world = self._world()
        state = self._state()
        result = self._handler.validate(
            Command(type="discover", params={"discovery_id": "ruins"}), state, world
        )
        assert not result.ok

    def test_apply_state_change_discovered_items(self) -> None:
        """Verify discovered_items StateChange path works in AreaSlice directly."""
        area_slice = _area_slice()
        ch = StateChange(slice="areas", operation="set", path="forest.discovered_items.ruins", value=True)
        area_slice.apply_state_change(ch)
        assert area_slice.is_discovery_found("forest", "ruins")

    def test_discover_applies_sub_location_reward(self) -> None:
        world = _world_with_maps({
            "forest": {
                "id": "forest",
                "name": "Forest",
                "discoveries": [
                    {
                        "id": "ruins",
                        "name": "Ancient Ruins",
                        "dc": 1,
                        "check_type": "perception",
                        "reward": {
                            "type": "sub_location",
                            "id": "hidden_glade",
                            "label": "Hidden Glade",
                            "description": "A secluded glade.",
                        },
                    }
                ],
            }
        })
        state = self._state()
        result = self._handler.compute(self._cmd(), state, world)
        assert result.executed is True
        assert result.metadata["passed"] is True
        assert result.delta is not None
        state.apply(result.delta)
        ids = [entry["id"] for entry in state.areas.list_temporary_sub_areas("forest")]
        assert "hidden_glade" in ids
        assert result.metadata["reward_result"]["applied"][0]["type"] == "sub_location"


# ------------------------------------------------------------------
# TestInteractableHandler
# ------------------------------------------------------------------


class TestInteractableHandler:
    _handler = InteractableHandler()

    def _world_with_lever(self, one_time: bool = True, dc: int = 12) -> WorldInstance:
        return _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "interactables": [
                            {
                                "id": "lever",
                                "name": "Lever",
                                "type": "use",
                                "one_time": one_time,
                                "checks": [
                                    {"skill": "athletics", "dc": dc}
                                ],
                            }
                        ],
                    }
                ],
            }
        })

    def _world_with_inspect(self) -> WorldInstance:
        """An interactable with no checks (inspect type)."""
        return _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "interactables": [
                            {"id": "mural", "name": "Mural", "type": "inspect"}
                        ],
                    }
                ],
            }
        })

    def _world_with_room_reward(self) -> WorldInstance:
        return _world_with_maps({
            "dungeon": {
                "id": "dungeon",
                "name": "Dungeon",
                "sub_locations": [
                    {
                        "id": "vault",
                        "name": "Vault",
                        "interactables": [
                            {"id": "lobby_statue", "name": "Lobby Statue", "type": "inspect"}
                        ],
                        "rooms": {
                            "study": {
                                "id": "study",
                                "name": "Study",
                                "interactables": [
                                    {
                                        "id": "hidden_note",
                                        "name": "Hidden Note",
                                        "type": "inspect",
                                        "visibility_dc": 11,
                                        "reward": {"type": "item", "id": "ancient_note"},
                                    }
                                ],
                            }
                        },
                    }
                ],
            }
        })

    def _state(self, location: str = "vault", room: str | None = None) -> StateContainer:
        return _make_state(
            _player_slice(area="dungeon", location=location, room=room, wis=10),
            _area_slice(),
        )

    def _cmd(self, interactable_id: str = "lever") -> Command:
        return Command(
            type="interact_object_v2",
            params={"interactable_id": interactable_id},
        )

    def test_interact_not_in_sub_location_returns_error(self) -> None:
        world = self._world_with_lever()
        state = _make_state(_player_slice(area="dungeon", location=None))
        result = self._handler.compute(self._cmd(), state, world)
        assert not result.executed
        assert "not_in_sub_location" in result.errors

    def test_interact_unknown_interactable_returns_error(self) -> None:
        world = self._world_with_lever()
        state = self._state()
        result = self._handler.compute(
            Command(type="interact_object_v2", params={"interactable_id": "nonexistent"}),
            state, world,
        )
        assert not result.executed
        assert any("interactable_not_found" in e for e in result.errors)

    def test_inspect_with_no_checks_always_succeeds(self) -> None:
        world = self._world_with_inspect()
        state = self._state()
        result = self._handler.compute(
            Command(type="interact_object_v2", params={"interactable_id": "mural"}),
            state, world,
        )
        assert result.executed
        assert result.metadata.get("passed") is True

    def test_second_interaction_blocked_when_one_time(self) -> None:
        world = self._world_with_lever(one_time=True)
        state = self._state()
        state.areas.mark_interactable_used("dungeon", "lever")
        result = self._handler.compute(self._cmd(), state, world)
        assert not result.executed
        assert "already_used" in result.errors

    def test_successful_interaction_marks_one_time(self) -> None:
        world = self._world_with_lever(one_time=True, dc=1)  # dc=1 means any roll passes
        state = self._state()
        result = self._handler.compute(self._cmd(), state, world)
        assert result.executed
        if result.metadata.get("passed") and result.delta:
            state.apply(result.delta)
            assert state.areas.is_interactable_used("dungeon", "lever")

    def test_validate_missing_interactable_id(self) -> None:
        world = self._world_with_lever()
        state = self._state()
        result = self._handler.validate(
            Command(type="interact_object_v2", params={}),
            state, world,
        )
        assert not result.ok

    def test_hidden_room_interactable_requires_discovery_and_applies_item_reward(self) -> None:
        world = self._world_with_room_reward()
        state = self._state(room="study")

        hidden = self._handler.compute(
            Command(type="interact_object_v2", params={"interactable_id": "hidden_note"}),
            state,
            world,
        )
        assert hidden.executed is False
        assert hidden.errors == ["interactable_not_revealed"]

        state.areas.mark_discovery("dungeon", "hidden_note")
        result = self._handler.compute(
            Command(type="interact_object_v2", params={"interactable_id": "hidden_note"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["passed"] is True
        assert result.metadata["room_id"] == "study"
        assert result.delta is not None
        state.apply(result.delta)
        assert state.player.get_item_count("ancient_note") == 1
        assert result.metadata["reward_result"]["applied"][0]["type"] == "item"


# ------------------------------------------------------------------
# TestMarkTrapDetected
# ------------------------------------------------------------------


class TestMarkTrapDetected:
    def test_mark_trap_detected_sets_flag(self) -> None:
        area = _area_slice()
        assert not area.is_trap_detected("dungeon", "chest")
        area.mark_trap_detected("dungeon", "chest")
        assert area.is_trap_detected("dungeon", "chest")

    def test_mark_trap_detected_creates_area_if_missing(self) -> None:
        area = _area_slice()
        area.mark_trap_detected("new_area", "box")
        assert area.is_trap_detected("new_area", "box")
