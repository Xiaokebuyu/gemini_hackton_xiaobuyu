"""Tests for the board interaction command handler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.defaults import DEFAULT_ACTION_COMMAND_TYPES
from app.game_core.rules import Command
from app.game_core.rules.handlers import BoardHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, NarrativePlanSlice, PlayerSlice, QuestSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "city": {
                "id": "city",
                "name": "City of Ash",
                "sub_locations": {
                    "square": {
                        "id": "square",
                        "name": "Market Square",
                        "interactables": [
                            {"id": "quest_board", "name": "Quest Board"},
                        ],
                    },
                    "dock": {
                        "id": "dock",
                        "name": "Dock",
                    },
                },
            }
        }
    )
    world.register(maps)
    return world


def _make_player_state(
    *,
    current_area: str = "city",
    current_location: str = "square",
) -> PlayerSlice:
    player = PlayerSlice()
    player.restore(
        {
            "current_area": current_area,
            "current_location": current_location,
        }
    )
    return player


def _make_narrative_state(
) -> NarrativePlanSlice:
    narrative = NarrativePlanSlice()
    narrative.restore({})
    return narrative


def _make_quest_state(dynamic_statuses: dict[str, str]) -> QuestSlice:
    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                quest_id: {"id": quest_id, "status": status}
                for quest_id, status in dynamic_statuses.items()
            }
        }
    )
    return quests


def _make_state(
    *,
    dynamic_statuses: dict[str, str] | None = None,
    board_bulletins: list[dict[str, str]] | None = None,
    current_area: str = "city",
    current_location: str = "square",
) -> StateContainer:
    state = StateContainer()
    state.register(_make_player_state(current_area=current_area, current_location=current_location))
    state.register(_make_narrative_state())
    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                current_area: {
                    "board_bulletins": {
                        "quest_board": board_bulletins
                        if board_bulletins is not None
                        else [
                            {"board_id": "quest_board", "quest_id": "q_available", "title": "拾取古物"},
                            {"board_id": "quest_board", "quest_id": "q_active", "title": "护送商队"},
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)
    state.register(_make_quest_state(dynamic_statuses or {}))
    return state


def _execute(command: Command, state: StateContainer, world: WorldInstance):
    return BoardHandler().compute(command, state, world)


def test_board_handler_registers_expected_command_types() -> None:
    handler = BoardHandler()
    assert set(handler.COMMAND_TYPES) == {
        "browse_board",
        "board_accept_quest",
        "board_complete_quest",
        "board_retire_quest",
    }


def test_default_action_map_registers_board_commands() -> None:
    mapping = {action: command for action, command in DEFAULT_ACTION_COMMAND_TYPES}
    assert mapping["browse_board"] == "browse_board"
    assert mapping["board_accept_quest"] == "board_accept_quest"
    assert mapping["board_complete_quest"] == "board_complete_quest"
    assert mapping["board_retire_quest"] == "board_retire_quest"


def test_validate_board_not_present_in_player_location() -> None:
    state = _make_state(current_location="dock")
    result = BoardHandler().validate(
        Command(type="browse_board", params={"board_id": "quest_board"}),
        state,
        _make_world(),
    )
    assert result.ok is False
    assert result.reason == "board interactable not found"


def test_validate_rejects_missing_quest_id_for_action() -> None:
    state = _make_state()
    result = BoardHandler().validate(
        Command(type="board_accept_quest", params={"board_id": "quest_board"}),
        state,
        _make_world(),
    )
    assert result.ok is False
    assert result.reason == "quest_id required"


def test_validate_rejects_player_without_sub_location() -> None:
    state = _make_state(current_location="")
    result = BoardHandler().validate(
        Command(type="browse_board", params={"board_id": "quest_board"}),
        state,
        _make_world(),
    )
    assert result.ok is False
    assert result.reason == "current sub-location required"


def test_browse_board_success_returns_filtered_entries_with_status() -> None:
    state = _make_state(
        dynamic_statuses={"q_available": "available", "q_active": "active"},
    )
    world = _make_world()
    result = _execute(Command(type="browse_board", params={"board_id": "quest_board"}), state, world)

    assert result.executed is True
    assert result.time_cost == 1 / 6
    assert result.delta is None
    assert result.metadata["board_id"] == "quest_board"
    assert len(result.metadata["entries"]) == 2
    assert {entry["quest_id"] for entry in result.metadata["entries"]} == {
        "q_available",
        "q_active",
    }
    status_map = {entry["quest_id"]: entry.get("quest_status") for entry in result.metadata["entries"]}
    assert status_map["q_available"] == "available"
    assert status_map["q_active"] == "active"


def test_board_accept_quest_success_marks_dynamic_quest_active() -> None:
    state = _make_state(
        dynamic_statuses={"q_available": "available"},
    )
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "q_available"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.time_cost == 1 / 6
    assert result.metadata["quest_id"] == "q_available"
    assert result.delta is not None
    state.apply(result.delta)
    assert state.quests.dynamic_quests["q_available"]["status"] == "active"


def test_board_accept_quest_rejects_completed_or_active() -> None:
    state = _make_state(
        dynamic_statuses={"q_active": "active", "q_done": "completed"},
        board_bulletins=[
            {"board_id": "quest_board", "quest_id": "q_active", "title": "护送商队"},
            {"board_id": "quest_board", "quest_id": "q_done", "title": "撤销的任务"},
        ],
    )
    world = _make_world()
    result_active = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "q_active"},
        ),
        state,
        world,
    )
    result_done = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "q_done"},
        ),
        state,
        world,
    )

    assert result_active.executed is False
    assert result_done.executed is False
    assert any("cannot be accepted" in err for err in result_active.errors)
    assert any("cannot be accepted" in err for err in result_done.errors)


def test_browse_board_empty_board_returns_empty_entries() -> None:
    state = _make_state(
        dynamic_statuses={"q_available": "available"},
        board_bulletins=[],
    )
    world = _make_world()
    result = _execute(
        Command(type="browse_board", params={"board_id": "quest_board"}),
        state,
        world,
    )

    assert result.executed is True
    assert result.metadata["entries"] == []


def test_board_accept_quest_rejects_missing_quest_in_board() -> None:
    state = _make_state(
        dynamic_statuses={"q_available": "available"},
        board_bulletins=[
            {"board_id": "quest_board", "quest_id": "q_other", "title": "其他任务"},
        ],
    )
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "q_missing"},
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("not found on board" in err for err in result.errors)


def test_board_complete_quest_success_marks_completed() -> None:
    state = _make_state(
        dynamic_statuses={"q_report": "ready_to_report"},
        board_bulletins=[
            {"board_id": "quest_board", "quest_id": "q_report", "title": "护送商队"},
        ],
    )
    world = _make_world()
    result = _execute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "q_report"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.time_cost == 1 / 6
    state.apply(result.delta)
    assert state.quests.dynamic_quests["q_report"]["status"] == "completed"


def test_board_complete_quest_rejects_active_status() -> None:
    """Active quests cannot be completed from the board — must reach ready_to_report first."""
    state = _make_state(dynamic_statuses={"q_active": "active"})
    world = _make_world()
    result = _execute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "q_active"},
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("not ready to report" in err for err in result.errors)


def test_board_complete_quest_rejects_available_status() -> None:
    state = _make_state(dynamic_statuses={"q_available": "available"})
    world = _make_world()
    result = _execute(
        Command(
            type="board_complete_quest",
            params={"board_id": "quest_board", "quest_id": "q_available"},
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("not ready to report" in err for err in result.errors)


def test_board_retire_quest_success_marks_retired() -> None:
    state = _make_state(dynamic_statuses={"q_active": "active"})
    world = _make_world()
    result = _execute(
        Command(
            type="board_retire_quest",
            params={"board_id": "quest_board", "quest_id": "q_active"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.time_cost == 1 / 6
    state.apply(result.delta)
    assert state.quests.dynamic_quests["q_active"]["status"] == "retired"


def test_board_commands_only_affect_quests_on_board() -> None:
    state = _make_state(
        dynamic_statuses={"q_other": "active"},
        current_area="city",
        current_location="square",
    )
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "q_other"},
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("not found on board" in err for err in result.errors)


# ---------------------------------------------------------------------------
# Phase 3 (P26): level wall tests
# ---------------------------------------------------------------------------

def _make_state_with_level(
    *,
    player_level: int = 1,
    quest_min_level: int = 1,
    quest_status: str = "available",
) -> StateContainer:
    """Build a minimal state with a player at a specific level and one levelled quest."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "city",
            "current_location": "square",
            "level": player_level,
        }
    )
    state.register(player)
    state.register(_make_narrative_state())

    areas = AreaSlice()
    areas.restore(
        {
            "areas": {
                "city": {
                    "board_bulletins": {
                        "quest_board": [
                            {
                                "board_id": "quest_board",
                                "quest_id": "dq_levelled",
                                "title": "中级讨伐任务",
                            }
                        ]
                    }
                }
            }
        }
    )
    state.register(areas)

    quests = QuestSlice()
    quests.restore(
        {
            "dynamic_quests": {
                "dq_levelled": {
                    "quest_id": "dq_levelled",
                    "status": quest_status,
                    "title": "中级讨伐任务",
                    "min_level": quest_min_level,
                }
            }
        }
    )
    state.register(quests)

    return state


