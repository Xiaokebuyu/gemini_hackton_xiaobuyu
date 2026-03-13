"""Tests for CrimeHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CrimeHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice


def _make_world() -> WorldInstance:
    return WorldInstance("test_world")


def _make_state(
    *,
    container_state: dict,
    inventory: list[dict] | None = None,
    stats: dict[str, int] | None = None,
    location: str | None = None,
    room: str | None = None,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "current_area": "forest",
            "current_location": location,
            "current_room": room,
            "inventory": inventory or [],
            "stats": stats
            or {
                "str": 10,
                "dex": 12,
                "con": 10,
                "int": 10,
                "wis": 10,
                "cha": 10,
            },
        }
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "forest": {
                    "container_states": {
                        "crate": {
                            "area_id": "forest",
                            **container_state,
                        }
                    }
                }
            }
        }
    )
    state.register(areas)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(CrimeHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    if result.delta is None:
        return
    state.apply(result.delta)


class TestCrimeHandler:
    def test_default_action_dispatcher_routes_steal(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(
            StructuredAction(action_type="steal", params={"item_id": "gem"})
        )

        assert command is not None
        assert command.type == "steal"

    def test_steal_from_container_transfers_item(self) -> None:
        state = _make_state(
            container_state={
                "opened": True,
                "lock_status": "unlocked",
                "looted": False,
                "remaining_items": [{"item_id": "gem", "count": 2, "tags": ["loot"]}],
                "remaining_gold": 0,
            }
        )
        result = _make_engine().execute(
            Command(
                type="steal",
                params={"container_id": "crate", "item_id": "gem", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "stolen"
        assert result.metadata["passed"] is True
        _apply(result, state)
        assert state.player.get_item_count("gem") == 1
        container_state = state.areas.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["remaining_items"][0]["count"] == 1

    def test_steal_failed_check_returns_detected_without_delta(self) -> None:
        state = _make_state(
            container_state={
                "opened": True,
                "lock_status": "unlocked",
                "looted": False,
                "remaining_items": [{"item_id": "gem", "count": 1}],
                "remaining_gold": 0,
            }
        )
        result = _make_engine().execute(
            Command(
                type="steal",
                params={"container_id": "crate", "item_id": "gem", "dc": 20},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "detected"
        assert result.metadata["detected"] is True

    def test_steal_rejects_npc_theft_in_mvp(self) -> None:
        result = _make_engine().execute(
            Command(type="steal", params={"target_npc": "merchant", "item_id": "gem"}),
            _make_state(container_state={"opened": True}),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["NPC theft unsupported in MVP"]

    def test_lockpick_unlocks_locked_container(self) -> None:
        state = _make_state(container_state={"opened": False, "lock_status": "locked"})
        result = _make_engine().execute(
            Command(type="lockpick", params={"target": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "unlocked"
        _apply(result, state)
        container_state = state.areas.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["lock_status"] == "unlocked"

    def test_lockpick_failed_check_returns_no_delta(self) -> None:
        state = _make_state(container_state={"opened": False, "lock_status": "locked"})
        result = _make_engine().execute(
            Command(type="lockpick", params={"container_id": "crate", "dc": 20}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "failed"
        assert result.metadata["passed"] is False

    def test_lockpick_rejects_container_in_other_room(self) -> None:
        state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "locked",
                "location_id": "camp",
                "room_id": "office",
            },
            location="camp",
            room="tent",
        )
        result = _make_engine().execute(
            Command(type="lockpick", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["container_not_in_current_scene"]
