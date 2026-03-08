"""Tests for ContainerHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import ContainerHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice


def _make_world() -> WorldInstance:
    return WorldInstance("test_world")


def _make_state(
    *,
    container_state: dict,
    hp: int = 12,
    gold: int = 0,
    inventory: list[dict] | None = None,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "current_area": "forest",
            "hp": hp,
            "max_hp": 12,
            "gold": gold,
            "inventory": inventory or [],
            "stats": {
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
    engine.register(ContainerHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    if result.delta is None:
        return
    state.apply(result.delta)


class TestContainerHandler:
    def test_default_action_dispatcher_routes_open_container(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(
            StructuredAction(action_type="open_container", params={"container_id": "crate"})
        )

        assert command is not None
        assert command.type == "open_container"

    def test_open_container_returns_existing_loot_when_already_open(self) -> None:
        state = _make_state(
            container_state={
                "opened": True,
                "lock_status": "unlocked",
                "remaining_items": [{"item_id": "gem", "count": 1}],
                "remaining_gold": 3,
            }
        )
        result = _make_engine().execute(
            Command(type="open_container", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "opened"
        assert result.metadata["gold"] == 3

    def test_open_container_triggers_undetected_trap_before_locked_gate(self) -> None:
        state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "locked",
                "trap_status": "armed",
                "trap_detected": False,
                "trap_damage": 3,
                "remaining_items": [],
                "remaining_gold": 0,
            }
        )
        result = _make_engine().execute(
            Command(type="open_container", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "locked"
        assert result.metadata["trap_triggered"] is True
        assert result.metadata["trap_damage"] == 3
        _apply(result, state)
        assert state.player.hp == 9
        container_state = state.areas.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["trap_status"] == "triggered"
        assert container_state.get("opened", False) is False

    def test_open_container_returns_trap_detected_when_player_knows_trap(self) -> None:
        state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "unlocked",
                "trap_status": "armed",
                "trap_detected": True,
            }
        )
        result = _make_engine().execute(
            Command(type="open_container", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "trap_detected"

    def test_open_container_sets_opened_when_safe(self) -> None:
        state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "unlocked",
                "remaining_items": [{"item_id": "gem", "count": 1}],
                "remaining_gold": 0,
            }
        )
        result = _make_engine().execute(
            Command(type="open_container", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "opened"
        _apply(result, state)
        assert state.areas.get_container_state("forest", "crate")["opened"] is True

    def test_disarm_trap_success_and_failure_paths(self) -> None:
        success_state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "unlocked",
                "trap_status": "armed",
                "trap_detected": True,
                "trap_disarm_dc": 12,
                "trap_damage": 4,
            }
        )
        failure_state = _make_state(
            container_state={
                "opened": False,
                "lock_status": "unlocked",
                "trap_status": "armed",
                "trap_detected": True,
                "trap_disarm_dc": 20,
                "trap_damage": 4,
            }
        )

        disarm_result = _make_engine().execute(
            Command(type="disarm_trap", params={"container_id": "crate"}),
            success_state,
            _make_world(),
        )
        failure = _make_engine().execute(
            Command(type="disarm_trap", params={"container_id": "crate"}),
            failure_state,
            _make_world(),
        )

        assert disarm_result.executed is True
        assert disarm_result.metadata["status"] == "disarmed"
        _apply(disarm_result, success_state)
        assert success_state.areas.get_container_state("forest", "crate")["trap_status"] == "disarmed"

        assert failure.executed is True
        assert failure.metadata["status"] == "trap_triggered"
        _apply(failure, failure_state)
        assert failure_state.player.hp == 8
        assert failure_state.areas.get_container_state("forest", "crate")["trap_status"] == "triggered"

    def test_take_from_container_transfers_specific_item(self) -> None:
        state = _make_state(
            container_state={
                "opened": True,
                "lock_status": "unlocked",
                "remaining_items": [{"item_id": "gem", "count": 2, "tags": ["loot"]}],
                "remaining_gold": 0,
            }
        )
        result = _make_engine().execute(
            Command(
                type="take_from_container",
                params={"container_id": "crate", "item_id": "gem", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "taken"
        _apply(result, state)
        assert state.player.get_item_count("gem") == 1
        assert state.areas.get_container_state("forest", "crate")["remaining_items"][0]["count"] == 1

    def test_take_all_transfers_items_and_gold(self) -> None:
        state = _make_state(
            container_state={
                "opened": True,
                "lock_status": "unlocked",
                "remaining_items": [
                    {"item_id": "gem", "count": 1},
                    {"item_id": "scroll", "count": 2},
                ],
                "remaining_gold": 7,
            },
            gold=3,
        )
        result = _make_engine().execute(
            Command(type="take_all", params={"container_id": "crate"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "looted"
        assert result.metadata["gold"] == 7
        _apply(result, state)
        assert state.player.gold == 10
        assert state.player.get_item_count("gem") == 1
        assert state.player.get_item_count("scroll") == 2
        container_state = state.areas.get_container_state("forest", "crate")
        assert container_state is not None
        assert container_state["remaining_items"] == []
        assert container_state["remaining_gold"] == 0
        assert container_state["looted"] is True


class TestInteractObject:
    @staticmethod
    def _make_interact_state(
        *,
        interactables: dict | None = None,
    ) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "character_id": "pc_1",
            "current_area": "forest",
            "hp": 12,
            "max_hp": 12,
        })
        state.register(player)

        areas = AreaSlice()
        props: dict = {}
        if interactables is not None:
            props["interactables"] = interactables
        areas.restore({
            "areas": {
                "forest": {
                    "properties": props,
                }
            }
        })
        state.register(areas)
        return state

    def test_interact_object_examine(self) -> None:
        state = self._make_interact_state(
            interactables={
                "bulletin_board": {
                    "type": "notice_board",
                    "description": "A weathered wooden board covered in notices.",
                },
            },
        )
        result = _make_engine().execute(
            Command(type="interact_object", params={"object_id": "bulletin_board"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["status"] == "examined"
        assert result.metadata["object_id"] == "bulletin_board"
        assert result.metadata["description"] == "A weathered wooden board covered in notices."
        assert result.delta is None

    def test_interact_object_not_found(self) -> None:
        state = self._make_interact_state(interactables={})
        result = _make_engine().execute(
            Command(type="interact_object", params={"object_id": "nonexistent"}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "object not found" in result.errors[0]

    def test_interact_object_requires_check(self) -> None:
        state = self._make_interact_state(
            interactables={
                "locked_gate": {
                    "type": "mechanism",
                    "description": "A rusty gate mechanism.",
                    "requires_check": True,
                    "check_skill": "athletics",
                    "check_dc": 15,
                },
            },
        )
        result = _make_engine().execute(
            Command(type="interact_object", params={"object_id": "locked_gate"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["status"] == "examined"
        assert result.metadata["requires_check"] is True
        assert result.metadata["check_skill"] == "athletics"
        assert result.metadata["check_dc"] == 15