def test_board_accept_quest_rejects_level_too_low() -> None:
    """Player level 1 should not be able to accept a min_level=2 quest."""
    state = _make_state_with_level(player_level=1, quest_min_level=2)
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "dq_levelled"},
        ),
        state,
        world,
    )

    assert result.executed is False
    assert any("below quest minimum" in err for err in result.errors)


def test_board_accept_quest_allows_sufficient_level() -> None:
    """Player level 2 can accept a min_level=2 quest."""
    state = _make_state_with_level(player_level=2, quest_min_level=2)
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "dq_levelled"},
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.quests.dynamic_quests["dq_levelled"]["status"] == "active"


def test_board_accept_quest_no_min_level_allows_level_1() -> None:
    """Quest with no min_level (defaults to 1) can be accepted by any level player."""
    state = _make_state_with_level(player_level=1, quest_min_level=1)
    world = _make_world()
    result = _execute(
        Command(
            type="board_accept_quest",
            params={"board_id": "quest_board", "quest_id": "dq_levelled"},
        ),
        state,
        world,
    )

    assert result.executed is True


def test_browse_board_includes_level_locked_field() -> None:
    """browse_board entries should contain min_level and level_locked fields."""
    state = _make_state_with_level(player_level=1, quest_min_level=2)
    world = _make_world()
    result = _execute(
        Command(type="browse_board", params={"board_id": "quest_board"}),
        state,
        world,
    )

    assert result.executed is True
    entries = result.metadata["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["min_level"] == 2
    assert entry["level_locked"] is True


def test_browse_board_level_unlocked_when_player_meets_requirement() -> None:
    """A level-2 player looking at a min_level=2 quest should see level_locked=False."""
    state = _make_state_with_level(player_level=2, quest_min_level=2)
    world = _make_world()
    result = _execute(
        Command(type="browse_board", params={"board_id": "quest_board"}),
        state,
        world,
    )

    assert result.executed is True
    entries = result.metadata["entries"]
    entry = entries[0]
    assert entry["min_level"] == 2
    assert entry["level_locked"] is False
